"""来源与检索选项 API。

- ``GET /api/sources/{chunk_id}``：默认只返回**当前可检索**的 RAG chunk，
  对 candidate / inactive / deleted / 未知 chunk 一律返回同一个安全错误，
  不泄漏其存在与否、存储路径或内部状态。
  学业证据切片不进入 RAG 流水线（无 pipeline state、retrievable=false），
  因此当且仅当「所属文档未删除」且「chunk_id 被学业来源表显式引用」时允许读取，
  返回字段与可检索来源完全一致；其余未知 / 未引用 / 已删除来源仍是同一个安全错误。
- ``GET /retrieval/options``：只从当前 ready 且 retrievable 的文档聚合，
  去重、稳定排序、空值不返回；没有数据时返回空数组。
本阶段不提供公共调试搜索接口，混合检索由测试直接驱动。
"""

from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import AppContext, get_context, get_session
from app.core.errors import SOURCE_NOT_FOUND, ApiError
from app.documents.categories import category_label
from app.search.eligibility import build_where
from app.search.hydrate import build_scope, hydrate
from app.search.types import RetrievalFilters

router = APIRouter(tags=["retrieval"])

_CHUNK_ID_RE = re.compile(r"^[0-9a-f]{64}$")

# 学业证据切片的兜底读取：只在 RAG 资格路径未命中时使用。
# 条件是「所属文档未删除」且「chunk_id 被学业来源表显式引用」；
# 未知、未引用、已删除以及原有 candidate / inactive 来源都不会命中。
_REFERENCED_EVIDENCE_SQL = text(
    """
    SELECT c.id, c.text, c.locator,
           d.id, d.file_name, d.file_type,
           d.document_version, d.effective_from, d.dataset_version
    FROM document_chunks c
    JOIN documents d ON d.id = c.doc_id
    WHERE c.id = :chunk_id
      AND d.deleted_at IS NULL
      AND (
        EXISTS (SELECT 1 FROM academic_record_sets r WHERE r.source_chunk_id = c.id)
        OR EXISTS (SELECT 1 FROM course_records r WHERE r.source_chunk_id = c.id)
        OR EXISTS (SELECT 1 FROM academic_rule_sets r WHERE r.source_chunk_id = c.id)
        OR EXISTS (SELECT 1 FROM degree_rules r WHERE r.source_chunk_id = c.id)
        OR EXISTS (SELECT 1 FROM degree_rule_courses r WHERE r.source_chunk_id = c.id)
      )
    """
)


def _not_found() -> ApiError:
    return ApiError(SOURCE_NOT_FOUND)


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


def _payload(
    *,
    chunk_id: str,
    doc_id: str,
    file_name: str,
    file_type: str,
    document_version: str | None,
    effective_from: str | None,
    dataset_version: str | None,
    text: str,
    locator: dict[str, Any],
) -> dict:
    """sources API 的固定响应体；两种来源路径返回完全相同的字段。"""
    return {
        "chunk_id": chunk_id,
        "doc_id": doc_id,
        "file_name": file_name,
        "file_type": file_type,
        "document_version": document_version,
        "effective_from": effective_from,
        "dataset_version": dataset_version,
        "text": text,
        "page_number": locator.get("page_number"),
        "sheet_name": locator.get("sheet_name"),
        "row_start": locator.get("row_start"),
        "row_end": locator.get("row_end"),
        "section_title": locator.get("section_title"),
    }


@router.get("/sources/{chunk_id}")
def read_source(
    chunk_id: str,
    context: AppContext = Depends(get_context),
    session: Session = Depends(get_session),
) -> dict:
    """返回单个来源；不可检索、未引用与不存在返回完全相同的错误。"""
    if not _CHUNK_ID_RE.match(chunk_id or ""):
        raise _not_found()

    # 原 RAG hydrate 路径保持不变：命中即按原样返回。
    scope = build_scope(session, context.settings)
    hydrated = hydrate(session, [chunk_id], scope, RetrievalFilters())
    chunk = hydrated.get(chunk_id)
    if chunk is not None:
        return _payload(
            chunk_id=chunk.chunk_id,
            doc_id=chunk.doc_id,
            file_name=chunk.file_name,
            file_type=chunk.file_type,
            document_version=chunk.document_version,
            effective_from=chunk.effective_from,
            dataset_version=chunk.dataset_version,
            text=chunk.text,
            locator=chunk.locator,
        )

    # 学业证据兜底：只放行被学业来源表显式引用且所属文档未删除的切片。
    row = session.execute(_REFERENCED_EVIDENCE_SQL, {"chunk_id": chunk_id}).first()
    if row is None:
        raise _not_found()

    return _payload(
        chunk_id=row[0],
        doc_id=row[3],
        file_name=row[4] or "",
        file_type=row[5] or "",
        document_version=row[6],
        effective_from=row[7],
        dataset_version=row[8],
        text=row[1] or "",
        locator=_json_object(row[2]),
    )


@router.get("/retrieval/options")
def read_retrieval_options(
    context: AppContext = Depends(get_context),
    session: Session = Depends(get_session),
) -> dict:
    """只从当前可检索文档聚合过滤选项；不硬编码、不包含 candidate/inactive 内容。"""
    scope = build_scope(session, context.settings)
    where_sql, params = build_where(scope, RetrievalFilters())

    rows = session.execute(
        text(
            f"""
            SELECT DISTINCT
                json_extract(c.citation, '$.major') AS major,
                json_extract(c.citation, '$.grade_year') AS grade_year,
                json_extract(c.citation, '$.semester') AS semester,
                d.doc_category AS doc_category
            FROM document_chunks c
            JOIN documents d ON d.id = c.doc_id
            JOIN document_pipeline_state s ON s.doc_id = c.doc_id
            WHERE 1 = 1{where_sql}
            """
        ),
        params,
    ).all()

    majors = sorted({row[0] for row in rows if row[0]})
    grade_years = sorted({int(row[1]) for row in rows if row[1] is not None})
    semesters = sorted({row[2] for row in rows if row[2]})
    categories = sorted({row[3] for row in rows if row[3]})

    return {
        "majors": majors,
        "grade_years": grade_years,
        "semesters": semesters,
        "doc_categories": [
            {"value": value, "label": category_label(value)} for value in categories
        ],
        "active_dataset_version": scope.active.dataset_version if scope.active else None,
        "demo_available": scope.demo_available,
    }
