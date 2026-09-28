"""BUG-8-E2E-02：Chroma 组合过滤条件必须被翻译成单层 ``$and``。

真实证据链（阶段 8 E2E 场景 5）：

- 请求过滤条件为 ``semester=2025-2026-2`` + ``doc_category=degree_plan``；
- 隔离库中该组合确实命中 0 个 chunk（31 个 degree_plan chunk 的 semester 全为 null），
  本应返回 refused / no_evidence；
- 但 ``chroma_where()`` 生成了带两个键的平铺字典，Chroma 直接抛
  ``ValueError: Expected where to have exactly one operator``，
  异常被 Chat 流收敛成 ``MODEL_STREAM_INTERRUPTED``。

本文件覆盖：纯函数契约、真实混合检索回归、Chat 真实终态回归。
"""

from __future__ import annotations

import pytest
from chromadb.api.types import validate_where

from app import constants
from app.search.dense import chroma_where
from app.search.types import RetrievalFilters
from tests.test_chat_flow import _run, _turn, _StubLLM

# E2E trace 中真实出现的过滤条件
E2E_FILTERS = RetrievalFilters(semester="2025-2026-2", doc_category="degree_plan")

FULL_ORDER = ("major", "grade_year", "semester", "doc_category")


# --- 1. chroma_where() 纯函数契约 -------------------------------------------


def test_chroma_where_returns_none_without_filters() -> None:
    assert chroma_where(RetrievalFilters()) is None


def test_chroma_where_keeps_a_single_filter_valid() -> None:
    where = chroma_where(RetrievalFilters(doc_category="degree_plan"))
    assert where == {"doc_category": {"$eq": "degree_plan"}}
    validate_where(where)


def test_chroma_where_wraps_multiple_filters_in_and() -> None:
    where = chroma_where(E2E_FILTERS)
    assert where is not None
    assert list(where) == ["$and"]
    assert where["$and"] == [
        {"semester": {"$eq": "2025-2026-2"}},
        {"doc_category": {"$eq": "degree_plan"}},
    ]
    validate_where(where)


def test_chroma_where_supports_all_filter_fields() -> None:
    where = chroma_where(
        RetrievalFilters(
            major="计算机科学与技术",
            grade_year=2025,
            semester="2025-2026-2",
            doc_category="degree_plan",
        )
    )
    assert where is not None
    assert list(where) == ["$and"]
    # 顺序稳定：major → grade_year → semester → doc_category
    assert [next(iter(item)) for item in where["$and"]] == list(FULL_ORDER)
    # 值类型保持：grade_year 必须是整数
    grade = next(item["grade_year"]["$eq"] for item in where["$and"] if "grade_year" in item)
    assert isinstance(grade, int)
    validate_where(where)


def test_chroma_where_is_accepted_by_chroma_validator() -> None:
    """任何组合都必须通过当前依赖的 Chroma 校验，且不得退化为 OR。"""
    for filters in (
        RetrievalFilters(major="计算机科学与技术"),
        RetrievalFilters(grade_year=2025),
        RetrievalFilters(semester="2025-2026-2"),
        RetrievalFilters(doc_category="degree_plan"),
        E2E_FILTERS,
        RetrievalFilters(grade_year=2025, doc_category="course_record"),
    ):
        where = chroma_where(filters)
        assert where is not None
        validate_where(where)
        assert "$or" not in repr(where)


# --- 2. 真实混合检索回归 -----------------------------------------------------


def test_combined_retrieval_filters_return_empty_instead_of_crashing(
    search, ingest_demo
) -> None:
    ingest_demo()

    def rows(result):
        # KeywordRetriever 返回 (results, mode)，其余路径返回列表
        return result[0] if isinstance(result, tuple) else result

    # Dense：修复前这里会抛 Chroma ValueError；修复后必须返回空候选
    assert rows(search.dense("量子纠缠补贴的报销标准是多少？", E2E_FILTERS)) == []
    # 关键词路径使用同一组 AND 条件
    assert rows(search.keyword("量子纠缠补贴", E2E_FILTERS)) == []
    assert rows(search.hybrid("量子纠缠补贴的报销标准是多少？", E2E_FILTERS)) == []
    assert rows(search.rerank("量子纠缠补贴的报销标准是多少？", E2E_FILTERS)) == []

    # 单条件仍可正常命中，证明过滤条件没有被整体丢弃
    assert rows(search.dense("培养方案", RetrievalFilters(doc_category="degree_plan")))


def test_combined_filters_never_drop_a_field(search, ingest_demo) -> None:
    """组合过滤必须同时保留两个字段（不得只剩一个、不得改成 OR）。"""
    ingest_demo()

    where = chroma_where(E2E_FILTERS)
    assert where is not None
    flat = where["$and"]
    assert {"semester": {"$eq": "2025-2026-2"}} in flat
    assert {"doc_category": {"$eq": "degree_plan"}} in flat
    assert "$or" not in repr(where)


# --- 3. Chat 真实终态回归 ---------------------------------------------------


def test_chat_combined_empty_filters_refuse_with_no_evidence(
    chat_runtime, ingest_demo
) -> None:
    """组合过滤命中 0 条时，必须在调用 LLM 之前拒答，而不是流中断。"""
    ingest_demo()

    llm = _StubLLM(answer='{"outcome": "answered", "answer": "不应被调用 [1]。", "citations": [1]}')
    from dataclasses import replace

    runtime = replace(chat_runtime, llm=llm)

    question = "量子纠缠补贴的报销标准是多少？"
    frames, _runner = _run(runtime, _turn(question, filters=E2E_FILTERS))
    from tests.conftest import parse_sse

    events = parse_sse(b"".join(frames))

    names = [name for name, _ in events]
    assert "error" not in names
    assert "MODEL_STREAM_INTERRUPTED" not in b"".join(frames).decode("utf-8")

    done = events[-1]
    assert done[0] == "done"
    assert done[1]["outcome"] == constants.CHAT_OUTCOME_REFUSED
    assert done[1]["reason_code"] == constants.REASON_NO_EVIDENCE
    assert done[1]["citation_count"] == 0
    assert done[1]["request_id"]

    assert not [name for name in names if name == "citation"]
    assert llm.calls == []
