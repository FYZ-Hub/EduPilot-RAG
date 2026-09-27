"""Embedding Provider：Fake / Local / API 的行为与安全边界。"""

from __future__ import annotations

import sys

import pytest

from tests.conftest import build_settings
from app import constants
from app.core.errors import ApiError
from app.documents.fingerprint import stage_fingerprints
from app.embedding.api import ApiEmbeddingProvider
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


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._payload


class _FakeClient:
    """记录请求并返回固定响应；不访问任何网络。"""

    calls: list = []
    payload: dict = {}

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False

    def post(self, url, headers=None, json=None):
        type(self).calls.append({"url": url, "headers": headers, "json": json})
        return _FakeResponse(type(self).payload)


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
    _FakeClient.calls = []
    _FakeClient.payload = {
        "data": [
            {"embedding": [0.5] * 1024},
            {"embedding": [0.25] * 1024},
        ]
    }
    monkeypatch.setattr("app.embedding.api.httpx.Client", _FakeClient)

    settings = build_settings(
        tmp_path,
        embedding_provider="api",
        embedding_base_url="https://invalid.example.invalid/v1",
        embedding_api_key="test-key-not-real",
        embedding_model="text-embedding-test",
        embedding_batch_size=2,
    )
    provider = ApiEmbeddingProvider(settings)
    vectors = provider.embed_documents(["甲", "乙"])

    assert len(vectors) == 2
    assert len(vectors[0]) == 1024
    assert _FakeClient.calls[0]["url"].endswith("/embeddings")
    assert _FakeClient.calls[0]["json"]["model"] == "text-embedding-test"
    assert _FakeClient.calls[0]["headers"]["Authorization"].startswith("Bearer ")


def test_api_provider_rejects_wrong_dimension(tmp_path, monkeypatch) -> None:
    _FakeClient.calls = []
    _FakeClient.payload = {"data": [{"embedding": [0.5] * 8}]}
    monkeypatch.setattr("app.embedding.api.httpx.Client", _FakeClient)

    settings = build_settings(
        tmp_path,
        embedding_provider="api",
        embedding_base_url="https://invalid.example.invalid/v1",
        embedding_api_key="test-key-not-real",
        embedding_model="text-embedding-test",
    )
    provider = ApiEmbeddingProvider(settings)
    with pytest.raises(ApiError) as error:
        provider.embed_documents(["甲"])
    assert error.value.code == "EMBEDDING_DIMENSION_MISMATCH"


def test_api_provider_error_does_not_leak_key(tmp_path, monkeypatch) -> None:
    _FakeClient.calls = []
    _FakeClient.payload = {"unexpected": "shape"}
    monkeypatch.setattr("app.embedding.api.httpx.Client", _FakeClient)

    secret = "sk-should-never-appear-12345"
    settings = build_settings(
        tmp_path,
        embedding_provider="api",
        embedding_base_url="https://invalid.example.invalid/v1",
        embedding_api_key=secret,
        embedding_model="text-embedding-test",
    )
    provider = ApiEmbeddingProvider(settings)
    with pytest.raises(ApiError) as error:
        provider.embed_documents(["甲"])

    assert error.value.code == "DOCUMENT_EMBEDDING_FAILED"
    assert secret not in str(error.value)
    assert secret not in repr(error.value.details)
    assert secret not in error.value.message
