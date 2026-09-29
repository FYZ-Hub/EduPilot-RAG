"""评测执行与聚合（纯逻辑，不直接依赖生产 App 模块）。

评测流程：

1. 按正例口径过滤用例（见 ``ground_truth.select_cases``）；
2. 对每个用例采集三路证据：Dense、Keyword、RRF 融合候选（top-20）与 Fake Reranker 后的最终顺序；
3. 用期望来源映射（``matching.canonicalize``）计算三层指标：
   ``candidate Recall@20``、``fusion Recall@k``/``fusion MRR``、``final Recall@k``/``final MRR``；
4. 对每个期望来源按其**真实排名**精确归类失败阶段（见 ``classify_source_stage``）。

指标口径与报告结构随 ``SCHEMA_VERSION`` 版本化，工具不读取答案、不改写检索结果。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Protocol

from eval_tools.ground_truth import EvaluationDataError, GroundTruthCase
from eval_tools.matching import RetrievedSource, canonicalize, expected_identity
from eval_tools.metrics import (
    dedupe_preserving_order,
    mean,
    percentile,
    recall_at_k,
    reciprocal_rank,
)

DEFAULT_TOP_K = 5
DEFAULT_CANDIDATE_K = 20
DEFAULT_MIN_RECALL = 0.85

# --- 评测 profile 与门禁 -----------------------------------------------------
# Reranker 分数种类（与 app.rerank.base 的取值一致，此处不反向依赖生产包）
SCORE_KIND_FAKE_DETERMINISTIC = "fake-deterministic"

PROFILE_OFFLINE_FAKE = "offline_fake"
PROFILE_ONLINE_RERANK = "online_rerank"

METRIC_FUSION_RECALL = "fusion_recall_at_k"
METRIC_FINAL_RECALL = "recall_at_k"

# 指标语义标注
SEMANTICS_GATE = "gate"
SEMANTICS_DIAGNOSTIC = "diagnostic"
SEMANTICS_NON_SEMANTIC = "non_semantic_diagnostic"


@dataclass(frozen=True)
class EvalProfile:
    """评测 profile：决定「哪一层指标构成质量门禁」。

    ``offline_fake`` 使用真实解析 / SQLite / Chroma / FTS5 / Dense / Keyword / RRF，
    但 Reranker 为 ``fake-deterministic``：其分数由 SHA-256 派生、**不具备相关性语义**，
    因此只能用于链路与契约诊断，质量门禁固定在 ``fusion Recall@k``；
    final Fake-rerank 指标仍必须记录，但标记为 ``non_semantic_diagnostic``。
    """

    name: str
    gate_metric: str
    gate_threshold: float
    rerank_score_kind: str
    rerank_semantic: bool
    reason: str


def resolve_profile(
    rerank_score_kind: str, *, threshold: float = DEFAULT_MIN_RECALL
) -> EvalProfile:
    """按 Reranker 分数种类选择门禁层；fake 与具有相关性语义的 provider 明确区分。"""
    if rerank_score_kind == SCORE_KIND_FAKE_DETERMINISTIC:
        return EvalProfile(
            name=PROFILE_OFFLINE_FAKE,
            gate_metric=METRIC_FUSION_RECALL,
            gate_threshold=threshold,
            rerank_score_kind=rerank_score_kind,
            rerank_semantic=False,
            reason=(
                "offline_fake：Reranker 分数为 fake-deterministic（与相关性无关），"
                "质量门禁固定为 fusion Recall@k；final Fake-rerank 指标仅作非语义诊断。"
            ),
        )
    return EvalProfile(
        name=PROFILE_ONLINE_RERANK,
        gate_metric=METRIC_FINAL_RECALL,
        gate_threshold=threshold,
        rerank_score_kind=rerank_score_kind,
        rerank_semantic=True,
        reason=(
            f"Reranker 分数种类 {rerank_score_kind} 具备相关性语义，"
            "质量门禁为最终(final) Recall@k。"
        ),
    )


def metric_value(metrics: dict, metric: str) -> float:
    """按门禁指标名读取真实聚合值；未知指标显式报错。"""
    if metric == METRIC_FUSION_RECALL:
        return float(metrics["fusion_recall_at_k"] or 0.0)
    if metric == METRIC_FINAL_RECALL:
        return float(metrics["recall_at_k"] or 0.0)
    raise EvaluationDataError(f"未知门禁指标：{metric}")


def build_gate(profile: EvalProfile, metrics: dict) -> dict:
    """构造门禁对象：profile / metric / value / threshold / passed / reason。"""
    value = metric_value(metrics, profile.gate_metric)
    return {
        "profile": profile.name,
        "metric": profile.gate_metric,
        "value": round(value, 4),
        "threshold": profile.gate_threshold,
        "passed": value >= profile.gate_threshold,
        "reason": profile.reason,
    }

# 失败阶段标签：与实现计划的「解析、切片、召回、融合、重排」分类对齐。
STAGE_PARSE_OR_CHUNK = "parse_or_chunk"
STAGE_RECALL = "recall"
STAGE_FUSION = "fusion"
STAGE_FUSION_CUTOFF = "fusion_cutoff"
STAGE_RERANK_DEMOTED = "rerank_demoted"
STAGE_RERANK_PROMOTED = "rerank_promoted"
STAGE_RETAINED = "retained_top5"

# 判定为「融合已进入最终前 K」的阈值与最终 top_k 一致
FUSION_TOP_K = DEFAULT_TOP_K


@dataclass(frozen=True)
class RankedTrace:
    """单个查询的最终检索结果（重排后顺序）与耗时。"""

    ranked: tuple[RetrievedSource, ...]
    elapsed_ms: float
    rerank_applied: bool
    degraded_reason: str | None = None


@dataclass(frozen=True)
class RouteProbe:
    """单查询的三路证据：Dense / Keyword / RRF 融合候选（保持各自排名顺序）。"""

    dense: tuple[RetrievedSource, ...]
    keyword: tuple[RetrievedSource, ...]
    fused: tuple[RetrievedSource, ...]


@dataclass(frozen=True)
class SourceStage:
    """某个期望来源在各阶段的真实排名与阶段归类。"""

    source: str
    dense_rank: int | None
    keyword_rank: int | None
    fused_rank: int | None
    final_rank: int | None
    stage: str


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    category: str
    question: str
    expected_sources: tuple[str, ...]
    matched_sources: tuple[str, ...]
    retrieved_sources: tuple[str, ...]
    # 三层指标
    recall_at_k: float
    reciprocal_rank: float
    fusion_recall_at_k: float
    fusion_reciprocal_rank: float
    candidate_recall_at_k: float
    # 判定
    hit: bool
    elapsed_ms: float
    rerank_applied: bool
    # 排名证据
    dense_top: tuple[str, ...]
    keyword_top: tuple[str, ...]
    fused_candidates: tuple[str, ...]
    fused_top_k: tuple[str, ...]
    final_top_k: tuple[str, ...]
    source_stages: tuple[SourceStage, ...] = field(default_factory=tuple)


class RetrievalChain(Protocol):
    """评测所需的最小检索接口。"""

    def indexed_identities(self) -> frozenset[str]:
        """当前索引中「可检索」的来源标识集合（用于解析/切片阶段判定）。"""

    def ranked(self, query: str, top_k: int) -> RankedTrace:
        """最终检索结果（重排后顺序）。"""

    def probe(self, query: str) -> RouteProbe:
        """单路与融合候选证据（保持排名顺序）。"""


def _rank_of(canonical: Sequence[str], target: str) -> int | None:
    """目标在去重后序列中的 1-based 排名；不存在返回 ``None``。"""
    for rank, source in enumerate(dedupe_preserving_order(canonical), start=1):
        if source == target:
            return rank
    return None


def classify_source_stage(
    *,
    indexed: bool,
    in_dense: bool,
    in_keyword: bool,
    fused_rank: int | None,
    final_rank: int | None,
    fusion_top_k: int = FUSION_TOP_K,
) -> str:
    """按真实排名精确归类期望来源所处的阶段。

    优先级与判据（不依赖任何「是否被重排挤出」的未经验证假设）：

    1. 未进入可检索索引 → ``parse_or_chunk``；
    2. Dense 与 Keyword 均未召回 → ``recall``；
    3. 单路已召回但未进入融合候选 → ``fusion``；
    4. 融合排名 > ``fusion_top_k`` 且未进入最终 top-k → ``fusion_cutoff``；
    5. 融合排名 ≤ ``fusion_top_k`` 且跌出最终 top-k → ``rerank_demoted``；
    6. 融合排名 > ``fusion_top_k`` 且最终进入 top-k → ``rerank_promoted``；
    7. 融合与最终均在 top-k → ``retained_top5``（非失败类别）。
    """
    if not indexed:
        return STAGE_PARSE_OR_CHUNK
    if not in_dense and not in_keyword:
        return STAGE_RECALL
    if fused_rank is None:
        return STAGE_FUSION
    if fused_rank > fusion_top_k:
        return STAGE_FUSION_CUTOFF if final_rank is None else STAGE_RERANK_PROMOTED
    return STAGE_RETAINED if final_rank is not None else STAGE_RERANK_DEMOTED


def evaluate_cases(
    cases: Sequence[GroundTruthCase],
    chain: RetrievalChain,
    *,
    top_k: int = DEFAULT_TOP_K,
    candidate_k: int = DEFAULT_CANDIDATE_K,
    dataset_version: str,
) -> list[CaseResult]:
    """对纳入用例逐一评测；返回逐案例结果（顺序与输入一致）。"""
    if not cases:
        raise EvaluationDataError("没有可评测的正例用例")
    if top_k <= 0:
        raise EvaluationDataError("top_k 必须为正整数")
    if candidate_k <= 0:
        raise EvaluationDataError("candidate_k 必须为正整数")

    indexed = chain.indexed_identities()
    results: list[CaseResult] = []

    for case in cases:
        trace = chain.ranked(case.question, top_k)
        probe = chain.probe(case.question)
        expected = case.expected_source_paths

        final_canonical = canonicalize(trace.ranked, expected, dataset_version)
        fused_canonical = canonicalize(probe.fused, expected, dataset_version)
        dense_canonical = canonicalize(probe.dense, expected, dataset_version)
        keyword_canonical = canonicalize(probe.keyword, expected, dataset_version)

        dense_ids = frozenset(source.identity for source in probe.dense)
        keyword_ids = frozenset(source.identity for source in probe.keyword)

        source_stages: list[SourceStage] = []
        for path in expected:
            identity = expected_identity(dataset_version, path)
            source_stages.append(
                SourceStage(
                    source=path,
                    dense_rank=_rank_of(dense_canonical, path),
                    keyword_rank=_rank_of(keyword_canonical, path),
                    fused_rank=_rank_of(fused_canonical, path),
                    final_rank=_rank_of(final_canonical, path),
                    stage=classify_source_stage(
                        indexed=identity in indexed,
                        in_dense=identity in dense_ids,
                        in_keyword=identity in keyword_ids,
                        fused_rank=_rank_of(fused_canonical, path),
                        final_rank=_rank_of(final_canonical, path),
                    ),
                )
            )

        recall = recall_at_k(expected, final_canonical, top_k)
        results.append(
            CaseResult(
                case_id=case.case_id,
                category=case.category,
                question=case.question,
                expected_sources=expected,
                matched_sources=tuple(path for path in expected if path in set(final_canonical)),
                retrieved_sources=_distinct_file_names(trace.ranked),
                recall_at_k=recall,
                reciprocal_rank=reciprocal_rank(expected, final_canonical),
                fusion_recall_at_k=recall_at_k(expected, fused_canonical, top_k),
                fusion_reciprocal_rank=reciprocal_rank(expected, fused_canonical),
                candidate_recall_at_k=recall_at_k(expected, fused_canonical, candidate_k),
                hit=recall >= 1.0,
                elapsed_ms=float(trace.elapsed_ms),
                rerank_applied=trace.rerank_applied,
                dense_top=_distinct_file_names(probe.dense)[:top_k],
                keyword_top=_distinct_file_names(probe.keyword)[:top_k],
                fused_candidates=_distinct_file_names(probe.fused)[:candidate_k],
                fused_top_k=_distinct_file_names(probe.fused)[:top_k],
                final_top_k=_distinct_file_names(trace.ranked)[:top_k],
                source_stages=tuple(source_stages),
            )
        )
    return results


def _distinct_file_names(sources: Sequence[RetrievedSource]) -> tuple[str, ...]:
    """按来源标识去重后，输出用于展示的文件名（保持排名顺序）。"""
    seen: set[str] = set()
    names: list[str] = []
    for source in sources:
        if source.identity in seen:
            continue
        seen.add(source.identity)
        names.append(source.file_name or source.source_key)
    return tuple(names)


def aggregate(
    results: Sequence[CaseResult],
    *,
    top_k: int = DEFAULT_TOP_K,
    candidate_k: int = DEFAULT_CANDIDATE_K,
) -> dict:
    """聚合三层指标、失败阶段分布与来源阶段分布。"""
    if not results:
        raise EvaluationDataError("没有可聚合的评测结果")

    latencies = [item.elapsed_ms for item in results]

    failure_stage_counts: dict[str, int] = {}
    source_stage_counts: dict[str, int] = {}
    for item in results:
        for stage in item.source_stages:
            source_stage_counts[stage.stage] = source_stage_counts.get(stage.stage, 0) + 1
            if stage.final_rank is None:
                # 未进入最终 top-k 才是失败来源
                failure_stage_counts[stage.stage] = failure_stage_counts.get(stage.stage, 0) + 1

    return {
        "top_k": top_k,
        "candidate_k": candidate_k,
        "cases": len(results),
        "fully_hit_cases": sum(1 for item in results if item.hit),
        "candidate_recall_at_k": mean([item.candidate_recall_at_k for item in results]),
        "fusion_recall_at_k": mean([item.fusion_recall_at_k for item in results]),
        "fusion_mrr": mean([item.fusion_reciprocal_rank for item in results]),
        "recall_at_k": mean([item.recall_at_k for item in results]),
        "mrr": mean([item.reciprocal_rank for item in results]),
        "latency_ms": {
            "count": len(latencies),
            "min": min(latencies),
            "max": max(latencies),
            "p50": percentile(latencies, 50.0),
            "p95": percentile(latencies, 95.0),
        },
        "failure_stage_counts": dict(sorted(failure_stage_counts.items())),
        "source_stage_counts": dict(sorted(source_stage_counts.items())),
    }


__all__ = [
    "CaseResult",
    "DEFAULT_CANDIDATE_K",
    "DEFAULT_MIN_RECALL",
    "DEFAULT_TOP_K",
    "EvalProfile",
    "FUSION_TOP_K",
    "METRIC_FINAL_RECALL",
    "METRIC_FUSION_RECALL",
    "PROFILE_OFFLINE_FAKE",
    "PROFILE_ONLINE_RERANK",
    "RankedTrace",
    "RetrievalChain",
    "RouteProbe",
    "SCORE_KIND_FAKE_DETERMINISTIC",
    "SEMANTICS_DIAGNOSTIC",
    "SEMANTICS_GATE",
    "SEMANTICS_NON_SEMANTIC",
    "STAGE_FUSION",
    "STAGE_FUSION_CUTOFF",
    "STAGE_PARSE_OR_CHUNK",
    "STAGE_RECALL",
    "STAGE_RERANK_DEMOTED",
    "STAGE_RERANK_PROMOTED",
    "STAGE_RETAINED",
    "SourceStage",
    "aggregate",
    "build_gate",
    "classify_source_stage",
    "evaluate_cases",
    "metric_value",
    "resolve_profile",
]
