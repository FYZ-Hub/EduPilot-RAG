"""来源与检索选项 API。

- ``GET /api/sources/{chunk_id}``：只返回**当前可检索**的 chunk；
  对 candidate / inactive / deleted / 未知 chunk 一律返回同一个安全错误，
  不泄漏其存在与否、存储路径或内部状态。
- ``GET /api/retrieval/options``：只从当前 ready 且 retrievable 的文档聚合，
  去重、稳定排序、空值不返回；没有数据时返回空数组。
本阶段不提供公共调试搜索接口，混合检索由测试直接驱动。
"""

from __future__ import annotations

import re

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


def _not_found() -> ApiError:
    return ApiError(SOURCE_NOT_FOUND)


@router.get("/sources/{chunk_id}")
def read_source(
    chunk_id: str,
    context: AppContext = Depends(get_context),
    session: Session = Depends(get_session),
) -> dict:
    """返回单个可检索来源；不可检索与不存在返回完全相同的错误。"""
    if not _CHUNK_ID_RE.match(chunk_id or ""):
        raise _not_found()

    scope = build_scope(session, context.settings)
    hydrated = hydrate(session, [chunk_id], scope, RetrievalFilters())
    chunk = hydrated.get(chunk_id)
    if chunk is None:
        raise _not_found()

    return {
        "chunk_id": chunk.chunk_id,
        "doc_id": chunk.doc_id,
        "file_name": chunk.file_name,
        "file_type": chunk.file_type,
        "document_version": chunk.document_version,
        "effective_from": chunk.effective_from,
        "dataset_version": chunk.dataset_version,
        "text": chunk.text,
        "page_number": chunk.page_number,
        "sheet_name": chunk.sheet_name,
        "row_start": chunk.row_start,
        "row_end": chunk.row_end,
        "section_title": chunk.section_title,
    }


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
