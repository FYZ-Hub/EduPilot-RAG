"""三方 ID 对账、差异精确修复与各检查点崩溃恢复。"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select, text, update

from app import constants
from app.core.errors import ApiError
from app.demo import service as demo_service
from app.models import (
    DemoActiveDataset,
    Document,
    DocumentChunk,
    DocumentPipelineState,
)
from app.search import fts as fts_index
from app.worker.runner import Worker


def _run_job(context, worker) -> dict:
    with context.session_factory() as session:
        job, _ = demo_service.seed_job(session, context.settings)
        session.commit()
        job_id = job.id
    worker.run_once()
    with context.session_factory() as session:
        return demo_service.serialize_job(
            session, demo_service.get_job(session, job_id), context.settings
        )


def _first_document(context) -> Document:
    with context.session_factory() as session:
        return session.scalar(
            select(Document)
            .where(Document.source_type == constants.SOURCE_DEMO)
            .order_by(Document.source_key)
        )


def _chunk_ids(context, doc_id: str) -> list[str]:
    with context.session_factory() as session:
        return sorted(
            session.scalars(select(DocumentChunk.id).where(DocumentChunk.doc_id == doc_id))
        )


def _fts_ids(context, doc_id: str) -> list[str]:
    with context.session_factory() as session:
        return sorted(fts_index.existing_ids(session, doc_id))


def _set_stage(context, doc_id: str, stage: str) -> None:
    with context.session_factory() as session:
        session.execute(
            update(DocumentPipelineState)
            .where(DocumentPipelineState.doc_id == doc_id)
            .values(last_completed_stage=stage)
        )
        session.commit()


def _spy_embeddings(monkeypatch, worker) -> dict:
    calls = {"batches": 0, "texts": 0}
    original = worker.embeddings.embed_documents

    def wrapper(texts):
        calls["batches"] += 1
        calls["texts"] += len(texts)
        return original(texts)

    monkeypatch.setattr(worker.embeddings, "embed_documents", wrapper)
    return calls


# --- 三方一致 ---------------------------------------------------------------


def test_three_way_sets_match_for_every_document(ingest_demo, worker, context) -> None:
    payload = ingest_demo()
    assert payload["status"] == constants.JOB_COMPLETED

    with context.session_factory() as session:
        documents = list(
            session.scalars(select(Document).where(Document.source_type == constants.SOURCE_DEMO))
        )

    total = 0
    for document in documents:
        chunk_ids = set(_chunk_ids(context, document.id))
        assert chunk_ids
        assert set(worker.vectors.vectors_for_document(document.id)) == chunk_ids
        assert set(_fts_ids(context, document.id)) == chunk_ids
        state = session_state(context, document.id)
        assert state.expected_chunk_count == state.vector_record_count == state.fts_record_count
        assert state.expected_chunk_count == len(chunk_ids)
        total += len(chunk_ids)

    # 91 个稳定 chunk（阶段 3 基线）在阶段 4 后保持不变
    assert total == 91


def session_state(context, doc_id: str) -> DocumentPipelineState:
    with context.session_factory() as session:
        return session.scalar(
            select(DocumentPipelineState).where(DocumentPipelineState.doc_id == doc_id)
        )


def test_equal_counts_but_different_ids_must_fail(ingest_demo, worker, context) -> None:
    """仅计数相同但 ID 集合不同时必须判定失败，不能写 completed。"""
    ingest_demo()
    document = _first_document(context)
    chunk_ids = _chunk_ids(context, document.id)

    # 精确删除一条 FTS 记录，但不更新 fts_record_count ⇒ 计数相同、集合不同
    with context.session_factory() as session:
        victim = session.scalar(
            select(DocumentChunk).where(DocumentChunk.doc_id == document.id).order_by(DocumentChunk.id)
        )
        fts_index.delete_rowids(session, [victim.fts_rowid])
        victim.fts_rowid = None
        session.commit()

    assert len(_fts_ids(context, document.id)) == len(chunk_ids) - 1

    with pytest.raises(ApiError) as error:
        worker._finalize_document(document.id, "owner", 0, "test")  # noqa: SLF001
    assert error.value.code == "DOCUMENT_INDEX_FAILED"
    assert error.value.details["reason"] == "fts_set_mismatch"


def test_missing_fts_records_are_repaired_without_embedding(
    ingest_demo, worker, context, monkeypatch
) -> None:
    ingest_demo()
    document = _first_document(context)
    chunk_ids = _chunk_ids(context, document.id)

    with context.session_factory() as session:
        victim = session.scalar(
            select(DocumentChunk).where(DocumentChunk.doc_id == document.id).order_by(DocumentChunk.id)
        )
        fts_index.delete_rowids(session, [victim.fts_rowid])
        victim.fts_rowid = None
        session.commit()

    _set_stage(context, document.id, constants.STAGE_VECTOR_INDEXED)
    calls = _spy_embeddings(monkeypatch, worker)
    _run_job(context, worker)

    # FTS 缺损只修 FTS：不重新解析、不重新切片、不重新调用 Embedding
    assert calls["texts"] == 0
    assert set(_fts_ids(context, document.id)) == set(chunk_ids)
    state = session_state(context, document.id)
    assert state.last_completed_stage == constants.STAGE_COMPLETED
    assert state.fts_record_count == len(chunk_ids)


def test_extra_fts_records_are_removed(ingest_demo, worker, context) -> None:
    ingest_demo()
    document = _first_document(context)
    chunk_ids = set(_chunk_ids(context, document.id))

    # 直接塞入一条不属于该文档的 FTS 记录（模拟历史残留）
    with context.session_factory() as session:
        session.execute(
            text(
                "INSERT INTO chunk_fts(chunk_id, doc_id, fts_fingerprint, body) "
                "VALUES ('9' || substr(hex(randomblob(31)), 1, 63), :doc_id, 'stale', '残留')"
            ),
            {"doc_id": document.id},
        )
        session.commit()
        assert fts_index.orphan_rowids(session) != []

    _set_stage(context, document.id, constants.STAGE_VECTOR_INDEXED)
    _run_job(context, worker)

    with context.session_factory() as session:
        assert fts_index.orphan_rowids(session) == []
    assert set(_fts_ids(context, document.id)) == chunk_ids


def test_fts_fingerprint_change_rebuilds_only_fts(
    ingest_demo, worker, context, monkeypatch
) -> None:
    ingest_demo()
    document = _first_document(context)
    chunk_ids = _chunk_ids(context, document.id)

    with context.session_factory() as session:
        session.execute(
            update(DocumentPipelineState)
            .where(DocumentPipelineState.doc_id == document.id)
            .values(fts_schema_fingerprint="stale-fts")
        )
        session.commit()

    calls = _spy_embeddings(monkeypatch, worker)
    _run_job(context, worker)

    assert calls["texts"] == 0, "FTS schema 变化不得重新调用 Embedding"
    assert set(_fts_ids(context, document.id)) == set(chunk_ids)
    assert set(worker.vectors.vectors_for_document(document.id)) == set(chunk_ids)


def test_fts_schema_and_tokenizer_are_recorded(ingest_demo, context) -> None:
    ingest_demo()
    document = _first_document(context)
    state = session_state(context, document.id)
    from app.documents.fingerprint import stage_fingerprints

    assert state.fts_schema_fingerprint == stage_fingerprints(context.settings)["fts_schema_fingerprint"]


# --- 崩溃恢复 ---------------------------------------------------------------


def test_recovery_after_vector_indexed(ingest_demo, worker, context, monkeypatch) -> None:
    ingest_demo()
    document = _first_document(context)
    chunk_ids = _chunk_ids(context, document.id)

    _set_stage(context, document.id, constants.STAGE_VECTOR_INDEXED)
    calls = _spy_embeddings(monkeypatch, worker)
    _run_job(context, worker)

    assert calls["texts"] == 0, "向量已正确时不得重新 Embedding"
    state = session_state(context, document.id)
    assert state.last_completed_stage == constants.STAGE_COMPLETED
    assert set(_fts_ids(context, document.id)) == set(chunk_ids)


def test_recovery_when_fts_transaction_never_committed(
    ingest_demo, worker, context, monkeypatch
) -> None:
    """FTS 事务中断：chunk 与向量已就绪但 FTS 为空，重启后只补 FTS。"""
    ingest_demo()
    document = _first_document(context)
    chunk_ids = set(_chunk_ids(context, document.id))

    with context.session_factory() as session:
        fts_index.delete_document_rows(session, document.id)
        session.execute(
            update(DocumentPipelineState)
            .where(DocumentPipelineState.doc_id == document.id)
            .values(fts_record_count=0)
        )
        session.commit()
        assert fts_index.existing_ids(session, document.id) == set()

    _set_stage(context, document.id, constants.STAGE_CHUNKED)
    calls = _spy_embeddings(monkeypatch, worker)
    _run_job(context, worker)

    assert calls["texts"] == 0
    assert set(_fts_ids(context, document.id)) == chunk_ids
    assert session_state(context, document.id).last_completed_stage == constants.STAGE_COMPLETED


def test_recovery_between_keyword_indexed_and_completed(ingest_demo, worker, context) -> None:
    ingest_demo()
    document = _first_document(context)

    _set_stage(context, document.id, constants.STAGE_KEYWORD_INDEXED)
    payload = _run_job(context, worker)

    assert payload["status"] == constants.JOB_COMPLETED
    assert session_state(context, document.id).last_completed_stage == constants.STAGE_COMPLETED
    with context.session_factory() as session:
        assert session.scalar(select(func.count(DemoActiveDataset.dataset_version))) == 1


def test_recovery_between_completed_and_activation(ingest_demo, worker, context) -> None:
    """completed 检查点已写但激活前崩溃：重启后完成对账并激活。"""
    ingest_demo()

    # 模拟“已 completed 但尚未激活”
    with context.session_factory() as session:
        session.execute(DemoActiveDataset.__table__.delete())
        session.execute(
            update(Document)
            .where(Document.source_type == constants.SOURCE_DEMO)
            .values(
                status=constants.STATUS_QUEUED,
                retrievable=False,
                activation_state=constants.ACTIVATION_CANDIDATE,
            )
        )
        session.commit()

    payload = _run_job(context, worker)

    assert payload["status"] == constants.JOB_COMPLETED
    assert payload["skipped"] == 15
    with context.session_factory() as session:
        pointer = session.scalar(select(DemoActiveDataset))
        assert pointer is not None and pointer.dataset_version == "2026.1"
        assert (
            session.scalar(
                select(func.count(Document.id)).where(
                    Document.status == constants.STATUS_READY,
                    Document.retrievable.is_(True),
                )
            )
            == 15
        )


def test_retry_does_not_duplicate_records(ingest_demo, worker, context) -> None:
    ingest_demo()
    with context.session_factory() as session:
        before_chunks = session.scalar(select(func.count(DocumentChunk.id)))
        before_fts = fts_index.count_rows(session)

    document = _first_document(context)
    _set_stage(context, document.id, constants.STAGE_PARSED)
    _run_job(context, worker)

    with context.session_factory() as session:
        assert session.scalar(select(func.count(DocumentChunk.id))) == before_chunks
        assert fts_index.count_rows(session) == before_fts
        assert fts_index.orphan_rowids(session) == []


def test_worker_requires_process_lock(context) -> None:
    """数据卷进程锁继续保护 Chroma / FTS 写入。"""
    other = Worker(context.settings, context.session_factory, worker_id="other")
    assert other.prepare() is True
    try:
        second = Worker(context.settings, context.session_factory, worker_id="second")
        assert second.prepare() is False
        assert second.run_once() is False
    finally:
        other.stop()
