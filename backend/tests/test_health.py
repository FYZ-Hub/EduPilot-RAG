"""``GET /api/health`` 契约与防泄漏测试。

阶段 8 前置修复轮（BUG-8-PRE-01）明确区分两件事：

- ``capabilities.documents`` = **文档子系统是否可用**（文档结构与文档查询可正常执行；
  空库同样是 ``ready``，否则 UI_SPEC 2.4 会在空库时禁用 upload / seed，形成启动死锁）；
- 可检索文档**数量** = 只用于 ``capabilities.chat`` 是否具备检索语料。
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.main import create_app
from app.models import Document, DocumentPipelineState
from tests.conftest import build_settings, demo_file

RECORDS_ENDPOINT = "/api/academic/records/import"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def test_health_returns_200(client: TestClient) -> None:
    response = client.get("/api/health")
    assert response.status_code == 200


def test_health_reports_degraded_without_business_features(client: TestClient) -> None:
    payload = client.get("/api/health").json()

    assert payload["status"] == "degraded"
    # 阶段 7C：规划路由与确定性引擎已注册 -> planning=ready（空库也成立）。
    # 阶段 8 前置：documents 表示**子系统可用性**，空库同样是 ready（否则上传/seed 被禁用）。
    # Fake LLM 已配置但没有可检索语料 -> chat=unavailable。
    assert payload["capabilities"] == {
        "documents": "ready",
        "chat": "unavailable",
        "planning": "ready",
    }
    assert isinstance(payload["version"], str) and payload["version"]


def test_empty_library_reports_documents_ready(client: TestClient) -> None:
    """空数据库 + 正常文档结构：``documents`` 必须为 ready，retrievable=0 不得降级它。"""
    payload = client.get("/api/health").json()
    assert payload["capabilities"]["documents"] == "ready"


def test_documents_present_but_none_retrievable_still_ready(
    client: TestClient, context
) -> None:
    """存在文档但没有可检索文档时，``documents`` 仍为 ready，只有 chat 因缺语料不可用。

    使用学业导入建立真实 Document：它有文档行、``retrievable=false``，也没有检查点。
    """
    path = demo_file("13-课程记录-匿名学生A")
    with open(path, "rb") as handle:
        response = client.post(
            RECORDS_ENDPOINT, files={"file": (path.name, handle, XLSX_MIME)}
        )
    assert response.status_code == 200, response.text

    with context.session_factory() as session:
        assert session.scalar(select(func.count(Document.id))) == 1
        assert session.scalar(select(func.count()).select_from(DocumentPipelineState)) == 0

    payload = client.get("/api/health").json()
    assert payload["capabilities"]["documents"] == "ready"
    assert payload["capabilities"]["chat"] == "unavailable"


def test_documents_ready_is_independent_of_llm_configuration(tmp_path) -> None:
    """空库 + LLM 未配置的组合正是启动死锁场景：documents 仍须为 ready 以便上传/seed。"""
    settings = build_settings(tmp_path, llm_provider="openai_compatible", llm_api_key="")
    application = create_app(settings)
    with TestClient(application) as client:
        payload = client.get("/api/health").json()
    application.state.context.engine.dispose()

    assert payload["capabilities"]["documents"] == "ready"
    assert payload["capabilities"]["chat"] == "unconfigured"
    assert payload["status"] == "degraded"


def test_health_hides_failures_and_marks_documents_unavailable(
    client: TestClient, monkeypatch
) -> None:
    """文档相关查询确实失败时 -> documents=unavailable，且响应不泄漏 SQL / 路径 / 异常。"""

    def _broken(*_args, **_kwargs):
        raise RuntimeError("SQL: SELECT COUNT(*) FROM documents -- /app/data/sqlite/app.db")

    monkeypatch.setattr("app.api.health.session_scope", _broken)

    response = client.get("/api/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["capabilities"]["documents"] == "unavailable"
    assert payload["capabilities"]["planning"] == "unavailable"

    body = response.text.lower()
    for leaked in ("select", "/app/", "traceback", "runtimeerror", "sqlite", "documents --"):
        assert leaked not in body, leaked
    assert set(payload) == {"status", "version", "capabilities", "providers"}


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


def test_health_status_is_healthy_when_all_capabilities_are_ready(
    client: TestClient, monkeypatch
) -> None:
    """三项能力前置条件都满足（含"存在可检索语料"）时 ``status`` 才为 ``healthy``。"""
    monkeypatch.setattr("app.api.health._retrievable_documents", lambda _context: 1)

    payload = client.get("/api/health").json()

    assert payload["capabilities"] == {
        "documents": "ready",
        "chat": "ready",
        "planning": "ready",
    }
    assert payload["status"] == "healthy"


def test_health_status_is_degraded_when_any_capability_is_missing(
    client: TestClient, monkeypatch
) -> None:
    """只要有一项能力不 ready 就是 ``degraded``——包括"有语料但规划查询失败"的部分可用场景。"""
    monkeypatch.setattr("app.api.health._retrievable_documents", lambda _context: 2)
    monkeypatch.setattr("app.api.health._planning_capability", lambda _context: "unavailable")

    payload = client.get("/api/health").json()

    assert payload["capabilities"]["chat"] == "ready"
    assert payload["capabilities"]["planning"] == "unavailable"
    assert payload["status"] == "degraded"


def test_provider_without_successful_call_neither_degrades_status_nor_blocks_first_chat(
    tmp_path, monkeypatch
) -> None:
    """API Provider 在首次成功调用前 ``ready=false`` 属正常：

    - 它**不得**把 ``status`` 压成 ``degraded``（否则服务被永久锁死）；
    - 它**不得**阻止 ``capabilities.chat`` 为 ready（否则永远无法产生第一次成功调用）。
    """
    settings = build_settings(
        tmp_path,
        embedding_provider="api",
        rerank_provider="api",
        llm_provider="openai_compatible",
        llm_base_url="https://llm.invalid/v1",
        llm_model="demo-model",
        llm_api_key="test-key-not-a-real-secret",
    )
    application = create_app(settings)
    with TestClient(application) as client:
        monkeypatch.setattr("app.api.health._retrievable_documents", lambda _context: 3)
        payload = client.get("/api/health").json()
    application.state.context.engine.dispose()

    # 尚无任何真实成功调用
    assert payload["providers"]["embedding"]["ready"] is False
    assert payload["providers"]["reranker"]["ready"] is False
    assert payload["providers"]["llm"]["ready"] is False
    # 但能力与状态都不被 Provider 的 ready 拖累
    assert payload["capabilities"]["chat"] == "ready"
    assert payload["status"] == "healthy"


def test_health_opens_no_socket_and_loads_no_model(client: TestClient, monkeypatch) -> None:
    """健康检查必须完全离线：不建连、不加载/调用任何模型。"""
    import socket

    def _forbidden(*_args, **_kwargs):
        raise AssertionError("健康检查不得发起任何网络连接")

    monkeypatch.setattr(socket.socket, "connect", _forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", _forbidden)
    monkeypatch.setattr(socket, "create_connection", _forbidden)

    response = client.get("/api/health")
    assert response.status_code == 200

    # Fake Embedding 永远不会"已加载"：说明健康检查没有触发任何模型工作
    assert response.json()["providers"]["embedding"]["ready"] is False
