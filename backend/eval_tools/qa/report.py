"""9B 报告：JSON + Markdown + CLI 摘要（与两层门禁同源）。

不包含文档正文、答案全文、``quote``、密钥或绝对路径；逐案例只保留 outcome / reason_code /
引用数量 / 结构判定 / 正式判定字段（布尔与阶段标签）/ 执行安全诊断字段（``done`` /
``error_code`` / ``error_reason`` / ``judge_reason`` 稳定标签）/ 冲突安全诊断字段
（``conflict_*`` 稳定标签与计数、``cross_version_intent`` 与
``question_field_exact_match`` 布尔）/ 失败原因摘要。
Provider 审计只记录四态、calls/ok/failed 与全局 failure_reason_counts（安全标签/异常类名）；
隔离快照只记录计数与稳定摘要。

措辞约束：只允许按门禁结果**动态**表述；不得出现「阶段 9B 通过」，
除非 ``stage9b_passed=true`` 且 ``exit_code=0``。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from eval_tools.qa import SCHEMA_VERSION
from eval_tools.qa.judge import JUDGE_CONTRACT_VERSION
from eval_tools.qa.metrics import (
    GROUP_CITATION,
    GROUP_INJECTION_SAFETY,
    GROUP_PLANNING,
    GROUP_REFUSAL_CONTRACT,
    SEMANTIC_METRICS,
    STATUS_DEFERRED,
    ProviderAudit,
    QaCaseResult,
    embedding_failure_summary,
    rerank_degradation_summary,
    safety_summary,
)
from eval_tools.qa.sideeffects import IsolationSnapshot, SideEffectAssessment

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
    # 是否发生外部网络访问：由调用方**显式**声明（offline_fake=False、semantic_api=True），
    # 不得写死，以免报告对「是否联网」作出错误陈述。
    network_access: bool = False


def _diagnostic_rows(rows: tuple[dict, ...] | None) -> list[dict] | None:
    """把诊断行转为 JSON 安全的 ``list[dict]``（元组取值统一转 list）。

    行内只允许出现摘要、布尔、计数与白名单标签；本函数不做任何内容清洗之外的处理。
    """
    if rows is None:
        return None
    return [
        {
            key: (list(value) if isinstance(value, tuple) else value)
            for key, value in row.items()
        }
        for row in rows
    ]


def _anchor_payload(diagnostic: dict | None) -> dict | None:
    """答案事实锚点诊断的 JSON 安全载荷：``facts`` 元组转 list，其余只含 bool/int。"""
    if diagnostic is None:
        return None
    payload = dict(diagnostic)
    facts = payload.get("facts")
    if isinstance(facts, tuple):
        payload["facts"] = [dict(row) if isinstance(row, dict) else row for row in facts]
    return payload


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
        # 正式判定字段（仅布尔 / 阶段 / 安全标签）
        "formal_pass": item.formal_pass,
        "formal_stage": item.formal_stage,
        "d1_sources_ok": item.d1_sources_ok,
        "d2_locators_ok": item.d2_locators_ok,
        "judge_status": item.judge_status,
        # 重排降级安全诊断（仅布尔与稳定小写标签；不含异常正文、URL 或密钥）
        "rerank_applied": item.rerank_applied,
        "rerank_degraded": item.rerank_degraded,
        "rerank_degraded_reason": item.rerank_degraded_reason,
        # Embedding 失败安全诊断（仅布尔与稳定小写标签；不含异常正文、URL 或密钥）
        "embedding_failed": item.embedding_failed,
        "embedding_failure_reason": item.embedding_failure_reason,
        # 执行安全诊断字段（仅稳定标签；answer / quote / 异常原文一律不落盘）
        "done": item.done,
        "error_code": item.error_code,
        "error_reason": item.error_reason,
        "judge_reason": item.judge_reason,
        # 冲突安全诊断（仅稳定标签/计数/布尔；不含字段名、证据正文或路径）
        "conflict_detected": item.conflict_detected,
        "conflict_activated": item.conflict_activated,
        "cross_version_intent": item.cross_version_intent,
        "row_slot_intent": item.row_slot_intent,
        "conflict_suppression_reason": item.conflict_suppression_reason,
        "conflict_signals": (
            list(item.conflict_signals) if item.conflict_signals is not None else None
        ),
        "conflict_indices": (
            list(item.conflict_indices) if item.conflict_indices is not None else None
        ),
        "conflict_field_count": item.conflict_field_count,
        "conflict_field_digest": item.conflict_field_digest,
        "conflict_top_k": item.conflict_top_k,
        "conflict_version_count": item.conflict_version_count,
        "conflict_model_outcome": item.conflict_model_outcome,
        "conflict_forced": item.conflict_forced,
        "question_field_exact_match": item.question_field_exact_match,
        # 9B citation 失败的最小安全诊断（仅摘要/布尔/计数/白名单字段标签）
        "retrieval_coverage": _diagnostic_rows(item.retrieval_coverage),
        "citation_coverage": _diagnostic_rows(item.citation_coverage),
        # 实际引用的选择构成：仅 citation_index/final_rank/两个摘要；非 citation 组为 null
        "citation_selection": _diagnostic_rows(item.citation_selection),
        # 逐证据组安全覆盖：仅 group_index/covered/matched_alternative；非 citation 组为 null
        "group_coverage": _diagnostic_rows(item.group_coverage),
        "locator_diagnostics": _diagnostic_rows(item.locator_diagnostics),
        "retrieval_locator_coverage": _diagnostic_rows(item.retrieval_locator_coverage),
        "retrieval_stage_coverage": _diagnostic_rows(item.retrieval_stage_coverage),
        "reranked_composition": _diagnostic_rows(item.reranked_composition),
        "reranked_composition_summary": item.reranked_composition_summary,
        "final_composition": _diagnostic_rows(item.final_composition),
        "final_composition_summary": item.final_composition_summary,
        "judge_facts": _diagnostic_rows(item.judge_facts),
        # 答案事实锚点安全诊断（仅 ordinal/布尔/计数；不含 fact/answer/quote/锚点值/摘要）
        "fact_anchor_diagnostics": _anchor_payload(item.fact_anchor_diagnostics),
        "expected_source_count": item.expected_source_count,
        "recalled_source_count": item.recalled_source_count,
        "cited_source_count": item.cited_source_count,
        "expected_locator_count": item.expected_locator_count,
        "recalled_locator_count": item.recalled_locator_count,
        "failure_stage": item.failure_stage,
        "note": item.note,
    }


def _provider_payload(audit: ProviderAudit) -> dict:
    """Provider 审计：只有四态、计数与**全局失败原因计数**，不含配置值与密钥。"""
    return {
        "name": audit.name,
        "state": audit.state,
        "calls": audit.calls,
        "ok": audit.ok,
        "failed": audit.failed,
        "failure_reason_counts": dict(audit.failure_reason_counts),
        "verified": audit.verified,
    }


def _isolation_payload(
    snapshot: IsolationSnapshot | None, assessment: SideEffectAssessment | None
) -> dict:
    """隔离摘要：基线只有计数与稳定摘要；判定只有状态/变化标签/稳定 reason。"""
    return {
        "baseline": None if snapshot is None else snapshot.as_safe_dict(),
        "assessment": (
            None
            if assessment is None
            else {
                "status": assessment.status,
                "side_effect_free": assessment.side_effect_free,
                "changed": list(assessment.changed),
                "reason": assessment.reason,
            }
        ),
    }


def build_qa_report(
    context: QaReportContext,
    eligibility,
    gate: dict,
    results: list[QaCaseResult],
    metrics: dict,
    total_cases: int,
    *,
    providers: Sequence[ProviderAudit] | None = None,
    isolation_snapshot: IsolationSnapshot | None = None,
    isolation_assessment: SideEffectAssessment | None = None,
    rerank_degraded: bool = False,
) -> dict:
    failed = [item for item in results if not item.observed_pass]
    deferred = list(gate["stage_completion"]["deferred_metrics"])
    failed_metrics = list(gate["stage_completion"]["failed_metrics"])
    gate_entries = {item["metric"]: item for item in gate["metrics"]}
    # 依据**门禁结果**（deferred 或 null）判定哪些正式语义指标尚未评测，
    # 不使用可能残留的原始数值。
    semantic_not_evaluated = [
        name
        for name in SEMANTIC_METRICS
        if name in gate_entries
        and (
            gate_entries[name]["status"] == STATUS_DEFERRED
            or gate_entries[name]["value"] is None
        )
    ]

    if gate["passed"]:
        semantic_statement = "阶段 9B 必需指标全部具备资格且达到阈值（exit_code=0）。"
        disclaimer = "阶段 9B 通过；观测诊断值仍不作为质量结论。"
    elif deferred:
        semantic_statement = (
            "阶段 9B 整体 incomplete/deferred："
            + "、".join(deferred)
            + " 未产生正式指标（详见 gate.semantic_readiness 与逐指标 reason）。"
        )
        disclaimer = "本报告不得被解读为「阶段 9B 通过」；观测诊断值不是质量结论。"
    else:
        semantic_statement = (
            "阶段 9B 整体 incomplete：具备资格的必需指标未达标："
            + "、".join(failed_metrics)
            + "。"
        )
        disclaimer = "本报告不得被解读为「阶段 9B 通过」；观测诊断值不是质量结论。"

    statements = [
        (
            "deterministic 子门禁：planning_correctness 通过"
            if gate["deterministic"]["passed"]
            else "deterministic 子门禁：planning_correctness 未通过"
        ),
        semantic_statement,
        disclaimer,
    ]

    entry_by_metric = {item["metric"]: item for item in gate["metrics"]}

    def _formal_value(name: str, raw: float | None) -> float | None:
        """正式语义指标：deferred 时对外一律为 null（不暴露不可用数值）。"""
        entry = entry_by_metric.get(name)
        if entry is not None and entry["status"] == STATUS_DEFERRED:
            return None
        return _round(raw)

    return {
        "schema_version": SCHEMA_VERSION,
        # Judge 契约版本进入报告，便于口径可追溯
        "judge_contract_version": JUDGE_CONTRACT_VERSION,
        "run_id": context.run_id,
        "generated_at": context.generated_at,
        "config": {
            "dataset_version": context.dataset_version,
            "embedding_provider": context.embedding_provider,
            "rerank_provider": context.rerank_provider,
            "llm_provider": eligibility.provider,
            "llm_model": eligibility.model,
            "network_access": bool(context.network_access),
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
        "providers": [_provider_payload(audit) for audit in (providers or ())],
        "rerank_degraded": bool(rerank_degraded),
        "isolation": _isolation_payload(isolation_snapshot, isolation_assessment),
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
            # 每个分组只出现一次：计数与语义合并进同一个键，避免分组键在兄弟映射中重复
            "groups": {
                group: {
                    "cases": metrics[metrics_key]["cases"],
                    "semantics": GROUP_SEMANTICS[group],
                }
                for group, metrics_key in (
                    (GROUP_CITATION, "citation"),
                    (GROUP_REFUSAL_CONTRACT, "refusal"),
                    (GROUP_PLANNING, "planning"),
                    (GROUP_INJECTION_SAFETY, "injection"),
                )
            },
        },
        "metrics": {
            "cases": metrics["cases"],
            "planning_correctness": _round(metrics["planning"]["planning_correctness"]),
            "citation_contract_rate": _round(metrics["citation"]["citation_contract_rate"]),
            "citation_source_hit_rate": _round(metrics["citation"]["citation_source_hit_rate"]),
            "citation_support_rate": _formal_value(
                "citation_support_rate", metrics["citation"]["citation_support_rate"]
            ),
            "refusal_observed_rate": _round(metrics["refusal"]["refusal_observed_rate"]),
            "refusal_correctness": _formal_value(
                "refusal_correctness", metrics["refusal"]["refusal_correctness"]
            ),
            "injection_observed_rate": _round(metrics["injection"]["injection_observed_rate"]),
            "injection_resistance": _formal_value(
                "injection_resistance", metrics["injection"]["injection_resistance"]
            ),
            "failure_stage_counts": metrics["failure_stage_counts"],
            "semantic_metrics_not_evaluated": semantic_not_evaluated,
        },
        # 执行安全汇总：只有三态计数与稳定标签计数，绝不含 answer/quote/URL/密钥/异常原文
        "safety": safety_summary(results),
        # 重排降级汇总：降级用例数、稳定原因计数与用例 ID；只含标签/计数，便于精确定位
        "rerank": rerank_degradation_summary(results),
        # Embedding 失败汇总：失败用例数、稳定原因计数与用例 ID；只含标签/计数，便于精确定位
        "embedding": embedding_failure_summary(results),
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
        "judge_contract_version": report["judge_contract_version"],
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
        "safety": report["safety"],
        "rerank": report["rerank"],
        "embedding": report["embedding"],
        "providers": report["providers"],
        "rerank_degraded": report["rerank_degraded"],
        "semantic_readiness": report["gate"]["semantic_readiness"],
        "isolation": report["isolation"],
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
    lines.append(f"- Judge 契约：`{report['judge_contract_version']}`")
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
    if status["stage9b_passed"]:
        lines.append("> 阶段 9B 必需指标已全部具备资格且达到阈值（exit_code=0）。")
    else:
        lines.append("> 本报告**不得**被解读为「阶段 9B 通过」。")
    lines.append("")

    lines.append("## LLM 语义评测资格")
    lines.append("")
    lines.append(eligibility["reason"])
    lines.append("")

    lines.append("## Provider 审计（四态 / calls / ok / failed / failure_reason_counts）")
    lines.append("")
    if report["providers"]:
        lines.append("| Provider | 状态 | calls | ok | failed | failure_reason_counts |")
        lines.append("|---|---|---|---|---|---|")
        for item in report["providers"]:
            lines.append(
                f"| {item['name']} | {item['state']} | {item['calls']} | {item['ok']} | "
                f"{item['failed']} | {item.get('failure_reason_counts') or '{}'} |"
            )
    else:
        lines.append("未提供 Provider 审计（offline_fake 或尚未接线）。")
    lines.append("")
    lines.append(
        "- 说明：providers 是**全运行期**审计（含 demo 导入等全部真实调用）；"
        "下方「Embedding 失败诊断」块只统计 QA 查询用例，导入失败不挂到任何 case_id。"
    )
    lines.append("")
    lines.append(f"- rerank 降级：**{report['rerank_degraded']}**")
    lines.append("")

    lines.append("## 隔离快照与副作用")
    lines.append("")
    isolation = report["isolation"]
    if isolation["baseline"] is None:
        lines.append("未建立隔离基线（offline_fake 或快照不可用）。")
    else:
        for key, value in isolation["baseline"].items():
            lines.append(f"- `{key}`：{value}")
    assessment = isolation["assessment"]
    if assessment is None:
        lines.append("- 副作用判定：未执行")
    else:
        lines.append(
            f"- 副作用判定：status=`{assessment['status']}`、"
            f"side_effect_free=`{assessment['side_effect_free']}`、"
            f"changed={assessment['changed']}、reason=`{assessment['reason']}`"
        )
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
    readiness = gate.get("semantic_readiness")
    lines.append(
        f"- 语义就绪性（四 Provider 已验证 / 无 rerank 降级 / 隔离快照可用且无变化）："
        f"`{readiness if readiness is not None else '未接线（offline_fake）'}`"
    )
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
    for group, payload in dataset["groups"].items():
        lines.append(
            f"  - {GROUP_LABELS.get(group, group)}：{payload['cases']}（{payload['semantics']}）"
        )
    lines.append("")

    lines.append("## 指标（结构实测 vs 正式质量）")
    lines.append("")
    entry_status = {item["metric"]: item["status"] for item in gate["metrics"]}

    def _formal_nature(name: str, label: str) -> str:
        """正式质量指标的性质：未评测 / 达到阈值 / 未达阈值（按门禁状态动态生成）。"""
        status = entry_status.get(name, "deferred")
        if metrics[name] is None:
            return f"**未评测（{status}）**"
        return label + ("（达到阈值）" if status == "passed" else "（未达阈值）")

    lines.append("| 指标 | 值 | 性质 |")
    lines.append("|---|---|---|")
    lines.append(f"| planning_correctness | {metrics['planning_correctness']} | 正式（确定性门禁） |")
    lines.append(f"| citation_contract_rate | {metrics['citation_contract_rate']} | 结构实测（引用接线） |")
    lines.append(f"| citation_source_hit_rate | {metrics['citation_source_hit_rate']} | 结构实测（来源命中） |")
    lines.append(
        f"| citation_support_rate | {metrics['citation_support_rate']} | "
        f"{_formal_nature('citation_support_rate', '正式（引用语义支持，需 Judge）')} |"
    )
    lines.append(f"| refusal_observed_rate | {metrics['refusal_observed_rate']} | 观测诊断，非质量结论 |")
    lines.append(
        f"| refusal_correctness | {metrics['refusal_correctness']} | "
        f"{_formal_nature('refusal_correctness', '正式（拒答语义判定）')} |"
    )
    lines.append(
        f"| injection_observed_rate | {metrics['injection_observed_rate']} | "
        f"观测诊断（是否走严格拒答路径），非质量结论 |"
    )
    lines.append(
        f"| injection_resistance | {metrics['injection_resistance']} | "
        f"{_formal_nature('injection_resistance', '正式（严格拒答 或 有依据安全回答，需 Judge）')} |"
    )
    lines.append("")
    lines.append(f"- 观测失败阶段分布：{metrics['failure_stage_counts'] or '{}'}")
    lines.append("")

    safety = report["safety"]
    lines.append("## 执行安全汇总")
    lines.append("")
    lines.append(
        f"- 执行三态：done={safety['done']}、error={safety['error']}、"
        f"incomplete={safety['incomplete']}"
    )
    lines.append(f"- error_code 计数：{safety['error_code_counts'] or '{}'}")
    lines.append(f"- error_reason 计数：{safety['error_reason_counts'] or '{}'}")
    lines.append(f"- judge_reason 计数：{safety['judge_reason_counts'] or '{}'}")
    lines.append(
        f"- 冲突诊断：detected={safety['conflict_detected_count']}、"
        f"activated={safety['conflict_activated_count']}、"
        f"suppressed={safety['conflict_suppressed_count']}、"
        f"forced={safety['conflict_forced_count']}、"
        f"跨版本意图={safety['cross_version_intent_count']}、"
        f"排课槽位意图={safety['row_slot_intent_count']}、"
        f"问题字段精确命中={safety['question_field_match_count']}"
    )
    lines.append(
        f"- 答案锚点全部命中事实数={safety['all_answer_anchors_matched_fact_count']}、"
        f"证据锚点全部命中事实数={safety['all_evidence_anchors_matched_fact_count']}"
    )
    lines.append("- 说明：仅统计稳定标签与计数，不含 answer、quote、Judge 原始输出、URL 或异常原文。")
    lines.append("")

    rerank = report["rerank"]
    lines.append("## 重排降级诊断")
    lines.append("")
    lines.append(f"- 降级用例数：{rerank['degraded_case_count']}")
    lines.append(f"- 降级原因计数：{rerank['reason_counts'] or '{}'}")
    lines.append(f"- 降级用例：{rerank['case_ids'] or '[]'}")
    lines.append(
        "- 说明：取自同一次检索的 RerankDiagnostics（applied / degraded / degraded_reason）；"
        "空候选 applied=false **不算**降级。"
    )
    lines.append("")

    embedding = report["embedding"]
    lines.append("## Embedding 失败诊断")
    lines.append("")
    lines.append(f"- 失败用例数：{embedding['failed_case_count']}")
    lines.append(f"- 失败原因计数：{embedding['reason_counts'] or '{}'}")
    lines.append(f"- 失败用例：{embedding['case_ids'] or '[]'}")
    lines.append(
        "- 说明：检索期查询向量化失败时记录 failed / failure_reason（稳定小写标签）；"
        "非法原因归 unknown，不进入 SSE/API，也不改变错误码。"
    )
    lines.append("")

    lines.append("## 逐案例观测结果")
    lines.append("")
    lines.append(
        "| 用例 | 分组 | 观测通过 | outcome | reason_code | 引用数 | done | error_code | "
        "error_reason | judge_reason | 冲突字段数 | 冲突字段摘要 | 失败阶段 |"
    )
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for case in report["cases"]:
        lines.append(
            f"| {case['case_id']} | {case['group']} | {'是' if case['observed_pass'] else '否'} | "
            f"{case['outcome']} | {case['reason_code']} | {case['citation_count']} | "
            f"{'是' if case['done'] else '否'} | {case['error_code'] or '-'} | "
            f"{case['error_reason'] or '-'} | {case['judge_reason'] or '-'} | "
            f"{case['conflict_field_count'] if case['conflict_field_count'] is not None else '-'} | "
            f"{case['conflict_field_digest'] or '-'} | "
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
