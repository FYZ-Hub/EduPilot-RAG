"""Embedding Provider：Fake / Local / API 的行为与安全边界。"""

from __future__ import annotations

import sys

import httpx
import pytest

from tests.conftest import build_settings
from app import constants
from app.core.errors import ApiError
from app.documents.fingerprint import stage_fingerprints
from app.embedding.api import ApiEmbeddingProvider, classify_embedding_error
from app.embedding.base import LOCAL_MODEL_REVISION, descriptor_for
from app.embedding.factory import build_embedding_provider
from app.embedding.fake import FakeEmbeddingProvider
from app.embedding.local import LocalEmbeddingProvider


# --- Fake -------------------------------------------------------------------


def test_fake_provider_is_deterministic_and_offline(tmp_path) -> None:
    settings = build_settings(tmp_path, embedding_provider="fake")
    provider = build_embedding_provider(settings)

    assert provider.dimension == 1024
    assert provider.provider_name == "fake"
    assert provider.loaded is False

    first = provider.embed_documents(["课程代码 QM-CS201"])
    again = provider.embed_documents(["课程代码 QM-CS201"])
    other = provider.embed_documents(["完全不同的另一段文本"])

    assert first == again
    assert first != other
    assert len(first[0]) == 1024
    assert all(-1.0 <= value <= 1.0 for value in first[0])

    # 批次结果与逐条调用一致
    batched = provider.embed_documents(["甲文本", "乙文本"])
    assert batched == provider.embed_documents(["甲文本"]) + provider.embed_documents(["乙文本"])

    # 不读取/写入模型缓存目录
    cache = tmp_path / "models"
    assert not cache.exists() or not any(cache.iterdir())

    # 不导入任何模型/网络库
    assert "sentence_transformers" not in sys.modules
    assert "torch" not in sys.modules


def test_fake_provider_is_rejected_in_production(tmp_path) -> None:
    settings = build_settings(tmp_path, embedding_provider="fake", app_env="production")
    with pytest.raises(ApiError) as error:
        build_embedding_provider(settings)
    assert error.value.code == "EMBEDDING_PROVIDER_FORBIDDEN"


def test_descriptor_fingerprint_matches_embedding_stage_fingerprint(tmp_path) -> None:
    settings = build_settings(tmp_path, embedding_provider="fake")
    provider = build_embedding_provider(settings)
    assert provider.fingerprint == stage_fingerprints(settings)["embedding_fingerprint"]


def test_fake_dimension_is_fixed_regardless_of_settings(tmp_path) -> None:
    settings = build_settings(tmp_path, embedding_provider="fake", embedding_dimension=8)
    provider = build_embedding_provider(settings)
    assert provider.dimension == constants.EMBEDDING_DIMENSION
    assert len(provider.embed_documents(["x"])[0]) == constants.EMBEDDING_DIMENSION


# --- Local ------------------------------------------------------------------


def test_local_provider_is_lazy_and_reports_missing_dependency(tmp_path) -> None:
    settings = build_settings(tmp_path, embedding_provider="local", embedding_device="cpu")
    provider = build_embedding_provider(settings)

    # 构造 Provider 不得加载模型，也不得导入可选依赖
    assert isinstance(provider, LocalEmbeddingProvider)
    assert provider.loaded is False
    assert "sentence_transformers" not in sys.modules
    assert "torch" not in sys.modules

    with pytest.raises(ApiError) as error:
        provider.embed_documents(["触发延迟加载"])
    assert error.value.code == "EMBEDDING_PROVIDER_UNAVAILABLE"
    assert error.value.details.get("reason") == "local_embedding_dependency_missing"
    # 缺依赖时绝不静默回退到其他 Provider
    assert "sentence_transformers" not in sys.modules


def test_local_provider_revision_is_pinned(tmp_path) -> None:
    default_settings = build_settings(tmp_path, embedding_provider="local")
    assert descriptor_for(default_settings).revision == LOCAL_MODEL_REVISION
    assert len(LOCAL_MODEL_REVISION) == 40
    assert LOCAL_MODEL_REVISION != "main"

    overridden = build_settings(tmp_path, embedding_provider="local", embedding_revision="deadbeef")
    assert descriptor_for(overridden).revision == "deadbeef"


def test_local_model_is_not_loaded_at_startup_or_health_check(tmp_path) -> None:
    from fastapi.testclient import TestClient

    from app.main import create_app

    settings = build_settings(tmp_path, embedding_provider="local", worker_enabled=False)
    application = create_app(settings)
    with TestClient(application) as client:
        payload = client.get("/api/health").json()
    application.state.context.engine.dispose()

    assert payload["providers"]["embedding"] == {"provider": "local", "device": "cpu", "ready": False}
    assert "sentence_transformers" not in sys.modules


# --- API --------------------------------------------------------------------

_UNSET = object()


class _FakeResponse:
    def __init__(self, payload, json_error: Exception | None = None):
        self._payload = payload
        self._json_error = json_error

    def raise_for_status(self) -> None:
        return None

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _FakeClient:
    """记录请求并返回固定响应；不访问任何网络。可注入异常模拟传输/状态失败。"""

    calls: list = []
    instances: list = []
    closed: int = 0
    payload: dict = {}
    error: Exception | None = None
    json_error: Exception | None = None

    def __init__(self, *args, **kwargs):
        type(self).instances.append(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def close(self):
        type(self).closed += 1

    def post(self, url, headers=None, json=None):
        type(self).calls.append({"url": url, "headers": headers, "json": json})
        if type(self).error is not None:
            raise type(self).error
        return _FakeResponse(type(self).payload, type(self).json_error)


def _api_settings(tmp_path, **overrides):
    values = {
        "embedding_provider": "api",
        "embedding_base_url": "https://invalid.example.invalid/v1",
        "embedding_api_key": "test-key-not-real",
        "embedding_model": "text-embedding-test",
    }
    values.update(overrides)
    return build_settings(tmp_path, **values)


def _use_fake_client(
    monkeypatch, *, payload=_UNSET, error=None, json_error=None
) -> None:
    """重置并装配假客户端（含清空 calls/instances/closed），保证测试之间互不串扰。"""
    _FakeClient.calls = []
    _FakeClient.instances = []
    _FakeClient.closed = 0
    _FakeClient.payload = {} if payload is _UNSET else payload
    _FakeClient.error = error
    _FakeClient.json_error = json_error
    monkeypatch.setattr("app.embedding.api.httpx.Client", _FakeClient)


def _status_error(
    status_code: int, body: str = "", message: str = "upstream-detail"
) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://invalid.example.invalid/v1/embeddings")
    response = httpx.Response(status_code, request=request, text=body)
    return httpx.HTTPStatusError(message, request=request, response=response)


def test_api_provider_requires_explicit_configuration(tmp_path) -> None:
    settings = build_settings(
        tmp_path, embedding_provider="api", embedding_base_url="", embedding_model=""
    )
    provider = ApiEmbeddingProvider(settings)
    with pytest.raises(ApiError) as error:
        provider.embed_documents(["x"])
    assert error.value.code == "EMBEDDING_PROVIDER_UNAVAILABLE"
    assert error.value.details.get("reason") == "api_embedding_not_configured"


def test_api_provider_parses_vectors_without_network(tmp_path, monkeypatch) -> None:
    _use_fake_client(
        monkeypatch,
        payload={
            "data": [
                {"embedding": [0.5] * 1024},
                {"embedding": [0.25] * 1024},
            ]
        },
    )
    provider = ApiEmbeddingProvider(_api_settings(tmp_path, embedding_batch_size=2))
    assert provider.loaded is False
    vectors = provider.embed_documents(["甲", "乙"])

    assert len(vectors) == 2
    assert len(vectors[0]) == 1024
    # 只有一次请求真正成功校验结构后，loaded 才为 true
    assert provider.loaded is True
    assert _FakeClient.calls[0]["url"].endswith("/embeddings")
    assert _FakeClient.calls[0]["json"]["model"] == "text-embedding-test"
    assert _FakeClient.calls[0]["headers"]["Authorization"].startswith("Bearer ")


def test_api_provider_rejects_wrong_dimension(tmp_path, monkeypatch) -> None:
    _use_fake_client(monkeypatch, payload={"data": [{"embedding": [0.5] * 8}]})
    provider = ApiEmbeddingProvider(_api_settings(tmp_path))
    with pytest.raises(ApiError) as error:
        provider.embed_documents(["甲"])
    assert error.value.code == "EMBEDDING_DIMENSION_MISMATCH"
    assert provider.loaded is False


def test_api_provider_error_does_not_leak_key(tmp_path, monkeypatch) -> None:
    _use_fake_client(monkeypatch, payload={"unexpected": "shape"})

    secret = "sk-should-never-appear-12345"
    provider = ApiEmbeddingProvider(_api_settings(tmp_path, embedding_api_key=secret))
    with pytest.raises(ApiError) as error:
        provider.embed_documents(["甲"])

    assert error.value.code == "DOCUMENT_EMBEDDING_FAILED"
    assert secret not in str(error.value)
    assert secret not in repr(error.value.details)
    assert secret not in error.value.message


# --- API 失败原因细分（共享分类器 + 稳定标签） --------------------------------

LABEL_TIMEOUT = "api_embedding_timeout"
LABEL_RATE_LIMITED = "api_embedding_rate_limited"
LABEL_SERVER_ERROR = "api_embedding_server_error"
LABEL_AUTH_ERROR = "api_embedding_auth_error"
LABEL_HTTP_ERROR = "api_embedding_http_error"
LABEL_CONNECT_ERROR = "api_embedding_connect_error"
LABEL_PROTOCOL_ERROR = "api_embedding_protocol_error"
LABEL_TRANSPORT_ERROR = "api_embedding_transport_error"
LABEL_UNAVAILABLE = "api_embedding_unavailable"

ALL_LABELS = (
    LABEL_TIMEOUT,
    LABEL_RATE_LIMITED,
    LABEL_SERVER_ERROR,
    LABEL_AUTH_ERROR,
    LABEL_HTTP_ERROR,
    LABEL_CONNECT_ERROR,
    LABEL_PROTOCOL_ERROR,
    LABEL_TRANSPORT_ERROR,
    LABEL_UNAVAILABLE,
)

_CLASSIFICATION_CASES = [
    pytest.param(httpx.TimeoutException("t"), LABEL_TIMEOUT, id="timeout"),
    pytest.param(httpx.ConnectTimeout("t"), LABEL_TIMEOUT, id="connect_timeout"),
    pytest.param(_status_error(429), LABEL_RATE_LIMITED, id="http_429"),
    pytest.param(_status_error(500), LABEL_SERVER_ERROR, id="http_500"),
    pytest.param(_status_error(503), LABEL_SERVER_ERROR, id="http_503"),
    pytest.param(_status_error(401), LABEL_AUTH_ERROR, id="http_401"),
    pytest.param(_status_error(403), LABEL_AUTH_ERROR, id="http_403"),
    pytest.param(_status_error(400), LABEL_HTTP_ERROR, id="http_400"),
    pytest.param(_status_error(404), LABEL_HTTP_ERROR, id="http_404"),
    pytest.param(httpx.ConnectError("c"), LABEL_CONNECT_ERROR, id="connect_error"),
    pytest.param(httpx.RemoteProtocolError("p"), LABEL_PROTOCOL_ERROR, id="protocol_error"),
    pytest.param(httpx.ReadError("r"), LABEL_TRANSPORT_ERROR, id="read_error"),
    pytest.param(httpx.WriteError("w"), LABEL_TRANSPORT_ERROR, id="write_error"),
    pytest.param(RuntimeError("boom"), LABEL_UNAVAILABLE, id="generic_exception"),
]


@pytest.mark.parametrize(("error", "expected"), _CLASSIFICATION_CASES)
def test_classify_embedding_error_covers_every_category(error, expected) -> None:
    """纯函数分类：复用共享分类器，覆盖全部类别并映射为 api_embedding_* 标签。"""
    label = classify_embedding_error(error)
    assert label == expected
    assert label in ALL_LABELS


def test_classify_embedding_error_never_echoes_exception_text() -> None:
    """分类结果只可能是白名单小写标签，绝不回显异常原文 / URL / 密钥 / 正文。"""
    raw = "failed sk-live-12345 at https://invalid.example.invalid/v1 body=机密内容"
    label = classify_embedding_error(RuntimeError(raw))
    assert label == LABEL_UNAVAILABLE
    for leaked in ("sk-live", "https://", "invalid.example.invalid", "body=", "机密内容", "failed"):
        assert leaked not in label


@pytest.mark.parametrize(("error", "expected"), _CLASSIFICATION_CASES)
def test_api_provider_reports_stable_reason_single_shot_and_not_ready(
    tmp_path, monkeypatch, error, expected
) -> None:
    """Provider 层：稳定细分原因 + retryable + 零重试单次请求 + 失败后 loaded=false。"""
    _use_fake_client(monkeypatch, error=error)
    provider = ApiEmbeddingProvider(_api_settings(tmp_path))
    assert provider.loaded is False

    with pytest.raises(ApiError) as failure:
        provider.embed_documents(["甲"])

    assert failure.value.code == "DOCUMENT_EMBEDDING_FAILED"
    assert failure.value.details.get("reason") == expected
    assert failure.value.retryable is True
    assert len(_FakeClient.calls) == 1, "失败不得重试或发起第二次请求"
    assert provider.loaded is False


def test_api_provider_marks_unparsable_body_as_response_invalid(tmp_path, monkeypatch) -> None:
    """无法解析响应体：统一为 api_embedding_response_invalid，错误码保持 DOCUMENT_EMBEDDING_FAILED。"""
    _use_fake_client(monkeypatch, payload={}, json_error=ValueError("bad json"))
    provider = ApiEmbeddingProvider(_api_settings(tmp_path))
    with pytest.raises(ApiError) as failure:
        provider.embed_documents(["甲"])
    assert failure.value.code == "DOCUMENT_EMBEDDING_FAILED"
    assert failure.value.details.get("reason") == "api_embedding_response_invalid"
    assert len(_FakeClient.calls) == 1
    assert provider.loaded is False


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param(None, id="not_a_dict"),
        pytest.param({"unexpected": []}, id="missing_data"),
        pytest.param({"data": []}, id="empty_data"),
        pytest.param({"data": [{"not_embedding": 1}]}, id="missing_embedding"),
    ],
)
def test_api_provider_marks_malformed_shape_as_response_invalid(
    tmp_path, monkeypatch, payload
) -> None:
    """显式非法响应结构：统一为 api_embedding_response_invalid。"""
    _use_fake_client(monkeypatch, payload=payload)
    provider = ApiEmbeddingProvider(_api_settings(tmp_path))
    with pytest.raises(ApiError) as failure:
        provider.embed_documents(["甲"])
    assert failure.value.code == "DOCUMENT_EMBEDDING_FAILED"
    assert failure.value.details.get("reason") == "api_embedding_response_invalid"
    assert provider.loaded is False


def test_api_provider_reasons_never_leak_status_body_or_url(tmp_path, monkeypatch) -> None:
    """HTTP 状态异常中的响应正文与 URL 绝不出现在细分原因里。"""
    secret = "sk-should-never-appear-12345"
    body = f"upstream failed {secret} at https://invalid.example.invalid/v1"
    _use_fake_client(
        monkeypatch, error=_status_error(500, body, message=f"boom {secret}")
    )
    provider = ApiEmbeddingProvider(_api_settings(tmp_path, embedding_api_key=secret))
    with pytest.raises(ApiError) as failure:
        provider.embed_documents(["甲"])

    haystack = f"{failure.value.message}{failure.value.details}{failure.value!s}{failure.value!r}"
    assert failure.value.details.get("reason") == LABEL_SERVER_ERROR
    assert secret not in haystack
    assert "invalid.example.invalid" not in haystack
    assert "upstream" not in haystack


@pytest.mark.parametrize(
    "bad_value",
    [
        pytest.param("not-a-number", id="non_numeric_str"),
        pytest.param(None, id="none"),
        pytest.param({"x": 1}, id="dict_element"),
        pytest.param(10 ** 400, id="overflow_int"),
    ],
)
def test_api_provider_marks_non_numeric_vector_as_response_invalid(
    tmp_path, monkeypatch, bad_value
) -> None:
    """非数值元素（字符串/None/对象/溢出）必须归 api_embedding_response_invalid，而非传输原因。"""
    embedding = [0.5] * 1024
    embedding[0] = bad_value
    _use_fake_client(monkeypatch, payload={"data": [{"embedding": embedding}]})
    provider = ApiEmbeddingProvider(_api_settings(tmp_path))
    with pytest.raises(ApiError) as failure:
        provider.embed_documents(["甲"])

    assert failure.value.code == "DOCUMENT_EMBEDDING_FAILED"
    assert failure.value.details.get("reason") == "api_embedding_response_invalid"
    assert len(_FakeClient.calls) == 1, "失败不得重试"
    assert provider.loaded is False


def test_api_provider_non_numeric_vector_never_leaks_value(tmp_path, monkeypatch) -> None:
    """非数值元素的原始内容（含密钥样正文）绝不出现在错误信息中。"""
    secret = "sk-should-never-appear-12345"
    embedding = [0.5] * 1024
    embedding[3] = secret
    _use_fake_client(monkeypatch, payload={"data": [{"embedding": embedding}]})
    provider = ApiEmbeddingProvider(_api_settings(tmp_path, embedding_api_key=secret))
    with pytest.raises(ApiError) as failure:
        provider.embed_documents(["甲"])

    assert failure.value.details.get("reason") == "api_embedding_response_invalid"
    haystack = f"{failure.value.message}{failure.value.details}{failure.value!s}{failure.value!r}"
    assert secret not in haystack
    assert "0.5" not in haystack


def test_api_provider_accepts_numeric_strings_without_regression(tmp_path, monkeypatch) -> None:
    """可转换的数值字符串仍按既有规则解析为 float，不回归为非法响应。"""
    _use_fake_client(monkeypatch, payload={"data": [{"embedding": ["0.5"] * 1024}]})
    provider = ApiEmbeddingProvider(_api_settings(tmp_path))
    assert provider.embed_documents(["甲"]) == [[0.5] * 1024]
    assert provider.loaded is True


# --- 8. 客户端复用（懒加载 + 线程安全 close） ---------------------------------


def test_api_provider_reuses_one_client_across_batches(tmp_path, monkeypatch) -> None:
    """多次成功调用（多 batch）只构造一个 Client，且请求数等于批次数。"""
    _use_fake_client(
        monkeypatch,
        payload={
            "data": [
                {"embedding": [0.5] * 1024},
                {"embedding": [0.25] * 1024},
            ]
        },
    )
    provider = ApiEmbeddingProvider(_api_settings(tmp_path, embedding_batch_size=2))
    vectors = provider.embed_documents(["甲", "乙", "丙", "丁"])

    assert len(vectors) == 4
    assert len(_FakeClient.instances) == 1, "同一实例复用同一个 Client"
    assert len(_FakeClient.calls) == 2, "每个 batch 只发一次请求"

    # 第二次独立调用继续复用同一客户端
    provider.embed_documents(["戊", "己"])
    assert len(_FakeClient.instances) == 1
    assert len(_FakeClient.calls) == 3


def test_api_provider_failure_no_retry_then_independent_call_recovers(
    tmp_path, monkeypatch
) -> None:
    _use_fake_client(
        monkeypatch,
        payload={"data": [{"embedding": [0.5] * 1024}]},
        error=httpx.ConnectError("c"),
    )
    provider = ApiEmbeddingProvider(_api_settings(tmp_path))
    with pytest.raises(ApiError) as error:
        provider.embed_documents(["甲"])
    assert error.value.details["reason"] == LABEL_CONNECT_ERROR
    assert len(_FakeClient.calls) == 1, "失败不得重试或发起第二次请求"
    assert provider.loaded is False

    # 下一次独立调用复用同一客户端并恢复成功
    _FakeClient.error = None
    assert provider.embed_documents(["甲"]) == [[0.5] * 1024]
    assert provider.loaded is True
    assert len(_FakeClient.instances) == 1, "恢复时复用同一个客户端"


def test_api_provider_close_is_idempotent_and_relazily_loads(tmp_path, monkeypatch) -> None:
    _use_fake_client(monkeypatch, payload={"data": [{"embedding": [0.5] * 1024}]})
    provider = ApiEmbeddingProvider(_api_settings(tmp_path))
    provider.embed_documents(["甲"])
    assert provider.loaded is True

    provider.close()
    provider.close()
    assert _FakeClient.closed == 1, "close 只关闭一次"
    assert provider.loaded is False

    provider.embed_documents(["甲"])
    assert len(_FakeClient.instances) == 2, "close 后可重新懒加载"
    assert provider.loaded is True


def test_api_provider_empty_input_creates_no_client(tmp_path, monkeypatch) -> None:
    _use_fake_client(monkeypatch, payload={"data": [{"embedding": [0.5] * 1024}]})
    provider = ApiEmbeddingProvider(_api_settings(tmp_path))
    assert provider.embed_documents([]) == []
    assert _FakeClient.instances == []
    assert _FakeClient.calls == []
    assert provider.loaded is False


def test_api_provider_missing_config_creates_no_client(tmp_path, monkeypatch) -> None:
    _use_fake_client(monkeypatch, payload={"data": [{"embedding": [0.5] * 1024}]})
    provider = ApiEmbeddingProvider(_api_settings(tmp_path, embedding_api_key=""))
    with pytest.raises(ApiError) as error:
        provider.embed_documents(["甲"])
    assert error.value.details.get("reason") == "api_embedding_not_configured"
    assert _FakeClient.instances == []
