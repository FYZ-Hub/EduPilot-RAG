"""LLM Provider 工厂。

``fake`` 只允许在非 production 环境使用；``openai_compatible`` 缺失配置时
在首次调用处安全失败。两者都不静默回退到对方。
"""

from __future__ import annotations

from app import constants
from app.config import Settings
from app.core.errors import LLM_PROVIDER_FORBIDDEN, LLM_PROVIDER_UNAVAILABLE, ApiError
from app.embedding.factory import is_production
from app.llm.api import OpenAiCompatibleLLMProvider
from app.llm.base import LLMDescriptor, LLMProvider, descriptor_for
from app.llm.fake import FakeLLMProvider


def build_llm_provider(settings: Settings) -> LLMProvider:
    """按配置构造 Provider；不联网、不加载模型。"""
    provider = settings.llm_provider
    if provider == constants.LLM_PROVIDER_FAKE:
        if is_production(settings):
            raise ApiError(LLM_PROVIDER_FORBIDDEN)
        return FakeLLMProvider()
    if provider == constants.LLM_PROVIDER_API:
        return OpenAiCompatibleLLMProvider(settings)
    raise ApiError(LLM_PROVIDER_UNAVAILABLE, details={"reason": "unknown_provider"})


__all__ = ["LLMDescriptor", "LLMProvider", "build_llm_provider", "descriptor_for"]
