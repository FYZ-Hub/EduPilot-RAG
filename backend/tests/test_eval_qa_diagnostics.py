"""9B citation 最小安全诊断：可区分性、标签准确性、落盘与不泄漏。

覆盖要求：

- 「已召回但未引用」与「未召回」必须可区分；
- D2 失败字段标签必须准确且只取白名单值；
- Judge 逐事实布尔与序号必须能落盘（报告层 JSON 安全）；
- 诊断必须复用同一次真实检索，**不产生额外检索或 Provider 调用**；
- 恶意路径 / 密钥 / 正文 / section 原文不得出现在诊断与报告载荷中。
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import replace

import pytest

from app.chat.service import (
    ChatStreamRunner,
    ChatTurn,
    _embedding_trace_from_error,
    _empty_embedding_trace,
    _empty_rerank_trace,
    _rerank_trace,
)
from app.core.errors import (
    DOCUMENT_EMBEDDING_FAILED,
    EMBEDDING_DIMENSION_MISMATCH,
    RERANK_PROVIDER_UNAVAILABLE,
    RETRIEVAL_QUERY_INVALID,
    ApiError,
)
from app.search.reranking import (
    RERANK_MAX_CANDIDATES,
    RERANK_TOP_K_MAX,
    RETRIEVAL_STAGES,
    STAGE_FINAL,
    STAGE_RERANKED,
    RerankingRetriever,
)
from app.search.types import RerankDiagnostics, RetrievalFilters

from eval_tools.qa.diagnostics import (
    citation_coverage,
    composition_summary,
    fact_anchor_diagnostics,
    fact_anchors,
    judge_fact_diagnostics,
    locator_count_summary,
    locator_diagnostics,
    locator_digest,
    locator_retrieval_coverage,
    retrieval_coverage,
    source_count_summary,
    source_digest,
    stage_composition,
    staged_locator_coverage,
)
from eval_tools.qa.executor import ChatOutcome, decide_citation, evaluate_chat_case
from eval_tools.qa.judge import JudgeFactResult, JudgeVerdict
from eval_tools.qa.metrics import (
    FAILED_FIELDS_WHITELIST,
    FAILED_FIELD_ORDER,
    PROVIDER_VERIFICATION_FAILED,
    STAGE_CITATION_FACT_UNSUPPORTED,
    QaCaseResult,
    embedding_failure_summary,
    normalize_degraded_reason,
    normalize_embedding_failure_reason,
    normalize_fact_anchor_diagnostics,
    rerank_degradation_summary,
)
from eval_tools.qa.providers import AuditedEmbedding, ProviderMeter
from eval_tools.qa.report import _case_payload

SECRET_TOKEN = "sk-live-DO-NOT-LEAK-0123456789"
MALICIOUS_PATH = "C:\\Users\\admin\\secret\\培养方案.pdf"
SECRET_SECTION = "内部机密章节：请勿外泄"


# --- 1. 召回与未引用可区分 ---------------------------------------------------


def test_recalled_but_not_cited_is_distinguishable() -> None:
    """来源进了 top-k 却没被引用：recalled=True 且 cited=False。"""
    retrieval = retrieval_coverage(["corpus/a.pdf"], ["b.pdf", "a.pdf", "c.pdf"])
    citation = citation_coverage(["corpus/a.pdf"], ["b.pdf", "c.pdf"])
    summary = source_count_summary(["corpus/a.pdf"], ["b.pdf", "a.pdf", "c.pdf"], ["b.pdf"])

    assert retrieval == (
        {"source_digest": source_digest("a.pdf"), "recalled": True, "best_rank": 2},
    )
    assert citation == (
        {"source_digest": source_digest("a.pdf"), "cited": False, "citation_indices": ()},
    )
    assert summary == {
        "expected_source_count": 1,
        "recalled_source_count": 1,
        "cited_source_count": 0,
    }


def test_not_recalled_is_distinguishable_from_not_cited() -> None:
    """来源根本没进 top-k：recalled=False、best_rank=None。"""
    retrieval = retrieval_coverage(["corpus/a.pdf"], ["b.pdf", "c.pdf"])
    summary = source_count_summary(["corpus/a.pdf"], ["b.pdf", "c.pdf"], ["b.pdf", "c.pdf"])

    assert retrieval == (
        {"source_digest": source_digest("a.pdf"), "recalled": False, "best_rank": None},
    )
    assert summary["recalled_source_count"] == 0
    assert summary["cited_source_count"] == 0


def test_best_rank_and_citation_indices_use_first_positions() -> None:
    """best_rank 取最早名次；citation_indices 记录全部命中位置（1 起）。"""
    retrieval = retrieval_coverage(["a.pdf"], ["b.pdf", "a.pdf", "a.pdf"])
    citation = citation_coverage(["a.pdf"], ["b.pdf", "a.pdf", "c.pdf", "a.pdf"])

    assert retrieval[0]["best_rank"] == 2
    assert citation[0]["citation_indices"] == (2, 4)


# --- 2. D2 字段标签准确 ------------------------------------------------------


def _full_locator(**overrides) -> dict:
    locator = {
        "path": "corpus/a.pdf",
        "page_number": 1,
        "sheet_name": "课表",
        "row_start": 3,
        "row_end": 5,
        "section_title": "三、学分要求",
    }
    locator.update(overrides)
    return locator


def _failed_fields(actual: dict, *, source: str = "a.pdf") -> tuple[str, ...]:
    rows = locator_diagnostics([_full_locator()], [(source, actual)])
    assert len(rows) == 1
    return rows[0]["failed_fields"]


def test_locator_diagnostics_matched_row_has_no_failed_fields() -> None:
    actual = {
        "page_number": 1,
        "sheet_name": "课表",
        "row_start": 4,
        "row_end": 6,
        "section_title": "三、学分要求",
    }
    rows = locator_diagnostics([_full_locator()], [("a.pdf", actual)])

    assert rows == ({"expected_index": 1, "matched": True, "failed_fields": ()},)


def test_locator_diagnostics_labels_each_field_accurately() -> None:
    base = {
        "page_number": 1,
        "sheet_name": "课表",
        "row_start": 4,
        "row_end": 6,
        "section_title": "三、学分要求",
    }

    assert _failed_fields({**base, "page_number": 2}) == ("page_number",)
    assert _failed_fields({**base, "sheet_name": "说明"}) == ("sheet_name",)
    assert _failed_fields({**base, "section_title": "四、课程设置与先修关系"}) == (
        "section_title",
    )
    assert _failed_fields({**base, "row_start": 9, "row_end": 10}) == ("row_range",)
    assert _failed_fields({**base, "row_start": None, "row_end": None}) == ("row_range",)
    # 来源不符：必须归因到 source
    assert _failed_fields(base, source="b.pdf") == ("source",)


def test_locator_diagnostics_orders_labels_by_whitelist_and_stays_in_whitelist() -> None:
    actual = {
        "page_number": 2,
        "sheet_name": "说明",
        "row_start": 9,
        "row_end": 10,
        "section_title": "别的东西",
    }
    fields = _failed_fields(actual)

    assert fields == ("page_number", "section_title", "sheet_name", "row_range")
    assert fields == tuple(label for label in FAILED_FIELD_ORDER if label in set(fields))
    assert set(fields) <= FAILED_FIELDS_WHITELIST
    # 标签只含字段名，绝不包含任何取值
    assert all(re.fullmatch(r"[a-z_]+", label) for label in fields)


def test_locator_diagnostics_without_any_citation_blames_source() -> None:
    rows = locator_diagnostics([_full_locator()], [])

    assert rows == ({"expected_index": 1, "matched": False, "failed_fields": ("source",)},)


def test_locator_diagnostics_keeps_expected_index_order() -> None:
    expected = [_full_locator(page_number=1), _full_locator(page_number=2)]
    cited = [("a.pdf", {**_full_locator(), "page_number": 2})]

    rows = locator_diagnostics(expected, cited)

    assert [row["expected_index"] for row in rows] == [1, 2]
    assert [row["matched"] for row in rows] == [False, True]


# --- 2b. 检索侧 locator 覆盖：未召回 vs 已召回未引用 --------------------------


def test_correct_locator_recalled_but_not_cited_is_distinguishable() -> None:
    """正确 chunk 已召回（rank 2）但未被引用：召回侧命中、D2 侧未命中。"""
    expected = [{"path": "corpus/a.pdf", "page_number": 1, "section_title": "三、学分要求"}]
    retrieved = [
        ("b.pdf", {"page_number": 1}),
        ("a.pdf", {"page_number": 1, "section_title": "三、学分要求"}),
    ]
    cited = [("b.pdf", {"page_number": 1})]

    recall_rows = locator_retrieval_coverage(expected, retrieved)
    d2_rows = locator_diagnostics(expected, cited)

    assert recall_rows == (
        {
            "expected_index": 1,
            "recalled_match": True,
            "best_rank": 2,
            "failed_fields": (),
        },
    )
    # 引用侧：未被引用 → D2 未命中（来源不符，且该引用块无 section）
    assert d2_rows == (
        {
            "expected_index": 1,
            "matched": False,
            "failed_fields": ("source", "section_title"),
        },
    )
    assert locator_count_summary(recall_rows) == {
        "expected_locator_count": 1,
        "recalled_locator_count": 1,
    }


def test_same_source_wrong_section_is_not_a_locator_recall() -> None:
    """同来源但 section 不符：不得算作 locator 召回。"""
    expected = [{"path": "corpus/a.pdf", "page_number": 1, "section_title": "三、学分要求"}]
    retrieved = [("a.pdf", {"page_number": 1, "section_title": "四、课程设置与先修关系"})]

    rows = locator_retrieval_coverage(expected, retrieved)

    assert rows == (
        {
            "expected_index": 1,
            "recalled_match": False,
            "best_rank": None,
            "failed_fields": ("section_title",),
        },
    )
    assert locator_count_summary(rows)["recalled_locator_count"] == 0


def test_not_recalled_attribution_is_safe_and_specific() -> None:
    """未召回时的安全归因：无块 → source；异来源 → source；同来源错页码 → page_number。"""
    expected = [{"path": "corpus/a.pdf", "page_number": 1}]

    empty = locator_retrieval_coverage(expected, [])
    assert empty == (
        {
            "expected_index": 1,
            "recalled_match": False,
            "best_rank": None,
            "failed_fields": ("source",),
        },
    )

    other_source = locator_retrieval_coverage(expected, [("b.pdf", {"page_number": 1})])
    assert other_source[0]["failed_fields"] == ("source",)

    wrong_page = locator_retrieval_coverage(expected, [("a.pdf", {"page_number": 9})])
    assert wrong_page[0]["failed_fields"] == ("page_number",)


def test_locator_recall_reports_earliest_matching_rank() -> None:
    """best_rank 取最早命中名次（1 起），且只取检索序列中的位置。"""
    expected = [{"path": "corpus/a.pdf", "page_number": 1}]
    retrieved = [
        ("a.pdf", {"page_number": 2}),
        ("a.pdf", {"page_number": 1}),
        ("a.pdf", {"page_number": 1}),
    ]

    rows = locator_retrieval_coverage(expected, retrieved)

    assert rows[0]["recalled_match"] is True
    assert rows[0]["best_rank"] == 2


def test_locator_count_summary_counts_recalled_locators() -> None:
    expected = [
        {"path": "corpus/a.pdf", "page_number": 1},
        {"path": "corpus/a.pdf", "page_number": 3},
    ]
    rows = locator_retrieval_coverage(expected, [("a.pdf", {"page_number": 1})])

    assert locator_count_summary(rows) == {
        "expected_locator_count": 2,
        "recalled_locator_count": 1,
    }


def test_d2_and_retrieval_locator_coverage_agree_on_the_same_input() -> None:
    """D2 与检索侧 locator 覆盖复用同一实现：同一输入必须给出一致的判定与归因。"""
    expected = [
        {
            "path": "corpus/a.pdf",
            "page_number": 1,
            "section_title": "四、课程设置与先修关系",
        },
        {"path": "corpus/a.pdf", "page_number": 3, "sheet_name": "课表"},
    ]
    items = [
        (
            "a.pdf",
            {
                "page_number": 1,
                "section_title": (
                    "启明大学计算机科学与技术专业培养方案（2026修订版） > "
                    "四、课程设置与先修关系 > （一）必修课程"
                ),
            },
        ),
        ("a.pdf", {"page_number": 9}),
    ]

    d2 = locator_diagnostics(expected, items)
    recall = locator_retrieval_coverage(expected, items)

    assert [row["expected_index"] for row in d2] == [
        row["expected_index"] for row in recall
    ]
    assert [row["matched"] for row in d2] == [row["recalled_match"] for row in recall]
    assert [row["failed_fields"] for row in d2] == [
        row["failed_fields"] for row in recall
    ]
    # 父章节叶级期望命中子章节 chunk（非根段），该 locator 两侧同为命中
    assert d2[0]["matched"] is True
    assert recall[0]["recalled_match"] is True
    assert recall[0]["best_rank"] == 1


# --- 2c. 检索分阶段覆盖（fused / reranked / final） --------------------------


def test_staged_locator_coverage_distinguishes_three_stages() -> None:
    """同一期望 locator 在三个阶段的命中情况与名次必须可区分。"""
    expected = [{"path": "corpus/a.pdf", "page_number": 1}]
    stages = {
        "fused": [("a.pdf", {"page_number": 9}), ("a.pdf", {"page_number": 1})],
        "reranked": [("a.pdf", {"page_number": 1}), ("a.pdf", {"page_number": 9})],
        "final": [("a.pdf", {"page_number": 9})],
    }

    rows = staged_locator_coverage(expected, stages)

    assert rows == (
        {
            "expected_index": 1,
            "fused_recalled": True,
            "fused_best_rank": 2,
            "fused_failed_fields": (),
            "reranked_recalled": True,
            "reranked_best_rank": 1,
            "reranked_failed_fields": (),
            "final_recalled": False,
            "final_best_rank": None,
            "final_failed_fields": ("page_number",),
        },
    )


def test_staged_locator_coverage_reports_stage_specific_misses() -> None:
    """只在 fused 出现、未进入后续阶段时，各阶段必须各自给出结论。"""
    expected = [{"path": "corpus/a.pdf", "section_title": "三、学分要求"}]
    stages = {
        "fused": [("a.pdf", {"section_title": "文档标题 > 三、学分要求"})],
        "reranked": [],
        "final": [],
    }

    row = staged_locator_coverage(expected, stages)[0]

    assert row["fused_recalled"] is True and row["fused_best_rank"] == 1
    assert row["reranked_recalled"] is False and row["reranked_best_rank"] is None
    assert row["final_recalled"] is False and row["final_best_rank"] is None
    # 空阶段没有可比对块 → 安全归因到 source
    assert row["reranked_failed_fields"] == ("source",)
    assert row["final_failed_fields"] == ("source",)


def test_staged_locator_coverage_tolerates_missing_stage_keys() -> None:
    """阶段缺失按空序列处理，行结构仍完整。"""
    row = staged_locator_coverage([{"path": "corpus/a.pdf", "page_number": 1}], {})[0]

    assert row["expected_index"] == 1
    for name in RETRIEVAL_STAGES:
        assert row[f"{name}_recalled"] is False
        assert row[f"{name}_best_rank"] is None
        assert row[f"{name}_failed_fields"] == ("source",)


def test_stage_trace_adds_no_provider_call_and_keeps_final_top_k(
    context, worker, reranker, evidence
) -> None:
    """分阶段记录只旁路读取：Rerank Provider 只调用一次，输出仍是截断后的 top-10。"""
    evidence("学分认定", top_k=6)  # 触发演示语料入库

    calls = {"rerank": 0}

    class _CountingReranker:
        descriptor = reranker.descriptor

        def rerank(self, query, candidates):
            calls["rerank"] += 1
            return reranker.rerank(query, candidates)

    sink: dict = {}
    with context.session_factory() as session:
        results, _diagnostics = RerankingRetriever(
            session,
            context.settings,
            worker.vectors,
            worker.embeddings,
            _CountingReranker(),
            worker.coordinator,
        ).search(
            "学分认定",
            RetrievalFilters(),
            top_k=context.settings.rerank_top_k,
            stage_sink=sink,
        )

    # 不增加任何 Provider 调用
    assert calls["rerank"] == 1
    # 最终输出仍是 hard cap 内的 top-10，未被记录逻辑改变
    assert 0 < len(results) <= RERANK_TOP_K_MAX
    assert set(sink) == set(RETRIEVAL_STAGES)
    assert len(sink["fused"]) <= RERANK_MAX_CANDIDATES
    # final 阶段与实际返回顺序逐条一致
    assert [
        (entry["file_name"], entry["locator"]["page_number"]) for entry in sink["final"]
    ] == [(item.chunk.file_name, item.chunk.page_number) for item in results]


def test_stage_trace_records_only_source_and_locator(chat_runtime, evidence) -> None:
    """分阶段旁路只含来源名与 5 个定位字段，不含正文与分数（走真实检索器）。"""
    probe = list(evidence("学分认定", top_k=3))
    assert probe

    runner = ChatStreamRunner(
        chat_runtime,
        ChatTurn(
            request_id="req-stages",
            question="学分认定",
            history=(),
            filters=RetrievalFilters(),
        ),
    )
    _drain(runner)

    stages = runner.retrieval_stages
    assert set(stages) == set(RETRIEVAL_STAGES)
    assert stages["fused"]
    assert stages["final"]
    assert len(stages["fused"]) <= RERANK_MAX_CANDIDATES
    assert len(stages["final"]) <= RERANK_TOP_K_MAX

    allowed = {"page_number", "sheet_name", "row_start", "row_end", "section_title"}
    for name in RETRIEVAL_STAGES:
        for entry in stages[name]:
            assert set(entry) == {"file_name", "locator"}
            assert set(entry["locator"]) == allowed

    blob = json.dumps(stages, ensure_ascii=False)
    assert "score" not in blob
    body = probe[0].chunk.text or ""
    if len(body) >= 20:
        assert body not in blob


# --- 2d. reranked / final 候选构成 ------------------------------------------


def _digest_is_12_hex(value: str) -> bool:
    assert len(value) == 12
    int(value, 16)  # 非十六进制会抛 ValueError
    return True


def test_stage_composition_records_rank_digests_and_matches() -> None:
    """构成逐槽位记录 rank、来源摘要、locator 摘要与命中的期望 locator 序号。"""
    expected = [
        {"path": "corpus/a.pdf", "page_number": 1, "section_title": "三、学分要求"},
        {"path": "corpus/b.pdf", "page_number": 2},
    ]
    stages = {
        "reranked": [
            ("a.pdf", {"page_number": 1, "section_title": "文档标题 > 三、学分要求"}),
            ("b.pdf", {"page_number": 2}),
            # 与第 1 条同来源同 locator → 重复槽位
            ("a.pdf", {"page_number": 1, "section_title": "文档标题>三、学分要求"}),
        ],
        "final": [
            ("a.pdf", {"page_number": 1, "section_title": "文档标题 > 三、学分要求"})
        ],
    }

    composition = stage_composition(stages, expected)
    reranked = composition[STAGE_RERANKED]

    # rank 稳定且连续，顺序与输入一致
    assert [row["rank"] for row in reranked] == [1, 2, 3]
    # 命中期望 locator 的序号（第 1 条命中期望 1，第 2 条命中期望 2）
    assert reranked[0]["matched_expected_indices"] == (1,)
    assert reranked[1]["matched_expected_indices"] == (2,)
    assert reranked[2]["matched_expected_indices"] == (1,)
    # 同来源同 locator ⇒ 摘要相同（空白与 ">" 写法差异经规范后等价）
    assert reranked[2]["source_digest"] == reranked[0]["source_digest"]
    assert reranked[2]["locator_digest"] == reranked[0]["locator_digest"]
    # 不同来源/不同 locator ⇒ 摘要不同
    assert reranked[1]["source_digest"] != reranked[0]["source_digest"]
    assert reranked[1]["locator_digest"] != reranked[0]["locator_digest"]
    for row in reranked:
        assert _digest_is_12_hex(row["source_digest"])
        assert _digest_is_12_hex(row["locator_digest"])

    # 重复槽位计数：第 3 条与第 1 条重复 → 1
    assert composition_summary(reranked) == {
        "distinct_source_count": 2,
        "distinct_locator_count": 2,
        "duplicate_slot_count": 1,
    }
    assert composition_summary(composition[STAGE_FINAL]) == {
        "distinct_source_count": 1,
        "distinct_locator_count": 1,
        "duplicate_slot_count": 0,
    }


def test_composition_summary_counts_duplicate_slots_and_distinct_keys() -> None:
    """汇总分别统计不同来源、不同 locator 与重复槽位（同来源不同 locator 不算重复）。"""
    rows = (
        {
            "rank": 1,
            "source_digest": "a" * 12,
            "locator_digest": "1" * 12,
        },
        {  # 同来源、不同 locator → 不算重复
            "rank": 2,
            "source_digest": "a" * 12,
            "locator_digest": "2" * 12,
        },
        {  # 完全重复 → 计一次重复槽位
            "rank": 3,
            "source_digest": "a" * 12,
            "locator_digest": "1" * 12,
        },
        {  # 新来源
            "rank": 4,
            "source_digest": "b" * 12,
            "locator_digest": "1" * 12,
        },
    )

    assert composition_summary(rows) == {
        "distinct_source_count": 2,
        "distinct_locator_count": 3,
        "duplicate_slot_count": 1,
    }
    assert composition_summary(()) == {
        "distinct_source_count": 0,
        "distinct_locator_count": 0,
        "duplicate_slot_count": 0,
    }


def test_stage_composition_marks_all_matching_expected_indices() -> None:
    """同一条候选命中多个期望 locator 时全部列出（复用 D2 判定）。"""
    expected = [
        {"path": "corpus/a.pdf", "page_number": 1, "section_title": "三、学分要求"},
        {"path": "corpus/a.pdf", "page_number": 1, "section_title": "三、学分要求"},
    ]
    composition = stage_composition(
        {"final": [("a.pdf", {"page_number": 1, "section_title": "三、学分要求"})]},
        expected,
    )

    assert composition[STAGE_FINAL][0]["matched_expected_indices"] == (1, 2)


def test_stage_composition_is_deterministic_and_tolerates_missing_stages() -> None:
    """同一输入两次结果一致；阶段缺失按空序列处理。"""
    expected = [{"path": "corpus/a.pdf", "page_number": 1}]
    stages = {"final": [("a.pdf", {"page_number": 1})]}

    first = stage_composition(stages, expected)
    second = stage_composition(stages, expected)

    assert first == second
    assert first[STAGE_FINAL][0]["rank"] == 1
    assert first[STAGE_RERANKED] == ()
    assert set(first) == {STAGE_RERANKED, STAGE_FINAL}


def test_locator_digest_is_stable_and_domain_separated() -> None:
    """locator 摘要对字段顺序与 section 空白写法不敏感，且不与来源摘要同取值空间。"""
    locator = {"page_number": 1, "section_title": "文档标题 > 三、学分要求"}
    reordered = {"section_title": "  文档标题>三、学分要求  ", "page_number": 1}

    assert locator_digest("corpus/a.pdf", locator) == locator_digest(
        "corpus/a.pdf", reordered
    )
    assert locator_digest("corpus/a.pdf", locator) != source_digest("corpus/a.pdf")
    assert locator_digest("corpus/a.pdf", locator) != locator_digest(
        "corpus/b.pdf", locator
    )
    # 目录不影响来源摘要（与 _basename 口径一致）
    assert locator_digest("corpus/a.pdf", locator) == locator_digest("a.pdf", locator)


# --- 3. Judge 事实布尔落盘 ---------------------------------------------------


def test_judge_fact_diagnostics_keeps_ordinal_and_booleans() -> None:
    rows = judge_fact_diagnostics(
        [
            JudgeFactResult(1, True, True, (1,)),
            JudgeFactResult(2, True, False, ()),
            JudgeFactResult(3, False, True, (2,)),
        ]
    )

    assert rows == (
        {
            "ordinal": 1,
            "answer_expresses": True,
            "evidence_supports": True,
            "evidence_indices": (1,),
        },
        {
            "ordinal": 2,
            "answer_expresses": True,
            "evidence_supports": False,
            "evidence_indices": (),
        },
        {
            "ordinal": 3,
            "answer_expresses": False,
            "evidence_supports": True,
            "evidence_indices": (2,),
        },
    )
    assert judge_fact_diagnostics(None) == ()


def _citation_case() -> dict:
    return {
        "id": "gt-diagnostic-001",
        "category": "single_doc",
        "question": "计算机科学与技术专业毕业总学分是多少？",
        "expected_answer_facts": ["毕业总学分：160.0 学分"],
        "expected_source_paths": ["corpus/a.pdf"],
        "expected_locators": [{"path": "corpus/a.pdf", "page_number": 1}],
        "should_refuse": False,
        "conflict_expected": False,
    }


def _citation_outcome() -> ChatOutcome:
    return ChatOutcome(
        outcome="answered",
        reason_code=None,
        citation_count=1,
        citation_chunk_ids=("c1",),
        citation_file_names=("a.pdf",),
        answer="毕业总学分是 160.0 学分 [1]。",
        done=True,
        citation_records=(
            {
                "chunk_id": "c1",
                "file_name": "a.pdf",
                "quote": "毕业总学分：160.0 学分。",
                "locator": {"page_number": 1, "section_title": "三、学分要求"},
            },
        ),
    )


def _unsupporting_judge(_request) -> JudgeVerdict:
    return JudgeVerdict(
        contract_version="qa-citation-judge/2",
        fact_results=(JudgeFactResult(1, True, False, ()),),
        verdict="unsupported",
    )


def test_decide_citation_attaches_judge_facts_and_report_persists_them() -> None:
    decision = decide_citation(
        _citation_case(), _citation_outcome(), judge=_unsupporting_judge
    )

    assert decision.judge_status == "ok"
    assert decision.formal_stage == STAGE_CITATION_FACT_UNSUPPORTED
    assert decision.judge_facts == (
        {
            "ordinal": 1,
            "answer_expresses": True,
            "evidence_supports": False,
            "evidence_indices": (),
        },
    )

    payload = _case_payload(
        QaCaseResult(
            case_id="gt-diagnostic-001",
            group="citation",
            question="q",
            observed_pass=False,
            judge_facts=decision.judge_facts,
        )
    )

    # 落盘必须 JSON 安全：元组 -> list
    assert payload["judge_facts"] == [
        {
            "ordinal": 1,
            "answer_expresses": True,
            "evidence_supports": False,
            "evidence_indices": [],
        }
    ]
    json.dumps(payload)


# --- 4. 无额外检索 / Provider 调用 -------------------------------------------


def test_evaluation_reuses_the_single_real_retrieval(
    chat_runtime, evidence, monkeypatch
) -> None:
    results = list(evidence("学分认定", top_k=3))
    assert results
    calls: list[str] = []

    def counting_retrieve(self, query: str):
        calls.append(query)
        return list(results)

    monkeypatch.setattr(ChatStreamRunner, "_retrieve", counting_retrieve)

    top_chunk = results[0].chunk
    top_name = top_chunk.file_name
    expected_locator: dict = {"path": f"corpus/{top_name}"}
    for key in ("page_number", "sheet_name", "row_start", "row_end", "section_title"):
        value = getattr(top_chunk, key)
        if value is not None:
            expected_locator[key] = value

    case = {
        **_citation_case(),
        "expected_source_paths": [f"corpus/{top_name}"],
        "expected_locators": [expected_locator],
    }

    result = evaluate_chat_case(chat_runtime, case)

    # 只检索一次：诊断没有触发第二次检索
    assert len(calls) == 1
    # 诊断确实来自同一批结果（rank 1 即该来源在 results 中的位置）
    assert result.retrieval_coverage == (
        {"source_digest": source_digest(top_name), "recalled": True, "best_rank": 1},
    )
    assert result.retrieval_locator_coverage == (
        {
            "expected_index": 1,
            "recalled_match": True,
            "best_rank": 1,
            "failed_fields": (),
        },
    )
    assert result.expected_source_count == 1
    assert result.recalled_source_count == 1
    assert result.expected_locator_count == 1
    assert result.recalled_locator_count == 1
    # final 阶段与既有 final 诊断同源：同一 locator 两侧结论与名次一致
    assert result.retrieval_stage_coverage[0]["final_recalled"] is True
    assert result.retrieval_stage_coverage[0]["final_best_rank"] == 1
    # 构成与最终结果完全一致：rank 连续 1..n、条数等于最终结果数、不超过 hard cap
    assert [row["rank"] for row in result.final_composition] == list(
        range(1, len(result.final_composition) + 1)
    )
    assert 0 < len(result.final_composition) <= RERANK_TOP_K_MAX
    assert len(result.reranked_composition) <= RERANK_MAX_CANDIDATES
    assert result.final_composition_summary["distinct_source_count"] >= 1


def _drain(runner: ChatStreamRunner) -> None:
    async def collect() -> None:
        async for _frame in runner.stream():
            pass

    asyncio.run(collect())


def test_retrieval_trace_keeps_only_source_and_locator(chat_runtime, evidence, monkeypatch) -> None:
    """检索旁路只记录 file_name + 5 个定位字段：不得记录正文或分数。"""
    results = list(evidence("学分认定", top_k=3))
    assert results
    monkeypatch.setattr(ChatStreamRunner, "_retrieve", lambda self, query: list(results))

    runner = ChatStreamRunner(
        chat_runtime,
        ChatTurn(
            request_id="req-trace",
            question="学分认定",
            history=(),
            filters=RetrievalFilters(),
        ),
    )
    _drain(runner)

    trace = runner.retrieval_trace
    assert len(trace) == len(results)

    allowed = {"page_number", "sheet_name", "row_start", "row_end", "section_title"}
    for index, entry in enumerate(trace, start=1):
        assert set(entry) == {"file_name", "locator"}
        assert set(entry["locator"]) == allowed
        # 顺序即 rank：逐项与真实检索结果一一对应
        assert entry["file_name"] == results[index - 1].chunk.file_name

    blob = json.dumps(trace, ensure_ascii=False)
    body = results[0].chunk.text or ""
    if len(body) >= 20:
        assert body not in blob
    assert "score" not in blob


# --- 5. 不泄漏 ---------------------------------------------------------------


def _diagnostic_blocks() -> dict:
    composition = stage_composition(
        {
            "reranked": [
                (MALICIOUS_PATH, {"page_number": 1, "section_title": SECRET_SECTION}),
                (MALICIOUS_PATH, {"page_number": 1, "section_title": SECRET_SECTION}),
            ],
            "final": [
                (MALICIOUS_PATH, {"page_number": 2, "section_title": SECRET_SECTION})
            ],
        },
        [_full_locator(path=MALICIOUS_PATH, section_title=SECRET_SECTION)],
    )
    return {
        "retrieval_coverage": retrieval_coverage([MALICIOUS_PATH], [MALICIOUS_PATH]),
        "citation_coverage": citation_coverage([MALICIOUS_PATH], [MALICIOUS_PATH]),
        "locator_diagnostics": locator_diagnostics(
            [_full_locator(path=MALICIOUS_PATH, section_title=SECRET_SECTION)],
            [(MALICIOUS_PATH, {"page_number": 2, "section_title": SECRET_SECTION})],
        ),
        "retrieval_locator_coverage": locator_retrieval_coverage(
            [_full_locator(path=MALICIOUS_PATH, section_title=SECRET_SECTION)],
            [(MALICIOUS_PATH, {"page_number": 2, "section_title": SECRET_SECTION})],
        ),
        "retrieval_stage_coverage": staged_locator_coverage(
            [_full_locator(path=MALICIOUS_PATH, section_title=SECRET_SECTION)],
            {
                "fused": [
                    (MALICIOUS_PATH, {"page_number": 1, "section_title": SECRET_SECTION})
                ],
                "reranked": [],
                "final": [],
            },
        ),
        "reranked_composition": composition[STAGE_RERANKED],
        "reranked_composition_summary": composition_summary(composition[STAGE_RERANKED]),
        "final_composition": composition[STAGE_FINAL],
        "final_composition_summary": composition_summary(composition[STAGE_FINAL]),
        "judge_facts": judge_fact_diagnostics([JudgeFactResult(1, True, False, ())]),
        "summary": source_count_summary(
            [MALICIOUS_PATH], [MALICIOUS_PATH], [MALICIOUS_PATH]
        ),
        "locator_counts": locator_count_summary(
            locator_retrieval_coverage([_full_locator(path=MALICIOUS_PATH)], [])
        ),
    }


def test_diagnostics_never_contain_names_paths_sections_or_secrets() -> None:
    blob = json.dumps(_diagnostic_blocks(), ensure_ascii=False)

    for leaked in (
        MALICIOUS_PATH,
        "培养方案.pdf",
        "admin",
        "Users",
        SECRET_SECTION,
        "内部机密",
        SECRET_TOKEN,
    ):
        assert leaked not in blob

    digest = _diagnostic_blocks()["retrieval_coverage"][0]["source_digest"]
    assert re.fullmatch(r"[0-9a-f]{12}", digest)


def test_report_payload_of_diagnostics_is_leak_free_and_json_safe() -> None:
    blocks = _diagnostic_blocks()
    payload = _case_payload(
        QaCaseResult(
            case_id="gt-leak-001",
            group="citation",
            question="问题",
            observed_pass=False,
            retrieval_coverage=blocks["retrieval_coverage"],
            citation_coverage=blocks["citation_coverage"],
            locator_diagnostics=blocks["locator_diagnostics"],
            retrieval_locator_coverage=blocks["retrieval_locator_coverage"],
            retrieval_stage_coverage=blocks["retrieval_stage_coverage"],
            reranked_composition=blocks["reranked_composition"],
            reranked_composition_summary=blocks["reranked_composition_summary"],
            final_composition=blocks["final_composition"],
            final_composition_summary=blocks["final_composition_summary"],
            judge_facts=blocks["judge_facts"],
            expected_source_count=blocks["summary"]["expected_source_count"],
            recalled_source_count=blocks["summary"]["recalled_source_count"],
            cited_source_count=blocks["summary"]["cited_source_count"],
            expected_locator_count=blocks["locator_counts"]["expected_locator_count"],
            recalled_locator_count=blocks["locator_counts"]["recalled_locator_count"],
        )
    )
    blob = json.dumps(payload, ensure_ascii=False)

    for leaked in (MALICIOUS_PATH, "培养方案.pdf", SECRET_SECTION, "内部机密"):
        assert leaked not in blob

    assert payload["retrieval_locator_coverage"] == [
        {
            "expected_index": 1,
            "recalled_match": False,
            "best_rank": None,
            # 期望 locator 含 page/sheet/row 且 section 一致；检索块缺 sheet 与行范围
            "failed_fields": ["page_number", "sheet_name", "row_range"],
        }
    ]
    assert payload["expected_locator_count"] == 1
    assert payload["recalled_locator_count"] == 0

    assert payload["retrieval_stage_coverage"] == [
        {
            "expected_index": 1,
            "fused_recalled": False,
            "fused_best_rank": None,
            "fused_failed_fields": ["sheet_name", "row_range"],
            "reranked_recalled": False,
            "reranked_best_rank": None,
            "reranked_failed_fields": ["source"],
            "final_recalled": False,
            "final_best_rank": None,
            "final_failed_fields": ["source"],
        }
    ]

    reranked_rows = payload["reranked_composition"]
    reranked_locator = {"page_number": 1, "section_title": SECRET_SECTION}
    final_locator = {"page_number": 2, "section_title": SECRET_SECTION}
    assert [row["rank"] for row in reranked_rows] == [1, 2]
    assert reranked_rows[0]["source_digest"] == source_digest(MALICIOUS_PATH)
    assert reranked_rows[0]["locator_digest"] == locator_digest(
        MALICIOUS_PATH, reranked_locator
    )
    assert reranked_rows[0]["source_digest"] == reranked_rows[1]["source_digest"]
    assert reranked_rows[0]["locator_digest"] == reranked_rows[1]["locator_digest"]
    assert reranked_rows[0]["matched_expected_indices"] == []
    assert payload["reranked_composition_summary"] == {
        "distinct_source_count": 1,
        "distinct_locator_count": 1,
        "duplicate_slot_count": 1,
    }
    assert payload["final_composition"] == [
        {
            "rank": 1,
            "source_digest": source_digest(MALICIOUS_PATH),
            "locator_digest": locator_digest(MALICIOUS_PATH, final_locator),
            "matched_expected_indices": [],
        }
    ]
    assert payload["final_composition_summary"] == {
        "distinct_source_count": 1,
        "distinct_locator_count": 1,
        "duplicate_slot_count": 0,
    }
    # final 槽位的 locator 摘要与 reranked 不同（页码不同）
    assert payload["final_composition"][0]["locator_digest"] != reranked_rows[0][
        "locator_digest"
    ]

    assert payload["locator_diagnostics"] == [
        {
            "expected_index": 1,
            "matched": False,
            # 期望 locator 含 page/sheet/row 且 section 一致；引用块缺 sheet 与行范围
            "failed_fields": ["page_number", "sheet_name", "row_range"],
        }
    ]
    assert payload["retrieval_coverage"] == [
        {
            "source_digest": source_digest(MALICIOUS_PATH),
            "recalled": True,
            "best_rank": 1,
        }
    ]
    assert payload["expected_source_count"] == 1


def test_diagnostics_are_absent_for_non_citation_groups(chat_runtime, monkeypatch) -> None:
    """非 citation 分组不产出诊断字段，保持既有报告形状。"""
    monkeypatch.setattr(ChatStreamRunner, "_retrieve", lambda self, query: [])

    result = evaluate_chat_case(
        chat_runtime,
        {
            "id": "gt-refuse-diagnostic",
            "category": "unanswerable",
            "question": "启明大学食堂今天中午供应哪些菜品？",
            "should_refuse": True,
            "expected_source_paths": [],
        },
    )

    assert result.retrieval_coverage is None
    assert result.citation_coverage is None
    assert result.locator_diagnostics is None
    assert result.retrieval_locator_coverage is None
    assert result.retrieval_stage_coverage is None
    assert result.reranked_composition is None
    assert result.final_composition is None
    assert result.reranked_composition_summary is None
    assert result.final_composition_summary is None
    assert result.expected_source_count is None
    assert result.expected_locator_count is None
    assert result.recalled_locator_count is None


# --- 6. 重排降级安全诊断（applied / degraded / degraded_reason） --------------


def test_rerank_trace_maps_success_without_degradation() -> None:
    """成功重排：applied=True、degraded=False、reason=None。"""
    assert _empty_rerank_trace() == {
        "applied": False,
        "degraded": False,
        "degraded_reason": None,
    }
    assert _rerank_trace(RerankDiagnostics(rerank_applied=True)) == {
        "applied": True,
        "degraded": False,
        "degraded_reason": None,
    }


def test_rerank_trace_marks_degradation_only_from_degraded_reason() -> None:
    """只有带稳定 ``degraded_reason`` 才算降级；applied=False 本身不是降级。"""
    for reason in ("reranker_unavailable", "rerank_timeout"):
        assert _rerank_trace(
            RerankDiagnostics(rerank_applied=False, degraded_reason=reason)
        ) == {"applied": False, "degraded": True, "degraded_reason": reason}
    # 空候选：applied=False 但无降级原因 → 不算降级
    assert _rerank_trace(RerankDiagnostics(rerank_applied=False)) == {
        "applied": False,
        "degraded": False,
        "degraded_reason": None,
    }


def test_zero_candidate_retrieval_is_not_degraded_and_calls_no_provider(
    context, worker, reranker, evidence
) -> None:
    """空候选 / 显式请求 0 条：不调用 Rerank，applied=False 且**不得**判为降级。"""
    evidence("学分认定", top_k=1)
    calls = {"rerank": 0}

    class _CountingReranker:
        descriptor = reranker.descriptor

        def rerank(self, query, candidates):
            calls["rerank"] += 1
            return reranker.rerank(query, candidates)

    with context.session_factory() as session:
        results, diagnostics = RerankingRetriever(
            session,
            context.settings,
            worker.vectors,
            worker.embeddings,
            _CountingReranker(),
            worker.coordinator,
        ).search("学分认定", RetrievalFilters(), top_k=0)

    assert results == []
    assert calls["rerank"] == 0
    assert _rerank_trace(diagnostics) == {
        "applied": False,
        "degraded": False,
        "degraded_reason": None,
    }


def test_runner_rerank_trace_success_calls_provider_once(chat_runtime, evidence) -> None:
    """真实成功路径：Rerank Provider 只调用一次，trace 记录 applied=True、未降级。"""
    evidence("学分认定", top_k=6)
    calls = {"rerank": 0}
    real = chat_runtime.reranker

    class _CountingReranker:
        descriptor = real.descriptor

        def rerank(self, query, candidates):
            calls["rerank"] += 1
            return real.rerank(query, candidates)

    runtime = replace(chat_runtime, reranker=_CountingReranker())
    runner = ChatStreamRunner(
        runtime,
        ChatTurn(
            request_id="req-rerank-ok",
            question="学分认定",
            history=(),
            filters=RetrievalFilters(),
        ),
    )
    _drain(runner)

    assert calls["rerank"] == 1
    assert runner.rerank_trace == {
        "applied": True,
        "degraded": False,
        "degraded_reason": None,
    }


def test_runner_rerank_trace_records_degraded_reason(chat_runtime, evidence) -> None:
    """Reranker 明确不可用：trace 记录 degraded=True 与稳定 reason，零重试。"""
    evidence("学分认定", top_k=6)
    calls = {"rerank": 0}
    desc = chat_runtime.reranker.descriptor

    class _UnavailableReranker:
        descriptor = desc

        def rerank(self, query, candidates):
            calls["rerank"] += 1
            raise ApiError(
                RERANK_PROVIDER_UNAVAILABLE,
                details={"reason": "reranker_unavailable"},
            )
    runtime = replace(chat_runtime, reranker=_UnavailableReranker())
    runner = ChatStreamRunner(
        runtime,
        ChatTurn(
            request_id="req-rerank-degraded",
            question="学分认定",
            history=(),
            filters=RetrievalFilters(),
        ),
    )
    _drain(runner)

    assert calls["rerank"] == 1
    assert runner.rerank_trace == {
        "applied": False,
        "degraded": True,
        "degraded_reason": "reranker_unavailable",
    }


def test_rerank_trace_never_enters_sse(chat_runtime, evidence) -> None:
    """重排诊断只在进程内，绝不进入 SSE 帧（与 API 响应）。"""
    evidence("学分认定", top_k=6)
    runner = ChatStreamRunner(
        chat_runtime,
        ChatTurn(
            request_id="req-rerank-sse",
            question="学分认定",
            history=(),
            filters=RetrievalFilters(),
        ),
    )

    async def collect() -> str:
        chunks: list[str] = []
        async for frame in runner.stream():
            chunks.append(frame.decode("utf-8"))
        return "".join(chunks)

    body = asyncio.run(collect())

    assert runner.rerank_trace["applied"] is True
    for leaked in ("rerank_applied", "rerank_degraded", "degraded_reason", "rerank_trace"):
        assert leaked not in body, leaked


def test_normalize_degraded_reason_blocks_malicious_values() -> None:
    """降级原因只允许稳定小写标签；异常正文 / URL / 密钥 / 超长串一律归 unknown。"""
    assert normalize_degraded_reason("reranker_unavailable") == "reranker_unavailable"
    for raw in (
        "https://evil.example/x?sk=abc",
        "TimeoutError: connection reset by peer",
        "sk-live-0123456789",
        "Upper_Reason",
        "x" * 80,
    ):
        assert normalize_degraded_reason(raw) == "unknown"

    # 经 QaCaseResult 构造时同样归一
    case = QaCaseResult(
        case_id="gt-x",
        group="citation",
        question="q",
        observed_pass=True,
        rerank_degraded=True,
        rerank_degraded_reason="https://evil.example/x?sk=abc",
    )
    assert case.rerank_degraded_reason == "unknown"


def test_rerank_degradation_summary_locates_exact_cases() -> None:
    """汇总必须一致：降级用例数、原因计数与用例 ID 精确对应同一批用例。"""
    results = [
        QaCaseResult(
            case_id="alpha",
            group="citation",
            question="q",
            observed_pass=True,
            rerank_applied=False,
            rerank_degraded=True,
            rerank_degraded_reason="reranker_unavailable",
        ),
        QaCaseResult(
            case_id="beta",
            group="citation",
            question="q",
            observed_pass=True,
            rerank_applied=True,
            rerank_degraded=False,
        ),
        QaCaseResult(
            case_id="gamma",
            group="refusal_contract",
            question="q",
            observed_pass=True,
            rerank_applied=False,
            rerank_degraded=True,
            rerank_degraded_reason="rerank_timeout",
        ),
    ]

    summary = rerank_degradation_summary(results)
    assert summary == {
        "degraded_case_count": 2,
        "reason_counts": {"rerank_timeout": 1, "reranker_unavailable": 1},
        "case_ids": ["alpha", "gamma"],
    }
    # 唯一失败用例可被精确定位（其余用例不在列表内）
    assert "beta" not in summary["case_ids"]


def test_rerank_diagnostics_do_not_enter_report_payload(chat_runtime, evidence, monkeypatch) -> None:
    """逐案例落盘的重排诊断只含布尔与稳定标签，且不泄漏密钥/正文。"""
    monkeypatch.setattr(ChatStreamRunner, "_retrieve", lambda self, query: [])
    result = evaluate_chat_case(
        chat_runtime,
        {
            "id": "gt-rerank-payload",
            "category": "unanswerable",
            "question": "启明大学食堂今天中午供应哪些菜品？",
            "should_refuse": True,
            "expected_source_paths": [],
        },
    )
    payload = _case_payload(result)
    assert payload["rerank_applied"] is False
    assert payload["rerank_degraded"] is False
    assert payload["rerank_degraded_reason"] is None
    blob = json.dumps(payload, ensure_ascii=False)
    for forbidden in (SECRET_TOKEN, "https://", "sk-"):
        assert forbidden not in blob


# --- 7. Embedding 失败安全诊断（failed / failure_reason） ---------------------


def _collect_body(runner: ChatStreamRunner) -> str:
    async def collect() -> str:
        chunks: list[str] = []
        async for frame in runner.stream():
            chunks.append(frame.decode("utf-8"))
        return "".join(chunks)

    return asyncio.run(collect())


class _FailingEmbedding:
    """检索期查询向量化失败的替身；只抛安全 ApiError，不访问网络。"""

    def __init__(self, descriptor, code, *, reason=None, calls=None):
        self.descriptor = descriptor
        self._code = code
        self._reason = reason
        self._calls = calls if calls is not None else {"embed": 0}

    def embed_documents(self, texts):
        self._calls["embed"] += 1
        details = {} if self._reason is None else {"reason": self._reason}
        raise ApiError(self._code, retryable=True, details=details)


def test_embedding_trace_default_and_mapping() -> None:
    """默认未失败；Embedding 失败码带稳定 reason 时记录 failed=True。"""
    assert _empty_embedding_trace() == {"failed": False, "failure_reason": None}
    assert _embedding_trace_from_error(
        ApiError(DOCUMENT_EMBEDDING_FAILED, details={"reason": "api_embedding_timeout"})
    ) == {"failed": True, "failure_reason": "api_embedding_timeout"}
    # 缺少 reason 时仍标记失败，但 reason 为 None（不臆造）
    assert _embedding_trace_from_error(ApiError(EMBEDDING_DIMENSION_MISMATCH)) == {
        "failed": True,
        "failure_reason": None,
    }


def test_embedding_trace_ignores_non_embedding_codes() -> None:
    """非 Embedding 错误不得被误记为 Embedding 失败。"""
    assert (
        _embedding_trace_from_error(
            ApiError(RETRIEVAL_QUERY_INVALID, details={"reason": "empty_query"})
        )
        is None
    )


def test_embedding_trace_preserves_response_invalid_reason() -> None:
    """非法向量归 api_embedding_response_invalid，并在 trace 与 QaCaseResult 中原样保留。"""
    trace = _embedding_trace_from_error(
        ApiError(
            DOCUMENT_EMBEDDING_FAILED,
            retryable=True,
            details={"reason": "api_embedding_response_invalid"},
        )
    )
    assert trace == {"failed": True, "failure_reason": "api_embedding_response_invalid"}

    case = QaCaseResult(
        case_id="gt-vec",
        group="citation",
        question="q",
        observed_pass=False,
        embedding_failed=True,
        embedding_failure_reason="api_embedding_response_invalid",
    )
    assert case.embedding_failure_reason == "api_embedding_response_invalid"
    assert _case_payload(case)["embedding_failure_reason"] == "api_embedding_response_invalid"


def test_runner_records_embedding_failure_without_entering_sse(chat_runtime) -> None:
    """检索期 Embedding 失败：只在进程内记录，错误码不变且诊断不进入 SSE。"""
    calls = {"embed": 0}
    runtime = replace(
        chat_runtime,
        embeddings=_FailingEmbedding(
            chat_runtime.embeddings.descriptor,
            DOCUMENT_EMBEDDING_FAILED,
            reason="api_embedding_timeout",
            calls=calls,
        ),
    )
    runner = ChatStreamRunner(
        runtime,
        ChatTurn(
            request_id="req-embed-fail",
            question="学分认定",
            history=(),
            filters=RetrievalFilters(),
        ),
    )
    body = _collect_body(runner)

    assert calls["embed"] == 1, "失败不得重试"
    assert runner.embedding_trace == {
        "failed": True,
        "failure_reason": "api_embedding_timeout",
    }
    # 诊断不进入 SSE：既有错误码保持，诊断键与原因都不出现
    assert "DOCUMENT_EMBEDDING_FAILED" in body
    for leaked in (
        "embedding_failed",
        "embedding_failure_reason",
        "embedding_trace",
        "api_embedding_timeout",
    ):
        assert leaked not in body, leaked


def test_normalize_embedding_failure_reason_blocks_malicious_values() -> None:
    """失败原因只允许稳定小写标签；异常正文 / URL / 密钥 / 超长串一律归 unknown。"""
    assert normalize_embedding_failure_reason("api_embedding_timeout") == "api_embedding_timeout"
    for raw in (
        "https://evil.example/x?sk=abc",
        "TimeoutError: connection reset by peer",
        "sk-live-0123456789",
        "Upper_Reason",
        "x" * 80,
    ):
        assert normalize_embedding_failure_reason(raw) == "unknown"

    case = QaCaseResult(
        case_id="gt-x",
        group="citation",
        question="q",
        observed_pass=True,
        embedding_failed=True,
        embedding_failure_reason="https://evil.example/x?sk=abc",
    )
    assert case.embedding_failure_reason == "unknown"


def test_embedding_failure_summary_locates_exact_cases() -> None:
    """汇总必须一致：失败用例数、原因计数与用例 ID 精确对应同一批用例。"""
    results = [
        QaCaseResult(
            case_id="alpha",
            group="citation",
            question="q",
            observed_pass=False,
            embedding_failed=True,
            embedding_failure_reason="api_embedding_timeout",
        ),
        QaCaseResult(
            case_id="beta",
            group="citation",
            question="q",
            observed_pass=True,
            embedding_failed=False,
        ),
        QaCaseResult(
            case_id="gamma",
            group="refusal_contract",
            question="q",
            observed_pass=False,
            embedding_failed=True,
            embedding_failure_reason="api_embedding_rate_limited",
        ),
    ]

    summary = embedding_failure_summary(results)
    assert summary == {
        "failed_case_count": 2,
        "reason_counts": {"api_embedding_rate_limited": 1, "api_embedding_timeout": 1},
        "case_ids": ["alpha", "gamma"],
    }
    assert "beta" not in summary["case_ids"]


def test_audited_embedding_records_single_failure_and_is_sticky() -> None:
    """审计一致性：一次真实失败即 failed=1 且状态粘滞；空输入不计次、零重试。"""
    meter = ProviderMeter("embedding")
    provider = _FailingEmbedding(None, DOCUMENT_EMBEDDING_FAILED, reason="api_embedding_timeout")
    audited = AuditedEmbedding(provider, meter)

    with pytest.raises(ApiError):
        audited.embed_documents(["查询"])

    assert meter.calls == 1, "失败不得重试"
    assert meter.failed == 1
    assert meter.ok == 0
    assert meter.state == PROVIDER_VERIFICATION_FAILED

    # 空输入不计次、不算失败、不下探底层
    assert audited.embed_documents([]) == []
    assert meter.calls == 1


def test_embedding_diagnostics_do_not_enter_report_payload(
    chat_runtime, evidence, monkeypatch
) -> None:
    """逐案例落盘的 Embedding 诊断只含布尔与稳定标签，且不泄漏密钥/正文。"""
    monkeypatch.setattr(ChatStreamRunner, "_retrieve", lambda self, query: [])
    result = evaluate_chat_case(
        chat_runtime,
        {
            "id": "gt-embed-payload",
            "category": "unanswerable",
            "question": "启明大学食堂今天中午供应哪些菜品？",
            "should_refuse": True,
            "expected_source_paths": [],
        },
    )
    payload = _case_payload(result)
    assert payload["embedding_failed"] is False
    assert payload["embedding_failure_reason"] is None
    blob = json.dumps(payload, ensure_ascii=False)
    for forbidden in (SECRET_TOKEN, "https://", "sk-"):
        assert forbidden not in blob


# --- 9. 答案事实锚点安全诊断 --------------------------------------------------


def test_fact_anchors_extracts_number_date_time_and_code() -> None:
    """确定性锚点：数字（含小数）、日期、时间、同时含字母与数字的代码。"""
    assert fact_anchors("QM-CS201 于 2026-09-01 10:00 开课，学分 3.5") == (
        "qm-cs201",
        "2026-09-01",
        "10:00",
        "3.5",
    )
    # 纯中文/纯字母不加数字不构成锚点
    assert fact_anchors("课程安排合理") == ()
    assert fact_anchors("数据结构与算法") == ()


def test_fact_anchors_normalizes_nfkc_whitespace_and_punctuation() -> None:
    """NFKC + 空白去除 + 常规标点统一后再提取。"""
    assert fact_anchors("学分：３．５　（2026－09－01）") == ("3.5", "2026-09-01")
    assert fact_anchors("课程代码　ＱＭ－ＣＳ２０１") == ("qm-cs201",)


def test_fact_anchor_diagnostics_is_deterministic() -> None:
    facts = ("毕业总学分 155.0 学分",)
    answer = "依据资料，毕业总学分是 155.0 学分。"
    quotes = ("毕业总学分：155.0 学分。",)
    assert fact_anchor_diagnostics(facts, answer, quotes) == fact_anchor_diagnostics(
        facts, answer, quotes
    )


def test_fact_anchor_diagnostics_counts_and_all_matched() -> None:
    facts = ("毕业总学分 155.0 学分", "课程代码 QM-CS201")
    answer = "毕业总学分是 155.0 学分，课程代码 QM-CS201。"
    evidence = ("毕业总学分：155.0 学分。", "课程代码 QM-CS201 见培养方案。")
    result = fact_anchor_diagnostics(facts, answer, evidence)

    assert result["anchor_count"] == 2
    assert result["answer_anchor_match_count"] == 2
    assert result["evidence_anchor_match_count"] == 2
    assert result["all_answer_anchors_matched"] is True
    assert result["all_evidence_anchors_matched"] is True
    assert result["all_answer_anchors_matched_fact_count"] == 2
    assert result["all_evidence_anchors_matched_fact_count"] == 2
    assert [row["ordinal"] for row in result["facts"]] == [1, 2]
    assert set(result["facts"][0]) == {
        "ordinal",
        "answer_exact_match",
        "evidence_exact_match",
    }
    # 事实 1 含额外措辞（非答案子串），但它的锚点已全部命中
    assert result["facts"][0]["answer_exact_match"] is False
    assert result["facts"][1]["answer_exact_match"] is True


def test_fact_anchor_diagnostics_partial_match() -> None:
    facts = ("总学分 155 与专业必修 58",)
    answer = "总学分是 155。"
    evidence = ("总学分：155。专业必修：58。",)
    result = fact_anchor_diagnostics(facts, answer, evidence)

    assert result["anchor_count"] == 2
    assert result["answer_anchor_match_count"] == 1
    assert result["evidence_anchor_match_count"] == 2
    assert result["all_answer_anchors_matched"] is False
    assert result["all_evidence_anchors_matched"] is True
    assert result["all_answer_anchors_matched_fact_count"] == 0
    assert result["all_evidence_anchors_matched_fact_count"] == 1


def test_fact_anchor_diagnostics_zero_anchors_is_not_a_pass() -> None:
    result = fact_anchor_diagnostics(("课程安排应当合理",), "依据资料。", ("课程安排应当合理。",))
    assert result["anchor_count"] == 0
    assert result["all_answer_anchors_matched"] is False
    assert result["all_evidence_anchors_matched"] is False
    assert result["all_answer_anchors_matched_fact_count"] == 0
    assert result["all_evidence_anchors_matched_fact_count"] == 0


def test_fact_anchor_diagnostics_never_leaks_text_paths_or_secrets() -> None:
    facts = (f"课程代码 {SECRET_TOKEN}",)
    answer = f"依据 {MALICIOUS_PATH} 得到 {SECRET_TOKEN}。"
    quotes = (f"引用自 {MALICIOUS_PATH}：{SECRET_TOKEN}",)
    result = fact_anchor_diagnostics(facts, answer, quotes)
    blob = json.dumps(result, ensure_ascii=False)
    for forbidden in (
        SECRET_TOKEN,
        MALICIOUS_PATH,
        "培养方案.pdf",
        "sk-live",
        "C:\\",
    ):
        assert forbidden not in blob


def test_fact_anchor_diagnostics_normalizes_illegal_structure() -> None:
    assert normalize_fact_anchor_diagnostics(None) is None
    assert normalize_fact_anchor_diagnostics([1, 2]) is None
    assert normalize_fact_anchor_diagnostics({"facts": "bad"}) is None
    # 非法事实行（ordinal 非正整数）→ 整体丢弃
    assert normalize_fact_anchor_diagnostics({"facts": [{"ordinal": 0}]}) is None

    clean = normalize_fact_anchor_diagnostics(
        {
            "facts": [
                {"ordinal": 1, "answer_exact_match": 1, "evidence_exact_match": 0, "evil": "sk"}
            ],
            "anchor_count": "NaN",
            "all_answer_anchors_matched": 1,
        }
    )
    assert set(clean) == {
        "facts",
        "anchor_count",
        "answer_anchor_match_count",
        "evidence_anchor_match_count",
        "all_answer_anchors_matched",
        "all_evidence_anchors_matched",
        "all_answer_anchors_matched_fact_count",
        "all_evidence_anchors_matched_fact_count",
    }
    assert clean["anchor_count"] == 0
    assert clean["all_answer_anchors_matched"] is True
    assert clean["facts"] == (
        {"ordinal": 1, "answer_exact_match": True, "evidence_exact_match": False},
    )
    # 通过 QaCaseResult 构造时同样归一
    case = QaCaseResult(
        case_id="x",
        group="citation",
        question="q",
        observed_pass=True,
        fact_anchor_diagnostics={"facts": "bad"},
    )
    assert case.fact_anchor_diagnostics is None


def test_fact_anchor_diagnostics_is_json_safe_in_case_payload() -> None:
    case = QaCaseResult(
        case_id="gt-anchor",
        group="citation",
        question="q",
        observed_pass=True,
        fact_anchor_diagnostics=fact_anchor_diagnostics(
            ("155.0 学分",), "155.0", ("155.0",)
        ),
    )
    payload = _case_payload(case)["fact_anchor_diagnostics"]
    assert isinstance(payload["facts"], list)
    json.dumps(payload)
    assert set(payload) == {
        "facts",
        "anchor_count",
        "answer_anchor_match_count",
        "evidence_anchor_match_count",
        "all_answer_anchors_matched",
        "all_evidence_anchors_matched",
        "all_answer_anchors_matched_fact_count",
        "all_evidence_anchors_matched_fact_count",
    }


def test_fact_anchor_diagnostics_is_null_for_non_citation(
    chat_runtime, monkeypatch
) -> None:
    monkeypatch.setattr(ChatStreamRunner, "_retrieve", lambda self, query: [])
    result = evaluate_chat_case(
        chat_runtime,
        {
            "id": "gt-anchor-refuse",
            "category": "unanswerable",
            "question": "启明大学食堂今天中午供应哪些菜品？",
            "should_refuse": True,
            "expected_source_paths": [],
        },
    )
    assert result.fact_anchor_diagnostics is None
    assert _case_payload(result)["fact_anchor_diagnostics"] is None


def test_fact_anchor_diagnostics_adds_no_extra_retrieval_or_llm_calls(
    chat_runtime, evidence, monkeypatch
) -> None:
    results = list(evidence("学分认定", top_k=2))
    assert results
    calls = {"retrieve": 0}

    def counting_retrieve(self, query):
        calls["retrieve"] += 1
        return list(results)

    monkeypatch.setattr(ChatStreamRunner, "_retrieve", counting_retrieve)
    result = evaluate_chat_case(
        chat_runtime,
        {
            "id": "gt-anchor-cite",
            "category": "academic",
            "question": "学分认定上限是多少？",
            "expected_source_paths": [results[0].chunk.file_name],
            "expected_answer_facts": ["学分认定上限"],
        },
    )
    assert calls["retrieve"] == 1
    assert result.fact_anchor_diagnostics is not None


def test_fact_anchor_diagnostics_does_not_enter_sse(
    chat_runtime, evidence, monkeypatch
) -> None:
    results = list(evidence("学分认定", top_k=2))
    monkeypatch.setattr(ChatStreamRunner, "_retrieve", lambda self, query: list(results))
    runner = ChatStreamRunner(
        chat_runtime,
        ChatTurn(
            request_id="req-anchor",
            question="学分认定上限是多少？",
            history=(),
            filters=RetrievalFilters(),
        ),
    )
    body = _collect_body(runner)
    assert "fact_anchor" not in body
    assert "answer_anchor" not in body
