"""阶段 6：健康状态语义测试。

覆盖：健康检查零网络调用；未配置 API 时 ``chat=unconfigured`` / ``llm.ready=false``；
配置完整且有可检索文档时 ``chat=ready``（**不因 loaded=false 被锁死**）；
Fake + 可检索文档时 ``chat=ready``；可检索文档统计复用 eligibility 口径；
默认 Local 无权重环境继续如实 degraded。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.db import create_db_engine, create_session_factory, init_database
from app.demo import service as demo_service
from app.main import create_app
from app.worker.runner import Worker
from tests.conftest import build_settings


def _ingest(settings) -> None:
    """用与 app 相同的 settings 导入一次演示语料（独立 worker，不污染默认 data/）。"""
    engine = create_db_engine(settings)
    init_database(engine)
    factory = create_session_factory(engine)
    worker = Worker(settings, factory, worker_id="health-context")
    assert worker.prepare()
    try:
        with factory() as session:
            demo_service.seed_job(session, settings)
            session.commit()
        worker.run_once()
    finally:
        worker.stop()
        engine.dispose()


def _health(client: TestClient) -> dict:
    response = client.get("/api/health")
    assert response.status_code == 200
    return response.json()


def test_health_makes_no_network_calls(tmp_path, monkeypatch) -> None:
    def _forbidden(*args, **kwargs):  # pragma: no cover - 触发即失败
        raise AssertionError("健康检查不得访问网络")

    monkeypatch.setattr("httpx.AsyncClient", _forbidden)
    monkeypatch.setattr("httpx.Client", _forbidden)

    application = create_app(build_settings(tmp_path))
    with TestClient(application) as client:
        _health(client)
    application.state.context.engine.dispose()


def test_unconfigured_api_provider_reports_unconfigured_chat(tmp_path) -> None:
    settings = build_settings(tmp_path, llm_provider="openai_compatible", llm_api_key="")
    application = create_app(settings)
    with TestClient(application) as client:
        payload = _health(client)
    application.state.context.engine.dispose()

    assert payload["capabilities"]["chat"] == "unconfigured"
    assert payload["providers"]["llm"]["ready"] is False
    assert payload["providers"]["llm"]["provider"] == "openai_compatible"
    assert payload["status"] == "degraded"


def test_configured_provider_with_documents_is_ready_while_llm_not_loaded(tmp_path) -> None:
    """关键回归：capabilities.chat 不得绑定 providers.llm.loaded，否则首次调用死锁。"""
    settings = build_settings(
        tmp_path,
        llm_provider="openai_compatible",
        llm_base_url="https://llm.invalid/v1",
        llm_api_key="test-key-not-real",
        llm_model="demo-model",
    )
    _ingest(settings)
    application = create_app(settings)
    with TestClient(application) as client:
        payload = _health(client)
    application.state.context.engine.dispose()

    assert payload["capabilities"]["documents"] == "ready"
    assert payload["capabilities"]["chat"] == "ready"
    # API Provider 尚未有过真实成功调用：ready 仍为 false，但不阻断 chat
    assert payload["providers"]["llm"]["ready"] is False


def test_fake_provider_with_documents_is_ready_and_can_chat(tmp_path) -> None:
    settings = build_settings(tmp_path, llm_provider="fake")
    _ingest(settings)
    application = create_app(settings)
    with TestClient(application) as client:
        payload = _health(client)
        assert payload["capabilities"]["chat"] == "ready"
        assert payload["providers"]["llm"]["ready"] is True

        response = client.post(
            "/api/chat/stream", json={"messages": [{"role": "user", "content": "毕业总学分是多少？"}]}
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert b"event: done" in response.content
        # 真实调用成功后，健康检查仍可读到 loaded=True
        assert _health(client)["providers"]["llm"]["ready"] is True
    application.state.context.engine.dispose()


def test_health_marks_chat_unavailable_without_retrievable_documents(tmp_path) -> None:
    settings = build_settings(tmp_path, llm_provider="fake")
    application = create_app(settings)
    with TestClient(application) as client:
        payload = _health(client)
    application.state.context.engine.dispose()

    assert payload["capabilities"]["documents"] == "unavailable"
    assert payload["capabilities"]["chat"] == "unavailable"
    assert payload["status"] == "degraded"


def test_health_excludes_documents_outside_the_active_dataset(tmp_path) -> None:
    """active 指针的 pipeline 指纹与运行配置不一致时，demo 文档不得算作可检索。"""
    _ingest(build_settings(tmp_path))

    # 用与导入时不同的流水线配置运行：active 指纹不再匹配 → demo 不可检索
    rotated = build_settings(tmp_path, chunk_target_chars=319)
    application = create_app(rotated)
    with TestClient(application) as client:
        payload = _health(client)
    application.state.context.engine.dispose()

    assert payload["capabilities"]["documents"] == "unavailable"
    assert payload["capabilities"]["chat"] == "unavailable"


def test_default_local_environment_stays_degraded(tmp_path) -> None:
    """默认 Local Embedding / Reranker 无权重时如实返回 ready=false。"""
    settings = build_settings(
        tmp_path,
        embedding_provider="local",
        rerank_provider="local",
        llm_provider="openai_compatible",
    )
    application = create_app(settings)
    with TestClient(application) as client:
        payload = _health(client)
    application.state.context.engine.dispose()

    assert payload["status"] == "degraded"
    # 阶段 7C 起 planning 与 Provider 是否配置无关，恒为可用
    assert payload["capabilities"]["planning"] == "ready"
    assert payload["providers"]["embedding"]["provider"] == "local"
    assert payload["providers"]["embedding"]["ready"] is False
    assert payload["providers"]["reranker"]["ready"] is False
    assert payload["providers"]["llm"]["ready"] is False


@pytest.mark.parametrize("path", ["/api/health"])
def test_health_never_loads_models(client, monkeypatch, path) -> None:
    """健康检查不得触发本地模型加载。"""

    def _forbidden(*args, **kwargs):  # pragma: no cover - 触发即失败
        raise AssertionError("健康检查不得加载模型")

    monkeypatch.setattr("app.embedding.local.LocalEmbeddingProvider._ensure_model", _forbidden)
    monkeypatch.setattr("app.rerank.local.LocalReranker._ensure_model", _forbidden)
    response = client.get(path)
    assert response.status_code == 200
