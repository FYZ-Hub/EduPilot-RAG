"""KeywordRetriever：FTS5/BM25 关键词召回与可检索资格过滤。"""

from __future__ import annotations

from sqlalchemy import select, update

from app import constants
from app.documents.fingerprint import pipeline_fingerprint
from app.models import DemoActiveDataset, Document, DocumentChunk, DocumentPipelineState, utcnow
from app.search.types import RetrievalFilters


def _queries(hits) -> list[str]:
    return [hit.chunk_id for hit in hits]


def _all_text(hits) -> str:
    return "\n".join(hit.text for hit in hits)


def test_keyword_retriever_returns_full_citation_metadata(ingest_demo, search) -> None:
    payload = ingest_demo()
    assert payload["status"] == constants.JOB_COMPLETED

    hits, mode = search.keyword("学分认定")
    assert hits, "中文条款必须可召回"
    assert mode in {"and", "or"}

    chunk = hits[0]
    assert chunk.chunk_id and len(chunk.chunk_id) == 64
    assert chunk.doc_id
    assert chunk.text
    assert chunk.keyword_rank == 1
    assert chunk.keyword_score is not None
    assert chunk.file_name
    assert chunk.file_type in constants.FILE_TYPES
    assert chunk.doc_category
    assert chunk.dataset_version == "2026.1"
    assert chunk.effective_from


def test_keyword_top_k_defaults_to_twelve(ingest_demo, search, context) -> None:
    ingest_demo()
    assert context.settings.keyword_top_k == 12
    hits, _mode = search.keyword("学分")
    assert len(hits) <= 12


def test_course_code_and_file_name_and_policy_queries(ingest_demo, search) -> None:
    ingest_demo()

    cases = {
        "QM-CS201": "QM-CS201",
        "补考": "补考",
        "重修": "重修",
        "学分认定": "学分认定",
        # 具体考试日期（演示语料中的真实日期）
        "2027-01-05": "2027-01-05",
        "培养方案": "培养方案",
    }
    for query, expected in cases.items():
        hits, _mode = search.keyword(query)
        assert hits, f"查询 {query!r} 必须返回结果"
        assert expected in _all_text(hits), f"查询 {query!r} 的结果必须包含 {expected!r}"


def test_keyword_retrieval_is_stable_and_tie_broken_by_chunk_id(ingest_demo, search) -> None:
    ingest_demo()
    first, _ = search.keyword("学分认定")
    second, _ = search.keyword("学分认定")
    assert _queries(first) == _queries(second)

    scores = [hit.keyword_score for hit in first]
    assert scores == sorted(scores, reverse=True), "BM25 分数必须单调不增"
    # 分数相同的相邻项必须按 chunk_id 升序
    for left, right in zip(first, first[1:]):
        if left.keyword_score == right.keyword_score:
            assert left.chunk_id < right.chunk_id


def test_keyword_excludes_non_retrievable_documents(ingest_demo, search, context) -> None:
    ingest_demo()
    hits, _ = search.keyword("学分认定")
    assert hits
    target = hits[0]

    with context.session_factory() as session:
        session.execute(
            update(Document)
            .where(Document.id == target.doc_id)
            .values(activation_state=constants.ACTIVATION_CANDIDATE, retrievable=False)
        )
        session.commit()

    after, _ = search.keyword("学分认定")
    assert target.doc_id not in {hit.doc_id for hit in after}


def test_keyword_excludes_inactive_failed_and_deleted_documents(ingest_demo, search, context) -> None:
    ingest_demo()
    hits, _ = search.keyword("培养方案")
    assert hits
    doc_ids = sorted({hit.doc_id for hit in hits})

    variants = (
        {"activation_state": constants.ACTIVATION_INACTIVE, "retrievable": False},
        {"status": constants.STATUS_FAILED, "retrievable": False},
        {"deleted_at": utcnow()},
        {"retrievable": False},
    )
    for index, values in enumerate(variants):
        with context.session_factory() as session:
            session.execute(
                update(Document).where(Document.id == doc_ids[index]).values(**values)
            )
            session.commit()

        remaining, _ = search.keyword("培养方案")
        for doc_id in doc_ids[: index + 1]:
            assert doc_id not in {hit.doc_id for hit in remaining}


def test_keyword_never_returns_unfinished_documents(ingest_demo, search, context) -> None:
    ingest_demo()
    hits, _ = search.keyword("学分认定")
    target = hits[0]

    with context.session_factory() as session:
        session.execute(
            update(DocumentPipelineState)
            .where(DocumentPipelineState.doc_id == target.doc_id)
            .values(last_completed_stage=constants.STAGE_VECTOR_INDEXED)
        )
        session.commit()

    after, _ = search.keyword("学分认定")
    assert target.doc_id not in {hit.doc_id for hit in after}


def test_keyword_requires_matching_active_pointer(ingest_demo, search, context) -> None:
    """active 指针的流水线指纹与当前配置不一致时，demo 整体不可检索。"""
    ingest_demo()
    hits, _ = search.keyword("学分认定")
    assert hits

    with context.session_factory() as session:
        session.execute(
            update(DemoActiveDataset).values(pipeline_fingerprint="stale-pipeline")
        )
        session.commit()

    after, _ = search.keyword("学分认定")
    assert after == []

    # 恢复后重新可检索（不依赖缓存）
    with context.session_factory() as session:
        session.execute(
            update(DemoActiveDataset).values(
                pipeline_fingerprint=pipeline_fingerprint(context.settings)
            )
        )
        session.commit()
    restored, _ = search.keyword("学分认定")
    assert restored


def test_keyword_filters_by_doc_category(ingest_demo, search) -> None:
    ingest_demo()
    filters = RetrievalFilters(doc_category="academic_policy")
    hits, _ = search.keyword("学分", filters)
    assert hits
    assert {hit.doc_category for hit in hits} == {"academic_policy"}

    empty, _ = search.keyword("学分", RetrievalFilters(doc_category="does_not_exist"))
    assert empty == []


def test_unknown_filter_field_is_rejected() -> None:
    import pytest

    from app.core.errors import ApiError

    with pytest.raises(ApiError) as error:
        RetrievalFilters.from_mapping({"unknown": "x"})
    assert error.value.code == "RETRIEVAL_FILTER_INVALID"
    assert error.value.details["unknown_fields"] == ["unknown"]


def test_empty_and_blank_query_returns_nothing(ingest_demo, search) -> None:
    ingest_demo()
    for query in ("", "   ", "!!!"):
        hits, mode = search.keyword(query)
        assert hits == []
        assert mode == "empty"


def test_fake_index_is_not_reused_after_provider_switch(
    ingest_demo, search, context, tmp_path
) -> None:
    """切换 Provider（Local/API）后指纹改变，绝不能复用 Fake 建立的索引。"""
    ingest_demo()
    hits, _ = search.keyword("学分认定")
    assert hits, "Fake 环境下先确认可检索"

    from tests.conftest import build_settings
    from app.search.keyword import KeywordRetriever

    local_settings = build_settings(tmp_path, embedding_provider="local")
    with context.session_factory() as session:
        local_hits, _mode = KeywordRetriever(session, local_settings).search("学分认定")
    assert local_hits == [], "Local 配置下 Fake 索引必须不可检索，需按新指纹重建"


def test_keyword_hits_are_not_sourced_from_other_documents(
    ingest_demo, search, context
) -> None:
    """返回的 chunk 必须真实属于 SQLite 中的该文档（防止索引与业务表错位）。"""
    ingest_demo()
    hits, _ = search.keyword("学分认定")
    with context.session_factory() as session:
        rows = {
            chunk.id: chunk.doc_id
            for chunk in session.scalars(
                select(DocumentChunk).where(
                    DocumentChunk.id.in_([hit.chunk_id for hit in hits])
                )
            )
        }
    assert set(rows) == set(_queries(hits))
    for hit in hits:
        assert rows[hit.chunk_id] == hit.doc_id
