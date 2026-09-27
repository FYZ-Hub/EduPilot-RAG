"""LLM Provider 抽象与实现（fake / openai_compatible；不提供本地 LLM）。"""

from app.llm.api import OpenAiCompatibleLLMProvider
from app.llm.base import LLMDescriptor, LLMProvider, descriptor_for
from app.llm.fake import FakeLLMProvider
from app.llm.factory import build_llm_provider

__all__ = [
    "FakeLLMProvider",
    "LLMDescriptor",
    "LLMProvider",
    "OpenAiCompatibleLLMProvider",
    "build_llm_provider",
    "descriptor_for",
]
