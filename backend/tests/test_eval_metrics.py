"""阶段 9A 指标与口径单元测试：公式、来源去重、空集合/非法数据、稳定排序。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval_tools.ground_truth import (
    EvaluationDataError,
    load_ground_truth,
    parse_case,
    select_cases,
)
from eval_tools.matching import RetrievedSource, canonicalize, expected_identity, matches_expected
from eval_tools.metrics import (
    dedupe_preserving_order,
    mean,
    percentile,
    recall_at_k,
    reciprocal_rank,
)

DEMO_GROUND_TRUTH = Path("/app/demo/ground_truth.jsonl")


# --- Recall@k ---------------------------------------------------------------


def test_recall_at_k_is_hit_ratio() -> None:
    expected = ["a", "b", "c", "d"]
    assert recall_at_k(expected, ["a", "b", "x", "y"], 5) == pytest.approx(0.5)
    assert recall_at_k(expected, ["a", "b", "c", "d"], 5) == pytest.approx(1.0)
    assert recall_at_k(expected, ["x", "y", "z"], 5) == pytest.approx(0.0)


def test_recall_at_k_truncates_to_k() -> None:
    expected = ["a", "b", "c"]
    retrieved = ["x", "y", "a", "b", "c"]
    assert recall_at_k(expected, retrieved, 2) == pytest.approx(0.0)
    assert recall_at_k(expected, retrieved, 3) == pytest.approx(1 / 3)
    assert recall_at_k(expected, retrieved, 5) == pytest.approx(1.0)


def test_recall_at_k_deduplicates_sources_before_truncation() -> None:
    """同一来源的多个 chunk 只算一次，不能靠重复占位挤掉其他来源。"""
    expected = ["a"]
    retrieved = ["a", "a", "a", "a"]
    assert recall_at_k(expected, retrieved, 5) == pytest.approx(1.0)


def test_recall_at_k_dedup_does_not_hide_later_source() -> None:
    expected = ["a", "b"]
    retrieved = ["a", "a", "b"]
    assert recall_at_k(expected, retrieved, 5) == pytest.approx(1.0)


def test_recall_at_k_rejects_empty_expected_and_bad_k() -> None:
    with pytest.raises(ValueError):
        recall_at_k([], ["a"], 5)
    with pytest.raises(ValueError):
        recall_at_k(["a"], ["a"], 0)


# --- MRR --------------------------------------------------------------------


def test_mrr_uses_first_relevant_rank() -> None:
    assert reciprocal_rank(["a"], ["a", "b"]) == pytest.approx(1.0)
    assert reciprocal_rank(["b", "c"], ["x", "b", "c"]) == pytest.approx(0.5)
    assert reciprocal_rank(["z"], ["a", "b", "c"]) == pytest.approx(0.0)


def test_mrr_deduplicates_before_ranking() -> None:
    """重复来源会占用位次，但去重后首个相关来源的排名才是真实排名。"""
    assert reciprocal_rank(["a"], ["a", "a", "b"]) == pytest.approx(1.0)


def test_mrr_rejects_empty_expected() -> None:
    with pytest.raises(ValueError):
        reciprocal_rank([], ["a"])


# --- 去重 / 百分位 / 均值 ----------------------------------------------------


def test_dedupe_preserving_order_is_stable() -> None:
    assert dedupe_preserving_order(["b", "a", "b", "c", "a"]) == ["b", "a", "c"]
    assert dedupe_preserving_order([]) == []


def test_percentile_linear_interpolation() -> None:
    values = [0.0, 10.0, 20.0, 30.0]
    assert percentile(values, 50.0) == pytest.approx(15.0)
    assert percentile(values, 95.0) == pytest.approx(28.5)
    assert percentile([7.0], 95.0) == pytest.approx(7.0)
    assert percentile([], 50.0) is None


def test_percentile_rejects_out_of_range() -> None:
    with pytest.raises(ValueError):
        percentile([1.0], 101.0)


def test_mean_handles_empty() -> None:
    assert mean([1.0, 2.0, 3.0]) == pytest.approx(2.0)
    assert mean([]) is None


# --- 来源映射 ---------------------------------------------------------------


def test_matches_expected_accepts_source_key_and_basename() -> None:
    source = RetrievedSource(
        source_key="2026.1:corpus/02-培养方案.pdf", file_name="02-培养方案.pdf"
    )
    assert matches_expected(source, "corpus/02-培养方案.pdf", "2026.1")
    # 版本不同但文件名相同仍视为同一来源（ground truth 只给相对路径）
    assert matches_expected(source, "corpus/02-培养方案.pdf", "2025.1")
    assert not matches_expected(source, "corpus/03-课程大纲.docx", "2026.1")


def test_canonicalize_maps_matches_and_keeps_identity() -> None:
    expected = ["corpus/a.pdf", "corpus/b.pdf"]
    retrieved = [
        RetrievedSource("2026.1:corpus/a.pdf", "a.pdf"),
        RetrievedSource("2026.1:corpus/z.pdf", "z.pdf"),
        RetrievedSource("2026.1:corpus/b.pdf", "b.pdf"),
    ]
    assert canonicalize(retrieved, expected, "2026.1") == [
        "corpus/a.pdf",
        "2026.1:corpus/z.pdf",
        "corpus/b.pdf",
    ]


def test_expected_identity_matches_demo_source_key() -> None:
    assert expected_identity("2026.1", "corpus/a.pdf") == "2026.1:corpus/a.pdf"


# --- 数据集加载与选择 -------------------------------------------------------


def test_load_rejects_invalid_lines_and_duplicates(tmp_path: Path) -> None:
    bad = tmp_path / "bad.jsonl"
    bad.write_text("{not json}\n", encoding="utf-8")
    with pytest.raises(EvaluationDataError):
        load_ground_truth(bad)

    missing_field = tmp_path / "missing.jsonl"
    missing_field.write_text(json.dumps({"id": "x"}) + "\n", encoding="utf-8")
    with pytest.raises(EvaluationDataError):
        load_ground_truth(missing_field)

    duplicate = tmp_path / "dup.jsonl"
    row = {
        "id": "dup",
        "category": "single_doc",
        "question": "q",
        "expected_source_paths": ["corpus/a.pdf"],
        "should_refuse": False,
    }
    duplicate.write_text(f"{json.dumps(row)}\n{json.dumps(row)}\n", encoding="utf-8")
    with pytest.raises(EvaluationDataError):
        load_ground_truth(duplicate)

    empty = tmp_path / "empty.jsonl"
    empty.write_text("\n\n", encoding="utf-8")
    with pytest.raises(EvaluationDataError):
        load_ground_truth(empty)


def test_parse_case_type_checks() -> None:
    with pytest.raises(EvaluationDataError):
        parse_case({"id": "", "category": "c"}, line_number=1)
    with pytest.raises(EvaluationDataError):
        parse_case(
            {
                "id": "x",
                "category": "c",
                "question": "q",
                "expected_source_paths": "not-a-list",
                "should_refuse": False,
            },
            line_number=1,
        )
    with pytest.raises(EvaluationDataError):
        parse_case(
            {
                "id": "x",
                "category": "c",
                "question": "q",
                "expected_source_paths": [],
                "should_refuse": "no",
            },
            line_number=1,
        )


def test_select_cases_applies_documented_rule() -> None:
    cases = load_ground_truth(DEMO_GROUND_TRUTH)
    assert len(cases) == 55

    selection = select_cases(cases)
    assert len(selection.included) == 41
    assert len(selection.excluded) == 14
    # 8 条 negative（unanswerable + prompt_injection）与 6 条 planning
    assert selection.excluded_counts() == {"should_refuse": 8, "excluded_category": 6}

    # 纳入用例必须全部有正例来源，且不是 planning / should_refuse
    for case in selection.included:
        assert case.expected_source_paths
        assert case.should_refuse is False
        assert case.category != "planning"

    # 稳定排序：纳入与排除顺序都保持原文件顺序
    assert [case.case_id for case in selection.included][:3] == [
        "gt-single-001",
        "gt-single-002",
        "gt-single-003",
    ]
