"""DenseRetriever、HybridRetriever 与确定性 RRF 融合。"""

from __future__ import annotations

import sys

import pytest
from sqlalchemy import update

from app import constants
from app.core.errors import ApiError
from app.models import DemoActiveDataset, Document
from app.search.hybrid import RRF_K, RRF_VERSION, fuse
from app.search.types import RetrievalFilters, RetrievedChunk


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
    }
    values.update(overrides)
    return RetrievedChunk(**values)


# --- RRF 纯函数 -------------------------------------------------------------


def test_rrf_fuses_ranks_with_versioned_k() -> None:
    assert RRF_K == 60
    assert RRF_VERSION == "rrf-v1"

    dense = [_chunk("a"), _chunk("b"), _chunk("c")]
    keyword = [_chunk("c"), _chunk("a"), _chunk("d")]

    fused = fuse(dense, keyword, RRF_K)
    scores = {item.chunk_id: item.fused_score for item in fused}

    # a: dense#1 + keyword#2 ；b: dense#2 ；c: dense#3 + keyword#1 ；d: keyword#3
    assert scores["a"] == pytest.approx(1 / 61 + 1 / 62)
    assert scores["b"] == pytest.approx(1 / 62)
    assert scores["c"] == pytest.approx(1 / 63 + 1 / 61)
    assert scores["d"] == pytest.approx(1 / 63)

    # 排序：fused 降序 → 最佳单路 rank → chunk_id
    assert [item.chunk_id for item in fused] == ["a", "c", "b", "d"]


def test_rrf_deduplicates_same_chunk() -> None:
    fused = fuse([_chunk("x")], [_chunk("x")])
    assert len(fused) == 1
    assert fused[0].dense_rank == 1
    assert fused[0].keyword_rank == 1
    assert fused[0].fused_score == pytest.approx(1 / 61 + 1 / 61)


def test_rrf_handles_single_route_results() -> None:
    dense_only = fuse([_chunk("a"), _chunk("b")], [])
    assert [item.chunk_id for item in dense_only] == ["a", "b"]
    assert all(item.keyword_rank is None for item in dense_only)

    keyword_only = fuse([], [_chunk("c")])
    assert [item.chunk_id for item in keyword_only] == ["c"]
    assert keyword_only[0].dense_rank is None


def test_rrf_tie_is_broken_stably_by_chunk_id() -> None:
    fused = fuse([_chunk("b"), _chunk("a")], [])
    # dense#1= b, dense#2= a ⇒ 分数不同，b 在前
    assert [item.chunk_id for item in fused] == ["b", "a"]

    same_score = fuse([_chunk("z")], [_chunk("y")])
    assert [item.chunk_id for item in same_score] == ["y", "z"]


def test_rrf_is_deterministic() -> None:
    dense = [_chunk("a"), _chunk("b")]
    keyword = [_chunk("b"), _chunk("c")]
    first = [(item.chunk_id, item.fused_score) for item in fuse(dense, keyword)]
    second = [(item.chunk_id, item.fused_score) for item in fuse(dense, keyword)]
    assert first == second


# --- DenseRetriever ---------------------------------------------------------


def test_dense_retriever_returns_full_metadata(ingest_demo, search, context, worker) -> None:
    ingest_demo()
    hits = search.dense("学分认定")
    assert hits
    assert len(hits) <= context.settings.dense_top_k

    chunk = hits[0]
    assert chunk.chunk_id
    assert chunk.dense_rank == 1
    assert chunk.dense_score is not None and 0.0 < chunk.dense_score <= 1.0
    assert chunk.file_name and chunk.doc_category
    # 基础测试不下载模型、不访问网络
    assert worker.embeddings.loaded is False
    assert "sentence_transformers" not in sys.modules
    assert "torch" not in sys.modules


def test_dense_dimension_must_match_collection(ingest_demo, search, worker, monkeypatch) -> None:
    ingest_demo()
    monkeypatch.setattr(
        worker.embeddings, "embed_documents", lambda texts: [[0.0] * 8 for _ in texts]
    )
    with pytest.raises(ApiError) as error:
        search.dense("学分认定")
    assert error.value.code == "EMBEDDING_DIMENSION_MISMATCH"


def test_dense_revalidates_eligibility_in_sqlite(ingest_demo, search, context) -> None:
    """Chroma metadata 不可信：必须由 SQLite 再判定资格。"""
    ingest_demo()
    hits = search.dense("学分认定")
    assert hits
    target = hits[0]

    with context.session_factory() as session:
        session.execute(
            update(Document)
            .where(Document.id == target.doc_id)
            .values(activation_state=constants.ACTIVATION_CANDIDATE, retrievable=False)
        )
        session.commit()

    after = search.dense("学分认定")
    assert target.doc_id not in {hit.doc_id for hit in after}


def test_dense_excludes_deleted_and_failed(ingest_demo, search, context) -> None:
    ingest_demo()
    hits = search.dense("学分认定")
    assert hits
    doc_ids = sorted({hit.doc_id for hit in hits})

    with context.session_factory() as session:
        session.execute(
            update(Document)
            .where(Document.id.in_(doc_ids))
            .values(status=constants.STATUS_FAILED, retrievable=False)
        )
        session.commit()

    after = search.dense("学分认定")
    assert {hit.doc_id for hit in after}.isdisjoint(doc_ids)


def test_dense_requires_active_pointer_for_demo(ingest_demo, search, context) -> None:
    ingest_demo()
    assert search.dense("学分认定")

    with context.session_factory() as session:
        session.execute(
            update(DemoActiveDataset).values(pipeline_fingerprint="stale-pipeline")
        )
        session.commit()

    assert search.dense("学分认定") == []


def test_dense_empty_query_is_rejected(ingest_demo, search) -> None:
    ingest_demo()
    with pytest.raises(ApiError) as error:
        search.dense("   ")
    assert error.value.code == "RETRIEVAL_QUERY_INVALID"


# --- HybridRetriever --------------------------------------------------------


def test_hybrid_fuses_dense_and_keyword(ingest_demo, search, context) -> None:
    ingest_demo()
    results, diagnostics = search.hybrid("学分认定")
    assert results

    chunk_ids = [item.chunk.chunk_id for item in results]
    assert len(chunk_ids) == len(set(chunk_ids)), "同一 chunk 只能出现一次"

    scores = [item.fused_score for item in results]
    assert scores == sorted(scores, reverse=True)

    for item in results[:3]:
        payload = item.as_dict()
        assert payload["chunk_id"]
        assert payload["fused_score"] > 0
        assert payload["quote"]
        assert payload["file_name"]
        assert payload["doc_category"]
        assert "page_number" in payload
        assert "section_title" in payload

    assert diagnostics.rrf_k == RRF_K
    assert diagnostics.dense_candidates >= 0
    assert diagnostics.keyword_candidates >= 0
    assert diagnostics.fused_candidates == len(results)
    assert diagnostics.demo_available is True
    assert diagnostics.fts_schema_version
    assert diagnostics.vector_schema_version
    assert diagnostics.embedding_provider == "fake"
    assert diagnostics.elapsed_ms >= 0
    assert context.settings.dense_top_k == 12 and context.settings.keyword_top_k == 12


def test_hybrid_diagnostics_do_not_leak(ingest_demo, search) -> None:
    ingest_demo()
    _results, diagnostics = search.hybrid("学分认定")
    serialized = str(diagnostics.as_dict())

    for forbidden in ("/app/", "F:\\", "C:\\", "api_key", "Bearer ", "sk-", "password"):
        assert forbidden not in serialized
    # 不得包含整份正文
    assert "学分认定" not in serialized


def test_hybrid_applies_filters_to_both_routes(ingest_demo, search) -> None:
    ingest_demo()
    filters = RetrievalFilters(doc_category="academic_policy")
    results, diagnostics = search.hybrid("学分", filters)
    assert results
    assert {item.chunk.doc_category for item in results} == {"academic_policy"}
    assert diagnostics.applied_filters == {"doc_category": "academic_policy"}

    empty_results, _ = search.hybrid("学分", RetrievalFilters(doc_category="nope"))
    assert empty_results == []


def test_hybrid_respects_top_k(ingest_demo, search) -> None:
    ingest_demo()
    results, diagnostics = search.hybrid("学分认定", top_k=2)
    assert len(results) <= 2
    assert diagnostics.fused_candidates == len(results)


def test_hybrid_degrades_to_keyword_when_dense_returns_nothing(
    ingest_demo, search, monkeypatch
) -> None:
    """Dense 无结果时，混合检索仍必须返回关键词结果。"""
    ingest_demo()

    from app.search.dense import DenseRetriever

    monkeypatch.setattr(DenseRetriever, "search", lambda self, *a, **k: [])
    results, diagnostics = search.hybrid("学分认定")
    assert results
    assert diagnostics.dense_candidates == 0
    assert diagnostics.keyword_candidates > 0
    assert all(item.chunk.dense_rank is None for item in results)
    assert all(item.chunk.keyword_rank is not None for item in results)


def test_hybrid_does_not_run_reranker(ingest_demo, search) -> None:
    """阶段 4 不执行 Reranker：结果里不得出现任何 rerank 痕迹。"""
    ingest_demo()
    results, diagnostics = search.hybrid("学分认定")
    payload = results[0].as_dict()
    assert "rerank_score" not in payload
    assert "rerank_rank" not in payload
    assert "rerank" not in str(diagnostics.as_dict())
    assert not hasattr(diagnostics, "reranker_provider")


def test_hybrid_never_returns_sources_from_chroma_only(ingest_demo, search, context) -> None:
    """Chroma 中残留但 SQLite 已不可检索的向量不得出现在结果里。"""
    ingest_demo()
    results, _ = search.hybrid("学分认定")
    assert results

    with context.session_factory() as session:
        session.execute(update(Document).values(retrievable=False))
        session.commit()

    after, diagnostics = search.hybrid("学分认定")
    assert after == []
    assert diagnostics.fused_candidates == 0


def test_hybrid_preserves_locator_and_section_fields(ingest_demo, search, context) -> None:
    """引用定位信息在融合后不得丢失。"""
    ingest_demo()
    results, _ = search.hybrid("学分认定")
    assert results

    from app.models import DocumentChunk

    payload = results[0].as_dict()
    with context.session_factory() as session:
        chunk = session.get(DocumentChunk, payload["chunk_id"])
        assert chunk is not None
        assert payload["doc_id"] == chunk.doc_id
        assert payload["section_title"] == (chunk.locator or {}).get("section_title")
        assert payload["page_number"] == (chunk.locator or {}).get("page_number")
        assert payload["dataset_version"] == "2026.1"
    assert payload["quote"] == chunk.text
