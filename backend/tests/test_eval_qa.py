"""阶段 9B 测试：schema、字段命名、资格判定、两层门禁、退出码 0/1/2、报告一致性、真实链路。"""

from __future__ import annotations

import json
from pathlib import Path

from eval_tools.pipeline import EvalEnvironment
from eval_tools.qa import SCHEMA_VERSION
from eval_tools.qa.executor import evaluate_cases
from eval_tools.qa.metrics import (
    EXIT_DEFERRED,
    EXIT_FAILED,
    EXIT_OK,
    GROUP_CITATION,
    GROUP_INJECTION_SAFETY,
    GROUP_PLANNING,
    GROUP_REFUSAL_CONTRACT,
    PROFILE_OFFLINE_FAKE_QA,
    PROFILE_SEMANTIC_QA,
    STATUS_DEFERRED,
    QaCaseResult,
    aggregate_qa,
    build_qa_gate,
    classify_case_group,
    resolve_llm_eligibility,
    resolve_qa_profile,
)
from eval_tools.qa.report import (
    QaReportContext,
    build_qa_cli_summary,
    build_qa_report,
    qa_exit_code,
    to_markdown,
)

GROUND_TRUTH = Path("/app/demo/ground_truth.jsonl")


def _raw_cases() -> list[dict]:
    return [
        json.loads(line)
        for line in GROUND_TRUTH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _fake_eligibility():
    return resolve_llm_eligibility("fake", "fake-grounded-v1")


def _semantic_eligibility():
    return resolve_llm_eligibility(
        "openai_compatible", "gpt-x", explicit_profile=True, verified_call=True
    )


def _result(
    case_id: str,
    group: str,
    observed: bool,
    stage: str | None = None,
    *,
    contract: bool | None = None,
    source_hit: bool | None = None,
) -> QaCaseResult:
    is_citation = group == GROUP_CITATION
    return QaCaseResult(
        case_id=case_id,
        group=group,
        question=f"q-{case_id}",
        observed_pass=observed,
        outcome="answered" if observed else "refused",
        reason_code=None,
        citation_count=1,
        citation_contract_ok=((observed if contract is None else contract) if is_citation else None),
        citation_source_hit=((observed if source_hit is None else source_hit) if is_citation else None),
        failure_stage=stage,
        note=None if observed else "详情",
    )


def _gate_metrics(
    *,
    planning: float | None = 1.0,
    citation_support: float | None = None,
    refusal: float | None = None,
    injection: float | None = None,
    contract: float | None = 1.0,
    source_hit: float | None = 1.0,
    refusal_observed: float | None = 0.0,
    injection_observed: float | None = 0.0,
    failure_stage_counts: dict | None = None,
) -> dict:
    """直接构造聚合指标字典（结构与 ``aggregate_qa`` 一致），用于门禁纯逻辑测试。"""
    return {
        "cases": 4,
        "planning": {"cases": 1, "planning_correctness": planning},
        "citation": {
            "cases": 1,
            "citation_contract_rate": contract,
            "citation_source_hit_rate": source_hit,
            "citation_support_rate": citation_support,
        },
        "refusal": {"cases": 1, "refusal_observed_rate": refusal_observed, "refusal_correctness": refusal},
        "injection": {
            "cases": 1,
            "injection_observed_rate": injection_observed,
            "injection_resistance": injection,
        },
        "failure_stage_counts": failure_stage_counts or {},
    }


def _report(eligibility, gate, results, metrics):
    return build_qa_report(
        QaReportContext(
            run_id="run-test",
            ground_truth_name="ground_truth.jsonl",
            dataset_version="2026.1",
            embedding_provider="fake",
            rerank_provider="fake",
            generated_at="2026-09-29T00:00:00+00:00",
        ),
        eligibility,
        gate,
        results,
        metrics,
        total_cases=55,
    )


# --- LLM 评测资格 -----------------------------------------------------------


def test_fake_llm_is_not_semantically_eligible() -> None:
    eligibility = _fake_eligibility()
    assert eligibility.semantic is False
    assert eligibility.explicit_profile is False
    assert eligibility.verified_call is False
    assert resolve_qa_profile(eligibility).name == PROFILE_OFFLINE_FAKE_QA


def test_non_fake_provider_is_not_automatically_semantic() -> None:
    """仅 provider 名称非 fake 不得自动获得语义资格。"""
    eligibility = resolve_llm_eligibility("openai_compatible", "gpt-x")
    assert eligibility.semantic is False
    assert eligibility.explicit_profile is False


def test_explicit_profile_without_verified_call_is_not_eligible() -> None:
    eligibility = resolve_llm_eligibility(
        "openai_compatible", "gpt-x", explicit_profile=True, verified_call=False
    )
    assert eligibility.semantic is False


def test_explicit_profile_with_verified_call_is_eligible() -> None:
    eligibility = _semantic_eligibility()
    assert eligibility.semantic is True
    assert resolve_qa_profile(eligibility).name == PROFILE_SEMANTIC_QA


# --- 分组 -------------------------------------------------------------------


def test_classify_case_group() -> None:
    cases = {case["id"]: case for case in _raw_cases()}
    assert classify_case_group(cases["gt-plan-001"]) == GROUP_PLANNING
    assert classify_case_group(cases["gt-refuse-001"]) == GROUP_REFUSAL_CONTRACT
    assert classify_case_group(cases["gt-injection-001"]) == GROUP_INJECTION_SAFETY
    assert classify_case_group(cases["gt-single-001"]) == GROUP_CITATION
    groups = [classify_case_group(case) for case in cases.values()]
    assert len(groups) == 55
    assert groups.count(GROUP_PLANNING) == 6
    assert groups.count(GROUP_REFUSAL_CONTRACT) == 5
    assert groups.count(GROUP_INJECTION_SAFETY) == 3
    assert groups.count(GROUP_CITATION) == 41


# --- 指标与两层门禁 ---------------------------------------------------------


def test_aggregate_separates_structural_and_official_metrics() -> None:
    results = [
        _result("c1", GROUP_CITATION, True),
        _result(
            "c2",
            GROUP_CITATION,
            False,
            "citation_contract_failure",
            contract=False,
            source_hit=True,
        ),
        _result("r1", GROUP_REFUSAL_CONTRACT, True),
        _result("p1", GROUP_PLANNING, True),
        _result("i1", GROUP_INJECTION_SAFETY, False, "llm_safety_semantics"),
    ]
    metrics = aggregate_qa(results)
    # 结构实测
    assert metrics["citation"]["citation_contract_rate"] == 0.5
    assert metrics["citation"]["citation_source_hit_rate"] == 1.0
    assert metrics["planning"]["planning_correctness"] == 1.0
    # 观测诊断
    assert metrics["refusal"]["refusal_observed_rate"] == 1.0
    assert metrics["injection"]["injection_observed_rate"] == 0.0
    # 正式质量指标：非语义 Provider 下必须为 None
    assert metrics["citation"]["citation_support_rate"] is None
    assert metrics["refusal"]["refusal_correctness"] is None
    assert metrics["injection"]["injection_resistance"] is None
    assert metrics["failure_stage_counts"] == {
        "citation_contract_failure": 1,
        "llm_safety_semantics": 1,
    }


def test_offline_fake_is_deferred_with_exit_code_2() -> None:
    gate = build_qa_gate(resolve_qa_profile(_fake_eligibility()), _gate_metrics())
    assert gate["deterministic"]["metrics"] == ["planning_correctness"]
    assert gate["deterministic"]["passed"] is True
    assert gate["stage_completion"]["status"] == "incomplete"
    assert gate["stage_completion"]["passed"] is False
    assert set(gate["stage_completion"]["deferred_metrics"]) == {
        "citation_support_rate",
        "refusal_correctness",
        "injection_resistance",
    }
    assert gate["passed"] is False  # 顶层不得为 true
    assert gate["exit_code"] == EXIT_DEFERRED == 2
    deferred = [item for item in gate["metrics"] if item["status"] == STATUS_DEFERRED]
    assert len(deferred) == 3
    assert all(item["value"] is None for item in deferred)


def test_semantic_profile_fails_with_exit_code_1() -> None:
    gate = build_qa_gate(
        resolve_qa_profile(_semantic_eligibility()),
        _gate_metrics(planning=1.0, citation_support=0.5, refusal=1.0, injection=1.0),
    )
    assert gate["stage_completion"]["failed_metrics"] == ["citation_support_rate"]
    assert gate["exit_code"] == EXIT_FAILED == 1
    assert gate["passed"] is False


def test_semantic_profile_passes_with_exit_code_0() -> None:
    gate = build_qa_gate(
        resolve_qa_profile(_semantic_eligibility()),
        _gate_metrics(planning=1.0, citation_support=0.95, refusal=1.0, injection=1.0),
    )
    assert gate["stage_completion"]["status"] == "complete"
    assert gate["passed"] is True
    assert gate["exit_code"] == EXIT_OK == 0


def test_planning_failure_still_fails_deterministic_gate() -> None:
    gate = build_qa_gate(resolve_qa_profile(_fake_eligibility()), _gate_metrics(planning=0.5))
    assert gate["deterministic"]["passed"] is False
    assert gate["exit_code"] == EXIT_DEFERRED  # 同时存在 deferred，仍按 2 处理


# --- 报告一致性 -------------------------------------------------------------


def test_report_states_incomplete_and_null_official_metrics() -> None:
    eligibility = _fake_eligibility()
    results = [
        _result("gt-single-001", GROUP_CITATION, True),
        _result("gt-refuse-001", GROUP_REFUSAL_CONTRACT, False, "refusal_not_triggered"),
        _result("gt-plan-001", GROUP_PLANNING, True),
        _result("gt-injection-001", GROUP_INJECTION_SAFETY, False, "llm_safety_semantics"),
    ]
    metrics = aggregate_qa(results)
    gate = build_qa_gate(resolve_qa_profile(eligibility), metrics)
    report = _report(eligibility, gate, results, metrics)

    assert report["schema_version"] == SCHEMA_VERSION == "rag-qa-eval/2.0"
    status = report["status"]
    assert status["deterministic_gate_passed"] is True
    assert status["stage_completion"] == "incomplete"
    assert status["stage9b_passed"] is False
    assert set(status["not_yet_evaluated"]) == {
        "citation_support_rate",
        "refusal_correctness",
        "injection_resistance",
    }
    assert any("阶段 9B 整体 incomplete/deferred" in item for item in status["statements"])
    assert any("不得被解读" in item for item in status["statements"])

    payload = report["metrics"]
    assert payload["planning_correctness"] == 1.0
    assert payload["citation_contract_rate"] == 1.0
    assert payload["citation_source_hit_rate"] == 1.0
    assert payload["citation_support_rate"] is None
    assert payload["refusal_correctness"] is None
    assert payload["injection_resistance"] is None
    assert payload["refusal_observed_rate"] == 0.0
    assert payload["injection_observed_rate"] == 0.0

    serialized = json.dumps(report, ensure_ascii=False)
    assert '"stage9b_passed": false' in serialized
    for forbidden in ("/app/", "F:\\", "C:\\", "sk-", "Bearer "):
        assert forbidden not in serialized

    summary = build_qa_cli_summary(
        report, json_report="qa-eval.json", markdown_report="qa-eval.md"
    )
    assert summary["exit_code"] == report["gate"]["exit_code"] == 2
    assert summary["stage9b_passed"] is False
    assert summary["stage_completion"] == "incomplete"
    assert summary["citation_support_rate"] is None
    assert summary["refusal_correctness"] is None
    assert summary["injection_resistance"] is None
    assert summary["statements"] == report["status"]["statements"]
    assert qa_exit_code(report) == 2

    markdown = to_markdown(report)
    assert "阶段 9B 问答与学业评测报告" in markdown
    assert "结论（勿误读）" in markdown
    assert "阶段 9B 整体状态：**incomplete**" in markdown
    assert "是否可判定阶段 9B 通过：**False**" in markdown
    assert "本报告**不得**被解读为「阶段 9B 通过」。" in markdown
    assert "| citation_support_rate | None |" in markdown
    assert "| refusal_correctness | None |" in markdown
    assert "| injection_resistance | None |" in markdown


# --- 真实链路集成 -----------------------------------------------------------


def test_qa_eval_on_real_pipeline(ingest_demo, context, worker, reranker) -> None:
    ingest_demo()
    environment = EvalEnvironment(
        settings=context.settings,
        session_factory=context.session_factory,
        worker=worker,
        reranker=reranker,
        chain=None,  # 9B 不使用检索链对象
    )
    results = evaluate_cases(environment, _raw_cases())
    assert len(results) == 55

    metrics = aggregate_qa(results)
    eligibility = _fake_eligibility()
    gate = build_qa_gate(resolve_qa_profile(eligibility), metrics)

    # 确定性指标可实测且通过
    assert metrics["planning"]["planning_correctness"] == 1.0
    # 结构实测指标必须有值（具体数值由真实检索结果决定，不在此写死）
    contract_rate = metrics["citation"]["citation_contract_rate"]
    source_hit_rate = metrics["citation"]["citation_source_hit_rate"]
    assert contract_rate is not None and 0.0 <= contract_rate <= 1.0
    assert source_hit_rate is not None and 0.0 <= source_hit_rate <= 1.0
    # 正式语义指标必须为 None（未评测）
    assert metrics["citation"]["citation_support_rate"] is None
    assert metrics["refusal"]["refusal_correctness"] is None
    assert metrics["injection"]["injection_resistance"] is None
    # 观测诊断值仍被记录（0.0，仅用于定位）
    assert metrics["refusal"]["refusal_observed_rate"] == 0.0
    assert metrics["injection"]["injection_observed_rate"] == 0.0

    # 两层门禁：deterministic 通过，但阶段整体 incomplete，退出码 2
    assert gate["deterministic"]["passed"] is True
    assert gate["stage_completion"]["status"] == "incomplete"
    assert gate["passed"] is False
    assert gate["exit_code"] == 2

    report = _report(eligibility, gate, results, metrics)
    assert report["status"]["stage9b_passed"] is False
    assert report["dataset"]["scored_cases"] == 55
    assert qa_exit_code(report) == 2
