"""健康状态与 Reranker 的指纹边界。

健康检查必须读取 Provider 真实的 loaded 状态：不加载模型、不访问网络、
不下载权重；默认 Local 环境没有依赖/权重时 readiness 必须为 false。
"""

from __future__ import annotations

import sys

from fastapi.testclient import TestClient

from app.documents.fingerprint import pipeline_fingerprint, stage_fingerprints
from app.main import create_app
from tests.conftest import build_settings


def _health(settings) -> dict:
    application = create_app(settings)
    try:
        with TestClient(application) as client:
            return client.get("/api/health").json()
    finally:
        application.state.context.engine.dispose()


def test_health_reports_fake_reranker_ready(tmp_path) -> None:
    payload = _health(build_settings(tmp_path, rerank_provider="fake", worker_enabled=False))
    assert payload["providers"]["reranker"] == {
        "provider": "fake",
        "device": "cpu",
        "ready": True,
    }
    assert payload["status"] == "degraded"
    # 阶段 7C：planning 恒为 ready；没有可检索文档时 chat=unavailable
    assert payload["capabilities"] == {
        "documents": "unavailable",
        "chat": "unavailable",
        "planning": "ready",
    }


def test_health_does_not_load_local_reranker_model(tmp_path) -> None:
    payload = _health(build_settings(tmp_path, rerank_provider="local", worker_enabled=False))

    assert payload["providers"]["reranker"] == {
        "provider": "local",
        "device": "cpu",
        "ready": False,
    }
    # 健康检查不得加载模型或导入可选依赖
    assert "sentence_transformers" not in sys.modules
    assert "torch" not in sys.modules


def test_health_does_not_claim_api_reranker_from_configuration(tmp_path) -> None:
    payload = _health(
        build_settings(
            tmp_path,
            rerank_provider="api",
            rerank_base_url="https://invalid.example.invalid/v1",
            rerank_api_key="test-key-not-real",
            rerank_model="rerank-test-model",
            worker_enabled=False,
        )
    )
    # 仅填写配置不等于远端真实可用
    assert payload["providers"]["reranker"]["ready"] is False
    assert payload["providers"]["reranker"]["provider"] == "api"


def test_health_does_not_leak_rerank_configuration(tmp_path) -> None:
    application = create_app(
        build_settings(
            tmp_path,
            rerank_provider="api",
            rerank_base_url="https://invalid.example.invalid/v1",
            rerank_api_key="sk-should-never-appear-12345",
            worker_enabled=False,
        )
    )
    try:
        with TestClient(application) as client:
            body = client.get("/api/health").text.lower()
    finally:
        application.state.context.engine.dispose()

    for leaked in (
        "base_url",
        "api_key",
        "sk-should-never-appear",
        "invalid.example.invalid",
        "bge-reranker",
        "/app/data",
    ):
        assert leaked not in body


def test_reranker_configuration_does_not_change_pipeline_fingerprint(tmp_path) -> None:
    """Reranker 是查询时能力：其配置与指纹绝不能进入文档流水线指纹。"""
    baseline = build_settings(tmp_path)
    changed = build_settings(
        tmp_path,
        rerank_provider="local",
        rerank_model="BAAI/bge-reranker-v2-m3",
        rerank_revision="deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
        rerank_device="cuda",
        rerank_batch_size=4,
        rerank_top_k=4,
        rerank_base_url="https://invalid.example.invalid/v1",
        rerank_api_key="test-key-not-real",
        rerank_timeout_seconds=5,
    )

    assert pipeline_fingerprint(baseline) == pipeline_fingerprint(changed)
    assert stage_fingerprints(baseline) == stage_fingerprints(changed)


def test_reranker_has_no_document_pipeline_checkpoint() -> None:
    import app.constants as constants

    # Reranker 不产生持久索引，因此没有对应检查点阶段
    assert not any("rerank" in stage for stage in constants.STAGES)
    assert constants.TARGET_STAGE == constants.STAGE_COMPLETED
