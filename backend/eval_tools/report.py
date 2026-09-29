"""报告构造：机器可读 JSON 与 Markdown（内容完全同源）。

报告中**不包含**任何文档正文、答案、密钥、绝对路径或个人信息，只保留：用例 id、
问题（评测集自身的虚构问题）、期望/命中的来源文件名、三层指标、逐阶段真实排名与阶段归类。

门禁语义：由评测 profile 决定「哪一层指标构成质量门禁」（见 ``runner.resolve_profile``）。
所有产物（JSON / Markdown / CLI 摘要 / 退出码）都从同一 ``gate`` 对象读取，保证一致。
"""

from __future__ import annotations

from dataclasses import dataclass

from eval_tools import SCHEMA_VERSION
from eval_tools.ground_truth import SelectionResult
from eval_tools.runner import (
    METRIC_FINAL_RECALL,
    METRIC_FUSION_RECALL,
    SEMANTICS_DIAGNOSTIC,
    SEMANTICS_GATE,
    SEMANTICS_NON_SEMANTIC,
    CaseResult,
    EvalProfile,
    build_gate,
)

ROUND_DIGITS = 4
METRIC_CANDIDATE_RECALL = "candidate_recall_at_k"


def _round(value: float | None) -> float | None:
    return None if value is None else round(float(value), ROUND_DIGITS)


@dataclass(frozen=True)
class ReportContext:
    run_id: str
    ground_truth_name: str
    dataset_version: str
    top_k: int
    candidate_k: int
    embedding_provider: str
    rerank_provider: str
    profile: EvalProfile
    generated_at: str


def _layer_semantics(profile: EvalProfile, layer_metric: str) -> str:
    if profile.gate_metric == layer_metric:
        return SEMANTICS_GATE
    if layer_metric == METRIC_FINAL_RECALL and not profile.rerank_semantic:
        return SEMANTICS_NON_SEMANTIC
    return SEMANTICS_DIAGNOSTIC


def _source_stage_payload(case: CaseResult) -> list[dict]:
    return [
        {
            "source": stage.source,
            "dense_rank": stage.dense_rank,
            "keyword_rank": stage.keyword_rank,
            "fused_rank": stage.fused_rank,
            "final_rank": stage.final_rank,
            "stage": stage.stage,
        }
        for stage in case.source_stages
    ]


def build_report(
    context: ReportContext,
    selection: SelectionResult,
    results: list[CaseResult],
    metrics: dict,
) -> dict:
    """组装完整报告字典（schema 与指标口径随 ``SCHEMA_VERSION`` 版本化）。"""
    failed_cases = [item for item in results if not item.hit]
    profile = context.profile
    gate = build_gate(profile, metrics)

    metrics_payload = {
        "top_k": metrics["top_k"],
        "candidate_k": metrics["candidate_k"],
        "cases": metrics["cases"],
        "fully_hit_cases": metrics["fully_hit_cases"],
        "candidate": {
            "recall_at_k": _round(metrics["candidate_recall_at_k"]),
            "semantics": SEMANTICS_DIAGNOSTIC,
        },
        "fusion": {
            "recall_at_k": _round(metrics["fusion_recall_at_k"]),
            "mrr": _round(metrics["fusion_mrr"]),
            "semantics": _layer_semantics(profile, METRIC_FUSION_RECALL),
        },
        "final": {
            "recall_at_k": _round(metrics["recall_at_k"]),
            "mrr": _round(metrics["mrr"]),
            "semantics": _layer_semantics(profile, METRIC_FINAL_RECALL),
            "rerank_score_kind": profile.rerank_score_kind,
        },
        "latency_ms": {key: _round(value) for key, value in metrics["latency_ms"].items()},
        "failure_stage_counts": metrics["failure_stage_counts"],
        "source_stage_counts": metrics["source_stage_counts"],
    }

    return {
        "schema_version": SCHEMA_VERSION,
        "run_id": context.run_id,
        "generated_at": context.generated_at,
        "config": {
            "top_k": context.top_k,
            "candidate_k": context.candidate_k,
            "dataset_version": context.dataset_version,
            "embedding_provider": context.embedding_provider,
            "rerank_provider": context.rerank_provider,
            "rerank_score_kind": profile.rerank_score_kind,
            "profile": profile.name,
            "network_access": False,
            "model_download": False,
            "metric_definitions": {
                "candidate_recall_at_k": "融合候选 top-candidate_k 内去重来源命中数 / |expected|",
                "fusion_recall_at_k": "RRF 融合顺序 top_k 内去重来源命中数 / |expected|",
                "fusion_mrr": "1 / 首个相关来源在 RRF 融合顺序中的 1-based 位次",
                "recall_at_k": "Fake Reranker 之后最终 top_k 内去重来源命中数 / |expected|",
                "mrr": "1 / 首个相关来源在最终顺序中的 1-based 位次，未命中为 0",
                "percentile": "线性插值（numpy linear），空样本为 null",
                "stage_rule": (
                    "parse_or_chunk: 未索引；recall: Dense/Keyword 均未召回；fusion: 单路存在但未进融合候选；"
                    "fusion_cutoff: 融合排名>k 且未进最终 top-k；rerank_demoted: 融合排名<=k 但跌出最终 top-k；"
                    "rerank_promoted: 融合排名>k 但最终进入 top-k；retained_top5: 融合与最终均在 top-k"
                ),
            },
        },
        "gate": gate,
        "dataset": {
            "ground_truth_file": context.ground_truth_name,
            "total_cases": len(selection.included) + len(selection.excluded),
            "included_cases": len(selection.included),
            "excluded_cases": len(selection.excluded),
            "excluded_reasons": selection.excluded_counts(),
            "selection_rule": (
                "纳入 expected_source_paths 非空、should_refuse=false 且 category 不属于 "
                "(planning) 的用例；其余按 should_refuse / excluded_category / "
                "no_expected_sources 分类排除"
            ),
            "excluded_case_ids": [item.case.case_id for item in selection.excluded],
        },
        "metrics": metrics_payload,
        "failures": {
            "count": len(failed_cases),
            "stage_counts": metrics["failure_stage_counts"],
            "cases": [
                {
                    "case_id": item.case_id,
                    "question": item.question,
                    "expected_sources": list(item.expected_sources),
                    "dense_top": list(item.dense_top),
                    "keyword_top": list(item.keyword_top),
                    "fused_candidates": list(item.fused_candidates),
                    "fused_top_k": list(item.fused_top_k),
                    "final_top_k": list(item.final_top_k),
                    "recall_at_k": _round(item.recall_at_k),
                    "fusion_recall_at_k": _round(item.fusion_recall_at_k),
                    "candidate_recall_at_k": _round(item.candidate_recall_at_k),
                    "sources": _source_stage_payload(item),
                }
                for item in failed_cases
            ],
        },
        "cases": [
            {
                "case_id": item.case_id,
                "category": item.category,
                "question": item.question,
                "expected_sources": list(item.expected_sources),
                "matched_sources": list(item.matched_sources),
                "retrieved_sources": list(item.retrieved_sources),
                "dense_top": list(item.dense_top),
                "keyword_top": list(item.keyword_top),
                "fused_candidates": list(item.fused_candidates),
                "fused_top_k": list(item.fused_top_k),
                "final_top_k": list(item.final_top_k),
                "recall_at_k": _round(item.recall_at_k),
                "reciprocal_rank": _round(item.reciprocal_rank),
                "fusion_recall_at_k": _round(item.fusion_recall_at_k),
                "fusion_reciprocal_rank": _round(item.fusion_reciprocal_rank),
                "candidate_recall_at_k": _round(item.candidate_recall_at_k),
                "hit": item.hit,
                "elapsed_ms": _round(item.elapsed_ms),
                "rerank_applied": item.rerank_applied,
                "sources": _source_stage_payload(item),
            }
            for item in results
        ],
    }


def build_cli_summary(report: dict, *, json_report: str, markdown_report: str) -> dict:
    """CLI 摘要：与 JSON/Markdown 同源的 gate 与三层指标。"""
    metrics = report["metrics"]
    return {
        "run_id": report["run_id"],
        "schema_version": report["schema_version"],
        "profile": report["gate"]["profile"],
        "gate": report["gate"],
        "included_cases": report["dataset"]["included_cases"],
        "excluded_cases": report["dataset"]["excluded_cases"],
        "candidate_recall_at_k": metrics["candidate"]["recall_at_k"],
        "fusion_recall_at_k": metrics["fusion"]["recall_at_k"],
        "fusion_mrr": metrics["fusion"]["mrr"],
        "final_recall_at_k": metrics["final"]["recall_at_k"],
        "final_mrr": metrics["final"]["mrr"],
        "final_semantics": metrics["final"]["semantics"],
        "latency_ms_p50": metrics["latency_ms"]["p50"],
        "latency_ms_p95": metrics["latency_ms"]["p95"],
        "json_report": json_report,
        "markdown_report": markdown_report,
        "failure_stage_counts": metrics["failure_stage_counts"],
        "source_stage_counts": metrics["source_stage_counts"],
    }


def exit_code(report: dict) -> int:
    """退出码只由 gate 决定。"""
    return 0 if report["gate"]["passed"] else 1


def to_markdown(report: dict) -> str:
    """把报告渲染为 Markdown（与 JSON 同源，无额外信息）。"""
    metrics = report["metrics"]
    latency = metrics["latency_ms"]
    config = report["config"]
    dataset = report["dataset"]
    gate = report["gate"]
    top_k = metrics["top_k"]
    candidate_k = metrics["candidate_k"]

    lines: list[str] = []
    lines.append("# 离线 RAG 检索评测报告（阶段 9A）")
    lines.append("")
    lines.append(f"- schema：`{report['schema_version']}`")
    lines.append(f"- run id：`{report['run_id']}`")
    lines.append(f"- 生成时间：{report['generated_at']}")
    lines.append(f"- 数据集：`{dataset['ground_truth_file']}`（演示版本 {config['dataset_version']}）")
    lines.append(f"- profile：`{config['profile']}`；rerank score kind：`{config['rerank_score_kind']}`")
    lines.append(
        f"- Provider：embedding=`{config['embedding_provider']}`、"
        f"rerank=`{config['rerank_provider']}`；联网={config['network_access']}、"
        f"下载模型={config['model_download']}"
    )
    lines.append("")

    lines.append("## 质量门禁")
    lines.append("")
    lines.append(f"- profile：`{gate['profile']}`")
    lines.append(f"- 门禁指标：`{gate['metric']}`")
    lines.append(f"- 实测值：{gate['value']}（阈值 {gate['threshold']}）")
    lines.append(f"- 结果：{'通过' if gate['passed'] else '未通过'}")
    lines.append(f"- 依据：{gate['reason']}")
    lines.append("")

    lines.append("## 样本口径")
    lines.append("")
    lines.append(f"- 总用例：{dataset['total_cases']}")
    lines.append(f"- 纳入正例：{dataset['included_cases']}")
    lines.append(f"- 排除：{dataset['excluded_cases']} {dataset['excluded_reasons']}")
    lines.append(f"- 口径：{dataset['selection_rule']}")
    lines.append("")

    lines.append("## 三层指标")
    lines.append("")
    lines.append("| 层 | 指标 | 值 | 语义 |")
    lines.append("|---|---|---|---|")
    lines.append(
        f"| 候选 | candidate Recall@{candidate_k} | {metrics['candidate']['recall_at_k']} | "
        f"{metrics['candidate']['semantics']} |"
    )
    lines.append(
        f"| 融合 | fusion Recall@{top_k} | {metrics['fusion']['recall_at_k']} | "
        f"{metrics['fusion']['semantics']} |"
    )
    lines.append(f"| 融合 | fusion MRR | {metrics['fusion']['mrr']} | {metrics['fusion']['semantics']} |")
    lines.append(
        f"| 最终({config['rerank_score_kind']}) | final Recall@{top_k} | "
        f"{metrics['final']['recall_at_k']} | {metrics['final']['semantics']} |"
    )
    lines.append(
        f"| 最终({config['rerank_score_kind']}) | final MRR | {metrics['final']['mrr']} | "
        f"{metrics['final']['semantics']} |"
    )
    lines.append("")
    lines.append(
        f"- 完全命中用例：{metrics['fully_hit_cases']} / {metrics['cases']}；"
        f"检索时延 ms：P50={latency['p50']}、P95={latency['p95']}、"
        f"min={latency['min']}、max={latency['max']}、n={latency['count']}"
    )
    lines.append(f"- 失败来源阶段分布：{metrics['failure_stage_counts'] or '{}'}")
    lines.append(f"- 全部期望来源阶段分布：{metrics['source_stage_counts'] or '{}'}")
    lines.append("")

    lines.append("## 逐案例结果")
    lines.append("")
    lines.append(
        f"| 用例 | 类别 | candR@{candidate_k} | fusionR@{top_k} | finalR@{top_k} | final RR | 命中 |"
    )
    lines.append("|---|---|---|---|---|---|---|")
    for case in report["cases"]:
        lines.append(
            f"| {case['case_id']} | {case['category']} | {case['candidate_recall_at_k']} | "
            f"{case['fusion_recall_at_k']} | {case['recall_at_k']} | "
            f"{case['reciprocal_rank']} | {'是' if case['hit'] else '否'} |"
        )
    lines.append("")

    lines.append("## 失败明细（含真实排名证据）")
    lines.append("")
    if not report["failures"]["cases"]:
        lines.append("无失败用例。")
    else:
        for case in report["failures"]["cases"]:
            lines.append(f"### `{case['case_id']}`（final Recall={case['recall_at_k']}）")
            lines.append("")
            lines.append(f"- 融合候选 top-{candidate_k}：{', '.join(case['fused_candidates']) or '-'}")
            lines.append(f"- 融合 top-{top_k}：{', '.join(case['fused_top_k']) or '-'}")
            lines.append(f"- 最终 top-{top_k}：{', '.join(case['final_top_k']) or '-'}")
            for source in case["sources"]:
                lines.append(
                    f"- {source['source']}：dense#{source['dense_rank']} "
                    f"keyword#{source['keyword_rank']} fused#{source['fused_rank']} "
                    f"final#{source['final_rank']} → `{source['stage']}`"
                )
            lines.append("")
    return "\n".join(lines)


__all__ = [
    "METRIC_CANDIDATE_RECALL",
    "ReportContext",
    "build_cli_summary",
    "build_report",
    "exit_code",
    "to_markdown",
]
