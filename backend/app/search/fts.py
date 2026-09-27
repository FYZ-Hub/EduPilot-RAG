"""FTS5 关键词索引与查询（SQLite 原生，不引入外部搜索服务）。

- 索引写入、精确删除、ID 对账与检查点由调用方放在**同一个事务**里提交
- 查询表达式由 :mod:`app.search.text` 构造：所有 token 都被引号包裹，
  用户输入中的 FTS 语法字符不可能注入查询语法
- 先尝试 AND（精确），无结果再退化为 OR（召回）；两者都是确定性规则
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import (
    DOCUMENT_KEYWORD_INDEX_FAILED,
    RETRIEVAL_QUERY_INVALID,
    ApiError,
)
from app.models import Document, DocumentChunk
from app.search.eligibility import build_where
from app.search.schema import FTS_TABLE_NAME
from app.search.text import MAX_QUERY_CHARS, build_match_expressions, cjk_bigrams
from app.search.types import RetrievalFilters, RetrievalScope

MATCH_MODE_AND = "and"
MATCH_MODE_OR = "or"
MATCH_MODE_EMPTY = "empty"


@dataclass(frozen=True)
class FtsReconcileResult:
    inserted: int
    deleted: int
    kept: int

    @property
    def total(self) -> int:
        return self.inserted + self.kept


def build_row_values(
    document: Document, chunk: DocumentChunk, fingerprint: str
) -> dict[str, Any]:
    """一条 FTS 记录；正文保持原文，另存确定性 CJK 二元组辅助列。"""
    citation = chunk.citation or {}
    locator = chunk.locator or {}
    grade_year = citation.get("grade_year")

    def _text(value: Any) -> str:
        if value is None:
            return ""
        return str(value)

    return {
        "chunk_id": chunk.id,
        "doc_id": document.id,
        "fts_fingerprint": fingerprint,
        "title": _text(locator.get("section_title")),
        "body": chunk.text or "",
        "body_ngram": cjk_bigrams(chunk.text or ""),
        "course_code": _text(citation.get("course_code")),
        "file_name": _text(document.file_name),
        "doc_category": _text(document.doc_category),
        "source_type": _text(document.source_type),
        "source_key": _text(document.source_key),
        "dataset_version": _text(document.dataset_version),
        "document_version": _text(document.document_version),
        "effective_from": _text(document.effective_from),
        "major": _text(citation.get("major")),
        "grade_year": _text(grade_year),
        "semester": _text(citation.get("semester")),
    }


_INSERT_SQL = text(
    f"INSERT INTO {FTS_TABLE_NAME} "
    "(chunk_id, doc_id, fts_fingerprint, title, body, body_ngram, course_code, file_name, "
    "doc_category, source_type, source_key, dataset_version, document_version, "
    "effective_from, major, grade_year, semester) "
    "VALUES (:chunk_id, :doc_id, :fts_fingerprint, :title, :body, :body_ngram, :course_code, "
    ":file_name, :doc_category, :source_type, :source_key, :dataset_version, "
    ":document_version, :effective_from, :major, :grade_year, :semester)"
)


def _placeholders(values: list[Any], prefix: str) -> tuple[str, dict[str, Any]]:
    names = [f"{prefix}{index}" for index in range(len(values))]
    return ", ".join(f":{name}" for name in names), dict(zip(names, values))


def recorded_rowids(session: Session, doc_id: str) -> dict[str, int]:
    """该文档已登记的 FTS rowid：``chunk_id -> rowid``。"""
    rows = session.execute(
        text(
            f"SELECT id, fts_rowid FROM document_chunks "
            f"WHERE doc_id = :doc_id AND fts_rowid IS NOT NULL"
        ),
        {"doc_id": doc_id},
    ).all()
    return {row[0]: int(row[1]) for row in rows}


def fetch_rows(session: Session, rowids: list[int]) -> dict[int, tuple[str, str]]:
    """``rowid -> (chunk_id, fts_fingerprint)``。"""
    if not rowids:
        return {}
    clause, params = _placeholders(rowids, "rid")
    rows = session.execute(
        text(
            f"SELECT rowid, chunk_id, fts_fingerprint FROM {FTS_TABLE_NAME} "
            f"WHERE rowid IN ({clause})"
        ),
        params,
    ).all()
    return {int(row[0]): (row[1], row[2]) for row in rows}


def existing_ids(session: Session, doc_id: str) -> set[str]:
    """该文档当前真实存在于 FTS 的 chunk_id 集合。"""
    rows = session.execute(
        text(
            f"SELECT chunk_fts.chunk_id FROM {FTS_TABLE_NAME} "
            "JOIN document_chunks c ON c.fts_rowid = chunk_fts.rowid "
            "WHERE c.doc_id = :doc_id"
        ),
        {"doc_id": doc_id},
    ).all()
    return {row[0] for row in rows}


def count_rows(session: Session) -> int:
    return int(session.execute(text(f"SELECT count(*) FROM {FTS_TABLE_NAME}")).scalar() or 0)


def orphan_rowids(session: Session) -> list[int]:
    """没有任何 chunk 引用的 FTS 行号（正常写入路径下必须始终为空）。"""
    rows = session.execute(
        text(
            f"SELECT rowid FROM {FTS_TABLE_NAME} WHERE rowid NOT IN "
            "(SELECT fts_rowid FROM document_chunks WHERE fts_rowid IS NOT NULL)"
        )
    ).all()
    return [int(row[0]) for row in rows]


def delete_rowids(session: Session, rowids: list[int]) -> None:
    if not rowids:
        return
    clause, params = _placeholders(rowids, "del")
    session.execute(text(f"DELETE FROM {FTS_TABLE_NAME} WHERE rowid IN ({clause})"), params)


def reconcile_document(
    session: Session, doc_id: str, values_by_chunk: dict[str, dict[str, Any]], fingerprint: str
) -> FtsReconcileResult:
    """只补缺失、只删多余/过期记录；返回本次改动计数。

    必须在调用方的事务内执行：FTS 与 ``document_chunks.fts_rowid`` 同库，
    要么一起提交，要么一起回滚。
    """
    recorded = recorded_rowids(session, doc_id)
    present = fetch_rows(session, list(recorded.values()))

    stale_rowids: list[int] = []
    good_ids: set[str] = set()
    for chunk_id, rowid in recorded.items():
        entry = present.get(rowid)
        if entry is None or entry[0] != chunk_id or entry[1] != fingerprint:
            stale_rowids.append(rowid)
            continue
        good_ids.add(chunk_id)

    if stale_rowids:
        delete_rowids(session, stale_rowids)
        session.execute(
            text(
                "UPDATE document_chunks SET fts_rowid = NULL WHERE doc_id = :doc_id "
                "AND fts_rowid IN ("
                + ", ".join(f":s{index}" for index in range(len(stale_rowids)))
                + ")"
            ),
            {"doc_id": doc_id, **{f"s{index}": value for index, value in enumerate(stale_rowids)}},
        )

    missing = [chunk_id for chunk_id in values_by_chunk if chunk_id not in good_ids]
    inserted = 0
    for chunk_id in missing:
        result = session.execute(_INSERT_SQL, values_by_chunk[chunk_id])
        rowid = result.lastrowid
        if rowid is None:  # pragma: no cover - FTS5 始终返回 rowid
            raise ApiError(
                DOCUMENT_KEYWORD_INDEX_FAILED, details={"reason": "missing_fts_rowid"}
            )
        session.execute(
            text("UPDATE document_chunks SET fts_rowid = :rowid WHERE id = :chunk_id"),
            {"rowid": int(rowid), "chunk_id": chunk_id},
        )
        inserted += 1

    # 兜底：删除没有任何 chunk 引用的残留 FTS 行（只删多余，不动其它文档的记录）
    orphans = orphan_rowids(session)
    if orphans:
        delete_rowids(session, orphans)

    return FtsReconcileResult(
        inserted=inserted,
        deleted=len(stale_rowids) + len(orphans),
        kept=len(good_ids),
    )


def delete_document_rows(session: Session, doc_id: str) -> int:
    """精确删除该文档的 FTS 记录（按登记的 rowid），不做整库清空。"""
    rowids = list(recorded_rowids(session, doc_id).values())
    if not rowids:
        return 0
    delete_rowids(session, rowids)
    session.execute(
        text("UPDATE document_chunks SET fts_rowid = NULL WHERE doc_id = :doc_id"),
        {"doc_id": doc_id},
    )
    return len(rowids)


def search(
    session: Session,
    *,
    scope: RetrievalScope,
    filters: RetrievalFilters,
    query: str,
    limit: int,
) -> tuple[list[tuple[str, float]], str]:
    """返回 ``([(chunk_id, keyword_score)], match_mode)``；score 越大越相关。"""
    if len(query or "") > MAX_QUERY_CHARS:
        raise ApiError(RETRIEVAL_QUERY_INVALID, details={"reason": "query_too_long"})

    and_expression, or_expression = build_match_expressions(query)
    if not and_expression:
        return [], MATCH_MODE_EMPTY

    where_sql, where_params = build_where(scope, filters)

    for mode, expression in ((MATCH_MODE_AND, and_expression), (MATCH_MODE_OR, or_expression)):
        rows = session.execute(
            text(
                f"""
                SELECT chunk_fts.chunk_id AS chunk_id, bm25(chunk_fts) AS raw_score
                FROM {FTS_TABLE_NAME}
                JOIN document_chunks c ON c.fts_rowid = chunk_fts.rowid
                JOIN documents d ON d.id = c.doc_id
                JOIN document_pipeline_state s ON s.doc_id = c.doc_id
                WHERE chunk_fts MATCH :match{where_sql}
                ORDER BY raw_score ASC, chunk_fts.chunk_id ASC
                LIMIT :limit
                """
            ),
            {"match": expression, "limit": int(limit), **where_params},
        ).all()
        if rows:
            return [(row[0], float(-row[1])) for row in rows], mode

    return [], MATCH_MODE_AND


__all__ = [
    "FtsReconcileResult",
    "MATCH_MODE_AND",
    "MATCH_MODE_EMPTY",
    "MATCH_MODE_OR",
    "build_row_values",
    "count_rows",
    "delete_document_rows",
    "existing_ids",
    "orphan_rowids",
    "reconcile_document",
    "search",
]
