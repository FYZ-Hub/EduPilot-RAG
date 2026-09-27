"""阶段 7C：学业导入文档的「真实证据切片」补建与回填测试。

覆盖：确定性补建、幂等、不建检查点、不写向量 / FTS、不改变 retrievable、
既有 NULL ``source_chunk_id`` 数据的回填。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import func, select, text

from app.academic.evidence import EVIDENCE_CHUNKER_VERSION
from app.models import (
    AcademicRecordSet,
    AcademicRuleSet,
    CourseRecordRow,
    DegreeRuleCourse,
    DegreeRuleRow,
    Document,
    DocumentChunk,
    DocumentPipelineState,
)
from tests.conftest import demo_file

RECORDS_ENDPOINT = "/api/academic/records/import"
RULES_ENDPOINT = "/api/academic/rules/import"
NON_RETRIEVAL_SOURCES = (RECORDS_ENDPOINT, RULES_ENDPOINT)
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _post(client, endpoint: str, path: Path, *, mime: str | None = None):
    with open(path, "rb") as handle:
        files = {"file": (path.name, handle, mime or "application/octet-stream")}
        return client.post(endpoint, files=files)


def _import_records(client, path: Path | None = None) -> dict:
    response = _post(client, RECORDS_ENDPOINT, path or demo_file("13-课程记录-匿名学生A"), mime=XLSX_MIME)
    assert response.status_code == 200, response.text
    return response.json()


def _import_rules(client, path: Path | None = None) -> dict:
    response = _post(client, RULES_ENDPOINT, path or demo_file("01-培养方案"), mime="application/pdf")
    assert response.status_code == 200, response.text
    return response.json()


def _source_doc_id(context, set_id: str, model=AcademicRecordSet) -> str:
    with context.session_factory() as session:
        return session.get(model, set_id).source_doc_id


def _chunks(context, doc_id: str) -> list[DocumentChunk]:
    with context.session_factory() as session:
        return list(
            session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.doc_id == doc_id)
                .order_by(DocumentChunk.chunk_index)
            ).all()
        )


def _null_chunk_count(context, model) -> int:
    with context.session_factory() as session:
        return session.scalar(
            select(func.count()).select_from(model).where(model.source_chunk_id.is_(None))
        )


# ---------------------------------------------------------------------------
# 20 / 22 / 23：确定性补建、幂等、不写向量与 FTS
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", NON_RETRIEVAL_SOURCES)
def test_import_creates_deterministic_evidence_chunks(client, context, endpoint: str) -> None:
    payload = (
        _import_records(client)
        if endpoint == RECORDS_ENDPOINT
        else _import_rules(client)
    )
    model = AcademicRecordSet if endpoint == RECORDS_ENDPOINT else AcademicRuleSet
    doc_id = _source_doc_id(context, payload["id"], model)

    chunks = _chunks(context, doc_id)
    assert chunks, "学业导入必须建立真实证据切片"
    assert all(chunk.fts_rowid is None for chunk in chunks)
    assert all(chunk.chunker_version == EVIDENCE_CHUNKER_VERSION for chunk in chunks)
    # 证据切片剖面与 RAG 切片剖面不同 -> 同一文件的两种通道不会碰撞 chunk_id
    assert all("+" in chunk.chunker_version for chunk in chunks)
    assert all(chunk.locator for chunk in chunks)


def test_evidence_chunks_do_not_enter_rag(client, context, worker) -> None:
    payload = _import_records(client)
    doc_id = _source_doc_id(context, payload["id"])

    with context.session_factory() as session:
        state = session.scalar(
            select(DocumentPipelineState).where(DocumentPipelineState.doc_id == doc_id)
        )
        document = session.get(Document, doc_id)
        fts_rows = session.execute(
            text("SELECT COUNT(*) FROM chunk_fts WHERE doc_id = :doc"), {"doc": doc_id}
        ).scalar()

    assert state is None, "证据补建不得创建 DocumentPipelineState"
    assert fts_rows == 0, "证据切片不得写入 FTS"
    assert document.retrievable is False
    assert worker.vectors.vectors_for_document(doc_id) == {}, "证据切片不得写入向量"
    assert worker.run_once() is False, "RAG worker 仍不得认领学业导入文档"


def test_repeated_import_keeps_same_evidence_chunk_ids(client, context) -> None:
    first = _import_records(client)
    doc_id = _source_doc_id(context, first["id"])
    before = [chunk.id for chunk in _chunks(context, doc_id)]

    second = _import_records(client)
    assert second["id"] == first["id"]
    after = [chunk.id for chunk in _chunks(context, doc_id)]
    assert after == before


def test_repeated_backfill_adds_nothing(client, context, worker) -> None:
    from app.academic.evidence import backfill_record_set

    payload = _import_records(client)
    doc_id = _source_doc_id(context, payload["id"])

    with context.session_factory() as session:
        record_set = session.get(AcademicRecordSet, payload["id"])
        assert backfill_record_set(session, context.settings, record_set) == 0
        session.commit()
    assert len(_chunks(context, doc_id)) == len(_chunks(context, doc_id))


# ---------------------------------------------------------------------------
# 24：既有 NULL source_chunk_id 数据的幂等回填
# ---------------------------------------------------------------------------


def test_legacy_null_source_chunk_ids_are_backfilled(client, context) -> None:
    """模拟 7B-2 时代的数据：清空切片与 source_chunk_id 后再规划应被幂等回填。"""
    from app.academic.evidence import backfill_record_set, backfill_rule_set

    records = _import_records(client)
    rules = _import_rules(client)
    records_doc = _source_doc_id(context, records["id"])
    rules_doc = _source_doc_id(context, rules["id"], AcademicRuleSet)

    with context.session_factory() as session:
        # 退化为「只有 DocumentBlock、没有证据切片、source_chunk_id 全为 NULL」
        session.execute(
            text("DELETE FROM document_chunks WHERE doc_id IN (:a, :b)"),
            {"a": records_doc, "b": rules_doc},
        )
        session.execute(
            text("UPDATE course_records SET source_chunk_id = NULL WHERE record_set_id = :id"),
            {"id": records["id"]},
        )
        session.execute(
            text(
                "UPDATE academic_rule_sets SET source_chunk_id = NULL WHERE id = :id"
            ),
            {"id": rules["id"]},
        )
        session.execute(
            text("UPDATE degree_rules SET source_chunk_id = NULL WHERE rule_set_id = :id"),
            {"id": rules["id"]},
        )
        session.execute(
            text("UPDATE degree_rule_courses SET source_chunk_id = NULL WHERE rule_set_id = :id"),
            {"id": rules["id"]},
        )
        session.commit()

    with context.session_factory() as session:
        assert backfill_record_set(
            session, context.settings, session.get(AcademicRecordSet, records["id"])
        ) > 0
        assert backfill_rule_set(
            session, context.settings, session.get(AcademicRuleSet, rules["id"])
        ) > 0
        session.commit()

    assert _null_chunk_count(context, CourseRecordRow) == 0
    assert _null_chunk_count(context, DegreeRuleRow) == 0
    assert _null_chunk_count(context, DegreeRuleCourse) == 0
    with context.session_factory() as session:
        assert session.get(AcademicRuleSet, rules["id"]).source_chunk_id is not None

    # 再次回填不得新增任何行
    with context.session_factory() as session:
        assert backfill_record_set(
            session, context.settings, session.get(AcademicRecordSet, records["id"])
        ) == 0
        assert backfill_rule_set(
            session, context.settings, session.get(AcademicRuleSet, rules["id"])
        ) == 0
        session.commit()


def test_load_evidence_fails_safely_on_broken_sources(client, context) -> None:
    """跨文档 chunk、失效 chunk 与不完整 locator 都必须安全失败，绝不返回伪造引用。"""
    from app.academic.planning import load_evidence
    from app.core.errors import ApiError

    payload = _import_records(client)
    doc_id = _source_doc_id(context, payload["id"])
    chunk = _chunks(context, doc_id)[0]

    with context.session_factory() as session:
        with pytest.raises(ApiError) as mismatch:
            load_evidence(session, {chunk.id: "00000000-0000-4000-8000-000000000000"})
        assert mismatch.value.code == "ACADEMIC_EVIDENCE_UNAVAILABLE"

        with pytest.raises(ApiError) as missing:
            load_evidence(session, {"f" * 64: doc_id})
        assert missing.value.code == "ACADEMIC_EVIDENCE_UNAVAILABLE"

        # 通过 ORM 破坏 locator（同一 session 内 SQLAlchemy 会复用 identity map 里的实例）
        node = session.get(DocumentChunk, chunk.id)
        node.locator = {}
        session.commit()
        with pytest.raises(ApiError) as locator:
            load_evidence(session, {chunk.id: doc_id})
        assert locator.value.code == "ACADEMIC_EVIDENCE_UNAVAILABLE"
        session.rollback()


def test_load_evidence_rejects_non_displayable_locators(client, context) -> None:
    """定位必须能让前端实际展示：XLSX 需要工作表与合法行区间；PDF/DOCX 需要页码或章节标题。

    只含块范围、缺字段、行号非正、行序颠倒或两类定位都没有时，一律安全失败，
    **不得**补默认页码或默认行号。
    """
    from app.academic.planning import load_evidence
    from app.core.errors import ApiError

    records = _import_records(client)
    rules = _import_rules(client)
    xlsx_doc = _source_doc_id(context, records["id"])
    pdf_doc = _source_doc_id(context, rules["id"], AcademicRuleSet)
    xlsx_chunk = _chunks(context, xlsx_doc)[0]
    pdf_chunk = _chunks(context, pdf_doc)[0]

    # 正例：正常的 XLSX / PDF 定位都能读取
    with context.session_factory() as session:
        assert load_evidence(session, {xlsx_chunk.id: xlsx_doc})
        assert load_evidence(session, {pdf_chunk.id: pdf_doc})

    bad_locators = (
        (xlsx_chunk, {"block_start": 0, "block_end": 3}),
        (
            xlsx_chunk,
            {"block_start": 0, "block_end": 3, "sheet_name": "S", "row_start": None, "row_end": 3},
        ),
        (
            xlsx_chunk,
            {"block_start": 0, "block_end": 3, "sheet_name": "S", "row_start": 2, "row_end": None},
        ),
        (
            xlsx_chunk,
            {"block_start": 0, "block_end": 3, "sheet_name": "S", "row_start": 0, "row_end": 3},
        ),
        (
            xlsx_chunk,
            {"block_start": 0, "block_end": 3, "sheet_name": "S", "row_start": 5, "row_end": 3},
        ),
        (
            xlsx_chunk,
            {"block_start": 0, "block_end": 3, "sheet_name": "   ", "row_start": 1, "row_end": 3},
        ),
        (pdf_chunk, {"block_start": 0, "block_end": 3}),
        (
            pdf_chunk,
            {"block_start": 0, "block_end": 3, "page_number": 0, "section_title": "   "},
        ),
    )
    for chunk, locator in bad_locators:
        with context.session_factory() as session:
            node = session.get(DocumentChunk, chunk.id)
            node.locator = dict(locator)
            session.commit()
            with pytest.raises(ApiError) as error:
                load_evidence(session, {chunk.id: chunk.doc_id})
            assert error.value.code == "ACADEMIC_EVIDENCE_UNAVAILABLE", locator
            session.rollback()


def test_evidence_backfill_requires_real_blocks(client, context) -> None:
    """DocumentBlock 缺失时不得伪造证据，必须安全失败。"""
    from app.academic.evidence import backfill_record_set
    from app.core.errors import ApiError

    payload = _import_records(client)
    doc_id = _source_doc_id(context, payload["id"])
    with context.session_factory() as session:
        session.execute(
            text("DELETE FROM document_chunks WHERE doc_id = :doc"), {"doc": doc_id}
        )
        session.execute(
            text("DELETE FROM document_blocks WHERE doc_id = :doc"), {"doc": doc_id}
        )
        session.commit()

    with context.session_factory() as session:
        with pytest.raises(ApiError) as error:
            backfill_record_set(
                session, context.settings, session.get(AcademicRecordSet, payload["id"])
            )
        assert error.value.code == "ACADEMIC_EVIDENCE_UNAVAILABLE"
        session.rollback()
