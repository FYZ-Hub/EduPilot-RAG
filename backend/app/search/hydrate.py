"""检索结果补全：SQLite 是资格与引用元数据的唯一权威。

Chroma 与 FTS 只用来定位候选 chunk_id；任何候选都必须再经过这里，
按与过滤条件**再校验一次**，并按排名顺序补全引用元数据。
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import bindparam, select, text
from sqlalchemy.orm import Session

from app.config import Settings
from app.documents.fingerprint import pipeline_fingerprint
from app.models import DemoActiveDataset
from app.search.eligibility import build_where
from app.search.types import (
    ActiveDataset,
    RetrievalFilters,
    RetrievalScope,
    RetrievedChunk,
)

_HYDRATE_SQL = (
    "SELECT c.id, c.text, c.locator, c.citation, c.chunk_index, "
    "d.id, d.file_name, d.file_type, d.doc_category, d.source_type, d.source_key, "
    "d.dataset_version, d.document_version, d.effective_from "
    "FROM document_chunks c "
    "JOIN documents d ON d.id = c.doc_id "
    "JOIN document_pipeline_state s ON s.doc_id = c.doc_id "
    "WHERE c.id IN :ids"
)


def load_active_dataset(session: Session) -> ActiveDataset | None:
    row = session.scalar(select(DemoActiveDataset).order_by(DemoActiveDataset.activated_at.desc()))
    if row is None:
        return None
    return ActiveDataset(
        dataset_version=row.dataset_version,
        manifest_sha256=row.manifest_sha256,
        pipeline_fingerprint=row.pipeline_fingerprint,
        activated_at=row.activated_at.isoformat() if row.activated_at else None,
    )


def build_scope(session: Session, settings: Settings) -> RetrievalScope:
    return RetrievalScope(
        pipeline_fingerprint=pipeline_fingerprint(settings),
        active=load_active_dataset(session),
    )


def _json_object(value: Any) -> dict[str, Any]:
    """原生 SQL 读回的 JSON 列是文本，需要显式反序列化。"""
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def hydrate(
    session: Session,
    chunk_ids: list[str],
    scope: RetrievalScope,
    filters: RetrievalFilters,
) -> dict[str, RetrievedChunk]:
    """按 chunk_id 加载并通过资格/过滤条件再校验；不合格的候选直接丢弃。"""
    if not chunk_ids:
        return {}
    where_sql, params = build_where(scope, filters)
    statement = text(_HYDRATE_SQL + where_sql).bindparams(bindparam("ids", expanding=True))
    rows = session.execute(
        statement, {"ids": list(chunk_ids), **params}
    ).all()

    hydrated: dict[str, RetrievedChunk] = {}
    for row in rows:
        locator = _json_object(row[2])
        citation = _json_object(row[3])
        hydrated[row[0]] = RetrievedChunk(
            chunk_id=row[0],
            text=row[1] or "",
            locator=locator,
            citation=citation,
            chunk_index=int(row[4] or 0),
            doc_id=row[5],
            file_name=row[6] or "",
            file_type=row[7] or "",
            doc_category=row[8] or "",
            source_type=row[9] or "",
            source_key=row[10] or "",
            dataset_version=row[11],
            document_version=row[12],
            effective_from=row[13],
            page_number=locator.get("page_number"),
            sheet_name=locator.get("sheet_name"),
            row_start=locator.get("row_start"),
            row_end=locator.get("row_end"),
            section_title=locator.get("section_title"),
        )
    return hydrated


__all__ = ["build_scope", "hydrate", "load_active_dataset"]
