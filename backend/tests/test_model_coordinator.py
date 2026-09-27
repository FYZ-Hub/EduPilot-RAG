"""本地模型运行协调：串行、有界等待、异常/关闭释放。"""

from __future__ import annotations

import sys
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.core.errors import ApiError
from app.embedding.factory import build_embedding_provider
from app.main import create_app
from app.rerank.base import descriptor_for
from app.rerank.factory import build_rerank_provider
from app.runtime.coordinator import LocalModelCoordinator, empty_cuda_cache
from tests.conftest import build_settings


def test_local_models_are_never_resident_simultaneously() -> None:
    coordinator = LocalModelCoordinator(wait_seconds=10)
    loaded: set[str] = set()
    peak: list[int] = []
    guard = threading.Lock()
    released: list[str] = []

    coordinator.register("embedding", lambda: released.append("embedding"))
    coordinator.register("reranker", lambda: released.append("reranker"))

    def _run(kind: str, rounds: int = 25) -> None:
        for _ in range(rounds):
            with coordinator.lease(kind):
                with guard:
                    loaded.add(kind)
                    peak.append(len(loaded))
                time.sleep(0.001)
                with guard:
                    loaded.discard(kind)

    threads = [
        threading.Thread(target=_run, args=("embedding",)),
        threading.Thread(target=_run, args=("reranker",)),
        threading.Thread(target=_run, args=("embedding",)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)

    assert peak, "两个能力都必须真实执行"
    assert max(peak) == 1, "任何时刻最多只有一个本地大模型驻留"
    assert loaded == set()
    assert released, "每个使用窗口结束后都必须释放"


def test_coordinator_releases_on_exception() -> None:
    coordinator = LocalModelCoordinator()
    released: list[str] = []
    coordinator.register("reranker", lambda: released.append("reranker"))

    with pytest.raises(RuntimeError):
        with coordinator.lease("reranker"):
            assert coordinator.active_kind == "reranker"
            raise RuntimeError("boom")

    assert released == ["reranker"]
    assert coordinator.active_kind is None
    # 异常路径已释放锁：可以立即再次获取
    with coordinator.lease("reranker"):
        pass
    assert released == ["reranker", "reranker"]


def test_coordinator_wait_is_bounded() -> None:
    coordinator = LocalModelCoordinator(wait_seconds=0.05)
    coordinator.register("embedding", lambda: None)

    with coordinator.lease("embedding"):
        with pytest.raises(ApiError) as error:
            with coordinator.lease("reranker"):
                pytest.fail("不应获取到已被占用的锁")
    assert error.value.code == "RETRIEVAL_UNAVAILABLE"
    assert error.value.details.get("reason") == "local_model_busy"

    # 超时后锁仍然可用
    with coordinator.lease("reranker"):
        pass


def test_coordinator_close_is_idempotent() -> None:
    coordinator = LocalModelCoordinator()
    released: list[str] = []
    coordinator.register("embedding", lambda: released.append("embedding"))
    coordinator.register("reranker", lambda: released.append("reranker"))

    coordinator.close()
    coordinator.close()

    assert sorted(released) == ["embedding", "embedding", "reranker", "reranker"]
    with coordinator.lease("embedding"):
        pass


def test_empty_cuda_cache_never_imports_torch() -> None:
    assert "torch" not in sys.modules
    empty_cuda_cache("cuda")
    empty_cuda_cache("cpu")
    assert "torch" not in sys.modules


def test_local_providers_register_on_the_shared_coordinator(tmp_path) -> None:
    settings = build_settings(
        tmp_path, embedding_provider="local", rerank_provider="local", worker_enabled=False
    )
    coordinator = LocalModelCoordinator()
    embeddings = build_embedding_provider(settings, coordinator)
    reranker = build_rerank_provider(settings, coordinator)

    assert set(coordinator.registered_kinds) == {"embedding", "reranker"}
    assert embeddings.coordinator is coordinator
    assert reranker.coordinator is coordinator


def test_app_context_shares_one_coordinator(tmp_path) -> None:
    settings = build_settings(tmp_path, rerank_provider="fake", worker_enabled=False)
    application = create_app(settings)
    try:
        context = application.state.context
        assert context.worker.coordinator is context.coordinator
        assert context.reranker is not None
        assert context.reranker.descriptor.provider == "fake"
    finally:
        application.state.context.engine.dispose()


def test_lifespan_releases_reranker_and_coordinator(tmp_path, monkeypatch) -> None:
    settings = build_settings(
        tmp_path, embedding_provider="local", rerank_provider="local", worker_enabled=False
    )
    released: list[str] = []

    class _SpyReranker:
        descriptor = descriptor_for(settings)
        loaded = False

        def rerank(self, query, candidates):  # pragma: no cover - 不在本测试中使用
            return []

        def close(self) -> None:
            released.append("reranker")

    monkeypatch.setattr("app.main.build_rerank_provider", lambda s, c: _SpyReranker())
    application = create_app(settings)
    application.state.context.coordinator.register(
        "embedding", lambda: released.append("embedding")
    )

    with TestClient(application):
        pass
    application.state.context.engine.dispose()

    assert "reranker" in released
    assert "embedding" in released


def test_dense_query_releases_embedding_after_query(ingest_demo, context, worker) -> None:
    """Dense 查询完成后必须释放 Embedding：通过共享协调器的释放回调验证。"""
    ingest_demo()
    released: list[str] = []
    worker.coordinator.register("embedding", lambda: released.append("embedding"))

    from app.search.dense import DenseRetriever

    with context.session_factory() as session:
        DenseRetriever(
            session, context.settings, worker.vectors, worker.embeddings, worker.coordinator
        ).search("学分认定")

    assert released == ["embedding"]
    assert worker.coordinator.active_kind is None


def test_worker_uses_the_injected_coordinator(tmp_path) -> None:
    from app.worker.runner import Worker

    settings: Settings = build_settings(tmp_path, embedding_provider="local")
    coordinator = LocalModelCoordinator()
    worker = Worker(settings, lambda: None, coordinator=coordinator)  # type: ignore[arg-type]
    assert worker.coordinator is coordinator
    # 本地 Embedding Provider 已登记到共享协调器
    assert "embedding" in coordinator.registered_kinds
