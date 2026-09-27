"""Reranker Provider 工厂。

``fake`` 只允许在非 production 环境使用；``local`` / ``api`` 的缺失配置或缺失可选依赖
一律给出明确、可操作的安全错误，绝不静默回退到其它 Provider。
"""

from __future__ import annotations

from app import constants
from app.config import Settings
from app.core.errors import RERANK_PROVIDER_FORBIDDEN, RERANK_PROVIDER_UNAVAILABLE, ApiError
from app.embedding.factory import is_production
from app.rerank.api import ApiReranker
from app.rerank.base import RerankDescriptor, RerankProvider, descriptor_for
from app.rerank.fake import FakeReranker
from app.rerank.local import LocalReranker
from app.runtime.coordinator import LocalModelCoordinator


def build_rerank_provider(
    settings: Settings, coordinator: LocalModelCoordinator | None = None
) -> RerankProvider:
    """按配置构造 Provider；不加载模型、不访问网络。"""
    provider = settings.rerank_provider
    if provider == constants.RERANK_PROVIDER_FAKE:
        if is_production(settings):
            raise ApiError(RERANK_PROVIDER_FORBIDDEN)
        return FakeReranker()
    if provider == constants.RERANK_PROVIDER_LOCAL:
        return LocalReranker(settings, coordinator)
    if provider == constants.RERANK_PROVIDER_API:
        return ApiReranker(settings)
    raise ApiError(RERANK_PROVIDER_UNAVAILABLE, details={"reason": "unknown_provider"})


__all__ = [
    "RerankDescriptor",
    "RerankProvider",
    "build_rerank_provider",
    "descriptor_for",
]
