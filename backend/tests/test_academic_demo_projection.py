"""阶段 7B-1：active demo 学业资料确定性投影落库测试。

全部离线：不访问网络、不调用 LLM / Embedding / Reranker。
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest
from sqlalchemy import func, select, text

from app.academic.service import (
    PROJECTION_READY,
    active_dataset_version,
    load_chunk_locators,
    project_active_demo,
    resolve_source_chunk,
)
from app.academic.types import AcademicDataError, STATUS_IN_PROGRESS, STATUS_PASSED
from app.models import (
    AcademicProjection,
    AcademicRecordSet,
    AcademicRuleSet,
    CourseRecordRow,
    DegreeRuleCourse,
    DegreeRuleRow,
    Document,
    DocumentChunk,
)

EXPECTED_STUDENT_A_PASSED = Decimal("20.5")
EXPECTED_STUDENT_A_IN_PROGRESS = Decimal("9.0")


@pytest.fixture
def projected(ingest_demo, context):
    """导入演示语料（worker 完成时已自动执行 7B-1 投影）。"""
    ingest_demo()
    return context


def _counts(session) -> dict[str, int]:
    return {
        "record_sets": session.scalar(select(func.count(AcademicRecordSet.id))) or 0,
        "course_records": session.scalar(select(func.count(CourseRecordRow.id))) or 0,
        "rule_sets": session.scalar(select(func.count(AcademicRuleSet.id))) or 0,
        "degree_rules": session.scalar(select(func.count(DegreeRuleRow.id))) or 0,
        "rule_courses": session.scalar(select(func.count(DegreeRuleCourse.id))) or 0,
        "projections": session.scalar(select(func.count(AcademicProjection.id))) or 0,
    }


def _records_of(session, display_name: str) -> list[CourseRecordRow]:
    record_set = session.scalars(
        select(AcademicRecordSet).where(AcademicRecordSet.display_name == display_name)
    ).one()
    return list(
        session.scalars(
            select(CourseRecordRow)
            .where(CourseRecordRow.record_set_id == record_set.id)
            .order_by(CourseRecordRow.ordinal)
        ).all()
    )


# --- 1/2/3：投影内容与真实来源 ----------------------------------------------


def test_demo_projection_creates_two_record_sets_and_two_rule_sets(projected) -> None:
    with projected.session_factory() as session:
        counts = _counts(session)
        names = sorted(session.scalars(select(AcademicRecordSet.display_name)).all())
        versions = sorted(session.scalars(select(AcademicRuleSet.rule_version)).all())

    assert counts["record_sets"] == 2
    assert counts["rule_sets"] == 2
    assert counts["projections"] == 4
    assert names == ["匿名学生A · 课程记录", "匿名学生B · 课程记录"]
    assert versions == ["2025.1", "2026.1"], "两个培养方案版本必须同时保留，不得静默选一个"


def test_projected_records_match_document_blocks(projected) -> None:
    with projected.session_factory() as session:
        rows = _records_of(session, "匿名学生A · 课程记录")
        passed = [row for row in rows if row.status == STATUS_PASSED]
        ongoing = [row for row in rows if row.status == STATUS_IN_PROGRESS]

        # 同一课程正考不及格 + 重修通过只算一次
        codes = [row.course_code for row in passed]
        assert codes.count("QM-CS102") == 1
        assert sum(Decimal(row.credits) for row in passed) == EXPECTED_STUDENT_A_PASSED
        assert sum(Decimal(row.credits) for row in ongoing) == EXPECTED_STUDENT_A_IN_PROGRESS
        assert {row.course_code for row in ongoing} == {"QM-CS201", "QM-CS202", "QM-GE101"}
        # 汇总表不得被当作课程记录
        assert all(row.course_code.startswith("QM-") for row in rows)
        # 在修记录无成绩，可选字段
        assert all(row.grade is None for row in ongoing)


def test_projected_rule_set_matches_document_blocks(projected) -> None:
    with projected.session_factory() as session:
        rules = {
            rule.rule_version: rule
            for rule in session.scalars(select(AcademicRuleSet)).all()
        }
        assert Decimal(rules["2025.1"].required_credits) == Decimal("155.0")
        assert Decimal(rules["2026.1"].required_credits) == Decimal("160.0")
        assert rules["2025.1"].major == "计算机科学与技术"
        assert rules["2025.1"].admission_year == 2025
        assert rules["2025.1"].effective_from == "2025-09-01"
        assert rules["2026.1"].effective_from == "2026-09-01"

        declarations = list(
            session.scalars(
                select(DegreeRuleRow)
                .where(DegreeRuleRow.rule_set_id == rules["2025.1"].id)
                .order_by(DegreeRuleRow.ordinal)
            ).all()
        )
        minimums = {item.category: Decimal(item.minimum_credits) for item in declarations}
        assert minimums == {
            "公共必修": Decimal("52.0"),
            "专业必修": Decimal("58.0"),
            "专业选修": Decimal("20.0"),
            "通识选修": Decimal("15.0"),
            "实践环节": Decimal("10.0"),
        }
        required = {
            item.category: list(item.required_course_codes) for item in declarations
        }
        assert required["专业必修"] == [
            "QM-CS101",
            "QM-CS104",
            "QM-CS201",
            "QM-CS202",
            "QM-CS204",
            "QM-CS301",
            "QM-CS302",
        ], "必修课程代码必须来自课程目录且按代码排序"
        assert required["公共必修"] == ["QM-CS102", "QM-CS103", "QM-CS105", "QM-CS203"]
        assert required["专业选修"] == []

        catalog = session.scalars(
            select(DegreeRuleCourse)
            .where(DegreeRuleCourse.rule_set_id == rules["2025.1"].id)
            .order_by(DegreeRuleCourse.ordinal)
        ).all()
        by_code = {item.course_code: item for item in catalog}
        assert len(by_code) >= 11
        assert by_code["QM-CS102"].course_name == "高等数学（一）"
        assert Decimal(by_code["QM-CS102"].credits) == Decimal("5.0")
        assert by_code["QM-CS102"].category == "公共必修"


def test_source_references_are_real_and_same_document(projected) -> None:
    with projected.session_factory() as session:
        doc_ids = set(session.scalars(select(Document.id)).all())
        chunk_owner = dict(
            session.execute(select(DocumentChunk.id, DocumentChunk.doc_id)).all()
        )
        for row in session.scalars(select(CourseRecordRow)).all():
            assert row.source_doc_id in doc_ids
            assert row.source_chunk_id in chunk_owner, "source_chunk_id 必须是真实切片"
            assert chunk_owner[row.source_chunk_id] == row.source_doc_id, "切片必须属于同一文档"
        for row in session.scalars(select(DegreeRuleRow)).all():
            assert row.source_doc_id in doc_ids
            assert row.source_chunk_id in chunk_owner
            assert chunk_owner[row.source_chunk_id] == row.source_doc_id
        for row in session.scalars(select(DegreeRuleCourse)).all():
            assert row.source_chunk_id in chunk_owner
            assert chunk_owner[row.source_chunk_id] == row.source_doc_id


def test_business_fields_never_come_from_chunk_text(projected) -> None:
    """把全部切片正文改成垃圾后重新投影，业务字段必须完全不变。"""
    with projected.session_factory() as session:
        before = [
            (row.course_code, row.credits, row.status) 
            for row in session.scalars(
                select(CourseRecordRow).order_by(CourseRecordRow.course_code, CourseRecordRow.ordinal)
            ).all()
        ]
        rules_before = sorted(
            (item.category, item.minimum_credits, tuple(item.required_course_codes))
            for item in session.scalars(select(DegreeRuleRow)).all()
        )
        session.execute(text("UPDATE document_chunks SET text = 'chunk 正文已被破坏'"))
        session.execute(text("DELETE FROM academic_projections"))
        session.execute(text("DELETE FROM course_records"))
        session.execute(text("DELETE FROM degree_rule_courses"))
        session.execute(text("DELETE FROM degree_rules"))
        session.execute(text("DELETE FROM academic_record_sets"))
        session.execute(text("DELETE FROM academic_rule_sets"))
        session.commit()

    with projected.session_factory() as session:
        report = project_active_demo(session, projected.settings)
        session.commit()
        after = [
            (row.course_code, row.credits, row.status)
            for row in session.scalars(
                select(CourseRecordRow).order_by(CourseRecordRow.course_code, CourseRecordRow.ordinal)
            ).all()
        ]
        rules_after = sorted(
            (item.category, item.minimum_credits, tuple(item.required_course_codes))
            for item in session.scalars(select(DegreeRuleRow)).all()
        )

    assert report.record_sets == 2 and report.rule_sets == 2
    assert after == before, "业务字段只能来自 DocumentBlock，chunk 正文变化不得影响结果"
    assert rules_after == rules_before


# --- 5/6/7：幂等、恢复、指纹 ------------------------------------------------


def test_repeated_projection_adds_no_rows(projected) -> None:
    with projected.session_factory() as session:
        before = _counts(session)
    with projected.session_factory() as session:
        report = project_active_demo(session, projected.settings)
        session.commit()
    with projected.session_factory() as session:
        after = _counts(session)

    assert report.record_sets == 0 and report.rule_sets == 0 and report.skipped == 4
    assert after == before, "重复投影不得新增任何行"


def test_options_survive_engine_restart(tmp_path, projected) -> None:
    from app.db import create_db_engine, create_session_factory

    before = None
    with projected.session_factory() as session:
        before = _counts(session)
    engine = create_db_engine(projected.settings)
    with create_session_factory(engine)() as session:
        assert _counts(session) == before
        assert session.scalar(select(AcademicRuleSet.rule_version)) is not None
    engine.dispose()


def test_fingerprint_change_replaces_stale_projection(projected) -> None:
    """指纹变化时不得继续复用陈旧投影，必须原子替换为新内容。"""
    with projected.session_factory() as session:
        rule_set = session.scalars(
            select(AcademicRuleSet).where(AcademicRuleSet.rule_version == "2025.1")
        ).one()
        stale_id = rule_set.id
        projection = session.scalars(
            select(AcademicProjection).where(AcademicProjection.rule_set_id == stale_id)
        ).one()
        projection.projection_fingerprint = "stale-fingerprint"
        session.commit()

    with projected.session_factory() as session:
        report = project_active_demo(session, projected.settings)
        session.commit()
    with projected.session_factory() as session:
        assert report.rule_sets == 1, "指纹变化必须重建"
        assert session.get(AcademicRuleSet, stale_id) is None, "陈旧规则不得继续保留"
        versions = sorted(session.scalars(select(AcademicRuleSet.rule_version)).all())
        assert versions == ["2025.1", "2026.1"]


def test_failed_projection_writes_nothing(projected, monkeypatch) -> None:
    """四份投影任一失败时整体零写入，不留下部分可选集合。"""
    from app.academic import service as academic_service

    with projected.session_factory() as session:
        session.execute(text("DELETE FROM academic_projections"))
        session.execute(text("DELETE FROM course_records"))
        session.execute(text("DELETE FROM degree_rule_courses"))
        session.execute(text("DELETE FROM degree_rules"))
        session.execute(text("DELETE FROM academic_record_sets"))
        session.execute(text("DELETE FROM academic_rule_sets"))
        session.commit()

    real = academic_service._collect_rule_projection
    calls = {"count": 0}

    def _flaky(session, document):
        calls["count"] += 1
        if calls["count"] == 2:  # 第二份培养方案失败
            raise AcademicDataError("simulated projection failure")
        return real(session, document)

    monkeypatch.setattr(academic_service, "_collect_rule_projection", _flaky)

    with projected.session_factory() as session:
        with pytest.raises(AcademicDataError):
            project_active_demo(session, projected.settings)
        session.rollback()

    with projected.session_factory() as session:
        counts = _counts(session)
    assert counts["record_sets"] == 0, "任一文档失败必须整体回滚"
    assert counts["rule_sets"] == 0
    assert counts["projections"] == 0


# --- 8/9/10：chunk 映射稳定性 -----------------------------------------------


def test_chunk_mapping_is_stable_and_order_independent() -> None:
    locators = [
        ("chunk-b", 1, {"sheet_name": "课程记录", "row_start": 2, "row_end": 6}),
        ("chunk-a", 0, {"sheet_name": "课程记录", "row_start": 2, "row_end": 10}),
        ("chunk-c", 2, {"sheet_name": "汇总", "row_start": 2, "row_end": 9}),
    ]
    first = resolve_source_chunk(
        locators, sheet_name="课程记录", row_start=3, row_end=3
    )
    reversed_same = resolve_source_chunk(
        list(reversed(locators)), sheet_name="课程记录", row_start=3, row_end=3
    )
    assert first == reversed_same == "chunk-b", "范围最窄的候选优先，且与输入顺序无关"


def test_chunk_mapping_prefers_exact_locator_then_index() -> None:
    locators = [
        ("chunk-wide", 5, {"sheet_name": "课程记录", "row_start": 1, "row_end": 20}),
        ("chunk-exact", 9, {"sheet_name": "课程记录", "row_start": 4, "row_end": 4}),
    ]
    assert (
        resolve_source_chunk(locators, sheet_name="课程记录", row_start=4, row_end=4)
        == "chunk-exact"
    )
    tied = [
        ("chunk-z", 3, {"sheet_name": "课程记录", "row_start": 4, "row_end": 4}),
        ("chunk-y", 3, {"sheet_name": "课程记录", "row_start": 4, "row_end": 4}),
    ]
    assert resolve_source_chunk(tied, sheet_name="课程记录", row_start=4, row_end=4) == "chunk-y"


def test_chunk_mapping_never_crosses_documents(projected) -> None:
    """跨文档引用被拒绝：只在同一文档的切片集合内解析。"""
    with projected.session_factory() as session:
        docs = session.scalars(
            select(Document).where(Document.doc_category == "course_records")
        ).all()
        assert len(docs) == 2
        left, right = docs[0], docs[1]
        left_locators = load_chunk_locators(session, left.id)
        right_chunk_ids = {item[0] for item in load_chunk_locators(session, right.id)}
        resolved = resolve_source_chunk(
            left_locators, sheet_name="课程记录", row_start=3, row_end=3
        )
        assert resolved is not None
        assert resolved not in right_chunk_ids, "不得解析到其它文档的切片"


def test_chunk_mapping_returns_none_without_candidate() -> None:
    assert resolve_source_chunk([], sheet_name="课程记录", row_start=1, row_end=1) is None
    assert (
        resolve_source_chunk(
            [("c", 0, {"sheet_name": "其它表", "row_start": 1, "row_end": 9})],
            sheet_name="课程记录",
            row_start=1,
            row_end=1,
        )
        is None
    )


# --- 12/13：active / inactive 隔离与 upload 隔离 -----------------------------


def test_projection_only_uses_the_active_dataset_version(projected) -> None:
    with projected.session_factory() as session:
        version = active_dataset_version(session)
        assert version == projected.settings.demo_dataset_version
        stale = session.scalars(
            select(AcademicRecordSet).where(AcademicRecordSet.dataset_version != version)
        ).all()
        assert stale == [], "只投影当前 active 数据集"


def test_demo_switch_keeps_upload_collections(projected) -> None:
    """upload 来源数据不得因 demo 版本切换而删除或失效。"""
    from app.models import Document as Doc

    with projected.session_factory() as session:
        upload_doc = Doc(
            source_type="upload",
            source_key="upload:manual.xlsx",
            file_name="manual.xlsx",
            file_type="xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            sha256="1" * 64,
            doc_category="course_records",
            status="ready",
            retrievable=True,
        )
        session.add(upload_doc)
        session.flush()
        upload_set = AcademicRecordSet(
            source_type="upload",
            source_key="upload:manual.xlsx",
            status="ready",
            display_name="上传 · 课程记录",
            record_count=0,
            content_hash="upload-hash",
            source_doc_id=upload_doc.id,
        )
        session.add(upload_set)
        session.commit()
        upload_set_id = upload_set.id

    with projected.session_factory() as session:
        report = project_active_demo(session, projected.settings)
        session.commit()
    with projected.session_factory() as session:
        survivor = session.get(AcademicRecordSet, upload_set_id)
        assert survivor is not None and survivor.source_type == "upload"
    assert report.skipped == 4, "upload 数据不参与 demo 投影对账"


# --- 15/16/17：隐私、健康与整体一致性 ---------------------------------------


def test_projection_records_no_paths_or_identity_fields(projected) -> None:
    with projected.session_factory() as session:
        names = session.scalars(select(AcademicRecordSet.display_name)).all()
        rule_names = session.scalars(select(AcademicRuleSet.display_name)).all()
        # source_key 是模型内部的来源所有权字段，允许并必须落库；
        # 但它与显示名都不得出现宿主机绝对路径或 uploads 存储路径
        keys = session.scalars(select(AcademicRecordSet.source_key)).all()
        assert keys, "source_key 必须正常落库"

    for key in keys:
        assert "/app/" not in key and "uploads" not in key
        assert not re.match(r"^[A-Za-z]:[\\/]", key), "不得保存宿主绝对路径"
        assert not key.startswith("/")

    for name in list(names) + list(rule_names):
        assert "/" not in name and "\\" not in name and ":" not in name
        assert "/app" not in name and "F:" not in name
        assert "/app/data" not in name
        assert "学号" not in name and "姓名" not in name

    with projected.session_factory() as session:
        rendered = " ".join(session.scalars(select(AcademicRecordSet.display_name)).all())
        payload = " ".join(session.scalars(select(DegreeRuleCourse.course_name)).all())
    assert "/app" not in rendered and "/app" not in payload
    assert "test.student@" not in rendered


def test_demo_source_key_is_internal_only(projected) -> None:
    """source_key 允许内部落库，但不得出现在面向外部的投影摘要中。"""
    with projected.session_factory() as session:
        report = project_active_demo(session, projected.settings)
        session.rollback()
    assert not hasattr(report, "source_key")
    assert "source_key" not in repr(report)


def test_health_reports_planning_ready(client) -> None:
    payload = client.get("/api/health").json()
    assert payload["capabilities"]["planning"] == "ready"
    assert payload["status"] == "degraded"


def test_projection_is_consistent_after_repeated_seed(projected, worker) -> None:
    """重复 seed（文档全部 skipped）仍必须完成幂等对账且零新增。"""
    with projected.session_factory() as session:
        before = _counts(session)

    from app.demo import service as demo_service

    with projected.session_factory() as session:
        demo_service.seed_job(session, projected.settings)
        session.commit()
    guard = 0
    while worker.run_once() and guard < 40:
        guard += 1

    with projected.session_factory() as session:
        assert _counts(session) == before


# --- BUG-7B-05：demo 学业集合的 active / inactive 状态 ---------------------


def test_current_demo_sets_are_marked_active(projected) -> None:
    with projected.session_factory() as session:
        for model in (AcademicRecordSet, AcademicRuleSet):
            states = set(
                session.scalars(
                    select(model.activation_state).where(model.source_type == "demo")
                ).all()
            )
            assert states == {"active"}, f"{model.__name__} 的当前 demo 集合必须为 active"


def test_switching_demo_version_retires_previous_sets_only(projected) -> None:
    """旧 demo 版本退役为 inactive；upload 集合完全不受影响。"""
    from app.models import Document as Doc

    with projected.session_factory() as session:
        stale_document = Doc(
            source_type="demo",
            source_key="demo:2025.0:corpus/01-培养方案.pdf",
            file_name="01-培养方案.pdf",
            file_type="pdf",
            mime_type="application/pdf",
            sha256="2" * 64,
            doc_category="degree_plan",
            status="ready",
            retrievable=True,
        )
        upload_document = Doc(
            source_type="upload",
            source_key="upload:manual.xlsx",
            file_name="manual.xlsx",
            file_type="xlsx",
            mime_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            sha256="3" * 64,
            doc_category="degree_plan",
            status="ready",
            retrievable=True,
        )
        session.add_all([stale_document, upload_document])
        session.flush()
        stale_set = AcademicRuleSet(
            source_type="demo",
            source_key="demo:2025.0:corpus/01-培养方案.pdf",
            dataset_version="2025.0",
            activation_state="active",
            status="ready",
            display_name="计算机科学与技术培养方案 2025.0",
            major="计算机科学与技术",
            admission_year=2025,
            rule_version="2025.0",
            required_credits="150.0",
            content_hash="stale",
            source_doc_id=stale_document.id,
        )
        upload_set = AcademicRuleSet(
            source_type="upload",
            source_key="upload:manual.xlsx",
            activation_state="active",
            status="ready",
            display_name="上传 · 培养方案",
            major="计算机科学与技术",
            admission_year=2025,
            rule_version="manual",
            required_credits="150.0",
            content_hash="upload",
            source_doc_id=upload_document.id,
        )
        session.add_all([stale_set, upload_set])
        session.commit()
        stale_id, upload_id = stale_set.id, upload_set.id

    with projected.session_factory() as session:
        project_active_demo(session, projected.settings)
        session.commit()

    with projected.session_factory() as session:
        assert session.get(AcademicRuleSet, stale_id).activation_state == "inactive", (
            "旧 demo 版本必须退役"
        )
        assert session.get(AcademicRuleSet, upload_id).activation_state == "active", (
            "upload 集合不得参与 demo 状态切换"
        )
        assert session.get(AcademicRuleSet, stale_id) is not None, "不得删除旧 demo 集合"
        for model in (AcademicRecordSet, AcademicRuleSet):
            current = session.scalars(
                select(model.activation_state).where(
                    model.source_type == "demo",
                    model.dataset_version == projected.settings.demo_dataset_version,
                )
            ).all()
            assert set(current) == {"active"}


def test_activation_state_sync_runs_when_everything_is_skipped(projected) -> None:
    """全部指纹相同被跳过时，状态对账仍必须执行且保持稳定。"""
    with projected.session_factory() as session:
        report = project_active_demo(session, projected.settings)
        session.commit()
    assert report.skipped == 4, "第二次执行应全部跳过"

    with projected.session_factory() as session:
        for model in (AcademicRecordSet, AcademicRuleSet):
            states = set(
                session.scalars(
                    select(model.activation_state).where(model.source_type == "demo")
                ).all()
            )
            assert states == {"active"}


def test_activation_state_is_stable_across_restart(projected) -> None:
    from app.db import create_db_engine, create_session_factory

    with projected.session_factory() as session:
        project_active_demo(session, projected.settings)
        session.commit()

    engine = create_db_engine(projected.settings)
    with create_session_factory(engine)() as session:
        project_active_demo(session, projected.settings)
        session.commit()
    with create_session_factory(engine)() as session:
        for model in (AcademicRecordSet, AcademicRuleSet):
            states = set(
                session.scalars(
                    select(model.activation_state).where(model.source_type == "demo")
                ).all()
            )
            assert states == {"active"}
    engine.dispose()


def test_projection_rows_are_ready(projected) -> None:
    with projected.session_factory() as session:
        assert set(session.scalars(select(AcademicProjection.status)).all()) == {
            PROJECTION_READY
        }
        assert set(session.scalars(select(AcademicRecordSet.status)).all()) == {PROJECTION_READY}
        assert set(session.scalars(select(AcademicRuleSet.status)).all()) == {PROJECTION_READY}
