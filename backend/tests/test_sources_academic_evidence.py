"""BUG-8-E2E-03 回归：学业证据来源可通过 ``GET /api/sources/{chunk_id}`` 读取。

修复前：真实导入的学业证据切片不进入 RAG 流水线（无 pipeline state、``retrievable=false``），
因此 ``POST /api/academic/plan`` 返回的 ``evidence[].chunk_id`` 调 ``GET /api/sources/{chunk_id}``
一律返回 404 ``SOURCE_NOT_FOUND``，前端证据抽屉无法显示正文与定位。

修复后：仅在「所属文档未删除」且「chunk_id 被学业来源表显式引用」时返回 200，
字段与既有 sources API 完全一致；未知 / 未引用 / 已删除来源仍是同一个统一错误。

全部离线：只使用真实演示语料与确定性导入，不访问网络、不调用模型。
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app import constants
from app.models import (
    AcademicRecordSet,
    AcademicRuleSet,
    CourseRecordRow,
    DegreeRuleCourse,
    DegreeRuleRow,
    Document,
    DocumentChunk,
    utcnow,
)
from tests.conftest import demo_file

PLAN_ENDPOINT = "/api/academic/plan"
RECORDS_ENDPOINT = "/api/academic/records/import"
RULES_ENDPOINT = "/api/academic/rules/import"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
PDF_MIME = "application/pdf"

SOURCE_FIELDS = {
    "chunk_id",
    "doc_id",
    "file_name",
    "file_type",
    "document_version",
    "effective_from",
    "dataset_version",
    "text",
    "page_number",
    "sheet_name",
    "row_start",
    "row_end",
    "section_title",
}
LEAK_TOKENS = ("source_key", "storage_path", "safe_storage_name", "sha256", "/app/")

# 学业来源表：任何一张显式引用 chunk_id 即视为「学业证据来源」。
REFERENCE_MODELS = (
    AcademicRecordSet,
    CourseRecordRow,
    AcademicRuleSet,
    DegreeRuleRow,
    DegreeRuleCourse,
)


def _import(client: TestClient, endpoint: str, name_fragment: str, mime: str) -> str:
    path = demo_file(name_fragment)
    with open(path, "rb") as handle:
        files = {"file": (path.name, handle, mime)}
        response = client.post(endpoint, files=files)
    assert response.status_code == 200, response.text
    return response.json()["id"]


def _imported_plan(client: TestClient) -> dict:
    """真实导入课程记录 + 培养方案后计算规划，返回 PlanningResult。"""
    record_set_id = _import(client, RECORDS_ENDPOINT, "13-课程记录-匿名学生A", XLSX_MIME)
    rule_set_id = _import(client, RULES_ENDPOINT, "01-培养方案", PDF_MIME)
    response = client.post(
        PLAN_ENDPOINT,
        json={"record_set_id": record_set_id, "rule_set_id": rule_set_id},
    )
    assert response.status_code == 200, response.text
    return response.json()


def _referenced_chunk_ids(context) -> set[str]:
    with context.session_factory() as session:
        ids: set[str] = set()
        for model in REFERENCE_MODELS:
            ids |= {
                str(value)
                for value in session.scalars(select(model.source_chunk_id)).all()
                if value
            }
        return ids


def _assert_not_found(response) -> dict:
    """统一的 404 安全错误体；不泄漏存在与否。"""
    assert response.status_code == 404, response.text
    body = response.json()
    assert body["code"] == "SOURCE_NOT_FOUND"
    assert body["request_id"]
    assert set(body) == {"code", "message", "details", "request_id"}
    assert "/app/" not in response.text
    return body


# ---------------------------------------------------------------------------
# 正向：修复前失败，修复后 200 且正文 / 定位与真实切片一致
# ---------------------------------------------------------------------------


def test_plan_evidence_sources_are_readable(client: TestClient, context) -> None:
    payload = _imported_plan(client)
    evidence = payload["evidence"]
    assert evidence, "规划结果必须带真实证据"

    for item in evidence:
        response = client.get(f"/api/sources/{item['chunk_id']}")
        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == SOURCE_FIELDS
        assert body["chunk_id"] == item["chunk_id"]
        assert body["doc_id"] == item["doc_id"]
        assert body["file_name"] == item["file_name"]
        assert body["document_version"] == item["document_version"]
        assert body["effective_from"] == item["effective_from"]
        assert body["text"] == item["quote"]

        # 正文与定位必须来自真实 DocumentChunk 行
        with context.session_factory() as session:
            chunk = session.get(DocumentChunk, item["chunk_id"])
            assert chunk is not None
            assert body["text"] == chunk.text
            locator = chunk.locator
            assert body["page_number"] == locator.get("page_number")
            assert body["sheet_name"] == locator.get("sheet_name")
            assert body["row_start"] == locator.get("row_start")
            assert body["row_end"] == locator.get("row_end")
            assert body["section_title"] == locator.get("section_title")

        # 与 PlanningResult 定位逐字段一致
        assert item["page_number"] == body["page_number"]
        assert item["sheet_name"] == body["sheet_name"]
        assert item["row_start"] == body["row_start"]
        assert item["row_end"] == body["row_end"]
        assert item["section_title"] == body["section_title"]

        serialized = str(body)
        for leaked in LEAK_TOKENS:
            assert leaked not in serialized, leaked


def test_every_referenced_evidence_chunk_is_readable(client: TestClient, context) -> None:
    """五张学业来源表显式引用的每个切片都必须可按现有字段读取。"""
    _imported_plan(client)
    referenced = _referenced_chunk_ids(context)
    assert referenced
    for chunk_id in referenced:
        response = client.get(f"/api/sources/{chunk_id}")
        assert response.status_code == 200, response.text
        assert set(response.json()) == SOURCE_FIELDS


# ---------------------------------------------------------------------------
# 反向：未引用 / 已删除仍是统一 404
# ---------------------------------------------------------------------------


def test_unreferenced_non_retrievable_chunk_is_hidden(client: TestClient, context) -> None:
    """无 pipeline state 且 ``retrievable=false`` 但未被任何学业来源引用的切片仍是 404。"""
    with context.session_factory() as session:
        document = Document(
            source_type=constants.SOURCE_UPLOAD,
            source_key="probe:unreferenced-evidence",
            file_name="unreferenced.pdf",
            file_type="pdf",
            mime_type="application/pdf",
            sha256="a" * 64,
            doc_category="academic_policy",
            status=constants.STATUS_QUEUED,
            retrievable=False,
        )
        session.add(document)
        session.flush()
        chunk = DocumentChunk(
            id="abcd" * 16,
            doc_id=document.id,
            chunk_index=0,
            text="未被任何学业来源引用的切片",
            locator={"page_number": 1},
            citation={},
            parser_version="test",
            chunker_version="test",
            chunker_fingerprint="b" * 64,
        )
        session.add(chunk)
        session.commit()
        chunk_id = chunk.id

    unknown = _assert_not_found(client.get(f"/api/sources/{'0' * 64}"))
    hidden = _assert_not_found(client.get(f"/api/sources/{chunk_id}"))
    # 未引用不可检索与「不存在」对外完全一致
    assert (hidden["code"], hidden["message"], hidden["details"]) == (
        unknown["code"],
        unknown["message"],
        unknown["details"],
    )


def test_deleted_source_document_evidence_is_hidden(client: TestClient, context) -> None:
    """所属文档一旦删除，即使切片仍被学业来源引用也返回 404。"""
    payload = _imported_plan(client)
    evidence = payload["evidence"]
    target = evidence[0]

    with context.session_factory() as session:
        session.execute(
            update(Document).where(Document.id == target["doc_id"]).values(deleted_at=utcnow())
        )
        session.commit()

    hidden = _assert_not_found(client.get(f"/api/sources/{target['chunk_id']}"))
    assert hidden["code"] == "SOURCE_NOT_FOUND"

    # 其它文档的证据不受影响，证明兜底按单个文档判定
    remaining = [item for item in evidence if item["doc_id"] != target["doc_id"]]
    if remaining:
        assert client.get(f"/api/sources/{remaining[0]['chunk_id']}").status_code == 200


# ---------------------------------------------------------------------------
# 反向：被引用但已脱离学业导入资格的 upload 来源仍是统一 404
# ---------------------------------------------------------------------------


def _referenced_upload_chunk(context, *, key: str, **overrides) -> str:
    """建立「upload 文档 + 真实切片 + 学业表引用」，返回被引用的 chunk_id。"""
    values = {
        "source_type": constants.SOURCE_UPLOAD,
        "source_key": f"probe:referenced:{key}",
        "file_name": f"probe-{key}.xlsx",
        "file_type": "xlsx",
        "mime_type": XLSX_MIME,
        "sha256": (key.encode().hex() + "0" * 64)[:64],
        "doc_category": "course_records",
        "status": constants.STATUS_QUEUED,
        "current_stage": None,
        "retrievable": False,
        "activation_state": None,
    }
    values.update(overrides)
    with context.session_factory() as session:
        document = Document(**values)
        session.add(document)
        session.flush()
        chunk = DocumentChunk(
            id=(key.encode().hex() + "0" * 64)[:64],
            doc_id=document.id,
            chunk_index=0,
            text=f"被学业表引用的切片 {key}",
            locator={"sheet_name": "课程记录", "row_start": 1, "row_end": 1},
            citation={},
            parser_version="test",
            chunker_version="test",
            chunker_fingerprint="c" * 64,
        )
        session.add(chunk)
        session.flush()
        session.add(
            AcademicRecordSet(
                source_type=constants.SOURCE_UPLOAD,
                source_key=document.source_key,
                status=constants.STATUS_READY,
                display_name=f"probe-{key}",
                content_hash=(key.encode().hex() + "0" * 64)[:64],
                source_doc_id=document.id,
                source_chunk_id=chunk.id,
            )
        )
        session.commit()
        chunk_id = chunk.id
    return chunk_id


@pytest.mark.parametrize(
    "overrides",
    [
        {"activation_state": constants.ACTIVATION_CANDIDATE},
        {"activation_state": constants.ACTIVATION_INACTIVE},
        {"status": constants.STATUS_FAILED},
    ],
    ids=["candidate", "inactive", "failed"],
)
def test_referenced_disqualified_upload_is_hidden(
    client: TestClient, context, overrides: dict
) -> None:
    """被学业表引用但已脱离学业导入资格的 upload 文档，仍必须统一 404。"""
    key = "-".join(f"{name}-{value}" for name, value in sorted(overrides.items()))
    chunk_id = _referenced_upload_chunk(context, key=key, **overrides)

    unknown = _assert_not_found(client.get(f"/api/sources/{'0' * 64}"))
    hidden = _assert_not_found(client.get(f"/api/sources/{chunk_id}"))
    assert (hidden["code"], hidden["message"], hidden["details"]) == (
        unknown["code"],
        unknown["message"],
        unknown["details"],
    )
