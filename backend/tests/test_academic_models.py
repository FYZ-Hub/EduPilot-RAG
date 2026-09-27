"""阶段 7A：学业规划持久化模型与 ``init_database`` 增量建表测试。

不引入 Alembic：新表由现有 ``create_all`` 增量创建，既有表与数据保持不变。
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError

from app.db import create_db_engine, create_session_factory, init_database
from app.models import (
    AcademicProjection,
    AcademicRecordSet,
    AcademicRuleSet,
    CourseRecordRow,
    DegreeRuleCourse,
    DegreeRuleRow,
    Document,
)

NEW_TABLES = (
    "academic_record_sets",
    "course_records",
    "academic_rule_sets",
    "degree_rules",
    "degree_rule_courses",
    "academic_projections",
)


def _document(session) -> Document:
    document = Document(
        source_type="demo",
        source_key="demo:2026.1:records.xlsx",
        file_name="records.xlsx",
        file_type="xlsx",
        mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        sha256="0" * 64,
        doc_category="academic_record",
        dataset_version="2026.1",
    )
    session.add(document)
    session.flush()
    return document


def _record_set(session, document: Document, *, content_hash: str = "h1") -> AcademicRecordSet:
    record_set = AcademicRecordSet(
        source_type="demo",
        source_key="demo:2026.1:records.xlsx",
        dataset_version="2026.1",
        status="ready",
        display_name="匿名学生A · 课程记录",
        major="计算机科学与技术",
        admission_year=2026,
        record_count=1,
        content_hash=content_hash,
        source_doc_id=document.id,
    )
    session.add(record_set)
    session.flush()
    return record_set


def _rule_set(session, document: Document, *, rule_version: str = "2026.1") -> AcademicRuleSet:
    rule_set = AcademicRuleSet(
        source_type="demo",
        source_key=f"demo:2026.1:plan-{rule_version}",
        dataset_version="2026.1",
        status="ready",
        display_name=f"计算机科学与技术培养方案（{rule_version}）",
        major="计算机科学与技术",
        admission_year=2026,
        rule_version=rule_version,
        effective_from="2026-09-01",
        required_credits="160.0",
        category_order=["专业必修", "通识选修"],
        course_count=1,
        content_hash=f"rules-{rule_version}",
        source_doc_id=document.id,
    )
    session.add(rule_set)
    session.flush()
    return rule_set


@pytest.fixture
def session_factory(tmp_path):
    engine = create_db_engine(_settings(tmp_path))
    init_database(engine)
    factory = create_session_factory(engine)
    yield factory
    engine.dispose()


def _settings(tmp_path):
    from tests.conftest import build_settings

    return build_settings(tmp_path)


def test_new_tables_are_created_by_init_database(session_factory) -> None:
    engine = session_factory.kw["bind"]
    existing = set(inspect(engine).get_table_names())
    assert set(NEW_TABLES) <= existing


def test_init_database_is_incremental_and_preserves_existing_data(session_factory, tmp_path) -> None:
    engine = session_factory.kw["bind"]
    with session_factory() as session:
        document = _document(session)
        record_set = _record_set(session, document)
        session.commit()
        record_set_id = record_set.id

    # 再次初始化：不得删除或重建既有表与数据
    init_database(engine)
    with session_factory() as session:
        assert session.get(AcademicRecordSet, record_set_id) is not None
        assert session.scalar(select(Document.id)) is not None


def test_record_set_unique_per_source_and_content(session_factory) -> None:
    with session_factory() as session:
        document = _document(session)
        _record_set(session, document, content_hash="same")
        session.commit()
        document_id = document.id

    with session_factory() as session:
        stored_document = session.get(Document, document_id)
        with pytest.raises(IntegrityError):
            _record_set(session, stored_document, content_hash="same")
            session.commit()
        session.rollback()

    with session_factory() as session:
        assert len(session.scalars(select(AcademicRecordSet)).all()) == 1

    # 内容不同的再次导入允许新增（不是无条件去重）
    with session_factory() as session:
        stored_document = session.get(Document, document_id)
        _record_set(session, stored_document, content_hash="different")
        session.commit()
        assert len(session.scalars(select(AcademicRecordSet)).all()) == 2


def test_rule_set_unique_per_source_major_version_and_content(session_factory) -> None:
    with session_factory() as session:
        document = _document(session)
        _rule_set(session, document, rule_version="2026.1")
        session.commit()
        document_id = document.id

    with session_factory() as session:
        stored_document = session.get(Document, document_id)
        with pytest.raises(IntegrityError):
            _rule_set(session, stored_document, rule_version="2026.1")
            session.commit()
        session.rollback()

    # 不同版本可以并存（不得静默选择一个版本）
    with session_factory() as session:
        stored_document = session.get(Document, document_id)
        _rule_set(session, stored_document, rule_version="2025.1")
        session.commit()

    with session_factory() as session:
        versions = sorted(session.scalars(select(AcademicRuleSet.rule_version)).all())
        assert versions == ["2025.1", "2026.1"]


def test_credits_round_trip_as_exact_decimal(session_factory) -> None:
    with session_factory() as session:
        document = _document(session)
        record_set = _record_set(session, document)
        session.add(
            CourseRecordRow(
                record_set_id=record_set.id,
                ordinal=0,
                course_code="QM-CS101",
                course_name="程序设计基础",
                credits="4.5",
                category="专业必修",
                grade="88",
                status="passed",
                semester="2026-2027-1",
                source_doc_id=document.id,
                sheet_name="课程记录",
                row_start=3,
                row_end=3,
            )
        )
        session.commit()

    with session_factory() as session:
        row = session.scalars(select(CourseRecordRow)).one()
        assert row.credits == "4.5"
        assert Decimal(row.credits) == Decimal("4.5")
        assert row.sheet_name == "课程记录"
        assert row.row_start == 3


def test_degree_rules_and_catalog_are_unique_and_cascade(session_factory) -> None:
    with session_factory() as session:
        document = _document(session)
        rule_set = _rule_set(session, document)
        session.add(
            DegreeRuleRow(
                rule_set_id=rule_set.id,
                ordinal=0,
                major=rule_set.major,
                admission_year=2026,
                rule_version=rule_set.rule_version,
                category="专业必修",
                minimum_credits="60.0",
                required_course_codes=["QM-CS101"],
                effective_from="2026-09-01",
                source_doc_id=document.id,
            )
        )
        session.add(
            DegreeRuleCourse(
                rule_set_id=rule_set.id,
                ordinal=0,
                course_code="QM-CS101",
                course_name="程序设计基础",
                credits="4.0",
                category="专业必修",
                source_doc_id=document.id,
            )
        )
        session.commit()
        rule_set_id = rule_set.id

        session.add(
            DegreeRuleRow(
                rule_set_id=rule_set_id,
                ordinal=1,
                major=rule_set.major,
                admission_year=2026,
                rule_version=rule_set.rule_version,
                category="专业必修",
                minimum_credits="61.0",
                required_course_codes=[],
                effective_from=None,
                source_doc_id=document.id,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    with session_factory() as session:
        target = session.get(AcademicRuleSet, rule_set_id)
        assert target is not None
        session.delete(target)
        session.commit()

    with session_factory() as session:
        assert session.scalars(select(DegreeRuleRow.id)).all() == []
        assert session.scalars(select(DegreeRuleCourse.id)).all() == []


def test_source_document_foreign_key_is_enforced(session_factory) -> None:
    with session_factory() as session:
        session.add(
            AcademicRecordSet(
                source_type="demo",
                source_key="demo:2026.1:missing.xlsx",
                status="ready",
                display_name="匿名学生A · 课程记录",
                record_count=0,
                content_hash="orphan",
                source_doc_id="00000000-0000-0000-0000-000000000000",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_academic_projection_is_unique_per_source_and_dataset_version(session_factory) -> None:
    with session_factory() as session:
        document = _document(session)
        record_set = _record_set(session, document)
        session.add(
            AcademicProjection(
                source_type="demo",
                source_key="demo:2026.1",
                dataset_version="2026.1",
                projection_fingerprint="fp",
                record_set_id=record_set.id,
                status="ready",
            )
        )
        session.commit()
        session.add(
            AcademicProjection(
                source_type="demo",
                source_key="demo:2026.1",
                dataset_version="2026.1",
                projection_fingerprint="fp2",
                record_set_id=record_set.id,
                status="ready",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_options_are_recoverable_after_engine_restart(tmp_path) -> None:
    """跨进程重启可恢复：重新建连接后仍能读到已落库的集合。"""
    settings = _settings(tmp_path)
    engine = create_db_engine(settings)
    init_database(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        document = _document(session)
        _record_set(session, document)
        session.commit()
    engine.dispose()

    reopened = create_db_engine(settings)
    init_database(reopened)
    with create_session_factory(reopened)() as session:
        stored = session.scalars(select(AcademicRecordSet)).all()
        assert [item.status for item in stored] == ["ready"]
        assert stored[0].display_name == "匿名学生A · 课程记录"
    reopened.dispose()
