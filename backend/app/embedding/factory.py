"""Embedding Provider 工厂。

``fake`` 只允许在非 production 环境使用；``local`` / ``api`` 的缺失配置或缺失可选依赖
一律给出明确、可操作的安全错误，绝不静默回退到其它 Provider。
"""

from __future__ import annotations

from app import constants
from app.config import Settings
from app.core.errors import EMBEDDING_PROVIDER_FORBIDDEN, EMBEDDING_PROVIDER_UNAVAILABLE, ApiError
from app.embedding.api import ApiEmbeddingProvider
from app.embedding.base import EmbeddingDescriptor, EmbeddingProvider, descriptor_for
from app.embedding.fake import FakeEmbeddingProvider
from app.embedding.local import LocalEmbeddingProvider
from app.runtime.coordinator import LocalModelCoordinator

PRODUCTION_ENVS = frozenset({"production", "prod"})


def is_production(settings: Settings) -> bool:
    return settings.app_env.strip().lower() in PRODUCTION_ENVS


def build_embedding_provider(
    settings: Settings, coordinator: LocalModelCoordinator | None = None
) -> EmbeddingProvider:
    """按配置构造 Provider；不加载模型、不访问网络。"""
    provider = settings.embedding_provider
    if provider == constants.EMBEDDING_PROVIDER_FAKE:
        if is_production(settings):
            raise ApiError(EMBEDDING_PROVIDER_FORBIDDEN)
        return FakeEmbeddingProvider()
    if provider == constants.EMBEDDING_PROVIDER_LOCAL:
        return LocalEmbeddingProvider(settings, coordinator)
    if provider == constants.EMBEDDING_PROVIDER_API:
        return ApiEmbeddingProvider(settings)
    raise ApiError(EMBEDDING_PROVIDER_UNAVAILABLE, details={"reason": "unknown_provider"})


__all__ = [
    "EmbeddingDescriptor",
    "EmbeddingProvider",
    "build_embedding_provider",
    "descriptor_for",
    "is_production",
]
