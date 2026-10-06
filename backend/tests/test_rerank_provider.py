"""Reranker Provider：Fake / Local / API 的行为与安全边界。

全部测试离线运行：不访问网络、不下载模型、不依赖 GPU、不导入 torch 或
sentence-transformers（Local 相关用例使用注入的替身模块）。
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import httpx
import pytest

from app import constants
from app.core.errors import ApiError
from app.core.privacy import scrub_texts
from tests.conftest import build_settings
from app.rerank.api import ApiReranker, classify_transport_error
from app.rerank.base import (
    LOCAL_RERANK_MODEL_REVISION,
    RERANK_IMPLEMENTATION_VERSION,
    RERANK_MAX_LENGTH,
    RerankProvider,
    descriptor_for,
)
from app.rerank.factory import build_rerank_provider
from app.rerank.fake import FakeReranker
from app.rerank.local import LocalReranker

CANDIDATES = ["甲候选", "乙候选", "丙候选"]


# --- Fake -------------------------------------------------------------------


def test_fake_reranker_is_deterministic_offline_and_batch_stable(tmp_path) -> None:
    settings = build_settings(tmp_path, rerank_provider="fake")
    provider = build_rerank_provider(settings)

    assert isinstance(provider, FakeReranker)
    assert provider.provider_name == "fake"
    assert provider.loaded is True

    first = provider.rerank("学分认定", CANDIDATES)
    again = provider.rerank("学分认定", CANDIDATES)
    other_query = provider.rerank("另一段查询", CANDIDATES)

    assert first == again
    assert first != other_query
    assert len(first) == len(CANDIDATES)
    assert all(0.0 <= value <= 1.0 for value in first)

    # 逐条与批量一致：分数只取决于 (query, text)，与批内位置无关
    per_item = [provider.rerank("学分认定", [text])[0] for text in CANDIDATES]
    assert first == per_item

    # 离线：不导入模型/网络库，不触碰模型缓存目录
    assert "torch" not in sys.modules
    assert "sentence_transformers" not in sys.modules
    cache = tmp_path / "models"
    assert not cache.exists() or not any(cache.iterdir())


def test_fake_reranker_is_stable_across_processes_and_hashseed() -> None:
    package_root = Path(sys.modules["app"].__file__).resolve().parents[1]
    code = (
        "import json;"
        "from app.rerank.fake import FakeReranker;"
        "print(json.dumps(FakeReranker().rerank('学分认定', ['甲候选','乙候选','丙候选'])))"
    )

    outputs = []
    for seed in ("0", "1", "123456"):
        env = {k: v for k, v in os.environ.items() if k != "PYTHONHASHSEED"}
        env["PYTHONHASHSEED"] = seed
        result = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(package_root),
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        outputs.append(json.loads(result.stdout.strip()))

    assert outputs[0] == outputs[1] == outputs[2]
    assert outputs[0] == FakeReranker().rerank("学分认定", CANDIDATES)


def test_fake_reranker_is_rejected_in_production(tmp_path) -> None:
    settings = build_settings(tmp_path, rerank_provider="fake", app_env="production")
    with pytest.raises(ApiError) as error:
        build_rerank_provider(settings)
    assert error.value.code == "RERANK_PROVIDER_FORBIDDEN"


# --- Local ------------------------------------------------------------------


class _StrictCrossEncoder:
    """严格复刻 ``sentence-transformers==3.3.1`` 的 ``CrossEncoder.__init__`` 签名。

    刻意**不使用** ``**kwargs``：任何拼错的或该版本不存在的关键字都必须抛出
    ``TypeError``，而不是被静默吞掉。
    """

    calls: list = []

    def __init__(
        self,
        model_name: str,
        num_labels: int | None = None,
        max_length: int | None = None,
        device: str | None = None,
        automodel_args: dict | None = None,
        tokenizer_args: dict | None = None,
        config_args: dict | None = None,
        cache_dir: str | None = None,
        trust_remote_code: bool = False,
        revision: str | None = None,
        local_files_only: bool = False,
        default_activation_function=None,
        classifier_dropout: float | None = None,
    ) -> None:
        type(self).calls.append(
            {
                "model": model_name,
                "num_labels": num_labels,
                "max_length": max_length,
                "device": device,
                "automodel_args": automodel_args,
                "tokenizer_args": tokenizer_args,
                "config_args": config_args,
                "cache_dir": cache_dir,
                "trust_remote_code": trust_remote_code,
                "revision": revision,
                "local_files_only": local_files_only,
                "default_activation_function": default_activation_function,
                "classifier_dropout": classifier_dropout,
            }
        )
        self.model = types.SimpleNamespace(eval=lambda: None)
        self.predict_calls: list = []

    def predict(self, pairs, batch_size=None, show_progress_bar=False):
        self.predict_calls.append({"batch_size": batch_size, "pairs": list(pairs)})
        return [0.75 for _ in pairs]


def _install_fake_local_stack(monkeypatch, *, cuda_available: bool = True) -> None:
    _StrictCrossEncoder.calls = []
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: cuda_available)
    torch.no_grad = lambda: contextlib.nullcontext()
    sentence_transformers = types.ModuleType("sentence_transformers")
    sentence_transformers.CrossEncoder = _StrictCrossEncoder
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "sentence_transformers", sentence_transformers)


def test_strict_stub_matches_sentence_transformers_3_3_1_signature() -> None:
    """Stub 与真实 3.3.1 一致：接受 cache_dir，拒绝 cache_folder。"""
    _StrictCrossEncoder("m", cache_dir="/tmp/models")
    with pytest.raises(TypeError):
        _StrictCrossEncoder("m", cache_folder="/tmp/models")  # type: ignore[call-arg]


def test_local_reranker_is_lazy_and_reports_missing_dependency(tmp_path) -> None:
    settings = build_settings(tmp_path, rerank_provider="local", rerank_device="cpu")
    provider = build_rerank_provider(settings)

    # 构造 Provider 不得加载模型，也不得导入可选依赖
    assert isinstance(provider, LocalReranker)
    assert provider.loaded is False
    assert "sentence_transformers" not in sys.modules
    assert "torch" not in sys.modules

    with pytest.raises(ApiError) as error:
        provider.rerank("学分认定", CANDIDATES)
    assert error.value.code == "RERANK_PROVIDER_UNAVAILABLE"
    assert error.value.details.get("reason") == "local_rerank_dependency_missing"
    # 缺依赖时绝不静默回退到其它 Provider
    assert "sentence_transformers" not in sys.modules


def test_local_reranker_revision_is_pinned(tmp_path) -> None:
    default_settings = build_settings(tmp_path, rerank_provider="local")
    assert descriptor_for(default_settings).revision == LOCAL_RERANK_MODEL_REVISION
    assert len(LOCAL_RERANK_MODEL_REVISION) == 40
    assert LOCAL_RERANK_MODEL_REVISION != "main"

    overridden = build_settings(tmp_path, rerank_provider="local", rerank_revision="deadbeef")
    assert descriptor_for(overridden).revision == "deadbeef"


def test_local_reranker_uses_bounded_pinned_configuration(tmp_path, monkeypatch) -> None:
    _install_fake_local_stack(monkeypatch, cuda_available=False)
    settings = build_settings(
        tmp_path, rerank_provider="local", rerank_device="cpu", rerank_batch_size=2
    )
    provider = LocalReranker(settings)

    assert provider.loaded is False
    scores = provider.rerank("学分认定", CANDIDATES)
    assert scores == [0.75, 0.75, 0.75]
    assert provider.loaded is True

    call = _StrictCrossEncoder.calls[0]
    assert call["model"] == "BAAI/bge-reranker-v2-m3"
    assert call["revision"] == LOCAL_RERANK_MODEL_REVISION
    assert call["device"] == "cpu"
    assert call["max_length"] == RERANK_MAX_LENGTH
    assert call["trust_remote_code"] is False
    assert call["local_files_only"] is True
    # sentence-transformers 3.3.1 的真实参数名是 cache_dir（不是 cache_folder）
    assert call["cache_dir"] == str(tmp_path / "models")


def test_local_reranker_cuda_unavailable_fails_without_cpu_fallback(tmp_path, monkeypatch) -> None:
    _install_fake_local_stack(monkeypatch, cuda_available=False)
    settings = build_settings(tmp_path, rerank_provider="local", rerank_device="cuda")
    provider = LocalReranker(settings)

    with pytest.raises(ApiError) as error:
        provider.rerank("学分认定", CANDIDATES)
    assert error.value.code == "RERANK_PROVIDER_UNAVAILABLE"
    assert error.value.details.get("reason") == "cuda_unavailable"
    # 绝不静默回退 CPU：模型根本没有被构造
    assert _StrictCrossEncoder.calls == []
    assert provider.loaded is False


def test_local_reranker_close_is_idempotent(tmp_path, monkeypatch) -> None:
    _install_fake_local_stack(monkeypatch)
    settings = build_settings(tmp_path, rerank_provider="local", rerank_device="cpu")
    provider = LocalReranker(settings)
    provider.rerank("学分认定", CANDIDATES)
    assert provider.loaded is True

    provider.close()
    provider.close()
    assert provider.loaded is False


def test_local_reranker_batch_size_bounds_input(tmp_path, monkeypatch) -> None:
    _install_fake_local_stack(monkeypatch)
    settings = build_settings(
        tmp_path, rerank_provider="local", rerank_device="cpu", rerank_batch_size=4
    )
    provider = LocalReranker(settings)
    provider.rerank("学分认定", CANDIDATES)

    model = provider._model  # noqa: SLF001 - 直接校验批次与截断参数
    assert model.predict_calls[0]["batch_size"] == 4


# --- API --------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, payload=None, json_error: Exception | None = None):
        self._payload = payload
        self._json_error = json_error

    def raise_for_status(self) -> None:
        return None

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class _FakeClient:
    """记录请求并返回固定响应；不访问任何网络。"""

    calls: list = []
    instances: list = []
    closed: int = 0
    responder = staticmethod(lambda payload: {"results": []})
    error: Exception | None = None
    json_error: Exception | None = None

    def __init__(self, *args, timeout=None, **kwargs):
        self.timeout = timeout
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
        return _FakeResponse(type(self).responder(json), type(self).json_error)


def _api_settings(tmp_path, **overrides):
    values = {
        "rerank_provider": "api",
        "rerank_base_url": "https://invalid.example.invalid/v1",
        "rerank_api_key": "test-key-not-real",
        "rerank_model": "rerank-test-model",
    }
    values.update(overrides)
    return build_settings(tmp_path, **values)


def _use_fake_client(monkeypatch, responder, *, error=None, json_error=None) -> None:
    _FakeClient.calls = []
    _FakeClient.instances = []
    _FakeClient.closed = 0
    _FakeClient.responder = staticmethod(responder)
    _FakeClient.error = error
    _FakeClient.json_error = json_error
    monkeypatch.setattr("app.rerank.api.httpx.Client", _FakeClient)


def test_api_reranker_requires_explicit_configuration(tmp_path) -> None:
    settings = _api_settings(tmp_path, rerank_base_url="", rerank_model="")
    provider = ApiReranker(settings)
    with pytest.raises(ApiError) as error:
        provider.rerank("学分认定", CANDIDATES)
    assert error.value.code == "RERANK_PROVIDER_UNAVAILABLE"
    assert error.value.details.get("reason") == "api_rerank_not_configured"


def test_api_reranker_never_claims_ready_from_configuration(tmp_path) -> None:
    provider = ApiReranker(_api_settings(tmp_path))
    # 未做连通性探测前，绝不能仅凭填写配置就声称远端可用
    assert provider.loaded is False


def test_api_reranker_sends_only_query_and_documents(tmp_path, monkeypatch) -> None:
    _use_fake_client(
        monkeypatch,
        lambda payload: {
            "results": [
                {"index": i, "relevance_score": 0.1 * i} for i in range(len(payload["documents"]))
            ]
        },
    )
    provider = ApiReranker(_api_settings(tmp_path, rerank_timeout_seconds=7))
    scores = provider.rerank("学分认定", CANDIDATES)

    assert scores == [0.0, 0.1, 0.2]
    call = _FakeClient.calls[0]
    assert call["url"].endswith("/rerank")
    assert call["headers"]["Authorization"].startswith("Bearer ")
    assert set(call["json"]) == {"model", "query", "documents", "top_n"}
    assert call["json"]["documents"] == CANDIDATES
    assert call["json"]["top_n"] == len(CANDIDATES)
    assert call["json"]["query"] == "学分认定"


def test_api_reranker_scrubs_the_full_candidate_string(tmp_path, monkeypatch) -> None:
    """隐私清洗覆盖**完整候选字符串**（正文 + 元数据行），元数据本身不被误删。"""
    candidate = (
        "联系邮箱 student@example.com，手机 13800138000\n"
        '{"course_code": "QM-CS201", "page_number": 3}'
    )
    _use_fake_client(
        monkeypatch, lambda payload: {"results": [{"index": 0, "relevance_score": 0.5}]}
    )
    provider = ApiReranker(_api_settings(tmp_path))
    provider.rerank("学分认定", [candidate])

    sent = _FakeClient.calls[0]["json"]["documents"][0]
    assert sent == scrub_texts([candidate])[0]
    assert "student@example.com" not in sent
    assert "13800138000" not in sent
    # 元数据字段不被误清洗（学术/业务字段不受隐私规则影响）
    assert "QM-CS201" in sent
    assert "course_code" in sent


def test_api_reranker_maps_out_of_order_indices(tmp_path, monkeypatch) -> None:
    # 服务端乱序返回：0.9 属于 index 2
    _use_fake_client(
        monkeypatch,
        lambda payload: {
            "results": [
                {"index": 2, "relevance_score": 0.9},
                {"index": 0, "relevance_score": 0.1},
                {"index": 1, "relevance_score": 0.5},
            ]
        },
    )
    provider = ApiReranker(_api_settings(tmp_path))
    assert provider.rerank("学分认定", CANDIDATES) == [0.1, 0.5, 0.9]


@pytest.mark.parametrize(
    "results",
    [
        pytest.param([{"index": 0, "relevance_score": 0.1}], id="missing_index"),
        pytest.param(
            [
                {"index": 0, "relevance_score": 0.1},
                {"index": 0, "relevance_score": 0.2},
                {"index": 2, "relevance_score": 0.3},
            ],
            id="duplicate_index",
        ),
        pytest.param(
            [
                {"index": 0, "relevance_score": 0.1},
                {"index": 1, "relevance_score": 0.2},
                {"index": 9, "relevance_score": 0.3},
            ],
            id="out_of_range_index",
        ),
        pytest.param(
            [
                {"index": 0, "relevance_score": float("nan")},
                {"index": 1, "relevance_score": 0.2},
                {"index": 2, "relevance_score": 0.3},
            ],
            id="nan_score",
        ),
        pytest.param(
            [
                {"index": 0, "relevance_score": float("inf")},
                {"index": 1, "relevance_score": 0.2},
                {"index": 2, "relevance_score": 0.3},
            ],
            id="infinity_score",
        ),
        pytest.param(
            [
                {"index": 0, "relevance_score": "high"},
                {"index": 1, "relevance_score": 0.2},
                {"index": 2, "relevance_score": 0.3},
            ],
            id="non_numeric_score",
        ),
    ],
)
def test_api_reranker_rejects_invalid_results(tmp_path, monkeypatch, results) -> None:
    _use_fake_client(monkeypatch, lambda payload: {"results": results})
    provider = ApiReranker(_api_settings(tmp_path))
    with pytest.raises(ApiError) as error:
        provider.rerank("学分认定", CANDIDATES)
    assert error.value.code == "RERANK_RESPONSE_INVALID"


def test_api_reranker_rejects_unparsable_body(tmp_path, monkeypatch) -> None:
    _use_fake_client(monkeypatch, lambda payload: None, json_error=ValueError("bad json"))
    provider = ApiReranker(_api_settings(tmp_path))
    with pytest.raises(ApiError) as error:
        provider.rerank("学分认定", CANDIDATES)
    assert error.value.code == "RERANK_RESPONSE_INVALID"


def test_api_reranker_rejects_malformed_payload_shape(tmp_path, monkeypatch) -> None:
    _use_fake_client(monkeypatch, lambda payload: {"unexpected": []})
    provider = ApiReranker(_api_settings(tmp_path))
    with pytest.raises(ApiError) as error:
        provider.rerank("学分认定", CANDIDATES)
    assert error.value.code == "RERANK_RESPONSE_INVALID"


def test_api_reranker_http_error_is_safe_and_retryable(tmp_path, monkeypatch) -> None:
    _use_fake_client(monkeypatch, lambda payload: None, error=RuntimeError("500 upstream"))
    provider = ApiReranker(_api_settings(tmp_path))
    with pytest.raises(ApiError) as error:
        provider.rerank("学分认定", CANDIDATES)
    assert error.value.code == "RERANK_PROVIDER_UNAVAILABLE"
    assert error.value.details.get("reason") == "api_rerank_unavailable"
    assert error.value.retryable is True


def test_api_reranker_timeout_is_reported_distinctly(tmp_path, monkeypatch) -> None:
    _use_fake_client(
        monkeypatch, lambda payload: None, error=httpx.TimeoutException("timed out")
    )
    provider = ApiReranker(_api_settings(tmp_path))
    with pytest.raises(ApiError) as error:
        provider.rerank("学分认定", CANDIDATES)
    assert error.value.code == "RERANK_PROVIDER_UNAVAILABLE"
    assert error.value.details.get("reason") == "api_rerank_timeout"


def test_api_reranker_error_does_not_leak_key_or_body(tmp_path, monkeypatch) -> None:
    secret = "sk-should-never-appear-12345"
    _use_fake_client(
        monkeypatch,
        lambda payload: None,
        error=RuntimeError(f"failed with {secret} at https://invalid.example.invalid/v1"),
    )
    provider = ApiReranker(_api_settings(tmp_path, rerank_api_key=secret))
    with pytest.raises(ApiError) as error:
        provider.rerank("学分认定", CANDIDATES)

    haystack = f"{error.value.message}{error.value.details}{error.value!s}{error.value!r}"
    assert secret not in haystack
    assert "invalid.example.invalid" not in haystack
    assert CANDIDATES[0] not in haystack


def test_rerank_provider_interface_is_shared(tmp_path) -> None:
    """fake / local / api 必须实现同一接口。"""
    from app.rerank.api import ApiReranker as _Api
    from app.rerank.fake import FakeReranker as _Fake
    from app.rerank.local import LocalReranker as _Local

    for cls in (_Fake, _Local, _Api):
        assert issubclass(cls, RerankProvider)
        assert callable(getattr(cls, "rerank"))
    assert constants.RERANK_PROVIDERS == ("fake", "local", "api")


# --- API 失败原因细分（有限稳定标签） ---------------------------------------

LABEL_TIMEOUT = "api_rerank_timeout"
LABEL_RATE_LIMITED = "api_rerank_rate_limited"
LABEL_SERVER_ERROR = "api_rerank_server_error"
LABEL_AUTH_ERROR = "api_rerank_auth_error"
LABEL_HTTP_ERROR = "api_rerank_http_error"
LABEL_CONNECT_ERROR = "api_rerank_connect_error"
LABEL_PROTOCOL_ERROR = "api_rerank_protocol_error"
LABEL_TRANSPORT_ERROR = "api_rerank_transport_error"
LABEL_UNAVAILABLE = "api_rerank_unavailable"

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


def _status_error(status_code: int, body: str = "", message: str = "upstream-detail") -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "https://invalid.example.invalid/v1/rerank")
    response = httpx.Response(status_code, request=request, text=body)
    return httpx.HTTPStatusError(message, request=request, response=response)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
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
        pytest.param(ValueError("x"), LABEL_UNAVAILABLE, id="value_error"),
    ],
)
def test_classify_transport_error_covers_every_label(error, expected) -> None:
    """纯函数分类：只依据异常类型与状态码，覆盖全部有限标签。"""
    label = classify_transport_error(error)
    assert label == expected
    assert label in ALL_LABELS


def test_classify_transport_error_never_echoes_exception_text() -> None:
    """分类结果只可能是白名单小写标签，绝不回显异常原文 / URL / 密钥 / 正文。"""
    raw = "failed sk-live-12345 at https://invalid.example.invalid/v1 body=机密内容"
    label = classify_transport_error(RuntimeError(raw))
    assert label == LABEL_UNAVAILABLE
    for leaked in ("sk-live", "https://", "invalid.example.invalid", "body=", "机密内容", "failed"):
        assert leaked not in label


def test_rerank_labels_unchanged_after_shared_classifier_extraction() -> None:
    """抽取共享分类器后，``api_rerank_*`` 标签必须完全不变（不回归）。"""
    from app.core.http_errors import HTTP_ERROR_CATEGORIES, prefixed_labels

    assert HTTP_ERROR_CATEGORIES == (
        "timeout",
        "rate_limited",
        "server_error",
        "auth_error",
        "http_error",
        "connect_error",
        "protocol_error",
        "transport_error",
        "unavailable",
    )
    assert prefixed_labels("api_rerank") == {
        "timeout": LABEL_TIMEOUT,
        "rate_limited": LABEL_RATE_LIMITED,
        "server_error": LABEL_SERVER_ERROR,
        "auth_error": LABEL_AUTH_ERROR,
        "http_error": LABEL_HTTP_ERROR,
        "connect_error": LABEL_CONNECT_ERROR,
        "protocol_error": LABEL_PROTOCOL_ERROR,
        "transport_error": LABEL_TRANSPORT_ERROR,
        "unavailable": LABEL_UNAVAILABLE,
    }


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        pytest.param(httpx.TimeoutException("t"), LABEL_TIMEOUT, id="timeout"),
        pytest.param(_status_error(429), LABEL_RATE_LIMITED, id="http_429"),
        pytest.param(_status_error(502), LABEL_SERVER_ERROR, id="http_502"),
        pytest.param(_status_error(403), LABEL_AUTH_ERROR, id="http_403"),
        pytest.param(_status_error(404), LABEL_HTTP_ERROR, id="http_404"),
        pytest.param(httpx.ConnectError("c"), LABEL_CONNECT_ERROR, id="connect_error"),
        pytest.param(httpx.RemoteProtocolError("p"), LABEL_PROTOCOL_ERROR, id="protocol_error"),
        pytest.param(httpx.ReadError("r"), LABEL_TRANSPORT_ERROR, id="read_error"),
        pytest.param(RuntimeError("boom"), LABEL_UNAVAILABLE, id="generic_exception"),
    ],
)
def test_api_reranker_reports_stable_reason_single_shot_and_not_ready(
    tmp_path, monkeypatch, error, expected
) -> None:
    """Provider 层：稳定细分原因 + retryable + 零重试单次请求 + 失败后 loaded=false。"""
    _use_fake_client(monkeypatch, lambda payload: None, error=error)
    provider = ApiReranker(_api_settings(tmp_path))
    assert provider.loaded is False

    with pytest.raises(ApiError) as failure:
        provider.rerank("学分认定", CANDIDATES)

    assert failure.value.code == "RERANK_PROVIDER_UNAVAILABLE"
    assert failure.value.details.get("reason") == expected
    assert failure.value.retryable is True
    assert len(_FakeClient.calls) == 1, "失败不得重试或发起第二次请求"
    assert provider.loaded is False


def test_api_reranker_classifies_status_raised_by_raise_for_status(tmp_path, monkeypatch) -> None:
    """真实路径：``raise_for_status`` 抛出的 HTTPStatusError 同样按状态码细分。"""
    calls = {"post": 0}

    class _StatusResponse:
        status_code = 429

        def raise_for_status(self) -> None:
            raise _status_error(429)

    class _StatusClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def post(self, url, headers=None, json=None):
            calls["post"] += 1
            return _StatusResponse()

    monkeypatch.setattr("app.rerank.api.httpx.Client", _StatusClient)
    provider = ApiReranker(_api_settings(tmp_path))
    with pytest.raises(ApiError) as failure:
        provider.rerank("学分认定", CANDIDATES)

    assert failure.value.details.get("reason") == LABEL_RATE_LIMITED
    assert failure.value.retryable is True
    assert calls["post"] == 1
    assert provider.loaded is False


def test_api_reranker_reasons_never_leak_status_body_or_url(tmp_path, monkeypatch) -> None:
    """HTTP 状态异常中的响应正文与 URL 绝不出现在细分原因里。"""
    secret = "sk-should-never-appear-12345"
    body = f"upstream failed {secret} at https://invalid.example.invalid/v1"
    _use_fake_client(
        monkeypatch,
        lambda payload: None,
        error=_status_error(500, body, message=f"boom {secret}"),
    )
    provider = ApiReranker(_api_settings(tmp_path, rerank_api_key=secret))
    with pytest.raises(ApiError) as failure:
        provider.rerank("学分认定", CANDIDATES)

    haystack = f"{failure.value.message}{failure.value.details}{failure.value!s}{failure.value!r}"
    assert failure.value.details.get("reason") == LABEL_SERVER_ERROR
    assert secret not in haystack
    assert "invalid.example.invalid" not in haystack
    assert "upstream" not in haystack
    assert CANDIDATES[0] not in haystack


def test_rerank_implementation_version_is_bumped(tmp_path) -> None:
    """版本随重排阶段行为演进（元数据 1.2.0 / 客户端复用 1.3.0 / 撤回硬覆盖 1.4.2 / 上限 6→10 的 1.5.0）记录在 descriptor。"""
    assert RERANK_IMPLEMENTATION_VERSION == "1.5.0"
    assert descriptor_for(_api_settings(tmp_path)).implementation_version == "1.5.0"
    assert (
        descriptor_for(build_settings(tmp_path, rerank_provider="fake")).implementation_version
        == "1.5.0"
    )


# --- 客户端复用（懒加载 + 线程安全 close） ------------------------------------


def _identity_scores(payload):
    return {
        "results": [
            {"index": i, "relevance_score": 0.1 * i}
            for i in range(len(payload["documents"]))
        ]
    }


def test_api_reranker_reuses_one_client_across_calls(tmp_path, monkeypatch) -> None:
    _use_fake_client(monkeypatch, _identity_scores)
    provider = ApiReranker(_api_settings(tmp_path))
    provider.rerank("学分认定", CANDIDATES)
    provider.rerank("学分认定", CANDIDATES)

    assert len(_FakeClient.instances) == 1, "同一实例复用同一个 Client"
    assert len(_FakeClient.calls) == 2, "每次调用只发一次请求"


def test_api_reranker_failure_no_retry_then_independent_call_recovers(
    tmp_path, monkeypatch
) -> None:
    _use_fake_client(monkeypatch, _identity_scores, error=httpx.ConnectError("c"))
    provider = ApiReranker(_api_settings(tmp_path))
    with pytest.raises(ApiError) as error:
        provider.rerank("学分认定", CANDIDATES)
    assert error.value.details["reason"] == LABEL_CONNECT_ERROR
    assert len(_FakeClient.calls) == 1, "失败不得重试"
    assert provider.loaded is False

    _FakeClient.error = None
    assert provider.rerank("学分认定", CANDIDATES) == [0.0, 0.1, 0.2]
    assert provider.loaded is True
    assert len(_FakeClient.instances) == 1, "恢复时复用同一个客户端"


def test_api_reranker_close_is_idempotent_and_relazily_loads(tmp_path, monkeypatch) -> None:
    _use_fake_client(monkeypatch, _identity_scores)
    provider = ApiReranker(_api_settings(tmp_path))
    provider.rerank("学分认定", CANDIDATES)
    assert provider.loaded is True

    provider.close()
    provider.close()
    assert _FakeClient.closed == 1, "close 只关闭一次"
    assert provider.loaded is False

    provider.rerank("学分认定", CANDIDATES)
    assert len(_FakeClient.instances) == 2, "close 后可重新懒加载"
    assert provider.loaded is True


def test_api_reranker_empty_input_creates_no_client(tmp_path, monkeypatch) -> None:
    _use_fake_client(monkeypatch, _identity_scores)
    provider = ApiReranker(_api_settings(tmp_path))
    assert provider.rerank("学分认定", []) == []
    assert _FakeClient.instances == []
    assert _FakeClient.calls == []
    assert provider.loaded is False


def test_api_reranker_missing_config_creates_no_client(tmp_path, monkeypatch) -> None:
    _use_fake_client(monkeypatch, _identity_scores)
    provider = ApiReranker(_api_settings(tmp_path, rerank_api_key=""))
    with pytest.raises(ApiError) as error:
        provider.rerank("学分认定", CANDIDATES)
    assert error.value.details.get("reason") == "api_rerank_not_configured"
    assert _FakeClient.instances == []
