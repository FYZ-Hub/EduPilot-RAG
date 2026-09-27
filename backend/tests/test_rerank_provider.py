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
from tests.conftest import build_settings
from app.rerank.api import ApiReranker
from app.rerank.base import (
    LOCAL_RERANK_MODEL_REVISION,
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


class _RecordingCrossEncoder:
    calls: list = []

    def __init__(self, model_name, **kwargs):
        type(self).calls.append({"model": model_name, **kwargs})
        self.model = types.SimpleNamespace(eval=lambda: None)
        self.predict_calls: list = []

    def predict(self, pairs, batch_size=None, show_progress_bar=False):
        self.predict_calls.append({"batch_size": batch_size, "pairs": list(pairs)})
        return [0.75 for _ in pairs]


def _install_fake_local_stack(monkeypatch, *, cuda_available: bool = True) -> None:
    _RecordingCrossEncoder.calls = []
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: cuda_available)
    torch.no_grad = lambda: contextlib.nullcontext()
    sentence_transformers = types.ModuleType("sentence_transformers")
    sentence_transformers.CrossEncoder = _RecordingCrossEncoder
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "sentence_transformers", sentence_transformers)


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

    call = _RecordingCrossEncoder.calls[0]
    assert call["model"] == "BAAI/bge-reranker-v2-m3"
    assert call["revision"] == LOCAL_RERANK_MODEL_REVISION
    assert call["device"] == "cpu"
    assert call["max_length"] == RERANK_MAX_LENGTH
    assert call["trust_remote_code"] is False
    assert call["local_files_only"] is True
    assert call["cache_folder"] == str(tmp_path / "models")


def test_local_reranker_cuda_unavailable_fails_without_cpu_fallback(tmp_path, monkeypatch) -> None:
    _install_fake_local_stack(monkeypatch, cuda_available=False)
    settings = build_settings(tmp_path, rerank_provider="local", rerank_device="cuda")
    provider = LocalReranker(settings)

    with pytest.raises(ApiError) as error:
        provider.rerank("学分认定", CANDIDATES)
    assert error.value.code == "RERANK_PROVIDER_UNAVAILABLE"
    assert error.value.details.get("reason") == "cuda_unavailable"
    # 绝不静默回退 CPU：模型根本没有被构造
    assert _RecordingCrossEncoder.calls == []
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
    responder = staticmethod(lambda payload: {"results": []})
    error: Exception | None = None
    json_error: Exception | None = None

    def __init__(self, *args, timeout=None, **kwargs):
        self.timeout = timeout

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

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
