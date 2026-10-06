"""重排管线：候选上限、稳定排序、引用保持、降级与非法输出。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from app.core.errors import ApiError
from app.documents.blocks import key_value_row
from app.models import DocumentChunk
from app.rerank.base import descriptor_for
from app.search.reranking import (
    RERANK_CANDIDATE_METADATA_FIELDS,
    RERANK_MAX_CANDIDATES,
    STAGE_FINAL,
    STAGE_RERANKED,
    RerankingRetriever,
    format_rerank_candidate,
)
from app.search.types import (
    HybridResult,
    RetrievalDiagnostics,
    RetrievedChunk,
)


def _chunk(chunk_id: str, **overrides) -> RetrievedChunk:
    values = {
        "chunk_id": chunk_id,
        "doc_id": "doc",
        "text": f"text-{chunk_id}",
        "file_name": "a.pdf",
        "file_type": "pdf",
        "doc_category": "academic_policy",
        "source_type": "demo",
        "source_key": "k",
        "fused_score": 1.0,
    }
    values.update(overrides)
    return RetrievedChunk(**values)


def _hybrid_results(count: int, *, fused: float = 1.0) -> list[HybridResult]:
    return [
        HybridResult(chunk=_chunk(f"c{index:02d}", fused_score=fused), fused_score=fused)
        for index in range(count)
    ]


class _StubReranker:
    """只返回分数的测试替身；记录 Provider 实际收到的 query 与候选文本。"""

    def __init__(self, fn, descriptor):
        self._fn = fn
        self.descriptor = descriptor
        self.calls: list = []

    def rerank(self, query, candidates):
        self.calls.append({"query": query, "candidates": list(candidates)})
        return self._fn(query, list(candidates))

    def close(self) -> None:
        return None


class _FakeHybrid:
    """替换 ``HybridRetriever``：返回受控候选并记录 top_k 请求。"""

    results: list = []
    search_calls: list = []

    def __init__(self, *args, **kwargs):
        pass

    def search(self, query, filters=None, top_k=None):
        type(self).search_calls.append(top_k)
        return list(type(self).results), RetrievalDiagnostics()


def _stub(settings, fn) -> _StubReranker:
    return _StubReranker(fn, descriptor_for(settings))


@pytest.fixture(autouse=True)
def _reset_fake_hybrid():
    _FakeHybrid.results = []
    _FakeHybrid.search_calls = []
    yield
    _FakeHybrid.results = []
    _FakeHybrid.search_calls = []


# --- 候选上限与输出数量 ------------------------------------------------------


def test_rerank_caps_candidates_at_20_and_returns_top_10(ingest_demo, search, context, monkeypatch) -> None:
    ingest_demo()
    _FakeHybrid.results = _hybrid_results(30)
    _FakeHybrid.search_calls = []
    monkeypatch.setattr("app.search.reranking.HybridRetriever", _FakeHybrid)
    stub = _stub(context.settings, lambda q, c: [1.0 / (i + 1) for i in range(len(c))])

    results, diagnostics = search.rerank("学分认定", reranker_override=stub)

    assert _FakeHybrid.search_calls == [RERANK_MAX_CANDIDATES]
    assert len(stub.calls[0]["candidates"]) == RERANK_MAX_CANDIDATES
    assert len(results) == context.settings.rerank_top_k == 10
    assert diagnostics.rerank_input_candidates == RERANK_MAX_CANDIDATES
    assert diagnostics.reranked_candidates == 10
    assert diagnostics.rerank_applied is True
    assert [item.rerank_rank for item in results] == list(range(1, 11))


def test_rerank_explicit_top_k_six_returns_six(ingest_demo, search, context, monkeypatch) -> None:
    """显式 top_k=6 仍恰好返回 6 条，小于默认 10。"""
    ingest_demo()
    _FakeHybrid.results = _hybrid_results(30)
    monkeypatch.setattr("app.search.reranking.HybridRetriever", _FakeHybrid)
    stub = _stub(context.settings, lambda q, c: [1.0 / (i + 1) for i in range(len(c))])

    results, diagnostics = search.rerank("学分认定", top_k=6, reranker_override=stub)

    assert len(results) == 6
    assert diagnostics.reranked_candidates == 6
    assert [item.rerank_rank for item in results] == [1, 2, 3, 4, 5, 6]


def test_new_top_10_preserves_original_top_6_prefix(ingest_demo, search, context, monkeypatch) -> None:
    """K=6→10 后，原 top-6 必须逐项成为新 top-10 的前 6 项（顺序、对象、分数、名次不变）。"""
    ingest_demo()
    _FakeHybrid.results = _hybrid_results(30)
    monkeypatch.setattr("app.search.reranking.HybridRetriever", _FakeHybrid)
    stub = _stub(context.settings, lambda q, c: [float(len(c) - i) for i in range(len(c))])

    six, _ = search.rerank("学分认定", top_k=6, reranker_override=stub)
    ten, _ = search.rerank("学分认定", top_k=10, reranker_override=stub)

    assert len(ten) == 10
    assert [item.chunk.chunk_id for item in ten[:6]] == [item.chunk.chunk_id for item in six]
    for top6_item, top10_item in zip(six, ten):
        assert top10_item.chunk is top6_item.chunk
        assert top10_item.rerank_score == top6_item.rerank_score
        assert top10_item.rerank_rank == top6_item.rerank_rank


def test_rerank_returns_actual_count_when_fewer_than_top_k(
    ingest_demo, search, monkeypatch, settings
) -> None:
    ingest_demo()
    _FakeHybrid.results = _hybrid_results(3)
    monkeypatch.setattr("app.search.reranking.HybridRetriever", _FakeHybrid)
    stub = _stub(settings, lambda q, c: [0.5] * len(c))

    results, diagnostics = search.rerank("学分认定", reranker_override=stub)
    assert len(results) == 3
    assert diagnostics.reranked_candidates == 3


def test_rerank_empty_candidates_does_not_call_provider(
    ingest_demo, search, monkeypatch, settings
) -> None:
    ingest_demo()
    _FakeHybrid.results = []
    monkeypatch.setattr("app.search.reranking.HybridRetriever", _FakeHybrid)

    def _explode(query, candidates):  # pragma: no cover - 不应被调用
        raise AssertionError("空候选不得调用 Reranker Provider")

    stub = _stub(settings, _explode)

    results, diagnostics = search.rerank("学分认定", reranker_override=stub)

    assert results == []
    assert stub.calls == []
    assert diagnostics.rerank_applied is False
    assert diagnostics.reranked_candidates == 0
    assert diagnostics.rerank_input_candidates == 0


# --- 排序稳定性 --------------------------------------------------------------


def test_rerank_breaks_score_ties_by_fused_score_then_chunk_id(
    ingest_demo, search, monkeypatch, settings
) -> None:
    ingest_demo()
    # 制造相同 rerank 分数与部分相同 fused 分数
    _FakeHybrid.results = [
        HybridResult(chunk=_chunk("c00", fused_score=1.0), fused_score=1.0),
        HybridResult(chunk=_chunk("c01", fused_score=1.0), fused_score=1.0),
        HybridResult(chunk=_chunk("c02", fused_score=0.5), fused_score=0.5),
        HybridResult(chunk=_chunk("c03", fused_score=0.5), fused_score=0.5),
    ]
    monkeypatch.setattr("app.search.reranking.HybridRetriever", _FakeHybrid)
    stub = _StubReranker(lambda q, c: [1.0 for _ in c], descriptor_for(settings))

    first, _ = search.rerank("学分认定", reranker_override=stub)
    second, _ = search.rerank("学分认定", reranker_override=stub)

    expected = sorted(
        _FakeHybrid.results, key=lambda item: (-item.fused_score, item.chunk.chunk_id)
    )
    assert [item.chunk.chunk_id for item in first] == [
        item.chunk.chunk_id for item in expected
    ]
    # 确定性：重复运行结果完全一致
    assert [item.chunk.chunk_id for item in first] == [
        item.chunk.chunk_id for item in second
    ]


def test_rerank_score_is_sorted_descending(ingest_demo, search, settings) -> None:
    ingest_demo()
    stub = _stub(settings, lambda q, c: [float(i) for i in range(len(c))])
    results, _ = search.rerank("学分认定", reranker_override=stub)
    scores = [item.rerank_score for item in results]
    assert scores == sorted(scores, reverse=True)


# --- 引用保持 ----------------------------------------------------------------


def test_rerank_keeps_citations_aligned_when_order_is_reversed(ingest_demo, search, settings) -> None:
    ingest_demo()
    hybrid_results, _ = search.hybrid("学分认定", top_k=RERANK_MAX_CANDIDATES)
    assert len(hybrid_results) >= 3
    original = {item.chunk.chunk_id: item.chunk for item in hybrid_results}

    # 升序分数 ⇒ 排序后整体逆序，用最强的错位压力验证引用不串位
    stub = _stub(settings, lambda q, c: [float(i) for i in range(len(c))])
    results, diagnostics = search.rerank("学分认定", reranker_override=stub)

    assert diagnostics.rerank_applied is True
    # 分数随候选顺序递增 ⇒ 重排后整体逆序；只取前 rerank_top_k 条
    candidates = hybrid_results[:RERANK_MAX_CANDIDATES]
    expected = list(reversed(candidates))[: len(results)]
    assert [item.chunk.chunk_id for item in results] == [
        item.chunk.chunk_id for item in expected
    ]

    for item in results:
        source = original[item.chunk.chunk_id]
        assert item.chunk.text == source.text
        assert item.chunk.locator == source.locator
        assert item.chunk.citation == source.citation
        assert item.chunk.doc_id == source.doc_id
        assert item.chunk.file_name == source.file_name
        assert item.chunk.page_number == source.page_number
        assert item.chunk.section_title == source.section_title

        payload = item.as_dict()
        assert payload["quote"] == source.text
        assert payload["doc_id"] == source.doc_id
        assert payload["file_name"] == source.file_name
        assert payload["source_type"] == source.source_type
        assert payload["section_title"] == source.section_title
        assert payload["rerank_score"] == item.rerank_score


# --- 降级 --------------------------------------------------------------------


def test_rerank_degrades_to_rrf_order_without_faking_scores(ingest_demo, search, settings) -> None:
    ingest_demo()
    hybrid_results, _ = search.hybrid("学分认定", top_k=RERANK_MAX_CANDIDATES)

    def _unavailable(query, candidates):
        raise ApiError(
            "RERANK_PROVIDER_UNAVAILABLE", details={"reason": "api_rerank_timeout"}
        )

    stub = _StubReranker(_unavailable, descriptor_for(settings))
    results, diagnostics = search.rerank("学分认定", reranker_override=stub)

    assert results, "降级必须仍返回可用的 RRF 结果"
    assert [item.chunk.chunk_id for item in results] == [
        item.chunk.chunk_id for item in hybrid_results[: len(results)]
    ]
    assert diagnostics.rerank_applied is False
    assert diagnostics.degraded_reason == "api_rerank_timeout"
    for item in results:
        assert item.rerank_applied is False
        assert item.rerank_score is None
        assert item.rerank_rank is None
        assert item.degraded_reason == "api_rerank_timeout"
        # 绝不用 fused_score 冒充 rerank_score
        assert item.fused_score is not None
        assert item.as_dict()["rerank_score"] is None


def test_rerank_degradation_reason_is_stable_and_safe(ingest_demo, search, settings) -> None:
    ingest_demo()

    def _unavailable(query, candidates):
        raise ApiError(
            "RERANK_PROVIDER_UNAVAILABLE",
            details={"reason": "https://invalid.example.invalid/v1"},
        )

    stub = _StubReranker(_unavailable, descriptor_for(settings))
    _results, diagnostics = search.rerank("学分认定", reranker_override=stub)

    # 非白名单原因统一回退为稳定常量，绝不泄漏 URL
    assert diagnostics.degraded_reason == "reranker_unavailable"
    assert "invalid.example.invalid" not in str(diagnostics.as_dict())


# --- 非法输出 ----------------------------------------------------------------


@pytest.mark.parametrize(
    "fn",
    [
        pytest.param(lambda q, c: [0.1], id="score_count_mismatch"),
        pytest.param(lambda q, c: [float("nan")] * len(c), id="nan_score"),
        pytest.param(lambda q, c: [float("inf")] * len(c), id="infinity_score"),
        pytest.param(lambda q, c: ["high"] * len(c), id="non_numeric_score"),
        pytest.param(lambda q, c: None, id="not_a_sequence"),
    ],
)
def test_rerank_rejects_invalid_provider_output(ingest_demo, search, settings, fn) -> None:
    ingest_demo()
    stub = _StubReranker(fn, descriptor_for(settings))
    with pytest.raises(ApiError) as error:
        search.rerank("学分认定", reranker_override=stub)
    assert error.value.code == "RERANK_RESPONSE_INVALID"


def test_rerank_does_not_swallow_internal_invariant_errors(ingest_demo, search, settings) -> None:
    """非法返回结构属于内部不变量错误：必须抛出，而不是降级。"""
    ingest_demo()
    stub = _StubReranker(lambda q, c: [float("nan")], descriptor_for(settings))
    with pytest.raises(ApiError) as error:
        search.rerank("学分认定", reranker_override=stub)
    assert error.value.code == "RERANK_RESPONSE_INVALID"


# --- 诊断与副作用 ------------------------------------------------------------


def test_rerank_diagnostics_are_safe_and_versioned(ingest_demo, search) -> None:
    ingest_demo()
    _results, diagnostics = search.rerank("学分认定")
    payload = diagnostics.as_dict()

    for key in (
        "rerank_input_candidates",
        "reranked_candidates",
        "rerank_elapsed_ms",
        "rerank_applied",
        "reranker_provider",
        "reranker_revision",
        "reranker_fingerprint",
        "rerank_score_kind",
        "degraded_reason",
        "rrf_version",
    ):
        assert key in payload
    assert payload["reranker_provider"] == "fake"
    assert payload["rrf_version"] == "rrf-v1"
    assert payload["rerank_elapsed_ms"] >= 0

    serialized = str(payload)
    for forbidden in ("/app/", "F:\\", "C:\\", "api_key", "Bearer ", "sk-", "password"):
        assert forbidden not in serialized
    # 不得包含 query 原文或整份正文
    assert "学分认定" not in serialized


def test_rerank_does_not_write_any_index(ingest_demo, search, context, worker) -> None:
    """Reranker 是查询时能力：不得写 SQLite / Chroma / FTS，也不得改变可检索集合。"""
    ingest_demo()

    def _snapshot() -> tuple[int, int, int]:
        with context.session_factory() as session:
            chunks = int(session.scalar(select(func.count(DocumentChunk.id))) or 0)
        return chunks, worker.vectors.count(), len(search.hybrid("学分认定")[0])

    before = _snapshot()
    search.rerank("学分认定")
    search.rerank("学分认定")
    after = _snapshot()

    assert before == after


def test_rerank_applies_filters(ingest_demo, search) -> None:
    from app.search.types import RetrievalFilters

    ingest_demo()
    filters = RetrievalFilters(doc_category="academic_policy")
    results, _diagnostics = search.rerank("学分", filters)
    assert results
    assert {item.chunk.doc_category for item in results} == {"academic_policy"}

    empty, diagnostics = search.rerank("学分", RetrievalFilters(doc_category="nope"))
    assert empty == []
    assert diagnostics.rerank_applied is False


# --- 候选文本：白名单 JSON 元数据 --------------------------------------------


def _rich_chunk(**overrides) -> RetrievedChunk:
    values = {
        "doc_id": "doc-secret",
        "text": "正文内容：学分要求总览",
        "file_name": "03-培养方案.pdf",
        "doc_category": "academic_policy",
        "source_key": "corpus/private",
        "document_version": "2026.1",
        "effective_from": "2026-02-01",
        "page_number": 3,
        "sheet_name": None,
        "row_start": None,
        "row_end": None,
        "section_title": "三、学分要求",
        "citation": {
            "course_code": "QM-CS201",
            "file_name": "03-培养方案.pdf",
            "doc_id": "doc-secret",
            "chunk_id": "c-rich",
            "source_key": "corpus/private",
            "rerank_score": 0.5,
        },
    }
    values.update(overrides)
    return _chunk("c-rich", **values)


def test_format_rerank_candidate_keeps_body_and_stable_key_order() -> None:
    text = format_rerank_candidate(_rich_chunk())
    body, meta = text.split("\n", 1)
    assert body == "正文内容：学分要求总览"

    payload = json.loads(meta)
    # 键顺序稳定（白名单顺序），缺失值（sheet_name/row_start/row_end）省略
    assert list(payload) == [
        "course_code",
        "doc_category",
        "document_version",
        "effective_from",
        "page_number",
        "section_title",
    ]
    assert payload["course_code"] == "QM-CS201"
    assert payload["page_number"] == 3
    assert set(payload) <= set(RERANK_CANDIDATE_METADATA_FIELDS)


def test_format_rerank_candidate_course_code_only_from_non_empty_citation_string() -> None:
    for citation, expected in (
        ({"course_code": "QM-CS201"}, "QM-CS201"),
        ({"course_code": "   "}, None),
        ({"course_code": 123}, None),
        ({"course_code": None}, None),
        ({}, None),
    ):
        meta = json.loads(format_rerank_candidate(_chunk("c", citation=citation)).split("\n", 1)[1])
        assert meta.get("course_code") == expected


def test_format_rerank_candidate_omits_sensitive_identifiers_and_scores() -> None:
    text = format_rerank_candidate(
        _rich_chunk(fused_score=0.9, dense_score=0.8, keyword_score=0.7)
    )
    for leaked in (
        "03-培养方案.pdf",
        "doc-secret",
        "c-rich",
        "corpus/private",
        "file_name",
        "source_key",
        "doc_id",
        "chunk_id",
        "fused_score",
        "dense_score",
        "keyword_score",
        "rerank_score",
    ):
        assert leaked not in text, leaked


def test_retriever_sends_formatted_candidates_once_and_maps_scores(
    ingest_demo, search, context, monkeypatch
) -> None:
    ingest_demo()
    _FakeHybrid.results = _hybrid_results(20)
    monkeypatch.setattr("app.search.reranking.HybridRetriever", _FakeHybrid)
    stub = _stub(context.settings, lambda q, c: [float(len(c) - i) for i in range(len(c))])

    results, _diagnostics = search.rerank("学分认定", reranker_override=stub)

    assert len(stub.calls) == 1, "20 条候选仍只调用一次 Rerank"
    sent = stub.calls[0]["candidates"]
    assert len(sent) == RERANK_MAX_CANDIDATES == 20
    # 候选 = 原正文 + 白名单元数据，顺序与 fused 候选一一对应
    assert sent == [format_rerank_candidate(item.chunk) for item in _FakeHybrid.results[:20]]
    # 分数按位置映射回原候选：分数随位置递减 ⇒ top-10 就是原顺序前 10 条
    assert [item.chunk.chunk_id for item in results] == [f"c{i:02d}" for i in range(10)]
    assert [item.rerank_score for item in results] == [
        20.0, 19.0, 18.0, 17.0, 16.0, 15.0, 14.0, 13.0, 12.0, 11.0,
    ]
    assert len(results) <= 10


def test_retriever_degradation_still_sends_formatted_candidates(
    ingest_demo, search, context, monkeypatch
) -> None:
    """降级路径不回归：候选仍格式化、仍只调用一次、仍返回 RRF 前 K 条且不伪造分数。"""
    ingest_demo()
    _FakeHybrid.results = _hybrid_results(20)
    monkeypatch.setattr("app.search.reranking.HybridRetriever", _FakeHybrid)
    seen: dict[str, int] = {}

    def _unavailable(query, candidates):
        seen["n"] = len(candidates)
        seen["formatted"] = int(candidates[0].startswith("text-c00\n{"))
        raise ApiError("RERANK_PROVIDER_UNAVAILABLE", details={"reason": "api_rerank_timeout"})

    stub = _stub(context.settings, _unavailable)
    results, diagnostics = search.rerank("学分认定", reranker_override=stub)

    assert len(stub.calls) == 1
    assert seen == {"n": 20, "formatted": 1}
    assert diagnostics.rerank_applied is False
    assert diagnostics.degraded_reason == "api_rerank_timeout"
    assert len(results) == context.settings.rerank_top_k == 10
    assert all(item.rerank_applied is False and item.rerank_score is None for item in results)


# --- 撤回硬分区后的回归：本地不得覆盖 Provider 排序 ---------------------------


def _coded_hybrid_results() -> list[HybridResult]:
    """20 条合成候选：原 rank 7、10 各带精确课程代码（citation / 多行 key_value_row）。

    ``page_number`` 作为可区分定位；分数随位置递减 ⇒ 原始名次 = index + 1。
    """
    results: list[HybridResult] = []
    for index in range(20):
        if index == 6:
            chunk = _chunk(f"c{index:02d}", page_number=index, citation={"course_code": "QM-CS201"})
        elif index == 9:
            chunk = _multi_code_chunk("c09", ["QM-CS201", "QM-GE101"], page_number=index)
        else:
            chunk = _chunk(f"c{index:02d}", page_number=index)
        results.append(HybridResult(chunk=chunk, fused_score=1.0))
    return results


def _descending(settings):
    return _stub(settings, lambda q, c: [float(len(c) - i) for i in range(len(c))])


@pytest.mark.parametrize("query", ["QM-CS201 的学分", "QM-GE101 的学分"])
def test_course_code_query_never_overrides_provider_order(search, context, monkeypatch, query) -> None:
    """query 含课程代码，且原 rank 7/10 候选带精确 citation/多行代码时，本地不得改变排序。"""
    _FakeHybrid.results = _coded_hybrid_results()
    monkeypatch.setattr("app.search.reranking.HybridRetriever", _FakeHybrid)
    stub = _descending(context.settings)

    results, diagnostics = search.rerank(query, reranker_override=stub)

    assert len(stub.calls) == 1
    assert diagnostics.rerank_applied is True
    # 严格按 Provider 排序前 10：不因课程代码把 c06/c09 提前
    assert [item.chunk.chunk_id for item in results] == [f"c{i:02d}" for i in range(10)]
    assert [item.rerank_rank for item in results] == list(range(1, 11))
    assert len(results) <= 10


def test_final_equals_reranked_prefix_and_ranks_are_sequential(context, worker, monkeypatch) -> None:
    _FakeHybrid.results = _coded_hybrid_results()
    monkeypatch.setattr("app.search.reranking.HybridRetriever", _FakeHybrid)
    retriever = RerankingRetriever(
        context.session_factory(),
        context.settings,
        worker.vectors,
        worker.embeddings,
        _descending(context.settings),
        worker.coordinator,
    )
    sink: dict[str, list[dict[str, object]]] = {}
    try:
        results, _diagnostics = retriever.search("QM-CS201 的学分", stage_sink=sink)
    finally:
        retriever.session.close()

    reranked_pages = [entry["locator"]["page_number"] for entry in sink[STAGE_RERANKED]]
    final_pages = [entry["locator"]["page_number"] for entry in sink[STAGE_FINAL]]
    # reranked 保留外部重排的完整顺序
    assert reranked_pages == list(range(20))
    # final 严格等于 reranked 前 10 项
    assert final_pages == reranked_pages[:10]
    assert [item.chunk.page_number for item in results] == final_pages
    assert [item.rerank_rank for item in results] == list(range(1, 11))
    assert len(results) <= 10


def test_rerank_sends_citation_course_code_in_candidate_metadata(search, context, monkeypatch) -> None:
    """课程代码仍通过候选白名单元数据交给 Reranker；本地不据此改写排序。"""
    _FakeHybrid.results = _coded_hybrid_results()
    monkeypatch.setattr("app.search.reranking.HybridRetriever", _FakeHybrid)
    stub = _descending(context.settings)

    search.rerank("QM-CS201 的学分", reranker_override=stub)

    sent = stub.calls[0]["candidates"]
    assert len(sent) == RERANK_MAX_CANDIDATES
    meta = json.loads(sent[6].split("\n", 1)[1])
    assert meta.get("course_code") == "QM-CS201"
    assert set(meta) <= set(RERANK_CANDIDATE_METADATA_FIELDS)


def test_rerank_degradation_ignores_course_code_query(search, context, monkeypatch) -> None:
    _FakeHybrid.results = _coded_hybrid_results()
    monkeypatch.setattr("app.search.reranking.HybridRetriever", _FakeHybrid)

    def _unavailable(query, candidates):
        raise ApiError("RERANK_PROVIDER_UNAVAILABLE", details={"reason": "api_rerank_timeout"})

    results, diagnostics = search.rerank(
        "QM-CS201 的学分", reranker_override=_stub(context.settings, _unavailable)
    )
    assert [item.chunk.chunk_id for item in results] == [f"c{i:02d}" for i in range(10)]
    assert diagnostics.rerank_applied is False
    assert all(item.rerank_applied is False and item.rerank_score is None for item in results)


def test_course_code_query_keeps_candidates_and_leaks_nothing(search, context, monkeypatch) -> None:
    _FakeHybrid.results = _coded_hybrid_results()
    monkeypatch.setattr("app.search.reranking.HybridRetriever", _FakeHybrid)
    results, diagnostics = search.rerank("QM-CS201 的学分", reranker_override=_descending(context.settings))

    originals = {item.chunk.chunk_id: item.chunk for item in _FakeHybrid.results}
    for item in results:
        assert item.chunk is originals[item.chunk.chunk_id]
        assert item.chunk.citation == originals[item.chunk.chunk_id].citation
        assert item.as_dict()["quote"] == originals[item.chunk.chunk_id].text

    serialized = str(diagnostics.as_dict())
    for forbidden in ("/app/", "F:", "C:", "sk-", "Bearer ", "api_key", "password"):
        assert forbidden not in serialized
    # 不泄漏 query 代码 / 候选正文
    assert "QM-CS201" not in serialized
    assert "text-c09" not in serialized


# --- 合成候选辅助：真实 key_value_row 多行 chunk -----------------------------

_COURSE_HEADER = ["星期", "节次", "时间", "课程代码", "课程名称"]


def _multi_code_chunk(chunk_id: str, codes, **overrides) -> RetrievedChunk:
    """用真实 ``key_value_row`` 格式构造含多行、每行一个课程代码的单个 chunk。"""
    lines = [
        key_value_row(
            _COURSE_HEADER,
            ["星期二", f"第{index + 1}-{index + 2}节", "10:00-11:40", code, f"课程{index}"],
        )
        for index, code in enumerate(codes)
    ]
    return _chunk(chunk_id, text="\n".join(lines), **overrides)
