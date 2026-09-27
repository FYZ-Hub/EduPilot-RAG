"""demo 数据集原子激活、唯一指针与旧版本退役。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app import constants
from app.core.errors import DOCUMENT_CHUNK_FAILED, ApiError
from app.demo import service as demo_service
from app.demo.activation import activate_dataset, evaluate_preconditions
from app.models import DemoActiveDataset, Document, DocumentPipelineState, utcnow
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


def _insert_previous_version(context, version: str = "2025.2") -> str:
    """插入一个“旧版本已激活数据集”，用于验证切换与退役语义。"""
    with context.session_factory() as session:
        document = Document(
            source_type=constants.SOURCE_DEMO,
            source_key=f"{version}:corpus/01-培养方案-计算机科学与技术-2025版.pdf",
            file_name="01-培养方案-计算机科学与技术-2025版.pdf",
            file_type=constants.FILE_TYPE_PDF,
            mime_type="application/pdf",
            sha256="a" * 64,
            doc_category="degree_plan",
            dataset_version=version,
            status=constants.STATUS_READY,
            retrievable=True,
            activation_state=constants.ACTIVATION_ACTIVE,
        )
        session.add(document)
        session.add(
            DemoActiveDataset(
                dataset_version=version,
                manifest_sha256="b" * 64,
                pipeline_fingerprint="legacy-pipeline",
                activated_at=utcnow(),
                active_marker=1,
            )
        )
        session.commit()
        return document.id


# --- 唯一指针 ---------------------------------------------------------------


def test_only_one_active_dataset_pointer_is_possible(context) -> None:
    with context.session_factory() as session:
        session.add(
            DemoActiveDataset(
                dataset_version="v1",
                manifest_sha256="a" * 64,
                pipeline_fingerprint="p",
                active_marker=1,
            )
        )
        session.commit()

    with context.session_factory() as session:
        session.add(
            DemoActiveDataset(
                dataset_version="v2",
                manifest_sha256="b" * 64,
                pipeline_fingerprint="p",
                active_marker=1,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    with context.session_factory() as session:
        assert session.scalar(select(func.count(DemoActiveDataset.dataset_version))) == 1


# --- 原子激活 ---------------------------------------------------------------


def test_activation_happens_only_after_everything_succeeds(ingest_demo, context) -> None:
    ingest_demo()

    with context.session_factory() as session:
        pointer = session.scalar(select(DemoActiveDataset))
        assert pointer is not None
        assert pointer.dataset_version == "2026.1"
        assert pointer.active_marker == 1

        documents = list(
            session.scalars(select(Document).where(Document.source_type == constants.SOURCE_DEMO))
        )
        assert len(documents) == 15
        for document in documents:
            assert document.status == constants.STATUS_READY
            assert document.retrievable is True
            assert document.activation_state == constants.ACTIVATION_ACTIVE


def test_preconditions_reject_incomplete_dataset(ingest_demo, worker, context) -> None:
    """只有全部 completed 且三方对账一致才允许激活。"""
    ingest_demo()
    document = context_scalar_document(context)

    with context.session_factory() as session:
        session.execute(DemoActiveDataset.__table__.delete())
        session.commit()

    with context.session_factory() as session:
        from app.demo.manifest import load_manifest

        manifest = load_manifest(context.settings.demo_dataset_path, "2026.1")
        # 让其中一个文档回到 parsed：必须拒绝激活
        session.execute(
            DocumentPipelineState.__table__.update()
            .where(DocumentPipelineState.doc_id == document.id)
            .values(last_completed_stage=constants.STAGE_PARSED)
        )
        session.commit()
        reason = evaluate_preconditions(session, context.settings, manifest, worker.vectors)
    assert reason == "stage_incomplete"

    with context.session_factory() as session:
        from app.demo.manifest import load_manifest

        manifest = load_manifest(context.settings.demo_dataset_path, "2026.1")
        outcome = activate_dataset(session, context.settings, manifest, worker.vectors)
        session.commit()
    assert outcome.activated is False
    assert outcome.reason == "stage_incomplete"


def context_scalar_document(context) -> Document:
    with context.session_factory() as session:
        return session.scalar(
            select(Document)
            .where(Document.source_type == constants.SOURCE_DEMO)
            .order_by(Document.source_key)
        )


def test_failed_version_is_never_activated_and_previous_keeps_serving(
    client: TestClient, worker, context, monkeypatch
) -> None:
    previous_document_id = _insert_previous_version(context)

    status = client.get("/api/demo/status").json()
    assert status["loaded"] is False
    assert status["serving_previous_version"] is True
    assert status["active_dataset_version"] == "2025.2"

    from app.worker import runner as runner_module

    original = runner_module.chunk_blocks

    def failing(source, *, target_chars, overlap_chars):
        if any("不可信指令" in block.text for block in source):
            raise ApiError(DOCUMENT_CHUNK_FAILED, retryable=True)
        return original(source, target_chars=target_chars, overlap_chars=overlap_chars)

    monkeypatch.setattr(runner_module, "chunk_blocks", failing)
    payload = _run_job(context, worker)
    monkeypatch.undo()

    assert payload["status"] == constants.JOB_COMPLETED_WITH_ERRORS
    assert payload["failed"] == 1

    after = client.get("/api/demo/status").json()
    assert after["loaded"] is False
    assert after["serving_previous_version"] is True
    assert after["active_dataset_version"] == "2025.2"

    with context.session_factory() as session:
        pointer = session.scalar(select(DemoActiveDataset))
        assert pointer.dataset_version == "2025.2"
        previous = session.get(Document, previous_document_id)
        # 旧 active 继续可检索
        assert previous.activation_state == constants.ACTIVATION_ACTIVE
        assert previous.retrievable is True

        # 新版本保持 candidate，绝不提前 ready
        new_documents = list(
            session.scalars(
                select(Document).where(
                    Document.source_type == constants.SOURCE_DEMO,
                    Document.dataset_version == "2026.1",
                )
            )
        )
        assert len(new_documents) == 15
        assert all(document.activation_state == constants.ACTIVATION_CANDIDATE for document in new_documents)
        assert all(document.retrievable is False for document in new_documents)


def test_previous_version_retired_and_new_activated(client: TestClient, worker, context) -> None:
    previous_document_id = _insert_previous_version(context)
    payload = _run_job(context, worker)
    assert payload["status"] == constants.JOB_COMPLETED
    assert payload["imported"] == 15

    status = client.get("/api/demo/status").json()
    assert status["loaded"] is True
    assert status["serving_previous_version"] is False
    assert status["active_dataset_version"] == "2026.1"

    with context.session_factory() as session:
        assert session.scalar(select(func.count(DemoActiveDataset.dataset_version))) == 1
        previous = session.get(Document, previous_document_id)
        assert previous.activation_state == constants.ACTIVATION_INACTIVE
        assert previous.retrievable is False

    # 旧版本退役后不得再出现在检索结果中
    from app.search.keyword import KeywordRetriever

    with context.session_factory() as session:
        hits, _mode = KeywordRetriever(session, context.settings).search("培养方案")
    assert hits
    assert previous_document_id not in {hit.doc_id for hit in hits}


def test_upload_becomes_ready_without_dataset_activation(client: TestClient, worker, context) -> None:
    """upload 不依赖 demo 整体激活。"""
    from tests.conftest import demo_file, upload_file

    document_id = upload_file(client, demo_file("11-课表")).json()["document_id"]
    worker.run_once()

    payload = client.get(f"/api/documents/{document_id}/status").json()
    assert payload["status"] == constants.STATUS_READY
    assert payload["retrievable"] is True
    assert payload["activation_state"] is None

    with context.session_factory() as session:
        assert session.scalar(select(func.count(DemoActiveDataset.dataset_version))) == 0

    # upload 可被检索，即使 demo 尚未激活
    from app.search.keyword import KeywordRetriever

    with context.session_factory() as session:
        hits, _mode = KeywordRetriever(session, context.settings).search("课表")
    assert hits
    assert all(hit.source_type == constants.SOURCE_UPLOAD for hit in hits)


def test_activation_is_idempotent(ingest_demo, context, worker) -> None:
    ingest_demo()
    from app.demo.manifest import load_manifest

    with context.session_factory() as session:
        manifest = load_manifest(context.settings.demo_dataset_path, "2026.1")
        outcome = activate_dataset(session, context.settings, manifest, worker.vectors)
        session.commit()
        assert outcome.activated is True
        assert session.scalar(select(func.count(DemoActiveDataset.dataset_version))) == 1


def test_worker_activation_requires_lock_and_manifest(context) -> None:
    """激活只由持有数据卷锁的 worker 触发；未取锁的实例不得执行任何写入。"""
    unlocked = Worker(context.settings, context.session_factory, worker_id="unlocked")
    assert unlocked.run_once() is False
    unlocked.stop()
