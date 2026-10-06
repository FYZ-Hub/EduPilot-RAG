"""RerankingRetriever：在 ``HybridRetriever`` 之上执行查询时重排。

``HybridRetriever`` 保持为纯粹的 Dense + Keyword + RRF，本模块是独立的包装层：

    HybridRetriever（RRF 顺序）
      → 取最多 ``RERANK_MAX_CANDIDATES``（20）条候选
      → Reranker 打分
      → 稳定排序（rerank_score 降序 → fused_score 降序 → chunk_id 升序）
      → 输出前 ``RERANK_TOP_K``（默认 10）条，且**永不**超过硬上限 ``RERANK_TOP_K_MAX``（10）条

- 空候选**不调用** Provider；候选少于 ``rerank_top_k`` 时只返回实际数量。
- 输出条数的唯一决定点是 ``resolve_rerank_limit``：显式 ``top_k`` 优先，
  非正数（含 0 与负数）表示不要结果，超大值被硬上限截断。
- Provider 只返回分数；引用字段始终直接来自原 ``RetrievedChunk``，不会与分数错位。
- 外发给 Provider 的候选是「原正文 + 一行稳定 JSON 元数据」（:func:`format_rerank_candidate`，
  仅白名单字段，用于区分来源/版本/定位）；不含 file_name / 路径 / doc_id / chunk_id /
  source_key / 任何分数或 citation 其它键，外发前整串仍经 ``app.core.privacy`` 清洗。
- Reranker 明确不可用或 API 超时时安全降级：返回原 RRF 顺序的前 K 条，
  ``rerank_applied=false``、``rerank_score=null`` 并记录稳定 ``degraded_reason``；
  但**不吞掉**引用错位、非法返回结构等内部不变量错误。
"""

from __future__ import annotations

import json
import math
import re
from collections.abc import Sequence
from time import perf_counter

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
    RetrievedChunk,
)
from app.vector.store import ChromaVectorStore

# 固定、版本化的重排输入上限（PRODUCT_SPEC 7.2：重排前 12–20 条）
RERANK_MAX_CANDIDATES = 20
# 硬上限：无论配置或调用参数如何，输出都不得超过 10 条（最终上下文硬上限）
RERANK_TOP_K_MAX = 10

# 降级原因兜底值；正确性要求：不含任何敏感数据、跨运行稳定
DEGRADED_RERANKER_UNAVAILABLE = "reranker_unavailable"
_DEGRADED_REASON_RE = re.compile(r"^[a-z0-9_]{1,48}$")

# 仅供进程内评测读取的分阶段检索指纹键名；不进入 SSE / API，也不影响排序与输出。
#   fused    = RRF 融合后的候选顺序（最多 20）
#   reranked = 重排后的**完整**顺序（截取最终 top-10 之前）
#   final    = 实际返回的输出顺序（最多 10）
STAGE_FUSED = "fused"
STAGE_RERANKED = "reranked"
STAGE_FINAL = "final"
RETRIEVAL_STAGES = (STAGE_FUSED, STAGE_RERANKED, STAGE_FINAL)


def stage_entry(item: object) -> dict[str, object]:
    """把检索结果归一化为「来源 + locator」记录（**不含正文与分数**），仅进程内使用。"""
    chunk = getattr(item, "chunk", item)
    return {
        "file_name": getattr(chunk, "file_name", None),
        "locator": {
            "page_number": getattr(chunk, "page_number", None),
            "sheet_name": getattr(chunk, "sheet_name", None),
            "row_start": getattr(chunk, "row_start", None),
            "row_end": getattr(chunk, "row_end", None),
            "section_title": getattr(chunk, "section_title", None),
        },
    }


def record_stage(
    sink: dict[str, list[dict[str, object]]] | None, stage: str, items: Sequence[object]
) -> None:
    """把某个阶段的顺序写入调用方提供的进程内 sink；``sink`` 为 ``None`` 时不记录。"""
    if sink is None:
        return
    sink[stage] = [stage_entry(item) for item in items]


# 可外发给 Reranker 的候选元数据**白名单**（顺序即 JSON 键顺序，保证输出稳定）。
# 刻意不含 file_name / 路径 / doc_id / chunk_id / source_key / 任何分数 / citation 其它键。
RERANK_CANDIDATE_METADATA_FIELDS = (
    "course_code",
    "doc_category",
    "document_version",
    "effective_from",
    "page_number",
    "sheet_name",
    "row_start",
    "row_end",
    "section_title",
)


def _candidate_metadata(chunk: RetrievedChunk) -> dict[str, object]:
    """按白名单顺序收集候选元数据；``course_code`` 只取 citation 中的**非空字符串**。"""
    citation = chunk.citation if isinstance(chunk.citation, dict) else {}
    course_code = citation.get("course_code")
    if not isinstance(course_code, str) or not course_code.strip():
        course_code = None
    return {
        "course_code": course_code,
        "doc_category": chunk.doc_category,
        "document_version": chunk.document_version,
        "effective_from": chunk.effective_from,
        "page_number": chunk.page_number,
        "sheet_name": chunk.sheet_name,
        "row_start": chunk.row_start,
        "row_end": chunk.row_end,
        "section_title": chunk.section_title,
    }


def format_rerank_candidate(chunk: RetrievedChunk) -> str:
    """把候选格式化为「原正文 + 一行稳定 JSON 元数据」（纯函数，无 I/O）。

    - 只遍历 :data:`RERANK_CANDIDATE_METADATA_FIELDS` 白名单，键顺序固定，
      缺失值（``None`` / 空串）省略；
    - 因此 ``file_name`` / 路径 / ``doc_id`` / ``chunk_id`` / ``source_key`` /
      任何分数 / citation 其它键**不可能**出现在外发内容里；
    - ``course_code`` 只接受 citation 中的非空字符串；
    - 返回的是**完整候选字符串**，外发前仍由 Provider 边界
      （``app.core.privacy.scrub``）对整串做个人信息清洗。
    """
    metadata = {
        key: value
        for key, value in _candidate_metadata(chunk).items()
        if value not in (None, "")
    }
    return f"{chunk.text}\n{json.dumps(metadata, ensure_ascii=False)}"


def resolve_rerank_limit(requested: int | None, configured: int) -> int:
    """输出条数的唯一决定点。

    稳定规则：显式 ``requested`` 优先于配置；非正数表示「不要结果」，返回 0；
    任何情况下都不会超过硬上限 ``RERANK_TOP_K_MAX``。
    """
    raw = configured if requested is None else requested
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = RERANK_TOP_K_MAX
    if value <= 0:
        return 0
    return min(value, RERANK_TOP_K_MAX)


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
    """混合检索 + 查询时重排；默认输出前 ``rerank_top_k``（10）条。"""

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
        *,
        stage_sink: dict[str, list[dict[str, object]]] | None = None,
    ) -> tuple[list[RerankedResult], RerankDiagnostics]:
        """执行一次检索；``stage_sink`` 只用于**进程内评测**旁路记录分阶段顺序。

        记录的是同一次 ``search`` 已经产出的顺序，**不额外调用** Embedding / Reranker，
        也不改变排序、输出条数或返回值；``stage_sink=None`` 时完全不记录。
        """
        limit = resolve_rerank_limit(top_k, self.settings.rerank_top_k)

        hybrid = HybridRetriever(
            self.session, self.settings, self.vectors, self.embeddings, self.coordinator
        )
        hybrid_results, retrieval = hybrid.search(query, filters, top_k=RERANK_MAX_CANDIDATES)
        candidates = hybrid_results[:RERANK_MAX_CANDIDATES]
        # 阶段 1：RRF 融合后的候选顺序（最多 20）
        record_stage(stage_sink, STAGE_FUSED, candidates)

        # 只统计 Reranker 阶段：Dense / FTS / RRF 的耗时保留在 RetrievalDiagnostics
        rerank_started = perf_counter()

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
                "rerank_elapsed_ms": int((perf_counter() - rerank_started) * 1000),
            }
            values.update(overrides)
            return RerankDiagnostics(**values)

        if not candidates or limit == 0:
            # 空候选不调用 Provider；显式请求 0 条时也不调用
            record_stage(stage_sink, STAGE_RERANKED, ())
            record_stage(stage_sink, STAGE_FINAL, ())
            return [], diagnostics(rerank_applied=False, reranked_candidates=0)

        # 候选 = 原正文 + 一行稳定 JSON 元数据（白名单字段）；数量与顺序与 candidates 一一对应，
        # 因此 Provider 返回的分数仍按位置映射回原候选，引用不会错位。
        texts = [format_rerank_candidate(item.chunk) for item in candidates]
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
            # 降级：没有重排分数，两个阶段的顺序都等于 fused 的前 limit 条
            record_stage(stage_sink, STAGE_RERANKED, degraded)
            record_stage(stage_sink, STAGE_FINAL, degraded)
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
        # 阶段 2：记录外部重排的**完整**顺序（截取最终 top-K 之前）
        record_stage(stage_sink, STAGE_RERANKED, [item for item, _score in ranked])
        # 严格按外部 Reranker 的排序取前 ``limit`` 条：本地不做任何重排或覆盖。
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
        # 阶段 3：实际返回的输出顺序（= reranked 前 limit 项）
        record_stage(stage_sink, STAGE_FINAL, results)
        return results, diagnostics(rerank_applied=True, reranked_candidates=len(results))


__all__ = [
    "DEGRADED_RERANKER_UNAVAILABLE",
    "RERANK_CANDIDATE_METADATA_FIELDS",
    "RERANK_MAX_CANDIDATES",
    "RERANK_TOP_K_MAX",
    "RETRIEVAL_STAGES",
    "RerankingRetriever",
    "STAGE_FINAL",
    "STAGE_FUSED",
    "STAGE_RERANKED",
    "format_rerank_candidate",
    "record_stage",
    "resolve_rerank_limit",
    "stage_entry",
    "validate_rerank_scores",
]