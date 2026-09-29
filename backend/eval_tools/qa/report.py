"""9B 报告：JSON + Markdown + CLI 摘要（与两层门禁同源）。

不包含文档正文、答案全文、密钥或绝对路径；逐案例只保留 outcome / reason_code /
引用数量 / 结构判定 / 阶段标签与失败原因摘要。

措辞约束：只允许写「deterministic 子门禁通过」与「阶段 9B 整体 incomplete/deferred」，
不得出现「阶段 9B 通过」或把观测诊断值当作质量通过的表述。
"""

from __future__ import annotations

from dataclasses import dataclass

from eval_tools.qa import SCHEMA_VERSION
from eval_tools.qa.metrics import (
    GROUP_CITATION,
    GROUP_INJECTION_SAFETY,
    GROUP_PLANNING,
    GROUP_REFUSAL_CONTRACT,
    QaCaseResult,
    SEMANTIC_METRICS,
)

ROUND_DIGITS = 4

GROUP_LABELS = {
    GROUP_PLANNING: "学分规划（确定性规则引擎）",
    GROUP_REFUSAL_CONTRACT: "拒答（需 LLM 语义判定证据不足）",
    GROUP_CITATION: "引用（结构可测；语义支持需 LLM）",
    GROUP_INJECTION_SAFETY: "提示注入抵抗（需 LLM 语义）",
}

GROUP_SEMANTICS = {
    GROUP_PLANNING: "deterministic",
    GROUP_REFUSAL_CONTRACT: "llm_semantic",
    GROUP_CITATION: "structural+llm_semantic",
    GROUP_INJECTION_SAFETY: "llm_semantic",
}


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), ROUND_DIGITS)


@dataclass(frozen=True)
class QaReportContext:
    run_id: str
    ground_truth_name: str
    dataset_version: str
    embedding_provider: str
    rerank_provider: str
    generated_at: str


def _case_payload(item: QaCaseResult) -> dict:
    return {
        "case_id": item.case_id,
        "group": item.group,
        "question": item.question,
        "observed_pass": item.observed_pass,
        "outcome": item.outcome,
        "reason_code": item.reason_code,
        "citation_count": item.citation_count,
        "citation_contract_ok": item.citation_contract_ok,
        "citation_source_hit": item.citation_source_hit,
        "failure_stage": item.failure_stage,
        "note": item.note,
    }


def build_qa_report(
    context: QaReportContext,
    eligibility,
    gate: dict,
    results: list[QaCaseResult],
    metrics: dict,
    total_cases: int,
) -> dict:
    failed = [item for item in results if not item.observed_pass]
    deferred = list(gate["stage_completion"]["deferred_metrics"])
    statements = [
        (
            "deterministic 子门禁：planning_correctness 通过"
            if gate["deterministic"]["passed"]
            else "deterministic 子门禁：planning_correctness 未通过"
        ),
        (
            "阶段 9B 整体 incomplete/deferred："
            + "、".join(deferred)
            + " 尚未评测（Provider 不具备语义评测资格）"
        )
        if deferred
        else "阶段 9B 必需指标全部具备资格。",
        "本报告不得被解读为「阶段 9B 通过」；观测诊断值不是质量结论。",
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": context.run_id,
        "generated_at": context.generated_at,
        "config": {
            "dataset_version": context.dataset_version,
            "embedding_provider": context.embedding_provider,
            "rerank_provider": context.rerank_provider,
            "llm_provider": eligibility.provider,
            "llm_model": eligibility.model,
            "network_access": False,
            "model_download": False,
        },
        "llm_eligibility": {
            "provider": eligibility.provider,
            "model": eligibility.model,
            "semantic": eligibility.semantic,
            "explicit_profile": eligibility.explicit_profile,
            "verified_call": eligibility.verified_call,
            "reason": eligibility.reason,
        },
        "gate": gate,
        "status": {
            "deterministic_gate_passed": gate["deterministic"]["passed"],
            "stage_completion": gate["stage_completion"]["status"],
            "stage9b_passed": gate["passed"],
            "not_yet_evaluated": deferred,
            "statements": statements,
        },
        "dataset": {
            "ground_truth_file": context.ground_truth_name,
            "total_cases": total_cases,
            "scored_cases": len(results),
            "groups": {
                GROUP_CITATION: metrics["citation"]["cases"],
                GROUP_REFUSAL_CONTRACT: metrics["refusal"]["cases"],
                GROUP_PLANNING: metrics["planning"]["cases"],
                GROUP_INJECTION_SAFETY: metrics["injection"]["cases"],
            },
            "group_semantics": dict(GROUP_SEMANTICS),
        },
        "metrics": {
            "cases": metrics["cases"],
            "planning_correctness": _round(metrics["planning"]["planning_correctness"]),
            "citation_contract_rate": _round(metrics["citation"]["citation_contract_rate"]),
            "citation_source_hit_rate": _round(metrics["citation"]["citation_source_hit_rate"]),
            "citation_support_rate": None,
            "refusal_observed_rate": _round(metrics["refusal"]["refusal_observed_rate"]),
            "refusal_correctness": None,
            "injection_observed_rate": _round(metrics["injection"]["injection_observed_rate"]),
            "injection_resistance": None,
            "failure_stage_counts": metrics["failure_stage_counts"],
            "semantic_metrics_not_evaluated": list(SEMANTIC_METRICS),
        },
        "failures": {
            "count": len(failed),
            "stage_counts": metrics["failure_stage_counts"],
            "scope": "观测失败（用于定位），不代表质量结论",
            "cases": [_case_payload(item) for item in failed],
        },
        "cases": [_case_payload(item) for item in results],
    }


def build_qa_cli_summary(report: dict, *, json_report: str, markdown_report: str) -> dict:
    metrics = report["metrics"]
    return {
        "run_id": report["run_id"],
        "schema_version": report["schema_version"],
        "profile": report["gate"]["profile"],
        "llm_provider": report["gate"]["llm_provider"],
        "llm_semantic": report["gate"]["llm_semantic"],
        "deterministic_gate_passed": report["status"]["deterministic_gate_passed"],
        "stage_completion": report["status"]["stage_completion"],
        "stage9b_passed": report["status"]["stage9b_passed"],
        "exit_code": report["gate"]["exit_code"],
        "not_yet_evaluated": report["status"]["not_yet_evaluated"],
        "planning_correctness": metrics["planning_correctness"],
        "citation_contract_rate": metrics["citation_contract_rate"],
        "citation_source_hit_rate": metrics["citation_source_hit_rate"],
        "citation_support_rate": metrics["citation_support_rate"],
        "refusal_observed_rate": metrics["refusal_observed_rate"],
        "refusal_correctness": metrics["refusal_correctness"],
        "injection_observed_rate": metrics["injection_observed_rate"],
        "injection_resistance": metrics["injection_resistance"],
        "cases": metrics["cases"],
        "failure_stage_counts": metrics["failure_stage_counts"],
        "statements": report["status"]["statements"],
        "json_report": json_report,
        "markdown_report": markdown_report,
    }


def qa_exit_code(report: dict) -> int:
    return int(report["gate"]["exit_code"])


def to_markdown(report: dict) -> str:
    gate = report["gate"]
    metrics = report["metrics"]
    dataset = report["dataset"]
    eligibility = report["llm_eligibility"]
    status = report["status"]

    lines: list[str] = []
    lines.append("# 阶段 9B 问答与学业评测报告")
    lines.append("")
    lines.append(f"- schema：`{report['schema_version']}`")
    lines.append(f"- run id：`{report['run_id']}`")
    lines.append(f"- 生成时间：{report['generated_at']}")
    lines.append(
        f"- LLM：provider=`{eligibility['provider']}`、model=`{eligibility['model']}`、"
        f"semantic=`{eligibility['semantic']}`（explicit_profile="
        f"`{eligibility['explicit_profile']}`、verified_call=`{eligibility['verified_call']}`）"
    )
    lines.append(f"- 联网={report['config']['network_access']}、下载模型={report['config']['model_download']}")
    lines.append("")

    lines.append("## 结论（勿误读）")
    lines.append("")
    lines.append(f"- deterministic 子门禁通过：**{status['deterministic_gate_passed']}**")
    lines.append(f"- 阶段 9B 整体状态：**{status['stage_completion']}**")
    lines.append(f"- 是否可判定阶段 9B 通过：**{status['stage9b_passed']}**")
    for statement in status["statements"]:
        lines.append(f"- {statement}")
    lines.append("")
    lines.append("> 本报告**不得**被解读为「阶段 9B 通过」。")
    lines.append("")

    lines.append("## LLM 语义评测资格")
    lines.append("")
    lines.append(eligibility["reason"])
    lines.append("")

    lines.append("## 两层门禁")
    lines.append("")
    lines.append(f"- profile：`{gate['profile']}`")
    lines.append(f"- deterministic gate 指标：{gate['deterministic']['metrics']}；"
                 f"通过：**{gate['deterministic']['passed']}**")
    lines.append(
        f"- stage completion gate：必需指标 {gate['stage_completion']['required_metrics']}；"
        f"状态 **{gate['stage_completion']['status']}**；"
        f"deferred={gate['stage_completion']['deferred_metrics']}；"
        f"failed={gate['stage_completion']['failed_metrics']}"
    )
    lines.append(f"- 退出码语义：0=全部必需指标具备资格且通过；1=具备资格的必需指标未达标；"
                 f"2=存在必需指标 deferred。本次 exit_code=**{gate['exit_code']}**")
    lines.append("")
    lines.append("| 必需指标 | 值 | 阈值 | 语义 | 资格 | 状态 |")
    lines.append("|---|---|---|---|---|---|")
    for item in gate["metrics"]:
        lines.append(
            f"| {item['metric']} | {item['value']} | {item['threshold']} | {item['semantics']} | "
            f"{'是' if item['eligible'] else '否（deferred）'} | {item['status']} |"
        )
    lines.append("")

    lines.append("## 样本口径")
    lines.append("")
    lines.append(f"- 总用例：{dataset['total_cases']}；已评分：{dataset['scored_cases']}")
    for group, count in dataset["groups"].items():
        lines.append(f"  - {GROUP_LABELS.get(group, group)}：{count}（{dataset['group_semantics'][group]}）")
    lines.append("")

    lines.append("## 指标（结构实测 vs 正式质量）")
    lines.append("")
    lines.append("| 指标 | 值 | 性质 |")
    lines.append("|---|---|---|")
    lines.append(f"| planning_correctness | {metrics['planning_correctness']} | 正式（确定性门禁） |")
    lines.append(f"| citation_contract_rate | {metrics['citation_contract_rate']} | 结构实测（引用接线） |")
    lines.append(f"| citation_source_hit_rate | {metrics['citation_source_hit_rate']} | 结构实测（来源命中） |")
    lines.append(f"| citation_support_rate | {metrics['citation_support_rate']} | **正式，未评测（deferred）** |")
    lines.append(f"| refusal_observed_rate | {metrics['refusal_observed_rate']} | 观测诊断，非质量结论 |")
    lines.append(f"| refusal_correctness | {metrics['refusal_correctness']} | **正式，未评测（deferred）** |")
    lines.append(f"| injection_observed_rate | {metrics['injection_observed_rate']} | 观测诊断，非质量结论 |")
    lines.append(f"| injection_resistance | {metrics['injection_resistance']} | **正式，未评测（deferred）** |")
    lines.append("")
    lines.append(f"- 观测失败阶段分布：{metrics['failure_stage_counts'] or '{}'}")
    lines.append("")

    lines.append("## 逐案例观测结果")
    lines.append("")
    lines.append("| 用例 | 分组 | 观测通过 | outcome | reason_code | 引用数 | 失败阶段 |")
    lines.append("|---|---|---|---|---|---|---|")
    for case in report["cases"]:
        lines.append(
            f"| {case['case_id']} | {case['group']} | {'是' if case['observed_pass'] else '否'} | "
            f"{case['outcome']} | {case['reason_code']} | {case['citation_count']} | "
            f"{case['failure_stage'] or '-'} |"
        )
    lines.append("")

    lines.append("## 观测失败明细")
    lines.append("")
    if not report["failures"]["cases"]:
        lines.append("无观测失败用例。")
    else:
        for case in report["failures"]["cases"]:
            lines.append(
                f"- `{case['case_id']}`（{case['group']}）：{case['failure_stage']} — {case['note']}"
            )
    lines.append("")
    return "\n".join(lines)


__all__ = [
    "GROUP_LABELS",
    "GROUP_SEMANTICS",
    "QaReportContext",
    "build_qa_cli_summary",
    "build_qa_report",
    "qa_exit_code",
    "to_markdown",
]
