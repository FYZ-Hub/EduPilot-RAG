"""可检索资格（upload 与 demo 口径不同）与元数据过滤的 SQL 构造。

Dense 与 Keyword **必须使用同一套资格条件**，因此这里只实现一次：
- upload：``completed`` + ``ready`` + ``retrievable`` + 未删除 + ``activation_state IS NULL``
- demo：在 upload 条件之上再要求 ``activation_state = 'active'``，且 ``dataset_version``
  等于唯一 active 指针的版本；active 指针不存在或其 ``pipeline_fingerprint``
  与当前运行配置不一致时，demo 整体不可检索。
"""

from __future__ import annotations

from typing import Any

from app import constants
from app.search.types import RetrievalFilters, RetrievalScope

# 检索 SQL 统一使用的表别名
CHUNK_ALIAS = "c"
DOC_ALIAS = "d"
STATE_ALIAS = "s"

ELIGIBILITY_FROM = (
    f"FROM document_chunks {CHUNK_ALIAS} "
    f"JOIN documents {DOC_ALIAS} ON {DOC_ALIAS}.id = {CHUNK_ALIAS}.doc_id "
    f"JOIN document_pipeline_state {STATE_ALIAS} ON {STATE_ALIAS}.doc_id = {CHUNK_ALIAS}.doc_id"
)


def build_eligibility(scope: RetrievalScope) -> tuple[str, dict[str, Any]]:
    """返回 ``(子句, 参数)``；子句可直接追加到 ``WHERE`` 之后。"""
    clauses = [
        f"{DOC_ALIAS}.deleted_at IS NULL",
        f"{DOC_ALIAS}.status = :elig_status",
        f"{DOC_ALIAS}.retrievable = 1",
        f"{STATE_ALIAS}.last_completed_stage = :elig_stage",
    ]
    params: dict[str, Any] = {
        "elig_status": constants.STATUS_READY,
        "elig_stage": constants.STAGE_COMPLETED,
    }

    branches = [
        f"({DOC_ALIAS}.source_type = :elig_upload AND {DOC_ALIAS}.activation_state IS NULL)"
    ]
    params["elig_upload"] = constants.SOURCE_UPLOAD

    if scope.demo_available and scope.active is not None:
        branches.append(
            f"({DOC_ALIAS}.source_type = :elig_demo "
            f"AND {DOC_ALIAS}.activation_state = :elig_active "
            f"AND {DOC_ALIAS}.dataset_version = :elig_version)"
        )
        params.update(
            elig_demo=constants.SOURCE_DEMO,
            elig_active=constants.ACTIVATION_ACTIVE,
            elig_version=scope.active.dataset_version,
        )

    clauses.append("(" + " OR ".join(branches) + ")")
    return " AND ".join(clauses), params


def build_filters(filters: RetrievalFilters) -> tuple[str, dict[str, Any]]:
    """把允许的过滤字段翻译为 SQL；选项值来自 chunk 的 citation JSON。"""
    clauses: list[str] = []
    params: dict[str, Any] = {}

    if filters.major is not None:
        clauses.append(f"json_extract({CHUNK_ALIAS}.citation, '$.major') = :f_major")
        params["f_major"] = filters.major
    if filters.grade_year is not None:
        clauses.append(f"json_extract({CHUNK_ALIAS}.citation, '$.grade_year') = :f_grade_year")
        params["f_grade_year"] = filters.grade_year
    if filters.semester is not None:
        clauses.append(f"json_extract({CHUNK_ALIAS}.citation, '$.semester') = :f_semester")
        params["f_semester"] = filters.semester
    if filters.doc_category is not None:
        clauses.append(f"{DOC_ALIAS}.doc_category = :f_doc_category")
        params["f_doc_category"] = filters.doc_category

    if not clauses:
        return "", {}
    return " AND " + " AND ".join(clauses), params


def build_where(scope: RetrievalScope, filters: RetrievalFilters) -> tuple[str, dict[str, Any]]:
    eligibility, params = build_eligibility(scope)
    filter_sql, filter_params = build_filters(filters)
    params.update(filter_params)
    return " AND " + eligibility + filter_sql, params


__all__ = [
    "CHUNK_ALIAS",
    "DOC_ALIAS",
    "ELIGIBILITY_FROM",
    "STATE_ALIAS",
    "build_eligibility",
    "build_filters",
    "build_where",
]
