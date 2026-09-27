"""来源与检索选项 API：可见性、统一错误体与选项聚合。"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import select, update

from app import constants
from app.models import Document, DocumentChunk, utcnow


def _chunk_id_of(context, doc_id: str) -> str:
    with context.session_factory() as session:
        return session.scalar(
            select(DocumentChunk.id).where(DocumentChunk.doc_id == doc_id).order_by(DocumentChunk.chunk_index)
        )


def _first_document(context) -> Document:
    with context.session_factory() as session:
        return session.scalar(select(Document).order_by(Document.source_key))


# --- sources ----------------------------------------------------------------


def test_source_returns_retrievable_chunk(client: TestClient, context, ingest_demo) -> None:
    ingest_demo()
    document = _first_document(context)
    chunk_id = _chunk_id_of(context, document.id)

    response = client.get(f"/api/sources/{chunk_id}")
    assert response.status_code == 200
    payload = response.json()

    assert set(payload) == {
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
    assert payload["chunk_id"] == chunk_id
    assert payload["doc_id"] == document.id
    assert payload["text"]
    assert payload["dataset_version"] == "2026.1"
    serialized = str(payload)
    assert "/app/" not in serialized
    assert "storage_path" not in serialized
    assert "safe_storage_name" not in serialized


def test_source_hides_candidate_inactive_failed_and_deleted(
    client: TestClient, context, ingest_demo
) -> None:
    ingest_demo()
    documents = []
    with context.session_factory() as session:
        documents = list(session.scalars(select(Document).order_by(Document.source_key)))

    variants = (
        {"activation_state": constants.ACTIVATION_CANDIDATE, "retrievable": False},
        {"activation_state": constants.ACTIVATION_INACTIVE, "retrievable": False},
        {"status": constants.STATUS_FAILED, "retrievable": False},
        {"deleted_at": utcnow()},
    )

    bodies = []
    for index, values in enumerate(variants):
        document = documents[index]
        chunk_id = _chunk_id_of(context, document.id)
        with context.session_factory() as session:
            session.execute(update(Document).where(Document.id == document.id).values(**values))
            session.commit()

        response = client.get(f"/api/sources/{chunk_id}")
        assert response.status_code == 404
        body = response.json()
        assert body["code"] == "SOURCE_NOT_FOUND"
        assert body["request_id"]
        assert set(body) == {"code", "message", "details", "request_id"}
        assert "/app/" not in str(body)
        bodies.append((body["code"], body["message"], body["details"]))

    # 不可见的原因不同，但对外响应完全一致 ⇒ 不泄漏存在与否
    assert len(set(map(str, bodies))) == 1


def test_unknown_and_malformed_chunk_ids_share_the_same_error(client: TestClient) -> None:
    bodies = []
    for chunk_id in ("0" * 64, "not-a-chunk-id", "GHIJKL"):
        response = client.get(f"/api/sources/{chunk_id}")
        assert response.status_code == 404
        body = response.json()
        assert body["code"] == "SOURCE_NOT_FOUND"
        assert body["request_id"]
        assert set(body) == {"code", "message", "details", "request_id"}
        bodies.append((body["code"], body["message"], body["details"]))
    # 非法格式与不存在的 ID 对外表现完全一致
    assert len(set(map(str, bodies))) == 1


def test_path_traversal_like_chunk_id_is_not_leaked(client: TestClient) -> None:
    response = client.get("/api/sources/..%2F..%2Fetc%2Fpasswd")
    assert response.status_code == 404
    body = response.json()
    assert "passwd" not in str(body)
    assert "/app/" not in str(body)
    assert "etc/" not in str(body)


def test_source_404_matches_invisible_chunk_exactly(client: TestClient, context, ingest_demo) -> None:
    ingest_demo()
    document = _first_document(context)
    chunk_id = _chunk_id_of(context, document.id)

    unknown = client.get(f"/api/sources/{'0' * 64}").json()
    with context.session_factory() as session:
        session.execute(
            update(Document)
            .where(Document.id == document.id)
            .values(retrievable=False, activation_state=constants.ACTIVATION_INACTIVE)
        )
        session.commit()
    hidden = client.get(f"/api/sources/{chunk_id}").json()

    assert (unknown["code"], unknown["message"], unknown["details"]) == (
        hidden["code"],
        hidden["message"],
        hidden["details"],
    )


# --- retrieval options ------------------------------------------------------


def test_retrieval_options_are_aggregated_and_sorted(client: TestClient, ingest_demo) -> None:
    ingest_demo()
    payload = client.get("/api/retrieval/options").json()

    assert payload["majors"] == sorted(set(payload["majors"]))
    assert payload["semesters"] == sorted(set(payload["semesters"]))
    assert payload["grade_years"] == sorted(set(payload["grade_years"]))
    values = [item["value"] for item in payload["doc_categories"]]
    assert values == sorted(set(values))

    assert "计算机科学与技术" in payload["majors"]
    assert "academic_policy" in values
    labels = {item["value"]: item["label"] for item in payload["doc_categories"]}
    assert labels["academic_policy"] == "学籍与教学管理规定"
    assert labels["degree_plan"] == "培养方案"

    assert all(item["label"] for item in payload["doc_categories"])
    assert None not in payload["majors"]
    assert payload["active_dataset_version"] == "2026.1"
    assert payload["demo_available"] is True


def test_retrieval_options_empty_when_nothing_retrievable(client: TestClient) -> None:
    payload = client.get("/api/retrieval/options").json()
    assert payload["majors"] == []
    assert payload["grade_years"] == []
    assert payload["semesters"] == []
    assert payload["doc_categories"] == []
    assert payload["active_dataset_version"] is None
    assert payload["demo_available"] is False


def test_retrieval_options_exclude_non_retrievable_documents(
    client: TestClient, context, ingest_demo
) -> None:
    ingest_demo()
    before = client.get("/api/retrieval/options").json()
    assert "academic_policy" in [item["value"] for item in before["doc_categories"]]

    with context.session_factory() as session:
        session.execute(
            update(Document)
            .where(Document.doc_category == "academic_policy")
            .values(retrievable=False)
        )
        session.commit()

    after = client.get("/api/retrieval/options").json()
    assert "academic_policy" not in [item["value"] for item in after["doc_categories"]]


def test_no_public_debug_search_endpoint(client: TestClient) -> None:
    """本阶段不新增规范外的公共调试搜索接口。"""
    for path in ("/api/search", "/api/retrieval/search", "/api/hybrid/search"):
        assert client.get(path).status_code == 404
        assert client.post(path, json={"query": "x"}).status_code == 404
