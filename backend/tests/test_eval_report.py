"""阶段 9A 报告与门禁测试：schema/版本、profile 门禁选择、三层指标、CLI 一致性、失败分类。"""

from __future__ import annotations

import json

from eval_tools import SCHEMA_VERSION
from eval_tools.ground_truth import (
    ExcludedCase,
    GroundTruthCase,
    REASON_EXCLUDED_CATEGORY,
    REASON_SHOULD_REFUSE,
    SelectionResult,
)
from eval_tools.report import (
    ReportContext,
    build_cli_summary,
    build_report,
    exit_code,
    to_markdown,
)
from eval_tools.runner import (
    METRIC_FINAL_RECALL,
    METRIC_FUSION_RECALL,
    PROFILE_OFFLINE_FAKE,
    PROFILE_ONLINE_RERANK,
    SCORE_KIND_FAKE_DETERMINISTIC,
    SEMANTICS_GATE,
    SEMANTICS_NON_SEMANTIC,
    STAGE_FUSION,
    STAGE_FUSION_CUTOFF,
    STAGE_PARSE_OR_CHUNK,
    STAGE_RECALL,
    STAGE_RERANK_DEMOTED,
    STAGE_RERANK_PROMOTED,
    STAGE_RETAINED,
    CaseResult,
    SourceStage,
    aggregate,
    build_gate,
    classify_source_stage,
    resolve_profile,
)


def _case(case_id: str, category: str = "single_doc") -> GroundTruthCase:
    return GroundTruthCase(
        case_id=case_id,
        category=category,
        question=f"问题-{case_id}",
        expected_source_paths=("corpus/a.pdf",),
        should_refuse=False,
        conflict_expected=False,
    )


def _selection() -> SelectionResult:
    included = (_case("hit-1"), _case("miss-1"))
    excluded = (
        ExcludedCase(case=_case("refuse-1", "unanswerable"), reason=REASON_SHOULD_REFUSE),
        ExcludedCase(case=_case("plan-1", "planning"), reason=REASON_EXCLUDED_CATEGORY),
    )
    return SelectionResult(included=included, excluded=excluded)


def _hit_case() -> CaseResult:
    return CaseResult(
        case_id="hit-1",
        category="single_doc",
        question="问题-hit-1",
        expected_sources=("corpus/a.pdf",),
        matched_sources=("corpus/a.pdf",),
        retrieved_sources=("a.pdf", "b.pdf"),
        recall_at_k=1.0,
        reciprocal_rank=1.0,
        fusion_recall_at_k=1.0,
        fusion_reciprocal_rank=1.0,
        candidate_recall_at_k=1.0,
        hit=True,
        elapsed_ms=5.0,
        rerank_applied=True,
        dense_top=("a.pdf",),
        keyword_top=("a.pdf",),
        fused_candidates=("a.pdf", "b.pdf"),
        fused_top_k=("a.pdf", "b.pdf"),
        final_top_k=("a.pdf", "b.pdf"),
        source_stages=(
            SourceStage(
                source="corpus/a.pdf",
                dense_rank=1,
                keyword_rank=1,
                fused_rank=1,
                final_rank=1,
                stage=STAGE_RETAINED,
            ),
        ),
    )


def _miss_case(stage: str = STAGE_RERANK_DEMOTED) -> CaseResult:
    return CaseResult(
        case_id="miss-1",
        category="single_doc",
        question="问题-miss-1",
        expected_sources=("corpus/a.pdf",),
        matched_sources=(),
        retrieved_sources=("z.pdf",),
        recall_at_k=0.0,
        reciprocal_rank=0.0,
        fusion_recall_at_k=1.0,
        fusion_reciprocal_rank=1.0,
        candidate_recall_at_k=1.0,
        hit=False,
        elapsed_ms=15.0,
        rerank_applied=True,
        dense_top=("a.pdf",),
        keyword_top=("a.pdf",),
        fused_candidates=("a.pdf", "z.pdf"),
        fused_top_k=("a.pdf", "z.pdf"),
        final_top_k=("z.pdf",),
        source_stages=(
            SourceStage(
                source="corpus/a.pdf",
                dense_rank=1,
                keyword_rank=2,
                fused_rank=1,
                final_rank=None,
                stage=stage,
            ),
        ),
    )


def _context(profile=None) -> ReportContext:
    return ReportContext(
        run_id="run-test",
        ground_truth_name="ground_truth.jsonl",
        dataset_version="2026.1",
        top_k=5,
        candidate_k=20,
        embedding_provider="fake",
        rerank_provider="fake",
        profile=profile or resolve_profile(SCORE_KIND_FAKE_DETERMINISTIC),
        generated_at="2026-09-29T00:00:00+00:00",
    )


def _metrics(*, fusion_recall: float, final_recall: float, candidate_recall: float = 1.0) -> dict:
    """构造聚合指标字典（与 ``runner.aggregate`` 同结构），用于门禁纯逻辑测试。"""
    return {
        "top_k": 5,
        "candidate_k": 20,
        "cases": 2,
        "fully_hit_cases": 1,
        "candidate_recall_at_k": candidate_recall,
        "fusion_recall_at_k": fusion_recall,
        "fusion_mrr": 0.6,
        "recall_at_k": final_recall,
        "mrr": 0.4,
        "latency_ms": {"count": 2, "min": 5.0, "max": 15.0, "p50": 10.0, "p95": 14.5},
        "failure_stage_counts": {STAGE_RERANK_DEMOTED: 1},
        "source_stage_counts": {STAGE_RETAINED: 1, STAGE_RERANK_DEMOTED: 1},
    }


# --- 报告结构（schema 2.0） -------------------------------------------------


def test_build_report_structure_and_ordering() -> None:
    results = [_hit_case(), _miss_case()]
    metrics = aggregate(results, top_k=5)
    report = build_report(_context(), _selection(), results, metrics)

    assert report["schema_version"] == SCHEMA_VERSION == "rag-retrieval-eval/2.0"
    assert report["run_id"] == "run-test"
    assert report["config"]["profile"] == PROFILE_OFFLINE_FAKE
    assert report["config"]["rerank_score_kind"] == SCORE_KIND_FAKE_DETERMINISTIC

    # 门禁对象：profile / metric / value / threshold / passed / reason
    gate = report["gate"]
    assert set(gate) == {"profile", "metric", "value", "threshold", "passed", "reason"}
    assert gate["profile"] == PROFILE_OFFLINE_FAKE
    assert gate["metric"] == METRIC_FUSION_RECALL
    assert gate["value"] == 1.0
    assert gate["threshold"] == 0.85
    assert gate["passed"] is True
    assert gate["reason"]

    dataset = report["dataset"]
    assert dataset["total_cases"] == 4
    assert dataset["included_cases"] == 2
    assert dataset["excluded_cases"] == 2
    assert dataset["excluded_reasons"] == {"excluded_category": 1, "should_refuse": 1}
    assert dataset["excluded_case_ids"] == ["refuse-1", "plan-1"]

    # 三层指标被显式区分，0.5407 之类的 final 值不会被隐藏
    metrics_block = report["metrics"]
    assert metrics_block["candidate"]["recall_at_k"] == 1.0
    assert metrics_block["fusion"]["recall_at_k"] == 1.0
    assert metrics_block["fusion"]["mrr"] == 1.0
    assert metrics_block["final"]["recall_at_k"] == 0.5
    assert metrics_block["final"]["mrr"] == 0.5
    # offline_fake：融合为门禁，final 为非语义诊断
    assert metrics_block["fusion"]["semantics"] == SEMANTICS_GATE
    assert metrics_block["final"]["semantics"] == SEMANTICS_NON_SEMANTIC
    assert metrics_block["candidate"]["semantics"] != SEMANTICS_GATE

    assert [case["case_id"] for case in report["cases"]] == ["hit-1", "miss-1"]
    assert report["cases"][1]["sources"][0] == {
        "source": "corpus/a.pdf",
        "dense_rank": 1,
        "keyword_rank": 2,
        "fused_rank": 1,
        "final_rank": None,
        "stage": STAGE_RERANK_DEMOTED,
    }
    assert report["failures"]["stage_counts"] == {STAGE_RERANK_DEMOTED: 1}


def test_report_is_json_serializable_and_free_of_absolute_paths() -> None:
    results = [_hit_case(), _miss_case()]
    report = build_report(_context(), _selection(), results, aggregate(results, top_k=5))
    serialized = json.dumps(report, ensure_ascii=False)
    for forbidden in ("/app/", "F:\\", "C:\\", "sk-", "Bearer "):
        assert forbidden not in serialized


def test_markdown_mirrors_report_and_gate() -> None:
    results = [_hit_case(), _miss_case()]
    report = build_report(_context(), _selection(), results, aggregate(results, top_k=5))
    markdown = to_markdown(report)
    assert "# 离线 RAG 检索评测报告（阶段 9A）" in markdown
    assert "## 质量门禁" in markdown
    assert f"`{report['gate']['metric']}`" in markdown
    assert "三层指标" in markdown
    assert "run-test" in markdown
    assert "miss-1" in markdown


# --- profile 与门禁选择 -----------------------------------------------------


def test_fake_deterministic_selects_fusion_gate() -> None:
    profile = resolve_profile(SCORE_KIND_FAKE_DETERMINISTIC, threshold=0.85)
    assert profile.name == PROFILE_OFFLINE_FAKE
    assert profile.gate_metric == METRIC_FUSION_RECALL
    assert profile.rerank_semantic is False


def test_relevance_bearing_score_kind_selects_final_gate() -> None:
    for score_kind in ("crossencoder-sigmoid", "api-relevance-score"):
        profile = resolve_profile(score_kind, threshold=0.85)
        assert profile.name == PROFILE_ONLINE_RERANK
        assert profile.gate_metric == METRIC_FINAL_RECALL
        assert profile.rerank_semantic is True


def test_offline_fake_ignores_low_final_metric() -> None:
    """final Fake 指标低于阈值（0.54）不得导致 offline_fake 失败。"""
    metrics = _metrics(fusion_recall=0.874, final_recall=0.5407)
    report = build_report(_context(), _selection(), [_hit_case(), _miss_case()], metrics)

    assert report["metrics"]["final"]["recall_at_k"] == 0.5407  # 未被隐藏
    assert report["metrics"]["final"]["semantics"] == SEMANTICS_NON_SEMANTIC
    assert report["gate"]["metric"] == METRIC_FUSION_RECALL
    assert report["gate"]["value"] == 0.874
    assert report["gate"]["passed"] is True
    assert exit_code(report) == 0


def test_fusion_below_threshold_still_fails() -> None:
    """融合指标低于阈值必须失败，即使 final 指标很高。"""
    metrics = _metrics(fusion_recall=0.80, final_recall=0.99)
    report = build_report(_context(), _selection(), [_hit_case(), _miss_case()], metrics)

    assert report["gate"]["metric"] == METRIC_FUSION_RECALL
    assert report["gate"]["value"] == 0.80
    assert report["gate"]["passed"] is False
    assert exit_code(report) == 1


def test_online_rerank_gate_uses_final_and_can_fail() -> None:
    profile = resolve_profile("crossencoder-sigmoid", threshold=0.85)
    passing = build_report(
        _context(profile), _selection(), [_hit_case()], _metrics(fusion_recall=0.80, final_recall=0.90)
    )
    assert passing["gate"]["metric"] == METRIC_FINAL_RECALL
    assert passing["metrics"]["final"]["semantics"] == SEMANTICS_GATE
    assert passing["gate"]["passed"] is True
    assert exit_code(passing) == 0

    failing = build_report(
        _context(profile), _selection(), [_hit_case()], _metrics(fusion_recall=0.99, final_recall=0.80)
    )
    assert failing["gate"]["passed"] is False
    assert exit_code(failing) == 1


def test_build_gate_reads_real_metric_values() -> None:
    metrics = _metrics(fusion_recall=0.874, final_recall=0.5407)
    offline = build_gate(resolve_profile(SCORE_KIND_FAKE_DETERMINISTIC), metrics)
    online = build_gate(resolve_profile("api-relevance-score"), metrics)
    assert offline["value"] == 0.874 and offline["passed"] is True
    assert online["value"] == 0.5407 and online["passed"] is False


def test_cli_summary_matches_report_gate_and_markdown() -> None:
    metrics = _metrics(fusion_recall=0.874, final_recall=0.5407)
    report = build_report(_context(), _selection(), [_hit_case()], metrics)
    summary = build_cli_summary(
        report, json_report="retrieval-eval.json", markdown_report="retrieval-eval.md"
    )
    markdown = to_markdown(report)

    # gate、CLI、JSON、Markdown 同源一致
    assert summary["gate"] == report["gate"]
    assert summary["profile"] == report["gate"]["profile"]
    assert summary["fusion_recall_at_k"] == report["metrics"]["fusion"]["recall_at_k"]
    assert summary["final_recall_at_k"] == report["metrics"]["final"]["recall_at_k"]
    assert summary["final_semantics"] == SEMANTICS_NON_SEMANTIC
    assert f"- 结果：{'通过' if report['gate']['passed'] else '未通过'}" in markdown
    assert f"- 实测值：{report['gate']['value']}（阈值 {report['gate']['threshold']}）" in markdown


# --- 阶段归类（精确规则） ---------------------------------------------------


def test_classify_source_stage_precedence() -> None:
    assert (
        classify_source_stage(
            indexed=False, in_dense=False, in_keyword=False, fused_rank=None, final_rank=None
        )
        == STAGE_PARSE_OR_CHUNK
    )
    assert (
        classify_source_stage(
            indexed=True, in_dense=False, in_keyword=False, fused_rank=None, final_rank=None
        )
        == STAGE_RECALL
    )
    assert (
        classify_source_stage(
            indexed=True, in_dense=True, in_keyword=False, fused_rank=None, final_rank=None
        )
        == STAGE_FUSION
    )
    assert (
        classify_source_stage(
            indexed=True, in_dense=True, in_keyword=True, fused_rank=7, final_rank=None
        )
        == STAGE_FUSION_CUTOFF
    )
    assert (
        classify_source_stage(
            indexed=True, in_dense=True, in_keyword=True, fused_rank=3, final_rank=None
        )
        == STAGE_RERANK_DEMOTED
    )
    assert (
        classify_source_stage(
            indexed=True, in_dense=True, in_keyword=True, fused_rank=6, final_rank=2
        )
        == STAGE_RERANK_PROMOTED
    )
    assert (
        classify_source_stage(
            indexed=True, in_dense=True, in_keyword=True, fused_rank=2, final_rank=1
        )
        == STAGE_RETAINED
    )
