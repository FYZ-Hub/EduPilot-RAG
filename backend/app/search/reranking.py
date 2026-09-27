"""RerankingRetriever：在 ``HybridRetriever`` 之上执行查询时重排。

``HybridRetriever`` 保持为纯粹的 Dense + Keyword + RRF，本模块是独立的包装层：

    HybridRetriever（RRF 顺序）
      → 取最多 ``RERANK_MAX_CANDIDATES``（20）条候选
      → Reranker 打分
      → 稳定排序（rerank_score 降序 → fused_score 降序 → chunk_id 升序）
      → 输出前 ``RERANK_TOP_K``（6）条

- 空候选**不调用** Provider；候选少于 ``rerank_top_k`` 时只返回实际数量。
- Provider 只返回分数；引用字段始终直接来自原 ``RetrievedChunk``，不会与分数错位。
- Reranker 明确不可用或 API 超时时安全降级：返回原 RRF 顺序的前 K 条，
  ``rerank_applied=false``、``rerank_score=null`` 并记录稳定 ``degraded_reason``；
  但**不吞掉**引用错位、非法返回结构等内部不变量错误。
"""

from __future__ import annotations

import math
import re
import time
from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.config import Settings
from app.core.errors import RERANK_PROVIDER_UNAVAILABLE, RERANK_RESPONSE_INVALID, ApiError
from app.embedding.base import EmbeddingProvider
from app.rerank.base import RerankProvider
from app.runtime.coordinator import LocalModelCoordinator
from app.search.hybrid import RRF_VERSION, HybridRetriever
from app.search.types import (
    RerankDiagnostics,
    RerankedResult,
    RetrievalFilters,
)
from app.vector.store import ChromaVectorStore

# 固定、版本化的重排输入上限（PRODUCT_SPEC 7.2：重排前 12–20 条）
RERANK_MAX_CANDIDATES = 20

# 降级原因兜底值；正确性要求：不含任何敏感数据、跨运行稳定
DEGRADED_RERANKER_UNAVAILABLE = "reranker_unavailable"
_DEGRADED_REASON_RE = re.compile(r"^[a-z0-9_]{1,48}$")


def validate_rerank_scores(scores: object, expected: int) -> list[float]:
    """校验 Provider 输出：数量一致且全部为有限浮点数，否则抛出内部不变量错误。"""
    if not isinstance(scores, Sequence) or isinstance(scores, (str, bytes)):
        raise ApiError(RERANK_RESPONSE_INVALID)
    if len(scores) != expected:
        raise ApiError(
            RERANK_RESPONSE_INVALID, details={"reason": "score_count_mismatch"}
        )
    values: list[float] = []
    for score in scores:
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ApiError(RERANK_RESPONSE_INVALID, details={"reason": "score_not_numeric"})
        value = float(score)
        if not math.isfinite(value):
            raise ApiError(RERANK_RESPONSE_INVALID, details={"reason": "score_not_finite"})
        values.append(value)
    return values


def _degraded_reason(error: ApiError) -> str:
    """从安全错误中提取稳定、无敏感数据的降级原因。"""
    reason = error.details.get("reason") if isinstance(error.details, dict) else None
    if isinstance(reason, str) and _DEGRADED_REASON_RE.match(reason):
        return reason
    return DEGRADED_RERANKER_UNAVAILABLE


class RerankingRetriever:
    """混合检索 + 查询时重排；默认输出前 ``rerank_top_k``（6）条。"""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        vectors: ChromaVectorStore,
        embeddings: EmbeddingProvider,
        reranker: RerankProvider,
        coordinator: LocalModelCoordinator | None = None,
    ):
        self.session = session
        self.settings = settings
        self.vectors = vectors
        self.embeddings = embeddings
        self.reranker = reranker
        self.coordinator = coordinator

    def search(
        self,
        query: str,
        filters: RetrievalFilters | None = None,
        top_k: int | None = None,
    ) -> tuple[list[RerankedResult], RerankDiagnostics]:
        limit = max(1, int(top_k or self.settings.rerank_top_k))
        started = time.perf_counter()

        hybrid = HybridRetriever(
            self.session, self.settings, self.vectors, self.embeddings, self.coordinator
        )
        hybrid_results, retrieval = hybrid.search(query, filters, top_k=RERANK_MAX_CANDIDATES)
        candidates = hybrid_results[:RERANK_MAX_CANDIDATES]

        def diagnostics(**overrides) -> RerankDiagnostics:
            descriptor = self.reranker.descriptor
            values = {
                "retrieval": retrieval,
                "rerank_input_candidates": len(candidates),
                "reranker_provider": descriptor.provider,
                "reranker_revision": descriptor.revision,
                "reranker_fingerprint": descriptor.fingerprint,
                "rerank_score_kind": descriptor.score_kind,
                "rrf_version": RRF_VERSION,
                "rerank_elapsed_ms": int((time.perf_counter() - started) * 1000),
            }
            values.update(overrides)
            return RerankDiagnostics(**values)

        if not candidates:
            # 空候选不调用 Provider
            return [], diagnostics(rerank_applied=False, reranked_candidates=0)

        texts = [item.chunk.text for item in candidates]
        try:
            raw_scores = self.reranker.rerank(query, texts)
        except ApiError as error:
            if error.code != RERANK_PROVIDER_UNAVAILABLE:
                raise
            reason = _degraded_reason(error)
            degraded = [
                RerankedResult(
                    chunk=item.chunk,
                    fused_score=item.fused_score,
                    rerank_score=None,
                    rerank_rank=None,
                    rerank_applied=False,
                    degraded_reason=reason,
                )
                for item in candidates[:limit]
            ]
            return degraded, diagnostics(
                rerank_applied=False,
                reranked_candidates=len(degraded),
                degraded_reason=reason,
            )

        scores = validate_rerank_scores(raw_scores, len(candidates))
        ranked = sorted(
            zip(candidates, scores),
            key=lambda pair: (-pair[1], -pair[0].fused_score, pair[0].chunk.chunk_id),
        )
        results = [
            RerankedResult(
                chunk=item.chunk,
                fused_score=item.fused_score,
                rerank_score=score,
                rerank_rank=rank,
                rerank_applied=True,
            )
            for rank, (item, score) in enumerate(ranked[:limit], start=1)
        ]
        return results, diagnostics(rerank_applied=True, reranked_candidates=len(results))


__all__ = [
    "DEGRADED_RERANKER_UNAVAILABLE",
    "RERANK_MAX_CANDIDATES",
    "RerankingRetriever",
    "validate_rerank_scores",
]
