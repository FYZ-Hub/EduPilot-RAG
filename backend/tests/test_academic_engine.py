"""阶段 7A：确定性学分引擎与投影纯函数单元测试。

全部离线、无数据库、无网络、无模型调用。
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.academic import (
    AcademicDataError,
    CourseRecord,
    DegreeRule,
    MissingRequiredCourse,
    PlanningEvidence,
    RuleCourse,
    TimeConflictHint,
    build_rule_set,
    compute_plan,
    planning_result_payload,
    project_course_records,
    record_set_fingerprint,
    rule_set_fingerprint,
)
from app.academic.projection import SourceBlock
from app.academic.types import (
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    STATUS_PASSED,
    WARN_CATEGORY_MISMATCH,
    WARN_COURSE_NOT_IN_RULE,
    WARN_RECORD_CONTRADICTION,
    WARN_REQUIRED_COURSE_UNKNOWN,
    WARN_TIME_CONFLICT,
    WARN_VERSION_CONFLICT,
)

DOC_ID = "doc-rule-1"
CHUNK_ID = "chunk-rule-1"


def _rule_set(
    *,
    rule_version: str = "2026.1",
    required_credits="160.0",
    major: str = "计算机科学与技术",
):
    return build_rule_set(
        rule_set_id=f"ruleset-{rule_version}",
        major=major,
        admission_year=2026,
        rule_version=rule_version,
        effective_from="2026-09-01",
        required_credits=required_credits,
        categories=(
            DegreeRule(
                major=major,
                admission_year=2026,
                rule_version=rule_version,
                category="专业必修",
                minimum_credits=Decimal("60.0"),
                required_course_codes=("QM-CS101", "QM-CS204", "QM-CS301", "QM-CS302", "QM-CS303"),
                effective_from="2026-09-01",
                source_doc_id=DOC_ID,
                source_chunk_id=CHUNK_ID,
            ),
            DegreeRule(
                major=major,
                admission_year=2026,
                rule_version=rule_version,
                category="通识选修",
                minimum_credits=Decimal("15.0"),
                required_course_codes=(),
                effective_from="2026-09-01",
                source_doc_id=DOC_ID,
                source_chunk_id=CHUNK_ID,
            ),
        ),
        courses=(
            RuleCourse("QM-CS101", "程序设计基础", Decimal("4.0"), "专业必修"),
            RuleCourse("QM-CS204", "计算机组成原理", Decimal("3.5"), "专业必修"),
            RuleCourse("QM-CS301", "操作系统", Decimal("3.0"), "专业必修"),
            RuleCourse("QM-CS302", "数据库系统", Decimal("4.0"), "专业必修"),
            RuleCourse("QM-CS303", "计算机网络", Decimal("3.0"), "专业必修"),
            RuleCourse("QM-GE101", "大学写作", Decimal("2.0"), "通识选修"),
        ),
        source_doc_id=DOC_ID,
        source_chunk_id=CHUNK_ID,
    )


def _record(code: str, credits: str, status: str, *, category="专业必修", semester="2026-2027-1"):
    return CourseRecord(
        course_code=code,
        course_name=f"{code} 课程",
        credits=Decimal(credits),
        category=category,
        grade="88" if status == STATUS_PASSED else None,
        status=status,
        semester=semester,
        schedule=None,
    )


def _evidence(chunk_id: str, doc_id: str = DOC_ID, quote: str = "原文") -> PlanningEvidence:
    return PlanningEvidence(
        chunk_id=chunk_id,
        doc_id=doc_id,
        file_name="方案.pdf",
        document_version="2026.1",
        effective_from="2026-09-01",
        page_number=1,
        sheet_name=None,
        row_start=None,
        row_end=None,
        section_title="三、学分要求",
        quote=quote,
    )


# --- 计分规则 ---------------------------------------------------------------


def test_passed_and_in_progress_are_counted_separately() -> None:
    plan = compute_plan(
        [_record("QM-CS101", "4.0", STATUS_PASSED), _record("QM-CS204", "3.5", STATUS_IN_PROGRESS)],
        _rule_set(),
    )
    assert plan.completed_credits == Decimal("4.0")
    assert plan.in_progress_credits == Decimal("3.5")
    assert plan.required_credits == Decimal("160.0")
    assert plan.remaining_credits == Decimal("152.5")


def test_failed_records_earn_no_credits() -> None:
    plan = compute_plan([_record("QM-CS301", "3.0", STATUS_FAILED)], _rule_set())
    assert plan.completed_credits == Decimal("0.0")
    assert plan.in_progress_credits == Decimal("0.0")
    assert plan.remaining_credits == Decimal("160.0")


def test_retaking_a_course_counts_credits_only_once() -> None:
    records = [
        _record("QM-CS101", "4.0", STATUS_FAILED, semester="2025-2026-1"),
        _record("QM-CS101", "4.0", STATUS_FAILED, semester="2025-2026-2"),
        _record("QM-CS101", "4.0", STATUS_PASSED, semester="2026-2027-1"),
    ]
    plan = compute_plan(records, _rule_set())
    assert plan.completed_credits == Decimal("4.0")
    assert plan.remaining_credits == Decimal("156.0")


def test_duplicate_passed_records_count_once() -> None:
    records = [_record("QM-CS101", "4.0", STATUS_PASSED) for _ in range(3)]
    plan = compute_plan(records, _rule_set())
    assert plan.completed_credits == Decimal("4.0")


def test_duplicate_in_progress_records_count_once() -> None:
    records = [_record("QM-CS204", "3.5", STATUS_IN_PROGRESS) for _ in range(2)]
    plan = compute_plan(records, _rule_set())
    assert plan.in_progress_credits == Decimal("3.5")


def test_passed_wins_over_in_progress_and_they_are_mutually_exclusive() -> None:
    records = [
        _record("QM-CS101", "4.0", STATUS_IN_PROGRESS),
        _record("QM-CS101", "4.0", STATUS_PASSED),
    ]
    plan = compute_plan(records, _rule_set())
    assert plan.completed_credits == Decimal("4.0")
    assert plan.in_progress_credits == Decimal("0.0")


def test_failed_and_passed_produces_contradiction_warning() -> None:
    records = [
        _record("QM-CS101", "4.0", STATUS_FAILED),
        _record("QM-CS101", "4.0", STATUS_PASSED),
    ]
    plan = compute_plan(records, _rule_set())
    codes = [warning.code for warning in plan.conflict_warnings]
    assert WARN_RECORD_CONTRADICTION in codes
    assert plan.completed_credits == Decimal("4.0")


# --- 类别与规则优先级 -------------------------------------------------------


def test_category_and_credits_come_from_the_selected_rule_set() -> None:
    record = _record("QM-GE101", "9.9", STATUS_PASSED, category="专业必修")
    plan = compute_plan([record], _rule_set())
    gap = next(item for item in plan.category_gaps if item.category == "通识选修")
    assert gap.completed_credits == Decimal("2.0"), "学分必须取所选规则目录"
    codes = [warning.code for warning in plan.conflict_warnings]
    assert WARN_CATEGORY_MISMATCH in codes


def test_category_mismatch_is_not_double_counted() -> None:
    records = [
        _record("QM-GE101", "2.0", STATUS_PASSED, category="专业必修"),
        _record("QM-GE101", "2.0", STATUS_PASSED, category="通识选修"),
    ]
    plan = compute_plan(records, _rule_set())
    assert plan.completed_credits == Decimal("2.0")
    professional = next(item for item in plan.category_gaps if item.category == "专业必修")
    assert professional.completed_credits == Decimal("0.0")


def test_course_outside_rule_catalog_falls_back_to_record_with_warning() -> None:
    plan = compute_plan([_record("QM-XX999", "2.0", STATUS_PASSED)], _rule_set())
    assert plan.completed_credits == Decimal("2.0")
    codes = [warning.code for warning in plan.conflict_warnings]
    assert WARN_COURSE_NOT_IN_RULE in codes


def test_other_rule_versions_produce_version_conflict_warning() -> None:
    plan = compute_plan(
        [_record("QM-CS101", "4.0", STATUS_PASSED)],
        _rule_set(rule_version="2026.1"),
        other_rule_versions=("2025.1", "2026.1"),
    )
    conflict = next(
        warning for warning in plan.conflict_warnings if warning.code == WARN_VERSION_CONFLICT
    )
    assert "2025.1" in conflict.message
    assert conflict.severity == "warning"
    # 数字仍严格来自用户选择的规则
    assert plan.required_credits == Decimal("160.0")


def test_time_conflict_hint_becomes_warning_with_real_chunk_ids() -> None:
    plan = compute_plan(
        [_record("QM-CS101", "4.0", STATUS_PASSED)],
        _rule_set(),
        time_conflicts=(TimeConflictHint("同一时间段安排了两门课程。", ("chunk-sched-1",)),),
    )
    warning = next(item for item in plan.conflict_warnings if item.code == WARN_TIME_CONFLICT)
    assert warning.evidence_chunk_ids == ("chunk-sched-1",)


# --- missing 与缺口 ---------------------------------------------------------


def test_required_courses_covered_by_passed_or_in_progress_are_not_missing() -> None:
    records = [
        _record("QM-CS101", "4.0", STATUS_PASSED),
        _record("QM-CS204", "3.5", STATUS_IN_PROGRESS),
        _record("QM-CS301", "3.0", STATUS_FAILED),
    ]
    plan = compute_plan(records, _rule_set())
    assert [item.course_code for item in plan.missing_required_courses] == [
        "QM-CS301",
        "QM-CS302",
        "QM-CS303",
    ], "failed 仍列入 missing；passed / in_progress 不列入"


def test_missing_courses_keep_rule_credits_and_category() -> None:
    plan = compute_plan([], _rule_set())
    missing = {item.course_code: item for item in plan.missing_required_courses}
    assert missing["QM-CS302"].credits == Decimal("4.0")
    assert missing["QM-CS302"].category == "专业必修"
    assert missing["QM-CS302"].course_name == "数据库系统"


def test_unknown_required_course_produces_warning_instead_of_guessing() -> None:
    rule = build_rule_set(
        rule_set_id="rs",
        major="计算机科学与技术",
        admission_year=2026,
        rule_version="2026.1",
        effective_from=None,
        required_credits="160.0",
        categories=(
            DegreeRule(
                major="计算机科学与技术",
                admission_year=2026,
                rule_version="2026.1",
                category="专业必修",
                minimum_credits=Decimal("60.0"),
                required_course_codes=("QM-NOPE",),
                effective_from=None,
                source_doc_id=DOC_ID,
                source_chunk_id=CHUNK_ID,
            ),
        ),
        courses=(),
        source_doc_id=DOC_ID,
        source_chunk_id=CHUNK_ID,
    )
    plan = compute_plan([], rule)
    codes = [warning.code for warning in plan.conflict_warnings]
    assert WARN_REQUIRED_COURSE_UNKNOWN in codes
    assert plan.missing_required_courses[0].course_code == "QM-NOPE"


def test_zero_remaining_still_reports_missing_required_courses() -> None:
    # 用不在规则目录中的课程：学分回退到记录自身，因此可以超过毕业总学分
    records = [_record("QM-XX999", "200.0", STATUS_PASSED, category="通识选修")]
    plan = compute_plan(records, _rule_set())
    assert plan.completed_credits == Decimal("200.0")
    assert plan.remaining_credits == Decimal("0.0")
    assert plan.missing_required_courses, "总学分已满也必须列出未满足的必修课"
    assert [item.course_code for item in plan.missing_required_courses] == [
        "QM-CS101",
        "QM-CS204",
        "QM-CS301",
        "QM-CS302",
        "QM-CS303",
    ]


def test_oversubscribed_categories_never_go_negative() -> None:
    rule = _rule_set()
    records = [
        _record("QM-GE101", "2.0", STATUS_PASSED, category="通识选修"),
        _record("QM-CS101", "4.0", STATUS_PASSED),
        _record("QM-CS302", "4.0", STATUS_PASSED),
    ]
    plan = compute_plan(records, rule)
    for gap in plan.category_gaps:
        assert gap.remaining_credits >= Decimal("0.0")
        assert gap.required_credits - gap.completed_credits - gap.in_progress_credits <= gap.remaining_credits
    assert plan.remaining_credits == Decimal("150.0")
    assert plan.remaining_credits >= Decimal("0.0")


def test_category_gap_remaining_is_clamped_at_zero() -> None:
    rule = _rule_set()
    extra = RuleCourse("QM-CS999", "额外专业课", Decimal("100.0"), "专业必修")
    rule = build_rule_set(
        rule_set_id=rule.rule_set_id,
        major=rule.major,
        admission_year=rule.admission_year,
        rule_version=rule.rule_version,
        effective_from=rule.effective_from,
        required_credits=rule.required_credits,
        categories=rule.categories,
        courses=rule.courses + (extra,),
        source_doc_id=DOC_ID,
        source_chunk_id=CHUNK_ID,
    )
    plan = compute_plan([_record("QM-CS999", "100.0", STATUS_PASSED)], rule)
    professional = next(item for item in plan.category_gaps if item.category == "专业必修")
    assert professional.remaining_credits == Decimal("0.0")


# --- Decimal 精度与确定性 ---------------------------------------------------


def test_decimal_accumulation_has_no_binary_drift() -> None:
    courses = tuple(
        RuleCourse(f"QM-T{i:03d}", f"微课{i}", Decimal("0.1"), "通识选修") for i in range(10)
    )
    rule = build_rule_set(
        rule_set_id="rs-float",
        major="计算机科学与技术",
        admission_year=2026,
        rule_version="2026.1",
        effective_from=None,
        required_credits="1.0",
        categories=(
            DegreeRule(
                major="计算机科学与技术",
                admission_year=2026,
                rule_version="2026.1",
                category="通识选修",
                minimum_credits=Decimal("1.0"),
                required_course_codes=(),
                effective_from=None,
                source_doc_id=DOC_ID,
                source_chunk_id=CHUNK_ID,
            ),
        ),
        courses=courses,
        source_doc_id=DOC_ID,
        source_chunk_id=CHUNK_ID,
    )
    records = [
        _record(course.course_code, "0.1", STATUS_PASSED, category="通识选修")
        for course in courses
    ]
    plan = compute_plan(records, rule)
    assert plan.completed_credits == Decimal("1.0"), "0.1×10 必须精确等于 1.0，不得出现浮点漂移"
    assert plan.remaining_credits == Decimal("0.0")


def test_result_payload_is_byte_identical_across_runs() -> None:
    records = [
        _record("QM-CS101", "4.0", STATUS_PASSED),
        _record("QM-CS204", "3.5", STATUS_IN_PROGRESS),
        _record("QM-CS301", "3.0", STATUS_FAILED),
    ]
    evidence = (_evidence("chunk-b"), _evidence("chunk-a"), _evidence("chunk-a"))
    first = planning_result_payload(compute_plan(records, _rule_set(), evidence=evidence))
    second = planning_result_payload(compute_plan(records, _rule_set(), evidence=evidence))
    assert json.dumps(first, ensure_ascii=False) == json.dumps(second, ensure_ascii=False)
    assert first["required_credits"] == 160.0
    assert isinstance(first["completed_credits"], float)


def test_evidence_is_deduplicated_and_stably_ordered() -> None:
    evidence = (
        _evidence("chunk-c", doc_id="doc-2"),
        _evidence("chunk-a", doc_id="doc-1"),
        _evidence("chunk-a", doc_id="doc-1"),
        _evidence("chunk-b", doc_id="doc-1"),
    )
    plan = compute_plan([_record("QM-CS101", "4.0", STATUS_PASSED)], _rule_set(), evidence=evidence)
    assert [item.chunk_id for item in plan.evidence] == ["chunk-a", "chunk-b", "chunk-c"]


def test_evidence_payload_only_contains_whitelisted_fields() -> None:
    plan = compute_plan(
        [_record("QM-CS101", "4.0", STATUS_PASSED)],
        _rule_set(),
        evidence=(_evidence("chunk-a"),),
    )
    payload = planning_result_payload(plan)["evidence"][0]
    assert set(payload) == {
        "chunk_id",
        "doc_id",
        "file_name",
        "document_version",
        "effective_from",
        "page_number",
        "sheet_name",
        "row_start",
        "row_end",
        "section_title",
        "quote",
    }
    rendered = json.dumps(payload)
    for forbidden in ("storage_path", "/app/data", "source_key", "score", "checksum"):
        assert forbidden not in rendered


# --- 非法输入 ---------------------------------------------------------------


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1.0", "abc", None, True])
def test_invalid_credits_are_rejected(bad) -> None:
    with pytest.raises(AcademicDataError):
        compute_plan(
            [
                CourseRecord(
                    course_code="QM-CS101",
                    course_name="程序设计基础",
                    credits=bad,  # type: ignore[arg-type]
                    category="专业必修",
                    grade=None,
                    status=STATUS_PASSED,
                    semester=None,
                    schedule=None,
                )
            ],
            _rule_set(),
        )


def test_unknown_status_is_rejected() -> None:
    with pytest.raises(AcademicDataError):
        compute_plan([_record("QM-CS101", "4.0", "graduated")], _rule_set())


def test_empty_course_code_is_rejected() -> None:
    with pytest.raises(AcademicDataError):
        compute_plan([_record("  ", "4.0", STATUS_PASSED)], _rule_set())


def test_duplicate_rule_categories_are_rejected() -> None:
    rule = _rule_set()
    with pytest.raises(AcademicDataError):
        build_rule_set(
            rule_set_id="rs-dup",
            major="计算机科学与技术",
            admission_year=2026,
            rule_version="2026.1",
            effective_from=None,
            required_credits="160.0",
            categories=rule.categories + (rule.categories[0],),
            courses=rule.courses,
            source_doc_id=DOC_ID,
            source_chunk_id=CHUNK_ID,
        )


# --- 投影纯函数 -------------------------------------------------------------


HEADER = ["课程代码", "课程名称", "学分", "课程类别", "成绩", "状态", "学期"]
ROWS = [
    ["QM-CS101", "程序设计基础", "4.0", "专业必修", "88", "已通过", "2026-2027-1"],
    ["QM-CS204", "计算机组成原理", "3.5", "专业必修", "", "在修", "2026-2027-1"],
    ["QM-CS301", "操作系统", "3.0", "专业必修", "51", "未通过", "2025-2026-2"],
    ["", "", "", "", "", "", ""],
]


def _table_block(block_type: str = "table_row") -> SourceBlock:
    return SourceBlock(
        block_id="block-1",
        doc_id="doc-records",
        block_type=block_type,
        text="课程代码 | 课程名称 | 学分 | 课程类别 | 成绩 | 状态 | 学期",
        chunk_ids=("chunk-r1", "chunk-r2"),
        sheet_name="课程记录",
        row_start=3,
        row_end=6,
    )


def test_records_are_projected_from_table_blocks() -> None:
    records = project_course_records(_table_block(), header=HEADER, rows=ROWS)
    assert [item.course_code for item in records] == ["QM-CS101", "QM-CS204", "QM-CS301"]
    assert [item.status for item in records] == [STATUS_PASSED, STATUS_IN_PROGRESS, STATUS_FAILED]
    assert records[0].credits == Decimal("4.0")
    assert records[0].grade == "88"
    assert records[1].grade is None


def test_projection_refuses_chunk_derived_text() -> None:
    """禁止从带重叠的 chunk 文本生成业务记录（会造成重复计分）。"""
    with pytest.raises(AcademicDataError):
        project_course_records(_table_block("chunk"), header=HEADER, rows=ROWS)


def test_projection_requires_essential_columns() -> None:
    with pytest.raises(AcademicDataError):
        project_course_records(_table_block(), header=["课程代码", "学分"], rows=[["X", "1.0"]])


def test_projection_rejects_invalid_credits_and_unknown_status() -> None:
    bad_credit = [["QM-CS101", "程序设计基础", "-2", "专业必修", "88", "已通过", "2026-2027-1"]]
    with pytest.raises(AcademicDataError):
        project_course_records(_table_block(), header=HEADER, rows=bad_credit)
    bad_status = [["QM-CS101", "程序设计基础", "4", "专业必修", "88", "毕业", "2026-2027-1"]]
    with pytest.raises(AcademicDataError):
        project_course_records(_table_block(), header=HEADER, rows=bad_status)


def test_projections_are_deterministic_and_content_addressed() -> None:
    first = project_course_records(_table_block(), header=HEADER, rows=ROWS)
    second = project_course_records(_table_block(), header=HEADER, rows=ROWS)
    assert first == second
    assert record_set_fingerprint(first, "匿名学生A · 课程记录") == record_set_fingerprint(
        second, "匿名学生A · 课程记录"
    )
    changed = project_course_records(
        _table_block(),
        header=HEADER,
        rows=[ROWS[0], ["QM-CS204", "计算机组成原理", "3.5", "专业必修", "", "在修", "2026-2027-2"]],
    )
    assert record_set_fingerprint(changed, "匿名学生A · 课程记录") != record_set_fingerprint(
        first, "匿名学生A · 课程记录"
    )


def test_rule_set_fingerprint_tracks_version_and_content() -> None:
    assert rule_set_fingerprint(_rule_set(rule_version="2026.1")) != rule_set_fingerprint(
        _rule_set(rule_version="2025.1", required_credits="155.0")
    )
    assert rule_set_fingerprint(_rule_set()) == rule_set_fingerprint(_rule_set())


def test_missing_required_course_shape_matches_spec() -> None:
    plan = compute_plan([], _rule_set())
    item = plan.missing_required_courses[0]
    assert isinstance(item, MissingRequiredCourse)
    assert item.evidence_chunk_ids == ()


# --- BUG-7A-01：PlanningResult 固定字段契约 ---------------------------------

# PRODUCT_SPEC 5.2 明确规定 ``PlanningResult`` 只能包含这 8 个字段
PLANNING_RESULT_FIELDS = (
    "required_credits",
    "completed_credits",
    "in_progress_credits",
    "remaining_credits",
    "missing_required_courses",
    "category_gaps",
    "conflict_warnings",
    "evidence",
)


def test_planning_result_dataclass_has_exactly_the_spec_fields() -> None:
    import dataclasses

    from app.academic.types import PlanningResult as SpecResult

    assert [field.name for field in dataclasses.fields(SpecResult)] == list(
        PLANNING_RESULT_FIELDS
    ), "PlanningResult 不得增删 PRODUCT_SPEC 5.2 规定的字段"


def test_planning_result_payload_has_exactly_the_spec_keys() -> None:
    payload = planning_result_payload(
        compute_plan([_record("QM-CS101", "4.0", STATUS_PASSED)], _rule_set())
    )
    assert list(payload) == list(
        PLANNING_RESULT_FIELDS
    ), "顶层 key 必须与 PRODUCT_SPEC 5.2 完全一致（不允许仅从 payload 隐藏字段）"


# --- BUG-7A-02：passed 与 in_progress 并存必须报冲突 --------------------------


def test_passed_and_in_progress_on_same_course_reports_contradiction() -> None:
    records = [
        _record("QM-CS101", "4.0", STATUS_IN_PROGRESS),
        _record("QM-CS101", "4.0", STATUS_PASSED),
    ]
    plan = compute_plan(records, _rule_set())

    assert plan.completed_credits == Decimal("4.0")
    assert plan.in_progress_credits == Decimal("0.0"), "passed 与 in_progress 互斥"
    codes = [warning.code for warning in plan.conflict_warnings]
    assert codes.count(WARN_RECORD_CONTRADICTION) == 1, "必须报出且只报出一次矛盾 warning"

    swapped = compute_plan(list(reversed(records)), _rule_set())
    assert [warning.code for warning in swapped.conflict_warnings] == codes
    assert planning_result_payload(swapped) == planning_result_payload(plan)


def test_failed_and_in_progress_on_same_course_reports_contradiction() -> None:
    records = [
        _record("QM-CS204", "3.5", STATUS_IN_PROGRESS),
        _record("QM-CS204", "3.5", STATUS_FAILED),
    ]
    plan = compute_plan(records, _rule_set())
    codes = [warning.code for warning in plan.conflict_warnings]
    assert codes.count(WARN_RECORD_CONTRADICTION) == 1
    assert plan.in_progress_credits == Decimal("3.5"), "在修仍然计入"


@pytest.mark.parametrize(
    "statuses",
    [
        (STATUS_FAILED, STATUS_PASSED),
        (STATUS_FAILED, STATUS_IN_PROGRESS),
        (STATUS_PASSED, STATUS_IN_PROGRESS),
    ],
)
def test_every_conflicting_status_pair_reports_exactly_one_warning(statuses) -> None:
    records = [_record("QM-CS101", "4.0", status) for status in statuses]
    plan = compute_plan(records, _rule_set())
    codes = [warning.code for warning in plan.conflict_warnings]
    assert codes.count(WARN_RECORD_CONTRADICTION) == 1, f"{statuses} 必须报出矛盾 warning"


def test_contradiction_warning_is_single_even_with_many_records() -> None:
    records = [
        _record("QM-CS101", "4.0", STATUS_FAILED),
        _record("QM-CS101", "4.0", STATUS_IN_PROGRESS),
        _record("QM-CS101", "4.0", STATUS_PASSED),
        _record("QM-CS302", "4.0", STATUS_FAILED),
        _record("QM-CS302", "4.0", STATUS_IN_PROGRESS),
    ]
    plan = compute_plan(records, _rule_set())
    warnings = [item for item in plan.conflict_warnings if item.code == WARN_RECORD_CONTRADICTION]
    assert len(warnings) == 1, "同一批矛盾只产生一个稳定 warning"
    assert "QM-CS101" in warnings[0].message and "QM-CS302" in warnings[0].message
    assert plan.completed_credits == Decimal("4.0")
    assert plan.in_progress_credits == Decimal("4.0"), "QM-CS302 仅在修，仍计入在修学分"


# --- 隔离性守卫 -------------------------------------------------------------


def test_engine_makes_no_network_or_model_calls(monkeypatch) -> None:
    def _forbidden(*args, **kwargs):  # pragma: no cover - 触发即失败
        raise AssertionError("规划计算不得访问网络")

    monkeypatch.setattr("httpx.Client", _forbidden)
    monkeypatch.setattr("httpx.AsyncClient", _forbidden)

    plan = compute_plan(
        [_record("QM-CS101", "4.0", STATUS_PASSED)], _rule_set(), evidence=(_evidence("chunk-a"),)
    )
    assert plan.completed_credits == Decimal("4.0")


def test_core_modules_have_no_io_or_model_dependencies() -> None:
    """核心计算与类型模块不得依赖数据库、网络、LLM、Embedding、Reranker 或演示事实源。"""
    import pathlib

    from app.academic import engine as engine_module
    from app.academic import types as types_module

    source = pathlib.Path(engine_module.__file__).read_text(encoding="utf-8")
    source += pathlib.Path(types_module.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "httpx",
        "requests",
        "sqlalchemy",
        "chromadb",
        "app.llm",
        "app.embedding",
        "app.rerank",
        "ground_truth",
        "facts.py",
        "manifest",
        "open(",
    ):
        assert forbidden not in source, f"核心计算模块不得依赖 {forbidden}"
