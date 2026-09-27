"""共享测试夹具。

所有测试都在容器内离线运行：不访问网络、不下载模型、不依赖 GPU。
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import create_db_engine, create_session_factory, init_database
from app.embedding.base import descriptor_for
from app.embedding.factory import build_embedding_provider
from app.llm.factory import build_llm_provider
from app.main import create_app
from app.rerank.factory import build_rerank_provider
from app.vector.store import ChromaVectorStore
from app.worker.runner import Worker

DEMO_DATASET_PATH = Path("/app/demo")
DEMO_CORPUS_PATH = DEMO_DATASET_PATH / "corpus"
DEMO_MANIFEST_PATH = DEMO_DATASET_PATH / "manifest.json"


def build_settings(tmp_path: Path, **overrides) -> Settings:
    """构造隔离的测试配置：临时数据库、临时上传/Chroma 目录、Fake Provider、关闭后台线程。

    全部自动化测试只使用 FakeEmbeddingProvider 与 FakeReranker：
    不访问网络、不下载模型、不依赖 GPU。
    """
    values: dict = {
        "app_env": "test",
        "log_level": "WARNING",
        "database_url": f"sqlite:///{tmp_path / 'app.db'}",
        "upload_path": str(tmp_path / "uploads"),
        "chroma_path": str(tmp_path / "chroma"),
        "model_cache_path": str(tmp_path / "models"),
        "demo_dataset_path": str(DEMO_DATASET_PATH),
        "demo_dataset_version": "2026.1",
        "embedding_provider": "fake",
        "rerank_provider": "fake",
        "llm_provider": "fake",
        "worker_enabled": False,
        "worker_poll_seconds": 0.05,
        "demo_job_poll_seconds": 1,
        "demo_job_lease_seconds": 60,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return build_settings(tmp_path)


@pytest.fixture
def context(settings):
    engine = create_db_engine(settings)
    init_database(engine)
    factory = create_session_factory(engine)
    yield SimpleNamespace(settings=settings, engine=engine, session_factory=factory)
    engine.dispose()


@pytest.fixture
def client(settings):
    application = create_app(settings)
    with TestClient(application) as test_client:
        yield test_client
    application.state.context.engine.dispose()


@pytest.fixture
def worker(context) -> Worker:
    """已持有进程锁但不启动后台线程的 worker，便于确定性驱动。"""
    instance = Worker(context.settings, context.session_factory, worker_id="test-worker")
    assert instance.prepare()
    yield instance
    instance.stop()


@pytest.fixture
def embeddings(settings):
    """Fake Embedding Provider：确定性、离线、1024 维。"""
    provider = build_embedding_provider(settings)
    yield provider
    provider.close()


@pytest.fixture
def reranker(worker, settings):
    """Fake Reranker：确定性、离线，与 worker 共用同一协调器。"""
    provider = build_rerank_provider(settings, worker.coordinator)
    yield provider
    provider.close()


@pytest.fixture
def vectors(settings):
    """隔离的临时 Chroma 目录，不污染正式 data/。"""
    store = ChromaVectorStore(settings, descriptor_for(settings))
    yield store
    store.close()


@pytest.fixture
def llm(settings):
    """Fake LLM Provider：确定性、离线。"""
    provider = build_llm_provider(settings)
    yield provider
    provider.close()


@pytest.fixture
def chat_runtime(context, worker, reranker, llm):
    """ChatStreamRunner 所需的进程级资源集合（不含任何 Session）。"""
    from app.chat.service import ChatRuntime

    return ChatRuntime(
        settings=context.settings,
        session_factory=context.session_factory,
        vectors=worker.vectors,
        embeddings=worker.embeddings,
        reranker=reranker,
        coordinator=worker.coordinator,
        llm=llm,
    )


@pytest.fixture
def evidence(search, ingest_demo):
    """真实演示语料的检索结果（已脱离 Session，可安全传给生成阶段）。

    供阶段 6 的 Chat 测试复用：先用真实语料拿到 ``RerankedResult``，
    再按需替换 ``ChatStreamRunner._retrieve`` 以精确控制证据。
    """
    ingest_demo()

    def _get(query: str, top_k: int = 6):
        results, _diagnostics = search.rerank(query, top_k=top_k)
        return results

    return _get


def parse_sse(body: bytes) -> list[tuple[str, dict]]:
    """把 SSE 原始字节解析为 ``[(event, payload), ...]``，并校验单行 JSON 帧格式。"""
    events: list[tuple[str, dict]] = []
    text = body.decode("utf-8")
    for block in text.split("\n\n"):
        if not block.strip():
            continue
        name = None
        data = None
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data = line[len("data: ") :]
        assert name is not None, f"帧缺少 event 行: {block!r}"
        assert data is not None, f"帧缺少 data 行: {block!r}"
        assert "\n" not in data, "data 必须是单行 JSON"
        events.append((name, json.loads(data)))
    return events


@pytest.fixture
def ingest_demo(context, worker):
    """执行一次完整演示导入并返回任务 payload。"""

    def _run(settings=None) -> dict:
        from app.demo import service as demo_service

        target = settings or context.settings
        with context.session_factory() as session:
            job, _ = demo_service.seed_job(session, target)
            session.commit()
            job_id = job.id
        worker.run_once()
        with context.session_factory() as session:
            return demo_service.serialize_job(
                session, demo_service.get_job(session, job_id), target
            )

    return _run


@pytest.fixture
def search(context, worker, reranker):
    """检索入口；每个调用使用独立短事务，返回脱离会话的数据类结果。"""
    from types import SimpleNamespace

    from app.search.dense import DenseRetriever
    from app.search.hybrid import HybridRetriever
    from app.search.keyword import KeywordRetriever
    from app.search.reranking import RerankingRetriever

    def _keyword(query, filters=None, top_k=None):
        with context.session_factory() as session:
            return KeywordRetriever(session, context.settings).search(query, filters, top_k)

    def _dense(query, filters=None, top_k=None):
        with context.session_factory() as session:
            return DenseRetriever(
                session, context.settings, worker.vectors, worker.embeddings, worker.coordinator
            ).search(query, filters, top_k)

    def _hybrid(query, filters=None, top_k=None):
        with context.session_factory() as session:
            return HybridRetriever(
                session, context.settings, worker.vectors, worker.embeddings, worker.coordinator
            ).search(query, filters, top_k)

    def _rerank(query, filters=None, top_k=None, reranker_override=None):
        with context.session_factory() as session:
            return RerankingRetriever(
                session,
                context.settings,
                worker.vectors,
                worker.embeddings,
                reranker_override or reranker,
                worker.coordinator,
            ).search(query, filters, top_k)

    return SimpleNamespace(
        keyword=_keyword, dense=_dense, hybrid=_hybrid, rerank=_rerank
    )


@pytest.fixture
def writable_dataset(tmp_path) -> Path:
    """可写的演示数据集副本，用于 manifest 变化等场景。"""
    target = tmp_path / "demo"
    shutil.copytree(DEMO_DATASET_PATH, target)
    return target


def demo_file(name_fragment: str) -> Path:
    matches = sorted(DEMO_CORPUS_PATH.glob(f"*{name_fragment}*"))
    assert matches, f"未找到演示文件 {name_fragment}"
    return matches[0]


def upload_file(client: TestClient, path: Path, *, filename: str | None = None, content_type: str | None = None):
    """以 multipart/form-data 上传单个文件。"""
    with open(path, "rb") as handle:
        files = {"file": (filename or path.name, handle, content_type or "application/octet-stream")}
        return client.post("/api/documents", files=files)


def upload_bytes(client: TestClient, payload: bytes, *, filename: str, content_type: str | None = None):
    files = {"file": (filename, payload, content_type or "application/octet-stream")}
    return client.post("/api/documents", files=files)
