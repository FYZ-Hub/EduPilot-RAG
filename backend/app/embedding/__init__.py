"""Embedding Provider 抽象与实现（fake / local / api）。"""

from app.embedding.api import ApiEmbeddingProvider
from app.embedding.base import EmbeddingDescriptor, EmbeddingProvider, descriptor_for
from app.embedding.fake import FakeEmbeddingProvider
from app.embedding.factory import build_embedding_provider, is_production
from app.embedding.local import LocalEmbeddingProvider

__all__ = [
    "ApiEmbeddingProvider",
    "EmbeddingDescriptor",
    "EmbeddingProvider",
    "FakeEmbeddingProvider",
    "LocalEmbeddingProvider",
    "build_embedding_provider",
    "descriptor_for",
    "is_production",
]
