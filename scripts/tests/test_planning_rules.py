"""确定性学业规划规则单元测试（阶段 7 将复用同一规则函数）。"""

from __future__ import annotations

from demo_corpus import facts


def _result(records_key: str, plan: dict, evidence: dict | None = None) -> dict:
    return facts.compute_planning_result(facts.RECORDS[records_key], plan, evidence or {})


def _missing_codes(result: dict) -> list[str]:
    return [item["course_code"] for item in result["missing_required_courses"]]


def test_retake_credits_counted_once():
    result = _result("student_a", facts.DEGREE_PLAN_2026)
    public = next(gap for gap in result["category_gaps"] if gap["category"] == "公共必修")
    # 高数一 5.0 + 英语一 3.0 + 线性代数 3.0 + 大学物理 3.5
    assert public["completed_credits"] == 14.5
    assert _missing_codes(result) == ["QM-CS204", "QM-CS301", "QM-CS302", "QM-CS303"]


def test_failed_course_is_not_counted():
    result = _result("student_b", facts.DEGREE_PLAN_2026)
    public = next(gap for gap in result["category_gaps"] if gap["category"] == "公共必修")
    # 高数一 5.0 + 英语一 3.0；线性代数两次不及格不计学分
    assert public["completed_credits"] == 8.0
    assert "QM-CS105" in _missing_codes(result)


def test_in_progress_is_tracked_separately():
    result_a = _result("student_a", facts.DEGREE_PLAN_2026)
    result_b = _result("student_b", facts.DEGREE_PLAN_2026)
    assert result_a["in_progress_credits"] == 9.0
    assert result_b["in_progress_credits"] == 10.5
    assert result_a["completed_credits"] == 20.5
    assert result_b["completed_credits"] == 14.0


def test_plan_version_changes_requirement_and_missing_courses():
    newer = _result("student_a", facts.DEGREE_PLAN_2026)
    older = _result("student_a", facts.DEGREE_PLAN_2025)
    assert (newer["required_credits"], newer["remaining_credits"]) == (160.0, 130.5)
    assert (older["required_credits"], older["remaining_credits"]) == (155.0, 125.5)
    assert _missing_codes(newer) == ["QM-CS204", "QM-CS301", "QM-CS302", "QM-CS303"]
    assert _missing_codes(older) == ["QM-CS204", "QM-CS301", "QM-CS302"]


def test_remaining_credits_never_negative():
    plan = {
        "version": "test",
        "effective_from": "2026-09-01",
        "total_credits": 1.0,
        "category_minimums": {category: 0.0 for category in facts.CATEGORIES},
        "required_course_codes": {"公共必修": [], "专业必修": []},
    }
    records = [{"course_code": "QM-CS101", "semester": "2025-2026-1", "grade": 90.0, "status": "passed", "record_type": "正考"}]
    result = facts.compute_planning_result(records, plan, {})
    assert result["completed_credits"] == 4.0
    assert result["remaining_credits"] == 0.0
    assert all(gap["remaining_credits"] == 0.0 for gap in result["category_gaps"])


def test_conflict_warnings_require_evidence():
    evidence = {
        "rules": {"document_version": "2026.1"},
        "rules_alt": {"document_version": "2025.1"},
        "records": {"document_version": "2026.1"},
        "schedule": {"document_version": "2026.1"},
        "schedule_conflict": {"document_version": "2026.1"},
    }
    result = _result("student_a", facts.DEGREE_PLAN_2026, evidence)
    assert [warning["code"] for warning in result["conflict_warnings"]] == [
        "DEGREE_PLAN_VERSION_CONFLICT",
        "COURSE_TIME_CONFLICT",
    ]
    assert all(warning["severity"] == "warning" for warning in result["conflict_warnings"])
