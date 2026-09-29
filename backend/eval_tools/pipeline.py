"""真实检索链装配：隔离 SQLite + Chroma + FTS5，Embedding / Reranker 使用 Fake Provider。

与生产运行的区别只有两处，且都在配置层：

1. 数据库、Chroma、上传与模型缓存目录全部指向调用方给定的隔离目录；
2. ``embedding_provider`` / ``rerank_provider`` 固定为 ``fake``（离线、确定性）。

解析、切片、向量化、FTS5 索引、RRF 融合与重排全部复用生产实现，未做任何简化。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import create_db_engine, create_session_factory, init_database
from app.demo import service as demo_service
from app.embedding.base import EmbeddingProvider
from app.rerank.base import RerankProvider
from app.rerank.factory import build_rerank_provider
from app.runtime.coordinator import LocalModelCoordinator
from app.search.dense import DenseRetriever
from app.search.eligibility import ELIGIBILITY_FROM, build_where
from app.search.hybrid import HybridRetriever
from app.search.hydrate import build_scope
from app.search.keyword import KeywordRetriever
from app.search.reranking import RERANK_MAX_CANDIDATES, RerankingRetriever
from app.search.types import RetrievalFilters
from app.vector.store import ChromaVectorStore
from app.worker.runner import Worker

from eval_tools.matching import RetrievedSource
from eval_tools.runner import RankedTrace, RouteProbe


def build_eval_settings(
    work_dir: Path,
    *,
    demo_dataset_path: Path,
    demo_dataset_version: str,
) -> Settings:
    """构造隔离评测配置：临时目录 + Fake Provider + 关闭后台线程。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        app_env="eval",
        log_level="WARNING",
        database_url=f"sqlite:///{work_dir / 'app.db'}",
        chroma_path=str(work_dir / "chroma"),
        upload_path=str(work_dir / "uploads"),
        model_cache_path=str(work_dir / "models"),
        demo_dataset_path=str(demo_dataset_path),
        demo_dataset_version=demo_dataset_version,
        embedding_provider="fake",
        rerank_provider="fake",
        llm_provider="fake",
        worker_enabled=False,
        worker_poll_seconds=0.05,
        demo_job_poll_seconds=1,
        demo_job_lease_seconds=60,
    )


def _identity(source_key: str, file_name: str) -> str:
    return source_key or file_name


def indexed_source_identities(
    session_factory: sessionmaker[Session], settings: Settings
) -> frozenset[str]:
    """当前索引中「可检索」的来源标识集合（与检索资格同一套 SQL 条件）。"""
    with session_factory() as session:
        scope = build_scope(session, settings)
        where_sql, params = build_where(scope, RetrievalFilters())
        rows = session.execute(
            text(
                "SELECT DISTINCT d.source_key, d.file_name "
                f"{ELIGIBILITY_FROM} WHERE 1 = 1 {where_sql}"
            ),
            params,
        ).all()
    return frozenset(_identity(row[0] or "", row[1] or "") for row in rows)


class PipelineRetrievalChain:
    """基于真实生产检索实现的评测链。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        vectors: ChromaVectorStore,
        embeddings: EmbeddingProvider,
        reranker: RerankProvider,
        coordinator: LocalModelCoordinator | None = None,
    ):
        self.session_factory = session_factory
        self.settings = settings
        self.vectors = vectors
        self.embeddings = embeddings
        self.reranker = reranker
        self.coordinator = coordinator

    def indexed_identities(self) -> frozenset[str]:
        return indexed_source_identities(self.session_factory, self.settings)

    def ranked(self, query: str, top_k: int) -> RankedTrace:
        started = perf_counter()
        with self.session_factory() as session:
            results, diagnostics = RerankingRetriever(
                session,
                self.settings,
                self.vectors,
                self.embeddings,
                self.reranker,
                self.coordinator,
            ).search(query, RetrievalFilters(), top_k=top_k)
        elapsed_ms = (perf_counter() - started) * 1000.0
        ranked = tuple(
            RetrievedSource(source_key=item.chunk.source_key, file_name=item.chunk.file_name)
            for item in results
        )
        return RankedTrace(
            ranked=ranked,
            elapsed_ms=elapsed_ms,
            rerank_applied=diagnostics.rerank_applied,
            degraded_reason=diagnostics.degraded_reason,
        )

    def probe(self, query: str) -> RouteProbe:
        with self.session_factory() as session:
            dense = DenseRetriever(
                session,
                self.settings,
                self.vectors,
                self.embeddings,
                self.coordinator,
            ).search(query, RetrievalFilters())
            keyword, _mode = KeywordRetriever(session, self.settings).search(
                query, RetrievalFilters()
            )
            fused, _diag = HybridRetriever(
                session,
                self.settings,
                self.vectors,
                self.embeddings,
                self.coordinator,
            ).search(query, RetrievalFilters(), top_k=RERANK_MAX_CANDIDATES)
        return RouteProbe(
            dense=tuple(
                RetrievedSource(chunk.source_key, chunk.file_name) for chunk in dense
            ),
            keyword=tuple(
                RetrievedSource(chunk.source_key, chunk.file_name) for chunk in keyword
            ),
            fused=tuple(
                RetrievedSource(item.chunk.source_key, item.chunk.file_name)
                for item in fused
            ),
        )


@dataclass
class EvalEnvironment:
    settings: Settings
    session_factory: sessionmaker[Session]
    worker: Worker
    reranker: RerankProvider
    chain: PipelineRetrievalChain

    @property
    def embedding_provider(self) -> str:
        return self.worker.embeddings.descriptor.provider

    @property
    def rerank_provider(self) -> str:
        return self.reranker.descriptor.provider

    @property
    def rerank_score_kind(self) -> str:
        """Reranker 分数种类；决定质量门禁落在融合层还是最终层。"""
        return self.reranker.descriptor.score_kind

    def ingest_demo(self) -> None:
        """执行一次完整演示导入（真实解析/切片/索引），幂等复用生产实现。"""
        with self.session_factory() as session:
            job, _reused = demo_service.seed_job(session, self.settings)
            session.commit()
            job_id = job.id
        self.worker.run_once()
        with self.session_factory() as session:
            demo_service.serialize_job(
                session, demo_service.get_job(session, job_id), self.settings
            )


@contextmanager
def open_eval_environment(settings: Settings) -> Iterator[EvalEnvironment]:
    """打开隔离评测环境；退出时关闭 Reranker、Worker、向量库与数据库连接。"""
    engine = create_db_engine(settings)
    init_database(engine)
    session_factory = create_session_factory(engine)
    worker = Worker(settings, session_factory, worker_id="eval-worker")
    if not worker.prepare():
        engine.dispose()
        raise RuntimeError("无法获取评测 worker 进程锁")
    reranker = build_rerank_provider(settings, worker.coordinator)
    chain = PipelineRetrievalChain(
        session_factory, settings, worker.vectors, worker.embeddings, reranker, worker.coordinator
    )
    environment = EvalEnvironment(
        settings=settings,
        session_factory=session_factory,
        worker=worker,
        reranker=reranker,
        chain=chain,
    )
    try:
        yield environment
    finally:
        reranker.close()
        worker.stop()
        engine.dispose()


__all__ = [
    "EvalEnvironment",
    "PipelineRetrievalChain",
    "build_eval_settings",
    "indexed_source_identities",
    "open_eval_environment",
]
