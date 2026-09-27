"""上传安全：类型、大小、容器、文件名与去重语义。"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient

from tests.conftest import build_settings, demo_file, upload_bytes, upload_file

DOCX_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.wordprocessingml.document.main+xml"/></Types>'
)
XLSX_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-'
    'officedocument.spreadsheetml.sheet.main+xml"/></Types>'
)
MACRO_CONTENT_TYPES = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
    '<Override PartName="/word/document.xml" ContentType="application/vnd.ms-word.document.'
    'macroEnabled.main+xml"/></Types>'
)


def _zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


def _assert_error(response, code: str) -> None:
    assert response.status_code >= 400, response.text
    payload = response.json()
    assert payload["code"] == code
    assert payload["request_id"]
    assert "Traceback" not in response.text
    assert "/app/" not in response.text


def _stored_files(settings) -> list[Path]:
    """上传目录内的实际文件（忽略受控临时目录 ``.tmp``）。"""
    root = Path(settings.upload_path)
    if not root.exists():
        return []
    return [path for path in root.iterdir() if path.is_file()]


def _upload_with_raw_filename(client: TestClient, path: Path, filename: str) -> "object":
    """手工构造 multipart 请求体，使服务端收到的文件名不被测试客户端改写。"""
    boundary = "----campusedges"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    body += path.read_bytes() + f"\r\n--{boundary}--\r\n".encode("utf-8")
    return client.post(
        "/api/documents",
        content=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )


# --- 正常上传 ---------------------------------------------------------------


@pytest.mark.parametrize("fragment", ["01-培养方案", "03-课程大纲-QM-CS201", "08-校历"])
def test_valid_documents_are_accepted(client: TestClient, fragment: str) -> None:
    path = demo_file(fragment)
    response = upload_file(client, path, content_type=None)
    assert response.status_code == 202, response.text
    payload = response.json()
    assert payload["disposition"] == "created"
    assert payload["deduplicated"] is False
    assert payload["status_url"].endswith("/status")
    assert payload["document_id"]


def test_valid_upload_gets_server_generated_storage_name(client: TestClient, settings) -> None:
    path = demo_file("03-课程大纲-QM-CS201")
    response = upload_file(client, path, filename="我的 课程大纲 (最终版).docx")
    assert response.status_code == 202
    document_id = response.json()["document_id"]

    detail = client.get(f"/api/documents/{document_id}").json()
    assert detail["file_name"] == "我的 课程大纲 (最终版).docx"

    stored = _stored_files(settings)
    assert len(stored) == 1
    assert stored[0].name != "我的 课程大纲 (最终版).docx"
    assert " " not in stored[0].name


# --- 类型与大小 -------------------------------------------------------------


def test_unknown_extension_is_rejected(client: TestClient) -> None:
    response = upload_bytes(client, b"hello", filename="notes.txt", content_type="text/plain")
    _assert_error(response, "DOCUMENT_INVALID_TYPE")


def test_empty_file_is_rejected(client: TestClient) -> None:
    response = upload_bytes(client, b"", filename="empty.pdf", content_type="application/pdf")
    _assert_error(response, "DOCUMENT_EMPTY")


def test_declared_mime_mismatch_is_rejected(client: TestClient) -> None:
    path = demo_file("01-培养方案")
    response = upload_file(client, path, filename="plan.pdf", content_type="text/plain")
    _assert_error(response, "DOCUMENT_CONTENT_TYPE_MISMATCH")


def test_extension_forgery_is_rejected(client: TestClient) -> None:
    """真实的 XLSX 字节改成 .docx 扩展名必须被拒绝。"""
    path = demo_file("08-校历")
    payload = path.read_bytes()
    response = upload_bytes(
        client,
        payload,
        filename="fake.docx",
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    _assert_error(response, "DOCUMENT_CONTENT_TYPE_MISMATCH")


def test_corrupt_pdf_is_rejected(client: TestClient) -> None:
    response = upload_bytes(
        client, b"%PDF-1.4\nthis is not a real pdf body", filename="broken.pdf", content_type="application/pdf"
    )
    _assert_error(response, "DOCUMENT_CORRUPT")


def test_oversized_file_is_rejected(tmp_path) -> None:
    from app.main import create_app
    from fastapi.testclient import TestClient as Client

    settings = build_settings(tmp_path, max_upload_mb=1)
    application = create_app(settings)
    with Client(application) as test_client:
        payload = b"%PDF-1.4\n" + b"0" * (2 * 1024 * 1024)
        response = upload_bytes(test_client, payload, filename="big.pdf", content_type="application/pdf")
        _assert_error(response, "DOCUMENT_TOO_LARGE")
        # 超限文件不得进入上传目录
        assert not _stored_files(settings)
    application.state.context.engine.dispose()


def test_upload_temp_directory_is_clean_after_success(client: TestClient, settings) -> None:
    upload_file(client, demo_file("03-课程大纲-QM-CS201"))
    temp_dir = Path(settings.upload_tmp_path)
    assert not temp_dir.exists() or not list(temp_dir.glob("*"))


# --- 文件名安全 -------------------------------------------------------------


@pytest.mark.parametrize(
    "filename",
    [
        "../evil.pdf",
        "..\\evil.pdf",
        "/etc/passwd.pdf",
        "C:\\windows\\evil.pdf",
        "\\\\server\\share\\evil.pdf",
        "folder/evil.pdf",
        "folder\\evil.pdf",
        "con.pdf",
        "nul.pdf",
        "evil\u202eexe.pdf",
        "evil\u2215path.pdf",
        "trailing .pdf ",
        "onlydots..pdf",
    ],
)
def test_unsafe_filenames_are_rejected(client: TestClient, filename: str) -> None:
    path = demo_file("01-培养方案")
    response = upload_file(client, path, filename=filename)
    _assert_error(response, "DOCUMENT_UNSAFE_NAME")


def test_control_characters_in_filename_are_rejected(client: TestClient) -> None:
    path = demo_file("01-培养方案")
    response = _upload_with_raw_filename(client, path, "bad\x01name.pdf")
    _assert_error(response, "DOCUMENT_UNSAFE_NAME")


def test_missing_file_field_is_rejected(client: TestClient) -> None:
    response = client.post("/api/documents")
    assert response.status_code == 422
    assert response.json()["code"] == "REQUEST_VALIDATION_ERROR"
    assert response.json()["request_id"]


# --- 容器安全 ---------------------------------------------------------------


def test_encrypted_pdf_is_rejected(client: TestClient, tmp_path) -> None:
    encrypted = tmp_path / "encrypted.pdf"
    document = pymupdf.open()
    page = document.new_page()
    page.insert_text((72, 72), "secret")
    document.save(
        str(encrypted),
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner",
        user_pw="user",
    )
    document.close()

    response = upload_file(client, encrypted, filename="encrypted.pdf", content_type="application/pdf")
    _assert_error(response, "DOCUMENT_UNSAFE_CONTAINER")


def test_macro_payload_is_rejected(client: TestClient) -> None:
    payload = _zip_bytes(
        {"[Content_Types].xml": DOCX_CONTENT_TYPES.encode(), "word/vbaProject.bin": b"macro"}
    )
    response = upload_bytes(client, payload, filename="macro.docx")
    _assert_error(response, "DOCUMENT_UNSAFE_CONTAINER")


def test_macro_enabled_content_type_is_rejected(client: TestClient) -> None:
    payload = _zip_bytes({"[Content_Types].xml": MACRO_CONTENT_TYPES.encode()})
    response = upload_bytes(client, payload, filename="macro2.docx")
    _assert_error(response, "DOCUMENT_UNSAFE_CONTAINER")


def test_embedded_ole_object_is_rejected(client: TestClient) -> None:
    payload = _zip_bytes(
        {
            "[Content_Types].xml": DOCX_CONTENT_TYPES.encode(),
            "word/embeddings/oleObject1.bin": b"ole",
        }
    )
    response = upload_bytes(client, payload, filename="ole.docx")
    _assert_error(response, "DOCUMENT_UNSAFE_CONTAINER")


def test_external_links_are_rejected(client: TestClient) -> None:
    payload = _zip_bytes(
        {
            "[Content_Types].xml": XLSX_CONTENT_TYPES.encode(),
            "xl/externalLinks/externalLink1.xml": b"<externalLink/>",
        }
    )
    response = upload_bytes(client, payload, filename="links.xlsx")
    _assert_error(response, "DOCUMENT_UNSAFE_CONTAINER")


def test_external_relationship_is_rejected(client: TestClient) -> None:
    payload = _zip_bytes(
        {
            "[Content_Types].xml": DOCX_CONTENT_TYPES.encode(),
            "_rels/.rels": (
                b'<?xml version="1.0"?><Relationships><Relationship '
                b'TargetMode="External" Target="http://example.test/x"/></Relationships>'
            ),
        }
    )
    response = upload_bytes(client, payload, filename="external.docx")
    _assert_error(response, "DOCUMENT_UNSAFE_CONTAINER")


def test_zip_bomb_ratio_is_rejected(client: TestClient) -> None:
    payload = _zip_bytes({"payload.bin": b"\0" * (2 * 1024 * 1024)})
    response = upload_bytes(client, payload, filename="bomb.docx")
    _assert_error(response, "DOCUMENT_UNSAFE_CONTAINER")


def test_zip_path_traversal_entry_is_rejected(client: TestClient) -> None:
    payload = _zip_bytes({"[Content_Types].xml": DOCX_CONTENT_TYPES.encode(), "../evil.xml": b"x"})
    response = upload_bytes(client, payload, filename="traversal.docx")
    _assert_error(response, "DOCUMENT_UNSAFE_CONTAINER")


def test_too_many_zip_entries_is_rejected(client: TestClient) -> None:
    entries = {"[Content_Types].xml": DOCX_CONTENT_TYPES.encode()}
    for index in range(2100):
        entries[f"word/part{index}.xml"] = b"x"
    response = upload_bytes(client, _zip_bytes(entries), filename="many.docx")
    _assert_error(response, "DOCUMENT_UNSAFE_CONTAINER")


def test_zip_without_content_types_is_rejected(client: TestClient) -> None:
    payload = _zip_bytes({"word/document.xml": b"<w:document/>"})
    response = upload_bytes(client, payload, filename="nocontent.docx")
    _assert_error(response, "DOCUMENT_CONTENT_TYPE_MISMATCH")


# --- 去重语义 ---------------------------------------------------------------


def test_same_sha_upload_is_attached_before_completion(client: TestClient) -> None:
    path = demo_file("01-培养方案")
    first = upload_file(client, path)
    assert first.status_code == 202
    assert first.json()["disposition"] == "created"

    second = upload_file(client, path)
    assert second.status_code == 202
    payload = second.json()
    assert payload["disposition"] == "attached"
    assert payload["deduplicated"] is True
    assert payload["document_id"] == first.json()["document_id"]


def test_duplicate_upload_does_not_persist_extra_files(client: TestClient, settings) -> None:
    path = demo_file("01-培养方案")
    upload_file(client, path)
    upload_file(client, path)
    assert len(_stored_files(settings)) == 1


def test_demo_and_upload_with_same_sha_are_independent(client: TestClient, worker, context) -> None:
    """即使 SHA-256 相同，demo 与 upload 也必须使用不同 doc_id 且删除互不影响。"""
    from sqlalchemy import select

    from app import constants
    from app.demo import service as demo_service
    from app.models import Document

    with context.session_factory() as session:
        job, _ = demo_service.seed_job(session, context.settings)
        assert job.id
        session.commit()
    worker.run_once()

    response = upload_file(client, demo_file("01-培养方案"))
    assert response.status_code == 202
    upload_id = response.json()["document_id"]

    with context.session_factory() as session:
        demo_documents = list(
            session.scalars(select(Document).where(Document.source_type == constants.SOURCE_DEMO))
        )
        upload_document = session.get(Document, upload_id)

    assert len(demo_documents) == 15
    assert upload_document is not None
    assert upload_document.sha256 in {document.sha256 for document in demo_documents}
    assert upload_id not in {document.id for document in demo_documents}

    assert client.delete(f"/api/documents/{upload_id}").status_code == 204
    with context.session_factory() as session:
        remaining = list(
            session.scalars(select(Document).where(Document.source_type == constants.SOURCE_DEMO))
        )
    assert len(remaining) == 15
    assert all(document.deleted_at is None for document in remaining)
