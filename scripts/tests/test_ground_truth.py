"""ground truth 验收：条数、ID、引用、定位、类别覆盖与规划期望值来源。"""

from __future__ import annotations

from demo_corpus import facts
from demo_corpus.validate import GROUND_TRUTH_FIELDS, validate_ground_truth


def test_ground_truth_passes_validator(dataset):
    _, problems = validate_ground_truth(dataset["root"], dataset["manifest"])
    assert problems == []


def test_ground_truth_has_at_least_fifty_unique_entries(dataset):
    entries = dataset["entries"]
    assert len(entries) >= 50
    ids = [entry["id"] for entry in entries]
    assert len(ids) == len(set(ids))


def test_field_order_is_stable(dataset):
    for entry in dataset["entries"]:
        assert list(entry.keys()) == GROUND_TRUTH_FIELDS


def test_referenced_paths_come_from_manifest(dataset):
    manifest_paths = {doc["path"] for doc in dataset["manifest"]["documents"]}
    for entry in dataset["entries"]:
        for path in entry["expected_source_paths"]:
            assert path in manifest_paths
        for locator in entry["expected_locators"]:
            assert locator["path"] in manifest_paths


def test_all_categories_are_covered(dataset):
    categories = {entry["category"] for entry in dataset["entries"]}
    assert categories == {
        "single_doc",
        "cross_doc",
        "course_code",
        "exam_date",
        "rule_calculation",
        "version_conflict",
        "unanswerable",
        "prompt_injection",
        "planning",
    }


def test_refusal_conflict_and_injection_samples_exist(dataset):
    entries = dataset["entries"]
    assert sum(1 for entry in entries if entry["should_refuse"]) >= 5
    conflict_ids = {
        conflict["conflict_id"]
        for doc in dataset["manifest"]["documents"]
        for conflict in doc["intentional_conflicts"]
    }
    expected_conflicts = {entry["id"] for entry in entries if entry["conflict_expected"]}
    assert expected_conflicts
    assert len(conflict_ids) == 3
    assert any(entry["category"] == "prompt_injection" for entry in entries)
    assert any(entry["category"] == "unanswerable" for entry in entries)


def test_planning_expectations_come_from_deterministic_rule_function(dataset):
    dummy_evidence = {
        name: {"document_version": "dummy"}
        for name in ("rules", "rules_alt", "records", "schedule", "schedule_conflict")
    }
    planning_entries = [entry for entry in dataset["entries"] if entry["category"] == "planning"]
    assert len(planning_entries) >= 4

    for entry in planning_entries:
        planning_input = entry["planning_input"]
        key = planning_input["record_set"]
        version = planning_input["rule_set"].split("-")[-1]
        plan = facts.PLAN_BY_VERSION[version]
        recomputed = facts.compute_planning_result(facts.RECORDS[key], plan, dummy_evidence)
        stored = entry["expected_planning_result"]

        for field in (
            "rule_version",
            "required_credits",
            "completed_credits",
            "in_progress_credits",
            "remaining_credits",
            "missing_required_courses",
            "category_gaps",
        ):
            assert stored[field] == recomputed[field], (entry["id"], field)
        assert [item["code"] for item in stored["conflict_warnings"]] == [
            item["code"] for item in recomputed["conflict_warnings"]
        ]


def test_planning_entries_reference_plan_and_records(dataset):
    for entry in dataset["entries"]:
        if entry["category"] != "planning":
            continue
        record_set = entry["planning_input"]["record_set"]
        expected_records = (
            "corpus/13-课程记录-匿名学生A.xlsx" if record_set == "student_a" else "corpus/14-课程记录-匿名学生B.xlsx"
        )
        assert expected_records in entry["expected_source_paths"]
        assert any(path.startswith("corpus/0") and path.endswith(".pdf") for path in entry["expected_source_paths"])


# XLSX 定位必须是内容级正确，而不是仅仅落在工作表范围内
XLSX_LOCATOR_EXPECTATIONS = {
    "gt-single-011": "第一学期期末考试周",
    "gt-single-012": "寒假",
    "gt-single-015": "QM-CS102",
    "gt-single-016": "QM-CS105",
    "gt-exam-005": "第二学期开课",
    "gt-exam-006": "国庆假期",
    "gt-cross-008": "QM-GE101",
    "gt-conflict-004": "QM-GE101",
    "gt-refuse-005": "课程代码",
}


def test_xlsx_locators_point_at_matching_content(dataset):
    from demo_corpus.validate import read_xlsx

    root = dataset["root"]
    entries = {entry["id"]: entry for entry in dataset["entries"]}
    cache: dict[str, dict] = {}

    for gt_id, expected in XLSX_LOCATOR_EXPECTATIONS.items():
        entry = entries[gt_id]
        assert entry["expected_locators"], gt_id
        for locator in entry["expected_locators"]:
            if not locator["path"].endswith(".xlsx"):
                continue
            if locator["path"] not in cache:
                cache[locator["path"]] = read_xlsx(root / locator["path"])["cells"]
            rows = cache[locator["path"]][locator["sheet_name"]][locator["row_start"] - 1:locator["row_end"]]
            cells = {str(cell) for row in rows for cell in row}
            assert expected in cells, (gt_id, expected)

