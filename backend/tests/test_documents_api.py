"""文档 API：上传语义、列表/详情/状态/预览/删除与错误体。"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select, text

from tests.conftest import demo_file, upload_file, upload_bytes
from app import constants
from app.models import Document, DocumentBlock, DocumentChunk, DocumentPipelineState


def _run_demo_job(context, worker) -> str:
    from app.demo import service as demo_service

    with context.session_factory() as session:
        job, _ = demo_service.seed_job(session, context.settings)
        job_id = job.id
        session.commit()
    worker.run_once()
    return job_id


def test_list_and_detail_after_demo_ingestion(client: TestClient, worker, context) -> None:
    _run_demo_job(context, worker)

    listing = client.get("/api/documents").json()
    assert listing["total"] == 15
    # 全部成功并原子激活后：15 份均为 ready / retrievable / active
    assert listing["counts"]["ready"] == 15
    assert listing["counts"]["retrievable"] == 15
    assert listing["counts"]["failed"] == 0
    assert listing["counts"]["processing"] == 0

    for item in listing["items"]:
        assert item["source_type"] == "demo"
        assert item["status"] == constants.STATUS_READY
        assert item["retrievable"] is True
        assert item["activation_state"] == constants.ACTIVATION_ACTIVE
        assert item["current_stage"] is None
        assert item["chunk_count"] > 0
        assert item["block_count"] > 0
        assert item["locator_types"]
        assert "/app/" not in str(item)

    detail = client.get(f"/api/documents/{listing['items'][0]['id']}").json()
    assert len(detail["checksum"]) == 64
    assert detail["dataset_version"] == "2026.1"
    assert "storage_path" not in detail


def test_status_endpoint_matches_detail(client: TestClient, worker, context) -> None:
    _run_demo_job(context, worker)
    document_id = client.get("/api/documents").json()["items"][0]["id"]

    status = client.get(f"/api/documents/{document_id}/status").json()
    assert status["id"] == document_id
    assert status["status"] == constants.STATUS_READY
    assert status["retrievable"] is True
    assert status["activation_state"] == constants.ACTIVATION_ACTIVE
    assert status["current_stage"] is None
    assert status["error"] is None


def test_preview_returns_ordered_blocks_without_host_paths(client: TestClient, worker, context) -> None:
    _run_demo_job(context, worker)
    items = client.get("/api/documents").json()["items"]
    pdf_item = next(item for item in items if item["file_type"] == "pdf")

    response = client.get(f"/api/documents/{pdf_item['id']}/preview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_blocks"] > 0
    assert payload["blocks"]
    indexes = [block["block_index"] for block in payload["blocks"]]
    assert indexes == sorted(indexes)
    assert any(block["page_number"] for block in payload["blocks"])
    assert "/app/" not in response.text
    assert "storage_path" not in response.text


def test_unknown_document_returns_request_id(client: TestClient) -> None:
    response = client.get("/api/documents/does-not-exist")
    assert response.status_code == 404
    payload = response.json()
    assert payload["code"] == "DOCUMENT_NOT_FOUND"
    assert payload["request_id"]
    assert set(payload) == {"code", "message", "details", "request_id"}


def test_unknown_route_error_body_is_safe(client: TestClient) -> None:
    response = client.get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"
    assert response.json()["request_id"]


def test_response_headers_carry_request_id(client: TestClient) -> None:
    response = client.get("/api/documents")
    assert response.headers.get("X-Request-ID")


# --- 去重与处置语义 ---------------------------------------------------------


def test_existing_ready_returns_200(client: TestClient, context) -> None:
    path = demo_file("01-培养方案")
    created = upload_file(client, path)
    document_id = created.json()["document_id"]

    with context.session_factory() as session:
        document = session.get(Document, document_id)
        document.status = constants.STATUS_READY
        session.commit()

    again = upload_file(client, path)
    assert again.status_code == 200
    assert again.json()["disposition"] == "existing_ready"
    assert again.json()["document_id"] == document_id


def test_failed_retryable_returns_retry_started(client: TestClient, context) -> None:
    path = demo_file("01-培养方案")
    document_id = upload_file(client, path).json()["document_id"]

    with context.session_factory() as session:
        document = session.get(Document, document_id)
        document.status = constants.STATUS_FAILED
        document.error_code = "DOCUMENT_PARSE_FAILED"
        document.error_message = "解析失败"
        document.error_retryable = True
        session.commit()

    again = upload_file(client, path)
    assert again.status_code == 202
    assert again.json()["disposition"] == "retry_started"
    assert again.json()["document_id"] == document_id

    with context.session_factory() as session:
        document = session.get(Document, document_id)
        assert document.status == constants.STATUS_QUEUED
        assert document.error_code is None


def test_failed_not_retryable_returns_409(client: TestClient, context) -> None:
    path = demo_file("01-培养方案")
    document_id = upload_file(client, path).json()["document_id"]

    with context.session_factory() as session:
        document = session.get(Document, document_id)
        document.status = constants.STATUS_FAILED
        document.error_code = "DOCUMENT_UNSAFE_CONTAINER"
        document.error_message = "包含不允许的内容"
        document.error_retryable = False
        session.commit()

    again = upload_file(client, path)
    assert again.status_code == 409
    assert again.json()["code"] == "DOCUMENT_RETRY_NOT_ALLOWED"


# --- 删除语义 ---------------------------------------------------------------


def test_deleting_upload_removes_file_blocks_and_state(client: TestClient, worker, context) -> None:
    response = upload_file(client, demo_file("03-课程大纲-QM-CS201"))
    document_id = response.json()["document_id"]
    worker.run_once()

    with context.session_factory() as session:
        document = session.get(Document, document_id)
        stored_path = Path(document.storage_path)
        assert document.pipeline_state is not None
        assert session.scalar(
            select(func.count(DocumentBlock.id)).where(DocumentBlock.doc_id == document_id)
        ) > 0
    assert stored_path.is_file()

    assert client.delete(f"/api/documents/{document_id}").status_code == 204

    with context.session_factory() as session:
        document = session.get(Document, document_id)
        assert document.deleted_at is not None
        assert document.storage_path is None
        assert session.scalar(
            select(func.count(DocumentBlock.id)).where(DocumentBlock.doc_id == document_id)
        ) == 0
        assert session.scalar(
            select(func.count(DocumentPipelineState.id)).where(
                DocumentPipelineState.doc_id == document_id
            )
        ) == 0
    assert not stored_path.exists()


def test_deleting_demo_document_never_touches_read_only_source(
    client: TestClient, worker, context, settings
) -> None:
    _run_demo_job(context, worker)
    items = client.get("/api/documents").json()["items"]
    target = next(item for item in items if item["source_type"] == "demo")

    with context.session_factory() as session:
        source_path = Path(session.get(Document, target["id"]).storage_path)
    assert source_path.is_file()
    before = source_path.read_bytes()

    assert client.delete(f"/api/documents/{target['id']}").status_code == 204

    assert source_path.is_file()
    assert source_path.read_bytes() == before
    assert str(source_path).startswith(str(Path(settings.demo_dataset_path)))


def test_deleting_twice_returns_not_found(client: TestClient) -> None:
    document_id = upload_file(client, demo_file("01-培养方案")).json()["document_id"]
    assert client.delete(f"/api/documents/{document_id}").status_code == 204
    assert client.delete(f"/api/documents/{document_id}").status_code == 404


def test_no_bulk_clear_endpoint(client: TestClient) -> None:
    for path in ("/api/documents", "/api/documents/all", "/api/demo/reset"):
        assert client.delete(path).status_code in (404, 405)


def test_upload_after_delete_creates_new_document(client: TestClient) -> None:
    path = demo_file("01-培养方案")
    first = upload_file(client, path).json()["document_id"]
    assert client.delete(f"/api/documents/{first}").status_code == 204

    second = upload_file(client, path)
    assert second.status_code == 202
    assert second.json()["disposition"] == "created"
    assert second.json()["document_id"] != first


def test_upload_document_reaches_ready_after_reconciliation(
    client: TestClient, worker, context
) -> None:
    """upload 不依赖 demo 整体激活：对账通过即 ready 且可检索。"""
    document_id = upload_file(client, demo_file("11-课表")).json()["document_id"]
    worker.run_once()

    status = client.get(f"/api/documents/{document_id}/status").json()
    assert status["status"] == constants.STATUS_READY
    assert status["retrievable"] is True
    assert status["activation_state"] is None
    assert status["current_stage"] is None
    assert status["block_count"] > 0
    assert status["chunk_count"] > 0

    with context.session_factory() as session:
        state = session.scalar(
            select(DocumentPipelineState).where(DocumentPipelineState.doc_id == document_id)
        )
        assert state.last_completed_stage == constants.TARGET_STAGE
        assert (
            state.vector_record_count
            == state.fts_record_count
            == state.expected_chunk_count
            > 0
        )
        chunk_ids = set(
            session.scalars(select(DocumentChunk.id).where(DocumentChunk.doc_id == document_id))
        )
        fts_ids = set(session.scalars(text("SELECT chunk_id FROM chunk_fts")).all())

    # 三方集合一致：SQLite chunk = Chroma 向量 = FTS 行
    assert chunk_ids
    assert set(worker.vectors.vectors_for_document(document_id)) == chunk_ids
    assert chunk_ids <= fts_ids


def test_upload_with_extra_field_still_accepts_single_file(client: TestClient) -> None:
    path = demo_file("01-培养方案")
    with open(path, "rb") as handle:
        response = client.post(
            "/api/documents",
            files={"file": (path.name, handle, "application/pdf")},
            data={"note": "extra"},
        )
    assert response.status_code == 202


def test_extra_file_field_is_ignored(client: TestClient) -> None:
    """每次只接收一个 file 字段；其它字段不会被当作文件登记。"""
    path = demo_file("01-培养方案")
    with open(path, "rb") as handle_one, open(path, "rb") as handle_two:
        response = client.post(
            "/api/documents",
            files=[
                ("file", (path.name, handle_one, "application/pdf")),
                ("other", (path.name, handle_two, "application/pdf")),
            ],
        )
    assert response.status_code == 202
    assert client.get("/api/documents").json()["total"] == 1


def test_corrupt_payload_error_is_safe(client: TestClient) -> None:
    response = upload_bytes(client, b"%PDF-1.4 broken", filename="broken.pdf", content_type="application/pdf")
    body = response.text
    assert response.json()["code"] == "DOCUMENT_CORRUPT"
    for leaked in ("Traceback", "/app/", "sqlite", "SELECT"):
        assert leaked not in body
