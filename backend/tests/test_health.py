"""``GET /api/health`` 契约与防泄漏测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_200(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200


def test_health_reports_degraded_without_business_features(client: TestClient) -> None:
    payload = client.get("/api/health").json()

    assert payload["status"] == "degraded"
    # 阶段 6：Chat 已实现。没有可检索文档时 chat=unavailable（不是 unconfigured）。
    assert payload["capabilities"] == {
        "documents": "unavailable",
        "chat": "unavailable",
        "planning": "unavailable",
    }
    assert isinstance(payload["version"], str) and payload["version"]


def test_health_reports_provider_devices_and_readiness(client: TestClient) -> None:
    providers = client.get("/api/health").json()["providers"]

    # Fake Embedding 不加载任何模型，因此永远不谎报 ready
    assert providers["embedding"] == {"provider": "fake", "device": "cpu", "ready": False}
    # 测试环境使用 Fake Reranker：它确实离线可用，因此真实 ready 为 True
    assert providers["reranker"] == {"provider": "fake", "device": "cpu", "ready": True}
    assert providers["llm"]["device"] is None
    # Fake LLM 离线可用：providers.llm.ready 反映真实证据，而不是“配置是否填写”
    assert providers["llm"]["ready"] is True


def test_health_does_not_leak_configuration(client: TestClient) -> None:
    response = client.get("/api/health")
    body = response.text.lower()

    for leaked in ("base_url", "api_key", "/app/data", "sqlite", "bge-m3", "localhost"):
        assert leaked not in body

    assert set(response.json().keys()) == {"status", "version", "capabilities", "providers"}
