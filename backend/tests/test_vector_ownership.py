"""来源所有权与删除语义：demo / upload 相同 SHA 也必须彼此独立。"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import func, select

from tests.conftest import demo_file, upload_file
from app import constants
from app.models import Document, DocumentBlock, DocumentChunk, DocumentPipelineState


def _demo_job(context, worker) -> None:
    from app.demo import service as demo_service

    with context.session_factory() as session:
        demo_service.seed_job(session, context.settings)
        session.commit()
    worker.run_once()


def _document(context, document_id: str) -> Document:
    with context.session_factory() as session:
        return session.get(Document, document_id)


def _chunk_ids(context, document_id: str) -> list[str]:
    with context.session_factory() as session:
        return list(
            session.scalars(select(DocumentChunk.id).where(DocumentChunk.doc_id == document_id))
        )


def _block_count(context, document_id: str) -> int:
    with context.session_factory() as session:
        return int(
            session.scalar(
                select(func.count(DocumentBlock.id)).where(DocumentBlock.doc_id == document_id)
            )
        )


def test_demo_and_upload_with_same_sha_are_independent(client, worker, context) -> None:
    _demo_job(context, worker)

    source = demo_file("01-培养方案")
    response = upload_file(client, source)
    assert response.status_code == 202
    upload_id = response.json()["document_id"]
    worker.run_once()

    with context.session_factory() as session:
        demo_documents = list(
            session.scalars(
                select(Document).where(Document.source_type == constants.SOURCE_DEMO)
            )
        )
        upload_document = session.get(Document, upload_id)

    assert len(demo_documents) == 15
    matching = next(doc for doc in demo_documents if doc.sha256 == upload_document.sha256)
    assert matching.id != upload_id

    # 两个来源各自拥有独立的块与向量
    assert _chunk_ids(context, upload_id)
    assert _chunk_ids(context, matching.id)
    assert worker.vectors.vectors_for_document(upload_id)
    assert worker.vectors.vectors_for_document(matching.id)
    assert set(worker.vectors.vectors_for_document(upload_id)) != set(
        worker.vectors.vectors_for_document(matching.id)
    )

    # 上传文档的向量 metadata 必须标注 upload 来源
    upload_metadata = next(iter(worker.vectors.vectors_for_document(upload_id).values()))
    assert upload_metadata["source_type"] == constants.SOURCE_UPLOAD
    assert upload_metadata["doc_id"] == upload_id


def test_deleting_upload_removes_its_vectors_only(client, worker, context) -> None:
    _demo_job(context, worker)
    source = demo_file("01-培养方案")
    upload_id = upload_file(client, source).json()["document_id"]
    worker.run_once()

    with context.session_factory() as session:
        demo_id = session.scalar(
            select(Document.id).where(
                Document.source_type == constants.SOURCE_DEMO,
                Document.sha256 == session.get(Document, upload_id).sha256,
            )
        )
    demo_vectors = set(worker.vectors.vectors_for_document(demo_id))
    assert demo_vectors

    assert client.delete(f"/api/documents/{upload_id}").status_code == 204

    # 上传来源：向量、块、切片全部清理
    assert worker.vectors.vectors_for_document(upload_id) == {}
    assert _chunk_ids(context, upload_id) == []
    assert _block_count(context, upload_id) == 0

    # 演示来源完全不受影响
    assert set(worker.vectors.vectors_for_document(demo_id)) == demo_vectors
    assert _chunk_ids(context, demo_id)
    assert _document(context, demo_id).deleted_at is None


def test_deleting_demo_document_never_touches_read_only_source(client, worker, context) -> None:
    _demo_job(context, worker)
    with context.session_factory() as session:
        demo = session.scalar(
            select(Document).where(Document.source_type == constants.SOURCE_DEMO)
        )
        source_path = Path(demo.storage_path)
    assert source_path.is_file()
    before = source_path.read_bytes()
    vectors = set(worker.vectors.vectors_for_document(demo.id))

    assert client.delete(f"/api/documents/{demo.id}").status_code == 204

    # 只读 demo 源文件必须原样保留
    assert source_path.is_file()
    assert source_path.read_bytes() == before
    assert str(source_path).startswith(str(Path(context.settings.demo_dataset_path)))

    # 运行时物化、块、切片与向量全部清理
    assert worker.vectors.vectors_for_document(demo.id) == {}
    assert _chunk_ids(context, demo.id) == []
    assert _block_count(context, demo.id) == 0
    with context.session_factory() as session:
        assert session.scalar(
            select(func.count(DocumentPipelineState.id)).where(
                DocumentPipelineState.doc_id == demo.id
            )
        ) == 0
    assert _document(context, demo.id).deleted_at is not None
    assert vectors  # 删除前确实有向量，避免空断言


def test_reseeding_after_demo_deletion_rebuilds_index(client, worker, context) -> None:
    _demo_job(context, worker)
    with context.session_factory() as session:
        demo = session.scalar(
            select(Document).where(Document.source_type == constants.SOURCE_DEMO)
        )
        demo_id = demo.id
    assert client.delete(f"/api/documents/{demo_id}").status_code == 204

    _demo_job(context, worker)

    with context.session_factory() as session:
        rebuilt = session.scalar(
            select(Document).where(
                Document.source_type == constants.SOURCE_DEMO,
                Document.source_key == demo.source_key,
                Document.deleted_at.is_(None),
            )
        )
    assert rebuilt is not None
    assert rebuilt.id != demo_id
    assert _chunk_ids(context, rebuilt.id)
    assert worker.vectors.vectors_for_document(rebuilt.id)


def test_deleting_one_document_leaves_other_vectors_reconciled(client, worker, context) -> None:
    _demo_job(context, worker)
    with context.session_factory() as session:
        documents = list(
            session.scalars(select(Document).where(Document.source_type == constants.SOURCE_DEMO))
        )
    target = documents[0]

    client.delete(f"/api/documents/{target.id}")

    for document in documents[1:]:
        with context.session_factory() as session:
            state = session.scalar(
                select(DocumentPipelineState).where(DocumentPipelineState.doc_id == document.id)
            )
        assert state.last_completed_stage == constants.STAGE_COMPLETED
        assert state.vector_record_count == len(_chunk_ids(context, document.id))
        assert state.fts_record_count == state.expected_chunk_count
        assert set(worker.vectors.vectors_for_document(document.id)) == set(
            _chunk_ids(context, document.id)
        )
