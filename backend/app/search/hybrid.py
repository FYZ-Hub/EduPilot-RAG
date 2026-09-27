"""HybridRetriever：Dense + Keyword 的确定性 RRF 融合。

``score = Σ 1 / (RRF_K + rank)``，其中 ``RRF_K`` 是版本化常量并进入检索诊断，
不允许在运行期临时变化。本阶段**不执行 Reranker**，输出即阶段 5 的候选输入。
"""

from __future__ import annotations

import time
from dataclasses import replace

from sqlalchemy.orm import Session

from app.config import Settings
from app.documents.fingerprint import VECTOR_SCHEMA_VERSION
from app.embedding.base import EmbeddingProvider
from app.search.dense import DenseRetriever
from app.search.hydrate import build_scope
from app.search.keyword import KeywordRetriever
from app.search.schema import (
    FTS_NGRAM_VERSION,
    FTS_SCHEMA_VERSION,
    FTS_TOKENIZER,
)
from app.search.types import (
    HybridResult,
    RetrievalDiagnostics,
    RetrievalFilters,
    RetrievedChunk,
)
from app.vector.store import ChromaVectorStore

# RRF 版本化参数（进入诊断，运行时不可变）
RRF_K = 60
RRF_VERSION = "rrf-v1"


def fuse(
    dense: list[RetrievedChunk], keyword: list[RetrievedChunk], rrf_k: int = RRF_K
) -> list[RetrievedChunk]:
    """RRF 融合；同一 chunk_id 只保留一次，输出按稳定规则排序。"""
    merged: dict[str, RetrievedChunk] = {}
    scores: dict[str, float] = {}

    for rank, chunk in enumerate(dense, start=1):
        merged[chunk.chunk_id] = replace(
            chunk, dense_rank=rank, keyword_rank=None, keyword_score=None
        )
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (rrf_k + rank)

    for rank, chunk in enumerate(keyword, start=1):
        existing = merged.get(chunk.chunk_id)
        if existing is None:
            merged[chunk.chunk_id] = replace(
                chunk, dense_rank=None, dense_score=None, keyword_rank=rank
            )
        else:
            merged[chunk.chunk_id] = replace(existing, keyword_rank=rank)
        scores[chunk.chunk_id] = scores.get(chunk.chunk_id, 0.0) + 1.0 / (rrf_k + rank)

    fused = [
        replace(chunk, fused_score=scores[chunk_id], dense_score=chunk.dense_score)
        for chunk_id, chunk in merged.items()
    ]

    def sort_key(chunk: RetrievedChunk) -> tuple[float, int, str]:
        best_rank = min(
            rank
            for rank in (chunk.dense_rank, chunk.keyword_rank)
            if rank is not None
        )
        return (-(chunk.fused_score or 0.0), best_rank, chunk.chunk_id)

    return sorted(fused, key=sort_key)


class HybridRetriever:
    """混合检索：Dense 前 K + Keyword 前 K → RRF。"""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        vectors: ChromaVectorStore,
        embeddings: EmbeddingProvider,
    ):
        self.session = session
        self.settings = settings
        self.vectors = vectors
        self.embeddings = embeddings

    def search(
        self,
        query: str,
        filters: RetrievalFilters | None = None,
        top_k: int | None = None,
    ) -> tuple[list[HybridResult], RetrievalDiagnostics]:
        filters = filters or RetrievalFilters()
        started = time.perf_counter()

        dense = DenseRetriever(
            self.session, self.settings, self.vectors, self.embeddings
        ).search(query, filters)
        keyword, match_mode = KeywordRetriever(self.session, self.settings).search(
            query, filters
        )

        fused = fuse(dense, keyword, RRF_K)
        if top_k is not None:
            fused = fused[: int(top_k)]

        scope = build_scope(self.session, self.settings)
        descriptor = self.vectors.descriptor
        diagnostics = RetrievalDiagnostics(
            dense_candidates=len(dense),
            keyword_candidates=len(keyword),
            fused_candidates=len(fused),
            applied_filters=filters.as_dict(),
            elapsed_ms=int((time.perf_counter() - started) * 1000),
            embedding_provider=descriptor.provider,
            embedding_revision=descriptor.revision,
            vector_schema_version=VECTOR_SCHEMA_VERSION,
            fts_schema_version=FTS_SCHEMA_VERSION,
            fts_tokenizer=FTS_TOKENIZER,
            fts_ngram_version=FTS_NGRAM_VERSION,
            rrf_k=RRF_K,
            match_mode=match_mode,
            demo_available=scope.demo_available,
        )
        results = [HybridResult(chunk=chunk, fused_score=chunk.fused_score or 0.0) for chunk in fused]
        return results, diagnostics


__all__ = ["RRF_K", "RRF_VERSION", "HybridRetriever", "fuse"]
