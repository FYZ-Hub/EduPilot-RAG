"""端到端幂等：真实后台 worker（lifespan 启动）驱动的两次 seed。

第二次 seed 必须是可审计的纯跳过任务：文档行主键、检查点主键与 DocumentBlock
的标识和计数都不得变化，也不得重新解析或重复写块。
"""

from __future__ import annotations

import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from tests.conftest import DEMO_DATASET_PATH, build_settings
from app import constants
from app.main import create_app
from app.models import Document, DocumentBlock, DocumentChunk, DocumentPipelineState


POLL_TIMEOUT_SECONDS = 120.0


@contextmanager
def running_app(tmp_path: Path, **overrides):
    """启动带真实后台 worker 的应用，并在退出时释放资源。"""
    settings = build_settings(
        tmp_path,
        demo_dataset_path=str(DEMO_DATASET_PATH),
        worker_enabled=True,
        worker_poll_seconds=0.05,
        **overrides,
    )
    application = create_app(settings)
    try:
        with TestClient(application) as client:
            yield client, application.state.context
    finally:
        application.state.context.engine.dispose()


def wait_for_job(client: TestClient, job_id: str, *, timeout: float = POLL_TIMEOUT_SECONDS) -> dict:
    """轮询任务直到离开 queued/running；返回最终 payload。"""
    deadline = time.monotonic() + timeout
    payload = client.get(f"/api/demo/jobs/{job_id}").json()
    while payload["status"] in {constants.JOB_QUEUED, constants.JOB_RUNNING}:
        if time.monotonic() > deadline:
            raise AssertionError(f"任务 {job_id} 在 {timeout}s 内未结束：{payload['status']}")
        time.sleep(0.02)
        payload = client.get(f"/api/demo/jobs/{job_id}").json()
    return payload


def snapshot(session_factory) -> dict:
    """采集四张表（含 FTS5）的标识与计数，用于比对两次 seed 前后是否一致。"""
    result: dict = {}
    with session_factory() as session:
        result["documents"] = sorted(session.scalars(select(Document.id)).all())
        result["states"] = sorted(session.scalars(select(DocumentPipelineState.id)).all())
        result["state_stages"] = sorted(
            session.scalars(select(DocumentPipelineState.last_completed_stage)).all()
        )
        result["blocks"] = sorted(session.scalars(select(DocumentBlock.id)).all())
        result["block_counts"] = dict(
            session.execute(
                select(DocumentBlock.doc_id, func.count(DocumentBlock.id)).group_by(DocumentBlock.doc_id)
            ).all()
        )
        result["chunks"] = sorted(session.scalars(select(DocumentChunk.id)).all())
        result["chunk_counts"] = dict(
            session.execute(
                select(DocumentChunk.doc_id, func.count(DocumentChunk.id)).group_by(DocumentChunk.doc_id)
            ).all()
        )
        result["fts_rows"] = sorted(
            session.scalars(text("SELECT chunk_id FROM chunk_fts")).all()
        )
        result["fts_rowids"] = sorted(
            session.scalars(select(DocumentChunk.fts_rowid)).all()
        )
    return result


@pytest.mark.slow
def test_second_seed_is_pure_skip_and_never_touches_storage(tmp_path) -> None:
    with running_app(tmp_path) as (client, context):
        first_id = client.post("/api/demo/seed").json()["job_id"]
        first = wait_for_job(client, first_id)
        assert first["status"] == constants.JOB_COMPLETED
        assert first["imported"] == 15
        assert first["processed"] == 15
        assert first["progress_percent"] == 100
        assert {item["last_completed_stage"] for item in first["documents"]} == {constants.TARGET_STAGE}

        before = snapshot(context.session_factory)
        # 三方索引记录数必须一致（SQLite chunk / FTS 行一一对应）
        assert len(before["chunks"]) == len(before["fts_rows"]) > 0

        second_response = client.post("/api/demo/seed")
        assert second_response.status_code == 202
        second_id = second_response.json()["job_id"]
        assert second_id != first_id

        second = wait_for_job(client, second_id)
        assert second["status"] == constants.JOB_COMPLETED
        assert second["imported"] == 0
        assert second["resumed"] == 0
        assert second["failed"] == 0
        assert second["skipped"] == 15
        assert second["processed"] == 15
        assert {item["result"] for item in second["documents"]} == {constants.RESULT_SKIPPED}
        assert second["errors"] == []

        after = snapshot(context.session_factory)

        # 文档行、检查点、块、切片与 FTS 行主键集合完全不变：不重复写任何索引
        assert before["documents"] == after["documents"]
        assert before["states"] == after["states"]
        assert before["state_stages"] == after["state_stages"]
        assert before["blocks"] == after["blocks"]
        assert before["block_counts"] == after["block_counts"]
        assert before["chunks"] == after["chunks"]
        assert before["chunk_counts"] == after["chunk_counts"]
        assert before["fts_rows"] == after["fts_rows"]
        assert before["fts_rowids"] == after["fts_rowids"]


@pytest.mark.slow
def test_demo_dataset_is_activated_and_only_then_retrievable(tmp_path) -> None:
    """隔离 Fake 环境：全部成功 → 原子激活 → loaded=true 且来源可检索。"""
    with running_app(tmp_path) as (client, context):
        job_id = client.post("/api/demo/seed").json()["job_id"]
        job = wait_for_job(client, job_id)
        assert job["status"] == constants.JOB_COMPLETED

        listing = client.get("/api/documents").json()
        assert listing["total"] == 15
        assert listing["counts"]["ready"] == 15
        assert listing["counts"]["retrievable"] == 15
        assert {item["status"] for item in listing["items"]} == {constants.STATUS_READY}
        assert all(item["retrievable"] is True for item in listing["items"])
        assert all(
            item["activation_state"] == constants.ACTIVATION_ACTIVE for item in listing["items"]
        )

        status = client.get("/api/demo/status").json()
        assert status["loaded"] is True
        assert status["ready_documents"] == 15
        assert status["active_dataset_version"] == "2026.1"
        assert status["serving_previous_version"] is False

        # 激活后来源可检索，检索选项来自真实可检索文档
        with context.session_factory() as session:
            chunk_id = session.scalar(select(DocumentChunk.id).order_by(DocumentChunk.id))
        source = client.get(f"/api/sources/{chunk_id}")
        assert source.status_code == 200
        body = source.json()
        assert body["chunk_id"] == chunk_id
        assert body["text"]
        assert body["file_name"]

        options = client.get("/api/retrieval/options").json()
        assert options["doc_categories"], options
        assert options["majors"], options
        assert options["active_dataset_version"] == "2026.1"
        assert options["demo_available"] is True
