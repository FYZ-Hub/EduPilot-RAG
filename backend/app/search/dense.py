"""DenseRetriever：基于 EmbeddingProvider + ChromaVectorStore 的向量召回。

Chroma 只负责给出候选与距离；候选仍需经 SQLite 资格过滤与元数据补全，
绝不因为 Chroma metadata 声称可检索就采信。
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.core.errors import (
    EMBEDDING_DIMENSION_MISMATCH,
    RETRIEVAL_QUERY_INVALID,
    ApiError,
)
from app.embedding.base import EmbeddingProvider
from app.runtime.coordinator import LocalModelCoordinator, embedding_lease
from app.search.hydrate import build_scope, hydrate
from app.search.types import RetrievalFilters, RetrievedChunk
from app.vector.store import ChromaVectorStore

# 候选过采样倍数：SQLite 侧会再过滤，留出余量以保证 top_k 结果
DENSE_OVERSAMPLE = 4


def chroma_where(filters: RetrievalFilters) -> dict[str, Any] | None:
    """把允许的过滤条件翻译为 Chroma 标量过滤（仅作为预筛，最终以 SQLite 为准）。

    - 没有任何条件时返回 ``None``；
    - 只有一个条件时返回 Chroma 接受的一层表达式；
    - **多个条件必须包装成单层 ``$and``**：Chroma 要求 where 的顶层只能有一个算子，
      平铺多个键会抛 ``ValueError: Expected where to have exactly one operator``；
    - 条件顺序稳定：major → grade_year → semester → doc_category；
    - 每个条件都是精确相等（``$eq``），不使用 OR、不忽略任何字段；
    - Chroma 只做预筛，SQLite ``hydrate`` 仍是最终权威校验。
    """
    conditions: list[dict[str, Any]] = []
    if filters.major is not None:
        conditions.append({"major": {"$eq": filters.major}})
    if filters.grade_year is not None:
        conditions.append({"grade_year": {"$eq": filters.grade_year}})
    if filters.semester is not None:
        conditions.append({"semester": {"$eq": filters.semester}})
    if filters.doc_category is not None:
        conditions.append({"doc_category": {"$eq": filters.doc_category}})
    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return {"$and": conditions}


class DenseRetriever:
    """向量召回；默认 ``dense_top_k=12``。"""

    def __init__(
        self,
        session: Session,
        settings: Settings,
        vectors: ChromaVectorStore,
        embeddings: EmbeddingProvider,
        coordinator: LocalModelCoordinator | None = None,
    ):
        self.session = session
        self.settings = settings
        self.vectors = vectors
        self.embeddings = embeddings
        self.coordinator = coordinator

    def search(
        self,
        query: str,
        filters: RetrievalFilters | None = None,
        top_k: int | None = None,
    ) -> list[RetrievedChunk]:
        filters = filters or RetrievalFilters()
        limit = int(top_k or self.settings.dense_top_k)
        scope = build_scope(self.session, self.settings)

        vector = self._embed_query(query)
        collection = self.vectors.collection()
        if collection.count() == 0:
            return []

        requested = min(int(collection.count()) or 1, max(limit, limit * DENSE_OVERSAMPLE))
        where = chroma_where(filters)
        result = collection.query(
            query_embeddings=[vector],
            n_results=requested,
            where=where,
            include=["distances"],
        )
        ids = list(result.get("ids") or [[]])[0]
        distances = list(result.get("distances") or [[]])[0]
        if not ids:
            return []

        hydrated = hydrate(self.session, list(ids), scope, filters)
        results: list[RetrievedChunk] = []
        for chunk_id, distance in zip(ids, distances):
            chunk = hydrated.get(chunk_id)
            if chunk is None:
                continue
            results.append(
                replace(
                    chunk,
                    dense_rank=len(results) + 1,
                    # 距离越小越相关；用 1/(1+d) 转成单调、有界的相似度分数
                    dense_score=1.0 / (1.0 + float(distance)),
                )
            )
            if len(results) >= limit:
                break
        return results

    def _embed_query(self, query: str) -> list[float]:
        if not (query or "").strip():
            raise ApiError(
                RETRIEVAL_QUERY_INVALID, details={"reason": "empty_query"}
            )
        # 与 worker Embedding、Local Reranker 共用同一协调器：
        # Dense 查询完成后（离开窗口）即释放 Embedding，再允许 Reranker 加载。
        with embedding_lease(self.coordinator):
            vector = self.embeddings.embed_documents([query])[0]
        expected = self.vectors.descriptor.dimension
        if len(vector) != expected:
            raise ApiError(
                EMBEDDING_DIMENSION_MISMATCH,
                details={"expected": expected, "actual": len(vector)},
            )
        return [float(value) for value in vector]


__all__ = ["DENSE_OVERSAMPLE", "DenseRetriever", "chroma_where"]
