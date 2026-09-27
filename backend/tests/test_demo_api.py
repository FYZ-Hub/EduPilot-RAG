"""演示 API 与 worker：状态推导、异步 seed、并发复用、恢复与重试。"""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from tests.conftest import DEMO_DATASET_PATH, build_settings
from app import constants
from app.core.errors import ApiError
from app.db import create_db_engine, create_session_factory, init_database
from app.demo import service as demo_service
from app.models import (
    DemoActiveDataset,
    DemoSeedJob,
    DemoSeedJobDocument,
    Document,
    DocumentBlock,
    DocumentPipelineState,
    utcnow,
)
from app.worker import runner as runner_module
from app.worker.runner import Worker, requeue_expired_jobs


@contextmanager
def demo_env(tmp_path: Path, dataset_path: Path, **overrides):
    """构造指向指定数据集的工作环境，并驱动 worker（不启动后台线程）。"""
    settings = build_settings(tmp_path, demo_dataset_path=str(dataset_path), **overrides)
    engine = create_db_engine(settings)
    init_database(engine)
    factory = create_session_factory(engine)
    worker = Worker(settings, factory, worker_id="test-worker")
    assert worker.prepare()
    try:
        yield SimpleNamespace(settings=settings, engine=engine, session_factory=factory, worker=worker)
    finally:
        worker.stop()
        engine.dispose()


def seed(context) -> str:
    with context.session_factory() as session:
        job, reused = demo_service.seed_job(session, context.settings)
        assert reused is False
        session.commit()
        return job.id


def job_payload(context, job_id: str) -> dict:
    with context.session_factory() as session:
        job = demo_service.get_job(session, job_id)
        return demo_service.serialize_job(session, job, context.settings)


# --- status -----------------------------------------------------------------


def test_status_reports_empty_dataset_without_loading(client: TestClient) -> None:
    payload = client.get("/api/demo/status").json()

    assert payload["enabled"] is True
    assert payload["state"] == constants.DEMO_STATE_EMPTY
    assert payload["dataset_version"] == "2026.1"
    assert payload["available_documents"] == 15
    assert payload["ready_documents"] == 0
    assert payload["failed_documents"] == 0
    assert payload["loaded"] is False
    assert payload["active_dataset_version"] is None
    assert payload["serving_previous_version"] is False
    assert payload["manifest_sha256"]
    assert payload["pipeline_fingerprint"]
    assert payload["reason"] is None


def test_status_disabled_reports_reason(tmp_path) -> None:
    with demo_env(tmp_path, DEMO_DATASET_PATH, demo_dataset_enabled=False) as context:
        from app.main import create_app

        application = create_app(context.settings)
        with TestClient(application) as client:
            payload = client.get("/api/demo/status").json()
        application.state.context.engine.dispose()

    assert payload["state"] == constants.DEMO_STATE_DISABLED
    assert payload["reason"]["code"] == "DEMO_DATASET_DISABLED"
    assert payload["loaded"] is False


def test_status_unavailable_when_manifest_missing(tmp_path) -> None:
    empty_dataset = tmp_path / "empty-demo"
    empty_dataset.mkdir()
    with demo_env(tmp_path, empty_dataset) as context:
        from app.main import create_app

        application = create_app(context.settings)
        with TestClient(application) as client:
            status = client.get("/api/demo/status").json()
            seed_response = client.post("/api/demo/seed")
        application.state.context.engine.dispose()

    assert status["state"] == constants.DEMO_STATE_UNAVAILABLE
    assert status["reason"]["code"] == "DEMO_MANIFEST_NOT_FOUND"
    assert seed_response.status_code == 422
    assert seed_response.json()["code"] == "DEMO_MANIFEST_NOT_FOUND"
    assert seed_response.json()["request_id"]


def test_invalid_manifest_is_rejected(tmp_path, writable_dataset) -> None:
    (writable_dataset / "manifest.json").write_text("{not json", encoding="utf-8")
    with demo_env(tmp_path, writable_dataset) as context:
        from app.main import create_app

        application = create_app(context.settings)
        with TestClient(application) as client:
            response = client.post("/api/demo/seed")
        application.state.context.engine.dispose()

    assert response.status_code == 422
    assert response.json()["code"] == "DEMO_MANIFEST_INVALID"


def test_checksum_mismatch_is_rejected(tmp_path, writable_dataset) -> None:
    target = sorted((writable_dataset / "corpus").glob("*.xlsx"))[0]
    target.write_bytes(target.read_bytes() + b"tampered")

    with demo_env(tmp_path, writable_dataset) as context:
        from app.main import create_app

        application = create_app(context.settings)
        with TestClient(application) as client:
            status = client.get("/api/demo/status").json()
            response = client.post("/api/demo/seed")
        application.state.context.engine.dispose()

    assert status["state"] == constants.DEMO_STATE_UNAVAILABLE
    assert status["reason"]["code"] == "DEMO_FILE_CHECKSUM_MISMATCH"
    assert response.status_code == 422
    assert response.json()["code"] == "DEMO_FILE_CHECKSUM_MISMATCH"


def test_manifest_extra_file_is_rejected(tmp_path, writable_dataset) -> None:
    (writable_dataset / "corpus" / "extra.pdf").write_bytes(b"%PDF-1.4 extra")
    with demo_env(tmp_path, writable_dataset) as context:
        from app.main import create_app

        application = create_app(context.settings)
        with TestClient(application) as client:
            response = client.post("/api/demo/seed")
        application.state.context.engine.dispose()

    assert response.status_code == 422
    assert response.json()["code"] == "DEMO_MANIFEST_INVALID"


# --- seed -------------------------------------------------------------------


def test_seed_returns_202_immediately_with_location(client: TestClient) -> None:
    response = client.post("/api/demo/seed")

    assert response.status_code == 202
    payload = response.json()
    assert payload["status"] == constants.JOB_QUEUED
    assert payload["target_stage"] == constants.STAGE_PARSED
    assert payload["reused_active_job"] is False
    assert payload["status_url"] == f"/api/demo/jobs/{payload['job_id']}"
    assert response.headers["Location"] == payload["status_url"]
    assert response.headers["Retry-After"] == "1"

    # 请求线程内不得执行解析
    assert client.get(f"/api/demo/jobs/{payload['job_id']}").json()["processed"] == 0


def test_seed_creates_exactly_fifteen_documents(client: TestClient, context) -> None:
    job_id = client.post("/api/demo/seed").json()["job_id"]
    with context.session_factory() as session:
        count = session.scalar(
            select(func.count(DemoSeedJobDocument.id)).where(DemoSeedJobDocument.job_id == job_id)
        )
    assert count == 15


def test_repeated_seed_reuses_active_job(client: TestClient) -> None:
    first = client.post("/api/demo/seed").json()
    second = client.post("/api/demo/seed")

    assert second.status_code == 202
    assert second.json()["reused_active_job"] is True
    assert second.json()["job_id"] == first["job_id"]


def test_concurrent_seed_creates_single_job(context) -> None:
    results: list[tuple[str, bool]] = []
    barrier = threading.Barrier(4)

    def run_seed() -> None:
        barrier.wait()
        with context.session_factory() as session:
            try:
                job, reused = demo_service.seed_job(session, context.settings)
                session.commit()
                results.append((job.id, reused))
            except IntegrityError:
                session.rollback()
                active = demo_service.active_job(session)
                results.append((active.id, True))

    threads = [threading.Thread(target=run_seed) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 4
    assert len({job_id for job_id, _ in results}) == 1
    with context.session_factory() as session:
        assert session.scalar(select(func.count(DemoSeedJob.id))) == 1


def test_unknown_job_returns_not_found(client: TestClient) -> None:
    response = client.get("/api/demo/jobs/does-not-exist")
    assert response.status_code == 404
    assert response.json()["code"] == "DEMO_JOB_NOT_FOUND"


# --- 首次与第二次任务 -------------------------------------------------------


def test_first_job_imports_all_fifteen_documents(client: TestClient, worker, context) -> None:
    job_id = client.post("/api/demo/seed").json()["job_id"]
    worker.run_once()
    payload = job_payload(context, job_id)

    assert payload["status"] == constants.JOB_COMPLETED
    assert payload["imported"] == 15
    assert payload["resumed"] == 0
    assert payload["skipped"] == 0
    assert payload["failed"] == 0
    assert payload["processed"] == 15
    assert payload["progress_percent"] == 100
    assert payload["current_stage"] is None
    assert payload["finished_at"]
    assert len(payload["documents"]) == 15
    assert {item["result"] for item in payload["documents"]} == {constants.RESULT_IMPORTED}
    assert {item["last_completed_stage"] for item in payload["documents"]} == {constants.STAGE_PARSED}
    assert payload["errors"] == []

    with context.session_factory() as session:
        statuses = {
            state.last_completed_stage
            for state in session.scalars(select(DocumentPipelineState))
        }
    assert statuses == {constants.STAGE_PARSED}


def test_second_job_is_pure_skip(client: TestClient, worker, context) -> None:
    first_id = client.post("/api/demo/seed").json()["job_id"]
    worker.run_once()
    assert job_payload(context, first_id)["status"] == constants.JOB_COMPLETED

    second_id = client.post("/api/demo/seed").json()["job_id"]
    worker.run_once()
    payload = job_payload(context, second_id)

    assert payload["status"] == constants.JOB_COMPLETED
    assert payload["imported"] == 0
    assert payload["resumed"] == 0
    assert payload["failed"] == 0
    assert payload["skipped"] == 15
    assert payload["processed"] == 15
    assert {item["result"] for item in payload["documents"]} == {constants.RESULT_SKIPPED}


def test_repeated_ingestion_does_not_duplicate_blocks(client: TestClient, worker, context) -> None:
    client.post("/api/demo/seed")
    worker.run_once()
    with context.session_factory() as session:
        first_counts = dict(
            session.execute(
                select(DocumentBlock.doc_id, func.count(DocumentBlock.id)).group_by(DocumentBlock.doc_id)
            ).all()
        )
    assert first_counts

    client.post("/api/demo/seed")
    worker.run_once()
    with context.session_factory() as session:
        second_counts = dict(
            session.execute(
                select(DocumentBlock.doc_id, func.count(DocumentBlock.id)).group_by(DocumentBlock.doc_id)
            ).all()
        )
    assert first_counts == second_counts


def test_loaded_stays_false_after_ingestion(client: TestClient, worker, context) -> None:
    client.post("/api/demo/seed")
    worker.run_once()

    payload = client.get("/api/demo/status").json()
    assert payload["loaded"] is False
    assert payload["active_dataset_version"] is None
    assert payload["ready_documents"] == 0
    assert payload["state"] == constants.DEMO_STATE_EMPTY

    with context.session_factory() as session:
        assert session.scalar(select(func.count(DemoActiveDataset.dataset_version))) == 0


# --- 恢复 -------------------------------------------------------------------


def test_expired_lease_is_requeued(client: TestClient, worker, context) -> None:
    job_id = client.post("/api/demo/seed").json()["job_id"]

    from datetime import timedelta

    from sqlalchemy import update

    with context.session_factory() as session:
        session.execute(
            update(DemoSeedJob)
            .where(DemoSeedJob.id == job_id)
            .values(
                status=constants.JOB_RUNNING,
                lease_owner="dead-worker",
                lease_generation=1,
                lease_expires_at=utcnow() - timedelta(seconds=5),
                current_stage=constants.JOB_STAGE_PARSING,
            )
        )
        session.commit()

    assert requeue_expired_jobs(context.session_factory) == 1
    with context.session_factory() as session:
        job = session.get(DemoSeedJob, job_id)
        assert job.status == constants.JOB_QUEUED
        assert job.lease_owner is None

    worker.run_once()
    assert job_payload(context, job_id)["status"] == constants.JOB_COMPLETED


def test_restart_recovers_running_job_and_completes(client: TestClient, context) -> None:
    job_id = client.post("/api/demo/seed").json()["job_id"]

    # 模拟“上一条进程崩溃”：任务处于 running 且租约未过期
    with context.session_factory() as session:
        job = session.get(DemoSeedJob, job_id)
        job.status = constants.JOB_RUNNING
        job.lease_owner = "crashed-worker"
        job.lease_generation = 1
        job.lease_expires_at = utcnow()
        job.current_stage = constants.JOB_STAGE_PARSING
        session.commit()

    # 新进程启动：持有独占锁后可以安全把 running 任务恢复为 queued
    restarted = Worker(context.settings, context.session_factory, worker_id="restart-worker")
    assert restarted.prepare()
    try:
        with context.session_factory() as session:
            assert session.get(DemoSeedJob, job_id).status == constants.JOB_QUEUED
        restarted.run_once()
    finally:
        restarted.stop()

    payload = job_payload(context, job_id)
    assert payload["status"] == constants.JOB_COMPLETED
    assert payload["imported"] == 15


def test_interrupted_upload_is_requeued_on_restart(context) -> None:
    with context.session_factory() as session:
        document = Document(
            source_type=constants.SOURCE_UPLOAD,
            source_key="upload:interrupted",
            file_name="a.pdf",
            file_type=constants.FILE_TYPE_PDF,
            mime_type="application/pdf",
            sha256="f" * 64,
            status=constants.STATUS_PARSING,
            current_stage=constants.STATUS_PARSING,
            lease_owner="crashed-worker",
        )
        session.add(document)
        session.commit()
        document_id = document.id

    restarted = Worker(context.settings, context.session_factory, worker_id="restart-worker")
    assert restarted.prepare()
    restarted.stop()

    with context.session_factory() as session:
        refreshed = session.get(Document, document_id)
        assert refreshed.status == constants.STATUS_QUEUED
        assert refreshed.lease_owner is None


# --- 失败与指纹变化 ---------------------------------------------------------


def test_single_document_failure_then_resume(client: TestClient, worker, context, monkeypatch) -> None:
    original = runner_module.parse_document

    def failing(path, file_type):
        if "09-" in Path(path).name:
            raise ApiError("DOCUMENT_PARSE_FAILED", retryable=True)
        return original(path, file_type)

    monkeypatch.setattr(runner_module, "parse_document", failing)

    first_id = client.post("/api/demo/seed").json()["job_id"]
    worker.run_once()
    first = job_payload(context, first_id)

    assert first["status"] == constants.JOB_COMPLETED_WITH_ERRORS
    assert first["failed"] == 1
    assert first["imported"] == 14
    assert first["processed"] == 15
    assert len(first["errors"]) == 1
    failed_item = next(item for item in first["documents"] if item["result"] == constants.RESULT_FAILED)
    assert failed_item["error_code"] == "DOCUMENT_PARSE_FAILED"
    assert first["errors"][0]["retryable"] is True

    # 其它文档的检查点不回滚
    with context.session_factory() as session:
        assert session.scalar(
            select(func.count(DocumentBlock.id))
        ) > 0

    monkeypatch.undo()
    second_id = client.post("/api/demo/seed").json()["job_id"]
    worker.run_once()
    second = job_payload(context, second_id)

    assert second["status"] == constants.JOB_COMPLETED
    assert second["failed"] == 0
    assert second["skipped"] == 14
    assert second["resumed"] == 1


def test_document_failure_retries_three_times(client: TestClient, worker, context, monkeypatch) -> None:
    attempts: list[str] = []
    original = runner_module.parse_document

    def failing(path, file_type):
        attempts.append(Path(path).name)
        if "09-" in Path(path).name:
            raise ApiError("DOCUMENT_PARSE_FAILED", retryable=True)
        return original(path, file_type)

    monkeypatch.setattr(runner_module, "parse_document", failing)

    job_id = client.post("/api/demo/seed").json()["job_id"]
    worker.run_once()

    assert sum(1 for name in attempts if name.startswith("09-")) == 3
    payload = job_payload(context, job_id)
    assert payload["failed"] == 1


def test_manifest_change_fails_job(tmp_path, writable_dataset) -> None:
    with demo_env(tmp_path, writable_dataset) as context:
        job_id = seed(context)

        manifest_path = writable_dataset / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["generated_at"] = "2030-01-01T00:00:00Z"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

        context.worker.run_once()
        payload = job_payload(context, job_id)

    assert payload["status"] == constants.JOB_FAILED
    assert payload["errors"] == []


def test_fingerprint_change_fails_job(tmp_path, writable_dataset) -> None:
    with demo_env(tmp_path, writable_dataset) as context:
        job_id = seed(context)

        # 用不同切片参数重建 worker：流水线指纹变化
        changed = Worker(
            build_settings(
                tmp_path,
                demo_dataset_path=str(writable_dataset),
                chunk_target_chars=999,
            ),
            context.session_factory,
            worker_id="changed-worker",
        )
        changed.has_process_lock = True
        try:
            changed.run_once()
        finally:
            changed.stop()

        with context.session_factory() as session:
            job = session.get(DemoSeedJob, job_id)
            assert job.status == constants.JOB_FAILED
            assert job.error_code == "DEMO_PIPELINE_CHANGED"
            assert job.error_message


def test_seed_after_failed_job_creates_new_job(tmp_path, writable_dataset) -> None:
    with demo_env(tmp_path, writable_dataset) as context:
        first_id = seed(context)
        manifest_path = writable_dataset / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["generated_at"] = "2031-01-01T00:00:00Z"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
        context.worker.run_once()
        assert job_payload(context, first_id)["status"] == constants.JOB_FAILED

        second_id = seed(context)
        assert second_id != first_id
        context.worker.run_once()
        payload = job_payload(context, second_id)

    assert payload["status"] == constants.JOB_COMPLETED
    assert payload["imported"] == 15
