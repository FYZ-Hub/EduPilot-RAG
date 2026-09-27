"""检索公共类型：过滤条件、可检索资格与统一结果结构。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.errors import RETRIEVAL_FILTER_INVALID, ApiError

FILTER_FIELDS = ("major", "grade_year", "semester", "doc_category")


@dataclass(frozen=True)
class RetrievalFilters:
    """允许的过滤字段固定；未知字段必须拒绝。"""

    major: str | None = None
    grade_year: int | None = None
    semester: str | None = None
    doc_category: str | None = None

    @classmethod
    def from_mapping(cls, mapping: dict[str, Any] | None) -> "RetrievalFilters":
        values = dict(mapping or {})
        unknown = sorted(set(values) - set(FILTER_FIELDS))
        if unknown:
            raise ApiError(
                RETRIEVAL_FILTER_INVALID, details={"unknown_fields": unknown}
            )
        grade_year = values.get("grade_year")
        if grade_year is not None and not isinstance(grade_year, int):
            raise ApiError(
                RETRIEVAL_FILTER_INVALID, details={"field": "grade_year", "reason": "not_integer"}
            )
        return cls(
            major=_clean_str(values.get("major")),
            grade_year=grade_year,
            semester=_clean_str(values.get("semester")),
            doc_category=_clean_str(values.get("doc_category")),
        )

    def as_dict(self) -> dict[str, Any]:
        """只返回已生效的过滤器，供诊断使用。"""
        return {
            name: getattr(self, name)
            for name in FILTER_FIELDS
            if getattr(self, name) is not None
        }


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True)
class ActiveDataset:
    """唯一 active demo dataset 指针的快照。"""

    dataset_version: str
    manifest_sha256: str
    pipeline_fingerprint: str
    activated_at: str | None = None


@dataclass(frozen=True)
class RetrievalScope:
    """检索资格上下文：当前流水线指纹 + 当前 active 指针。

    demo 文档只有在 active 指针存在、且该指针的 ``pipeline_fingerprint``
    与**当前运行配置**一致时才可能被检索；否则整套 demo 索引都属于另一条流水线
    （例如切换 Provider 后），必须重建而不能复用。
    """

    pipeline_fingerprint: str
    active: ActiveDataset | None = None

    @property
    def demo_available(self) -> bool:
        return (
            self.active is not None
            and self.active.pipeline_fingerprint == self.pipeline_fingerprint
        )


@dataclass(frozen=True)
class RetrievedChunk:
    """单路检索结果；融合后附带各路排名与分数。"""

    chunk_id: str
    doc_id: str
    text: str
    file_name: str
    file_type: str
    doc_category: str
    source_type: str
    source_key: str
    dataset_version: str | None = None
    document_version: str | None = None
    effective_from: str | None = None
    page_number: int | None = None
    sheet_name: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    section_title: str | None = None
    chunk_index: int = 0
    dense_rank: int | None = None
    dense_score: float | None = None
    keyword_rank: int | None = None
    keyword_score: float | None = None
    fused_score: float | None = None
    locator: dict[str, Any] = field(default_factory=dict)
    citation: dict[str, Any] = field(default_factory=dict)

    def citation_payload(self) -> dict[str, Any]:
        """PRODUCT_SPEC 的引用字段（不含正文）。"""
        return {
            "chunk_id": self.chunk_id,
            "doc_id": self.doc_id,
            "file_name": self.file_name,
            "file_type": self.file_type,
            "doc_category": self.doc_category,
            "document_version": self.document_version,
            "effective_from": self.effective_from,
            "dataset_version": self.dataset_version,
            "page_number": self.page_number,
            "sheet_name": self.sheet_name,
            "row_start": self.row_start,
            "row_end": self.row_end,
            "section_title": self.section_title,
        }


@dataclass(frozen=True)
class HybridResult:
    """融合结果；阶段 5 的 Reranker 输入。"""

    chunk: RetrievedChunk
    fused_score: float

    def as_dict(self) -> dict[str, Any]:
        payload = self.chunk.citation_payload()
        payload.update(
            {
                "fused_score": self.fused_score,
                "dense_rank": self.chunk.dense_rank,
                "dense_score": self.chunk.dense_score,
                "keyword_rank": self.chunk.keyword_rank,
                "keyword_score": self.chunk.keyword_score,
                "quote": self.chunk.text,
            }
        )
        return payload


@dataclass
class RetrievalDiagnostics:
    """只包含安全统计，不含密钥、绝对路径或整份正文。"""

    dense_candidates: int = 0
    keyword_candidates: int = 0
    fused_candidates: int = 0
    applied_filters: dict[str, Any] = field(default_factory=dict)
    elapsed_ms: int = 0
    embedding_provider: str | None = None
    embedding_revision: str | None = None
    vector_schema_version: str | None = None
    fts_schema_version: str | None = None
    fts_tokenizer: str | None = None
    fts_ngram_version: str | None = None
    rrf_k: int | None = None
    match_mode: str | None = None
    demo_available: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "dense_candidates": self.dense_candidates,
            "keyword_candidates": self.keyword_candidates,
            "fused_candidates": self.fused_candidates,
            "applied_filters": dict(self.applied_filters),
            "elapsed_ms": self.elapsed_ms,
            "embedding_provider": self.embedding_provider,
            "embedding_revision": self.embedding_revision,
            "vector_schema_version": self.vector_schema_version,
            "fts_schema_version": self.fts_schema_version,
            "fts_tokenizer": self.fts_tokenizer,
            "fts_ngram_version": self.fts_ngram_version,
            "rrf_k": self.rrf_k,
            "match_mode": self.match_mode,
            "demo_available": self.demo_available,
        }


def default_filters() -> RetrievalFilters:
    return RetrievalFilters()


__all__ = [
    "ActiveDataset",
    "FILTER_FIELDS",
    "HybridResult",
    "RetrievalDiagnostics",
    "RetrievalFilters",
    "RetrievalScope",
    "RetrievedChunk",
    "default_filters",
]
