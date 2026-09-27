"""worker 向量流水线：检查点、崩溃恢复、向量复用与指纹定向重建。"""

from __future__ import annotations

from sqlalchemy import func, select, text, update

from tests.conftest import build_settings
from app import constants
from app.core.errors import DOCUMENT_CHUNK_FAILED, ApiError
from app.demo import service as demo_service
from app.documents.fingerprint import CHUNKER_VERSION, pipeline_fingerprint, stage_fingerprints
from app.embedding.base import descriptor_for
from app.models import (
    DemoActiveDataset,
    Document,
    DocumentBlock,
    DocumentChunk,
    DocumentPipelineState,
)
from app.vector.store import VectorRecord
from app.worker.runner import Worker


def _run_job(context, worker, settings=None) -> dict:
    settings = settings or context.settings
    with context.session_factory() as session:
        job, _ = demo_service.seed_job(session, settings)
        session.commit()
        job_id = job.id
    worker.run_once()
    with context.session_factory() as session:
        return demo_service.serialize_job(session, demo_service.get_job(session, job_id), settings)


def _demo_documents(context) -> list[Document]:
    with context.session_factory() as session:
        return list(
            session.scalars(
                select(Document)
                .where(Document.source_type == constants.SOURCE_DEMO)
                .order_by(Document.source_key)
            )
        )


def _chunk_ids(context, document_id: str) -> list[str]:
    with context.session_factory() as session:
        return sorted(
            session.scalars(select(DocumentChunk.id).where(DocumentChunk.doc_id == document_id))
        )


def _block_ids(context, document_id: str) -> list[int]:
    with context.session_factory() as session:
        return sorted(
            session.scalars(
                select(DocumentBlock.id)
                .where(DocumentBlock.doc_id == document_id)
                .order_by(DocumentBlock.id)
            )
        )


def _fts_ids(context, document_id: str) -> list[str]:
    """该文档在 FTS5 中真实存在的 chunk_id 集合。"""
    with context.session_factory() as session:
        return sorted(
            session.scalars(
                text(
                    "SELECT chunk_fts.chunk_id FROM chunk_fts "
                    "JOIN document_chunks c ON c.fts_rowid = chunk_fts.rowid "
                    "WHERE c.doc_id = :doc_id"
                ),
                {"doc_id": document_id},
            ).all()
        )


def _state(context, document_id: str) -> DocumentPipelineState:
    with context.session_factory() as session:
        return session.scalar(
            select(DocumentPipelineState).where(DocumentPipelineState.doc_id == document_id)
        )


def _set_stage(context, document_id: str, stage: str) -> None:
    with context.session_factory() as session:
        session.execute(
            update(DocumentPipelineState)
            .where(DocumentPipelineState.doc_id == document_id)
            .values(last_completed_stage=stage)
        )
        session.commit()


def _set_fingerprint(context, document_id: str, column: str, value: str) -> None:
    with context.session_factory() as session:
        session.execute(
            update(DocumentPipelineState)
            .where(DocumentPipelineState.doc_id == document_id)
            .values(**{column: value})
        )
        session.commit()


def _stale_vectors(worker, document_id: str, **overrides) -> list[str]:
    """把该文档全部向量的 metadata 改成过期版本（模拟组件版本变化后的旧记录）。"""
    vector_ids = list(worker.vectors.vectors_for_document(document_id))
    collection = worker.vectors.collection()
    collection.update(
        ids=vector_ids,
        metadatas=[{"doc_id": document_id, **overrides} for _ in vector_ids],
    )
    return vector_ids


def _spy_embeddings(monkeypatch, worker) -> dict:
    calls = {"batches": 0, "texts": 0}
    original = worker.embeddings.embed_documents

    def wrapper(texts):
        calls["batches"] += 1
        calls["texts"] += len(texts)
        return original(texts)

    monkeypatch.setattr(worker.embeddings, "embed_documents", wrapper)
    return calls


# --- 检查点与对账 -----------------------------------------------------------


def test_documents_reach_completed_with_reconciled_records(worker, context) -> None:
    payload = _run_job(context, worker)

    assert payload["status"] == constants.JOB_COMPLETED
    assert payload["imported"] == 15

    for document in _demo_documents(context):
        state = _state(context, document.id)
        chunk_ids = _chunk_ids(context, document.id)
        vector_ids = sorted(worker.vectors.vectors_for_document(document.id))
        fts_ids = _fts_ids(context, document.id)

        assert state.last_completed_stage == constants.STAGE_COMPLETED
        assert state.expected_chunk_count == len(chunk_ids) > 0
        assert state.vector_record_count == len(vector_ids)
        assert state.fts_record_count == len(fts_ids)
        # SQLite / Chroma / FTS5 三方集合必须完全一致
        assert set(vector_ids) == set(chunk_ids)
        assert set(fts_ids) == set(chunk_ids)
        # 全部成功 → 原子激活 → demo 文档 ready 且可检索
        assert document.status == constants.STATUS_READY
        assert document.retrievable is True
        assert document.current_stage is None
        assert document.activation_state == constants.ACTIVATION_ACTIVE


def test_vector_metadata_is_scalar_and_complete(worker, context) -> None:
    _run_job(context, worker)
    document = _demo_documents(context)[0]
    descriptor = descriptor_for(context.settings)

    metadata = next(iter(worker.vectors.vectors_for_document(document.id).values()))
    assert metadata["doc_id"] == document.id
    assert metadata["source_type"] == constants.SOURCE_DEMO
    assert metadata["source_key"] == document.source_key
    assert metadata["file_name"] == document.file_name
    assert metadata["file_type"] == document.file_type
    assert metadata["doc_category"] == document.doc_category
    assert metadata["checksum"] == document.sha256
    assert metadata["chunker_version"] == CHUNKER_VERSION
    assert metadata["embedding_provider"] == "fake"
    assert metadata["embedding_model"] == descriptor.model
    assert metadata["embedding_revision"] == descriptor.revision
    assert metadata["embedding_fingerprint"] == descriptor.fingerprint
    assert metadata["pipeline_fingerprint"] == pipeline_fingerprint(context.settings)
    assert "chunk_index" in metadata
    assert None not in metadata.values()
    for value in metadata.values():
        assert isinstance(value, (str, int, float, bool))


def test_second_job_is_pure_skip_and_reuses_vectors(worker, context, monkeypatch) -> None:
    first = _run_job(context, worker)
    assert first["imported"] == 15

    document = _demo_documents(context)[0]
    chunk_ids = _chunk_ids(context, document.id)
    block_ids = _block_ids(context, document.id)
    calls = _spy_embeddings(monkeypatch, worker)

    second = _run_job(context, worker)

    assert second["status"] == constants.JOB_COMPLETED
    assert second["skipped"] == 15
    assert second["imported"] == 0
    assert second["resumed"] == 0
    assert second["failed"] == 0
    # 纯跳过：不重新解析、不重新切片、不重新生成向量
    assert calls["batches"] == 0
    assert _chunk_ids(context, document.id) == chunk_ids
    assert _block_ids(context, document.id) == block_ids
    assert set(worker.vectors.vectors_for_document(document.id)) == set(chunk_ids)


def test_existing_document_row_alone_does_not_skip_processing(worker, context) -> None:
    """“数据库已有 document 行”不能作为跳过依据。"""
    with context.session_factory() as session:
        session.add(
            Document(
                source_type=constants.SOURCE_DEMO,
                source_key=(
                    f"{context.settings.demo_dataset_version}:"
                    "corpus/01-培养方案-计算机科学与技术-2025版.pdf"
                ),
                file_name="01-培养方案-计算机科学与技术-2025版.pdf",
                file_type=constants.FILE_TYPE_PDF,
                mime_type="application/pdf",
                sha256="0" * 64,
                doc_category="degree_plan",
                status=constants.STATUS_READY,
                retrievable=True,
            )
        )
        session.commit()

    payload = _run_job(context, worker)

    assert payload["status"] == constants.JOB_COMPLETED
    assert payload["skipped"] == 0
    assert payload["imported"] == 15

    target = next(
        document
        for document in _demo_documents(context)
        if document.source_key.endswith("01-培养方案-计算机科学与技术-2025版.pdf")
    )
    assert _chunk_ids(context, target.id)
    assert worker.vectors.vectors_for_document(target.id)


# --- 崩溃恢复与向量复用 -----------------------------------------------------


def test_resume_from_parsed_keeps_blocks_and_reuses_vectors(worker, context, monkeypatch) -> None:
    _run_job(context, worker)
    document = _demo_documents(context)[0]
    block_ids = _block_ids(context, document.id)
    chunk_ids = _chunk_ids(context, document.id)

    _set_stage(context, document.id, constants.STAGE_PARSED)
    calls = _spy_embeddings(monkeypatch, worker)
    payload = _run_job(context, worker)

    assert payload["resumed"] == 1
    assert payload["skipped"] == 14
    # 从 parsed 恢复：不重新解析（块主键不变），切片稳定，向量指纹一致故复用
    assert _block_ids(context, document.id) == block_ids
    assert _chunk_ids(context, document.id) == chunk_ids
    assert calls["batches"] == 0
    assert set(worker.vectors.vectors_for_document(document.id)) == set(chunk_ids)


def test_crash_after_upsert_before_checkpoint_reuses_vectors(worker, context, monkeypatch) -> None:
    """Chroma upsert 后、SQLite 检查点前崩溃：重启必须复用向量，不得再次调用 Embedding。"""
    _run_job(context, worker)
    document = _demo_documents(context)[0]
    chunk_ids = _chunk_ids(context, document.id)

    # 模拟“检查点未写入”：SQLite 回到 chunked，Chroma 已存在正确向量
    _set_stage(context, document.id, constants.STAGE_CHUNKED)
    calls = _spy_embeddings(monkeypatch, worker)
    payload = _run_job(context, worker)

    assert payload["resumed"] == 1
    assert calls["batches"] == 0
    assert calls["texts"] == 0
    assert set(worker.vectors.vectors_for_document(document.id)) == set(chunk_ids)
    assert _state(context, document.id).last_completed_stage == constants.STAGE_COMPLETED


def test_missing_vector_is_repaired_precisely(worker, context, monkeypatch) -> None:
    _run_job(context, worker)
    document = _demo_documents(context)[0]
    chunk_ids = _chunk_ids(context, document.id)

    worker.vectors.delete_ids([chunk_ids[0]])
    _set_stage(context, document.id, constants.STAGE_CHUNKED)
    calls = _spy_embeddings(monkeypatch, worker)
    _run_job(context, worker)

    assert calls["texts"] == 1  # 只补缺失的一条
    assert set(worker.vectors.vectors_for_document(document.id)) == set(chunk_ids)


def test_extra_vector_is_removed(worker, context) -> None:
    _run_job(context, worker)
    document = _demo_documents(context)[0]
    chunk_ids = _chunk_ids(context, document.id)

    worker.vectors.upsert(
        [
            VectorRecord(
                chunk_id="9" * 64,
                text="多余的向量",
                metadata={
                    "doc_id": document.id,
                    "chunk_index": 999,
                    "file_name": document.file_name,
                },
            )
        ],
        [[0.0] * 1024],
    )
    assert "9" * 64 in worker.vectors.vectors_for_document(document.id)

    _set_stage(context, document.id, constants.STAGE_CHUNKED)
    _run_job(context, worker)

    assert set(worker.vectors.vectors_for_document(document.id)) == set(chunk_ids)


def test_wrong_revision_vector_is_rebuilt(worker, context, monkeypatch) -> None:
    _run_job(context, worker)
    document = _demo_documents(context)[0]
    chunk_ids = _chunk_ids(context, document.id)
    stale = chunk_ids[0]

    collection = worker.vectors.collection()
    collection.update(
        ids=[stale], metadatas=[{"doc_id": document.id, "embedding_fingerprint": "stale"}]
    )

    _set_stage(context, document.id, constants.STAGE_CHUNKED)
    calls = _spy_embeddings(monkeypatch, worker)
    _run_job(context, worker)

    assert calls["texts"] == 1
    metadata = worker.vectors.vectors_for_document(document.id)[stale]
    assert metadata["embedding_fingerprint"] != "stale"
    assert set(worker.vectors.vectors_for_document(document.id)) == set(chunk_ids)


# --- 指纹定向重建 -----------------------------------------------------------


def test_parser_fingerprint_change_triggers_reparse(worker, context, monkeypatch) -> None:
    _run_job(context, worker)
    document = _demo_documents(context)[0]
    original_blocks = _block_ids(context, document.id)
    original_chunks = _chunk_ids(context, document.id)

    _set_fingerprint(context, document.id, "parser_fingerprint", "stale-parser")
    calls = _spy_embeddings(monkeypatch, worker)
    payload = _run_job(context, worker)

    assert payload["resumed"] == 1
    # 解析阶段本身失效：必须重新解析（块主键变化）并重新提交解析指纹
    assert _block_ids(context, document.id) != original_blocks
    # chunk_id 由「校验值 + 定位 + index + 版本」决定，因此集合保持稳定
    assert _chunk_ids(context, document.id) == original_chunks
    state = _state(context, document.id)
    assert state.last_completed_stage == constants.STAGE_COMPLETED
    assert state.parser_fingerprint == stage_fingerprints(context.settings)["parser_fingerprint"]
    # 文本与版本都未变化 ⇒ 向量仍可安全复用，不得无谓调用 Embedding
    assert calls["batches"] == 0
    assert set(worker.vectors.vectors_for_document(document.id)) == set(original_chunks)


def test_chunker_change_rebuilds_from_parsed_without_reparsing(
    tmp_path, worker, context, monkeypatch
) -> None:
    _run_job(context, worker)
    documents = _demo_documents(context)
    blocks_before = {document.id: _block_ids(context, document.id) for document in documents}
    chunks_before = {document.id: _chunk_ids(context, document.id) for document in documents}

    # 切片配置变化（550 → 999）：chunker 指纹随之变化
    changed_settings = build_settings(tmp_path, chunk_target_chars=999)
    changed = Worker(changed_settings, context.session_factory, worker_id="changed")
    # worker fixture 已持有数据卷进程锁；这里只复用同一 session_factory 驱动
    changed.has_process_lock = True
    try:
        _run_job(context, changed, changed_settings)

        chunks_after = {document.id: _chunk_ids(context, document.id) for document in documents}
        # 只回退到 parsed：块不重新解析，切片按新配置重建
        for document in documents:
            assert _block_ids(context, document.id) == blocks_before[document.id], document.file_name
            assert set(changed.vectors.vectors_for_document(document.id)) == set(
                chunks_after[document.id]
            ), document.file_name

        total_before = sum(len(value) for value in chunks_before.values())
        total_after = sum(len(value) for value in chunks_after.values())
        assert total_after < total_before  # 目标长度变大 ⇒ 切片数量必须减少

        state = _state(context, documents[0].id)
        assert state.chunker_fingerprint == stage_fingerprints(changed_settings)["chunker_fingerprint"]
        assert state.last_completed_stage == constants.STAGE_COMPLETED
    finally:
        changed.stop()


def test_embedding_fingerprint_change_rebuilds_vectors_only(worker, context, monkeypatch) -> None:
    _run_job(context, worker)
    document = _demo_documents(context)[0]
    original_blocks = _block_ids(context, document.id)
    original_chunks = _chunk_ids(context, document.id)

    # Embedding 身份变化：已有向量带着过期指纹，必须重建向量
    _stale_vectors(worker, document.id, embedding_fingerprint="stale-embedding")
    calls = _spy_embeddings(monkeypatch, worker)
    payload = _run_job(context, worker)

    assert payload["resumed"] == 1
    # 保留有效 chunk：块与 chunk 集合都不变，只重建向量
    assert _block_ids(context, document.id) == original_blocks
    assert _chunk_ids(context, document.id) == original_chunks
    assert calls["texts"] == len(original_chunks)
    assert set(worker.vectors.vectors_for_document(document.id)) == set(original_chunks)


def test_vector_schema_fingerprint_change_rebuilds_records(worker, context, monkeypatch) -> None:
    _run_job(context, worker)
    document = _demo_documents(context)[0]
    original_chunks = _chunk_ids(context, document.id)

    _set_fingerprint(context, document.id, "vector_schema_fingerprint", "stale-schema")
    _stale_vectors(worker, document.id, vector_schema_fingerprint="stale-schema")
    calls = _spy_embeddings(monkeypatch, worker)
    _run_job(context, worker)

    # 向量 schema 失效：chunk 保留，向量按当前 schema 重建
    assert _chunk_ids(context, document.id) == original_chunks
    assert calls["texts"] == len(original_chunks)
    state = _state(context, document.id)
    assert state.last_completed_stage == constants.STAGE_COMPLETED
    assert (
        state.vector_schema_fingerprint
        == stage_fingerprints(context.settings)["vector_schema_fingerprint"]
    )
    metadata = next(iter(worker.vectors.vectors_for_document(document.id).values()))
    assert (
        metadata["vector_schema_fingerprint"]
        == stage_fingerprints(context.settings)["vector_schema_fingerprint"]
    )


def test_chunker_version_change_alters_chunk_ids(tmp_path) -> None:
    """切片版本是 chunk_id 的输入之一：版本变化必须改变 ID，避免旧向量被误复用。"""
    from app.documents.chunking import compute_chunk_id

    locator = {"page_number": 1, "block_start": 0, "block_end": 0}
    base = compute_chunk_id(
        document_checksum="a" * 64,
        locator=locator,
        chunk_index=0,
        parser_version="1.0.0",
        chunker_version="1.0.0",
    )
    bumped = compute_chunk_id(
        document_checksum="a" * 64,
        locator=locator,
        chunk_index=0,
        parser_version="1.0.0",
        chunker_version="1.1.0",
    )
    assert base != bumped


def test_embedding_revision_changes_fingerprint(tmp_path) -> None:
    baseline = stage_fingerprints(
        build_settings(tmp_path, embedding_provider="local", embedding_revision="rev-a")
    )
    bumped = stage_fingerprints(
        build_settings(tmp_path, embedding_provider="local", embedding_revision="rev-b")
    )
    assert baseline["embedding_fingerprint"] != bumped["embedding_fingerprint"]


# --- 单文件失败 -------------------------------------------------------------


def _patch_chunk_failure(monkeypatch) -> None:
    from app.worker import runner as runner_module

    original = runner_module.chunk_blocks

    def failing(source, *, target_chars, overlap_chars):
        if any("不可信指令" in block.text for block in source):
            raise ApiError(DOCUMENT_CHUNK_FAILED, retryable=True)
        return original(source, target_chars=target_chars, overlap_chars=overlap_chars)

    monkeypatch.setattr(runner_module, "chunk_blocks", failing)


def test_single_document_failure_does_not_rollback_others(worker, context, monkeypatch) -> None:
    _patch_chunk_failure(monkeypatch)
    payload = _run_job(context, worker)

    assert payload["status"] == constants.JOB_COMPLETED_WITH_ERRORS
    assert payload["failed"] == 1
    assert payload["imported"] == 14
    assert payload["processed"] == 15

    documents = _demo_documents(context)
    completed = [document for document in documents if _chunk_ids(context, document.id)]
    assert len(completed) == 14
    for document in completed:
        assert set(worker.vectors.vectors_for_document(document.id)) == set(
            _chunk_ids(context, document.id)
        )

    failed = [document for document in documents if not _chunk_ids(context, document.id)]
    assert len(failed) == 1
    assert worker.vectors.vectors_for_document(failed[0].id) == {}
    assert _fts_ids(context, failed[0].id) == []
    # 不完整版本绝不能激活：active 指针必须仍然为空
    with context.session_factory() as session:
        assert session.scalar(select(func.count(DemoActiveDataset.dataset_version))) == 0
    assert all(
        document.activation_state == constants.ACTIVATION_CANDIDATE
        for document in documents
        if document.status != constants.STATUS_FAILED
    )


def test_failed_document_resumes_after_fix(worker, context, monkeypatch) -> None:
    _patch_chunk_failure(monkeypatch)
    first = _run_job(context, worker)
    assert first["failed"] == 1

    monkeypatch.undo()
    second = _run_job(context, worker)
    assert second["status"] == constants.JOB_COMPLETED
    assert second["failed"] == 0
    assert second["resumed"] == 1
    assert second["skipped"] == 14
