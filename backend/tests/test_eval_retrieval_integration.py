"""阶段 9A 集成测试：真实 SQLite + Chroma + FTS5 检索链上的 fusion Recall@5 门禁。

使用与生产一致的解析、切片、索引、RRF 融合与重排实现，仅 Provider 为 Fake；
offline_fake profile 的质量门禁为 **fusion Recall@5**（Fake Reranker 分数不具相关性语义，
final 指标只作非语义诊断），断言 fusion Recall@5 ≥ 85%。
"""

from __future__ import annotations

from pathlib import Path
from time import perf_counter

from app.search.reranking import RERANK_MAX_CANDIDATES

from eval_tools import SCHEMA_VERSION
from eval_tools.ground_truth import load_ground_truth, select_cases
from eval_tools.matching import RetrievedSource
from eval_tools.pipeline import indexed_source_identities
from eval_tools.report import ReportContext, build_report, exit_code
from eval_tools.runner import (
    METRIC_FUSION_RECALL,
    SEMANTICS_NON_SEMANTIC,
    RankedTrace,
    RouteProbe,
    aggregate,
    evaluate_cases,
    resolve_profile,
)

DEMO_GROUND_TRUTH = Path("/app/demo/ground_truth.jsonl")
TOP_K = 5
CANDIDATE_K = 20
MIN_RECALL = 0.85


class _SearchChain:
    """把 conftest 的检索夹具适配为评测链接口。"""

    def __init__(self, context, search):
        self._context = context
        self._search = search

    def indexed_identities(self) -> frozenset[str]:
        return indexed_source_identities(
            self._context.session_factory, self._context.settings
        )

    def ranked(self, query: str, top_k: int) -> RankedTrace:
        started = perf_counter()
        results, diagnostics = self._search.rerank(query, top_k=top_k)
        elapsed_ms = (perf_counter() - started) * 1000.0
        return RankedTrace(
            ranked=tuple(
                RetrievedSource(item.chunk.source_key, item.chunk.file_name)
                for item in results
            ),
            elapsed_ms=elapsed_ms,
            rerank_applied=diagnostics.rerank_applied,
            degraded_reason=diagnostics.degraded_reason,
        )

    def probe(self, query: str) -> RouteProbe:
        dense = self._search.dense(query)
        keyword, _mode = self._search.keyword(query)
        fused, _diag = self._search.hybrid(query, top_k=RERANK_MAX_CANDIDATES)
        return RouteProbe(
            dense=tuple(
                RetrievedSource(chunk.source_key, chunk.file_name) for chunk in dense
            ),
            keyword=tuple(
                RetrievedSource(chunk.source_key, chunk.file_name) for chunk in keyword
            ),
            fused=tuple(
                RetrievedSource(item.chunk.source_key, item.chunk.file_name)
                for item in fused
            ),
        )


def test_retrieval_eval_meets_fusion_recall_gate(ingest_demo, search, context, reranker) -> None:
    ingest_demo()

    cases = load_ground_truth(DEMO_GROUND_TRUTH)
    selection = select_cases(cases)
    assert len(selection.included) == 41

    dataset_version = context.settings.demo_dataset_version
    chain = _SearchChain(context, search)
    results = evaluate_cases(
        selection.included,
        chain,
        top_k=TOP_K,
        candidate_k=CANDIDATE_K,
        dataset_version=dataset_version,
    )
    metrics = aggregate(results, top_k=TOP_K, candidate_k=CANDIDATE_K)

    assert metrics["cases"] == 41
    # 门禁层：融合
    assert metrics["fusion_recall_at_k"] >= MIN_RECALL
    assert 0.0 <= metrics["fusion_mrr"] <= 1.0
    # 诊断层：候选（健康）与非语义 final（仍记录，低于阈值也不影响门禁）
    assert metrics["candidate_recall_at_k"] >= 0.99
    assert 0.0 <= metrics["recall_at_k"] <= 1.0
    assert metrics["latency_ms"]["p50"] is not None
    assert metrics["latency_ms"]["p95"] is not None

    profile = resolve_profile(reranker.descriptor.score_kind, threshold=MIN_RECALL)
    report = build_report(
        ReportContext(
            run_id="integration-test",
            ground_truth_name=DEMO_GROUND_TRUTH.name,
            dataset_version=dataset_version,
            top_k=TOP_K,
            candidate_k=CANDIDATE_K,
            embedding_provider=context.settings.embedding_provider,
            rerank_provider=context.settings.rerank_provider,
            profile=profile,
            generated_at="2026-09-29T00:00:00+00:00",
        ),
        selection,
        results,
        metrics,
    )
    assert report["schema_version"] == SCHEMA_VERSION
    assert report["gate"]["metric"] == METRIC_FUSION_RECALL
    assert report["gate"]["passed"] is True
    assert report["gate"]["value"] == round(metrics["fusion_recall_at_k"], 4)
    assert report["metrics"]["fusion"]["recall_at_k"] == round(metrics["fusion_recall_at_k"], 4)
    assert report["metrics"]["final"]["recall_at_k"] == round(metrics["recall_at_k"], 4)
    assert report["metrics"]["final"]["semantics"] == SEMANTICS_NON_SEMANTIC
    assert len(report["cases"]) == 41
    assert report["dataset"]["included_cases"] == 41
    assert exit_code(report) == 0
