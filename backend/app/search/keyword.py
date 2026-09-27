"""KeywordRetriever：基于 SQLite FTS5/BM25 的关键词召回。"""

from __future__ import annotations

from dataclasses import replace

from sqlalchemy.orm import Session

from app.config import Settings
from app.search import fts
from app.search.hydrate import build_scope, hydrate
from app.search.types import RetrievalFilters, RetrievedChunk


class KeywordRetriever:
    """自行实现的关键词检索；默认 ``keyword_top_k=12``。"""

    def __init__(self, session: Session, settings: Settings):
        self.session = session
        self.settings = settings

    def search(
        self,
        query: str,
        filters: RetrievalFilters | None = None,
        top_k: int | None = None,
    ) -> tuple[list[RetrievedChunk], str]:
        """返回 ``(结果, match_mode)``；结果按 BM25 升序（越相关越靠前）。"""
        filters = filters or RetrievalFilters()
        limit = int(top_k or self.settings.keyword_top_k)
        scope = build_scope(self.session, self.settings)

        hits, match_mode = fts.search(
            self.session, scope=scope, filters=filters, query=query, limit=limit
        )
        if not hits:
            return [], match_mode

        hydrated = hydrate(self.session, [chunk_id for chunk_id, _ in hits], scope, filters)
        results: list[RetrievedChunk] = []
        for chunk_id, score in hits:
            chunk = hydrated.get(chunk_id)
            if chunk is None:
                # 候选不可检索（candidate/inactive/failed/deleted/未完成对账）
                continue
            results.append(
                replace(chunk, keyword_rank=len(results) + 1, keyword_score=score)
            )
        return results, match_mode


__all__ = ["KeywordRetriever"]
