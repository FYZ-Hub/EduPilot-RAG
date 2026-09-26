"""``GET /api/health`` 契约与防泄漏测试。"""

from __future__ import annotations

from fastapi.testclient import TestClient


def test_health_returns_200(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200


def test_health_reports_degraded_without_business_features(client: TestClient) -> None:
    payload = client.get("/api/health").json()

    assert payload["status"] == "degraded"
    assert payload["capabilities"] == {
        "documents": "unavailable",
        "chat": "unconfigured",
        "planning": "unavailable",
    }
    assert isinstance(payload["version"], str) and payload["version"]


def test_health_reports_provider_devices_and_not_ready(client: TestClient) -> None:
    providers = client.get("/api/health").json()["providers"]

    assert providers["embedding"] == {"provider": "local", "device": "cpu", "ready": False}
    assert providers["reranker"] == {"provider": "local", "device": "cpu", "ready": False}
    assert providers["llm"]["device"] is None
    assert providers["llm"]["ready"] is False


def test_health_does_not_leak_configuration(client: TestClient) -> None:
    response = client.get("/api/health")
    body = response.text.lower()

    for leaked in ("base_url", "api_key", "/app/data", "sqlite", "bge-m3", "localhost"):
        assert leaked not in body

    assert set(response.json().keys()) == {"status", "version", "capabilities", "providers"}
