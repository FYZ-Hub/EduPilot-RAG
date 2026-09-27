"""Reranker Provider 抽象与实现（fake / local / api）。"""

from app.rerank.api import ApiReranker
from app.rerank.base import RerankDescriptor, RerankProvider, descriptor_for
from app.rerank.fake import FakeReranker
from app.rerank.factory import build_rerank_provider
from app.rerank.local import LocalReranker

__all__ = [
    "ApiReranker",
    "FakeReranker",
    "LocalReranker",
    "RerankDescriptor",
    "RerankProvider",
    "build_rerank_provider",
    "descriptor_for",
]
