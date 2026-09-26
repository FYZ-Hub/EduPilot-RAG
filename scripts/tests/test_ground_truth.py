"""ground truth 验收：条数、ID、引用、定位、类别覆盖与规划期望值来源。"""

from __future__ import annotations

from pathlib import Path

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
    assert len(conflict_ids) == 4
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


def test_every_source_path_has_a_matching_locator(dataset):
    """每个 expected_source_paths 中的路径都必须有 path 相同的 locator；无来源时定位必须为空。"""
    for entry in dataset["entries"]:
        sources = set(entry["expected_source_paths"])
        locator_paths = {locator["path"] for locator in entry["expected_locators"]}
        assert sources == locator_paths, entry["id"]
        if not sources:
            assert entry["expected_locators"] == []
            assert entry["should_refuse"] is True


def test_refusals_without_sources_have_no_fake_evidence(dataset):
    empty_entries = [entry for entry in dataset["entries"] if not entry["expected_source_paths"]]
    assert len(empty_entries) >= 4
    for entry in empty_entries:
        assert entry["should_refuse"] is True, entry["id"]
        assert entry["expected_locators"] == []


def _resolve_path(manifest: dict, fragment: str) -> str:
    matches = [doc["path"] for doc in manifest["documents"] if fragment in doc["path"]]
    assert len(matches) == 1, (fragment, matches)
    return matches[0]


def _located_text(dataset, entry: dict, rel: str) -> str:
    """读取该 entry 中 path == rel 的所有 locator 覆盖的真实文件内容。"""
    from demo_corpus.validate import read_docx, read_pdf_pages, read_xlsx

    root = dataset["root"]
    chunks: list[str] = []
    for locator in entry["expected_locators"]:
        if locator["path"] != rel:
            continue
        suffix = Path(rel).suffix.lower()
        if suffix == ".pdf":
            chunks.append(read_pdf_pages(root / rel)[locator["page_number"] - 1])
        elif suffix == ".docx":
            chunks.append(read_docx(root / rel)["text"])
        else:
            cells = read_xlsx(root / rel)["cells"][locator["sheet_name"]]
            rows = cells[locator["row_start"] - 1:locator["row_end"]]
            chunks.append("\n".join(str(cell) for row in rows for cell in row))
    return "\n".join(chunks)


# 跨文档题：每个来源的定位内容必须真正支撑答案中的事实
CROSS_LOCATOR_EXPECTATIONS = {
    "gt-cross-001": [("02-培养方案", "QM-CS201"), ("03-课程大纲-QM-CS201", "QM-CS201"), ("11-课表", "数据结构")],
    "gt-cross-002": [("04-课程大纲-QM-CS301", "QM-CS201"), ("12-课表", "操作系统")],
    "gt-cross-003": [("13-课程记录", "QM-CS101"), ("02-培养方案", "专业必修")],
    "gt-cross-004": [("08-校历", "第一学期期末考试周"), ("09-考试通知", "2027-01-05")],
    "gt-cross-005": [("07-制度", "重修"), ("10-考试通知", "QM-CS105"), ("14-课程记录", "QM-CS105")],
    "gt-cross-006": [("02-培养方案", "QM-CS303"), ("01-培养方案", "专业必修课程共 7 门")],
    "gt-cross-007": [("14-课程记录", "QM-CS105"), ("02-培养方案", "公共必修")],
    "gt-cross-008": [("11-课表", "QM-GE101"), ("02-培养方案", "QM-GE101")],
}


def test_cross_doc_locators_resolve_to_supporting_content(dataset):
    entries = {entry["id"]: entry for entry in dataset["entries"]}
    for gt_id, expectations in CROSS_LOCATOR_EXPECTATIONS.items():
        entry = entries[gt_id]
        paths = {locator["path"] for locator in entry["expected_locators"]}
        for fragment, token in expectations:
            rel = _resolve_path(dataset["manifest"], fragment)
            assert rel in paths, (gt_id, fragment)
            assert token in _located_text(dataset, entry, rel), (gt_id, fragment, token)


def test_planning_locators_resolve_to_supporting_content(dataset):
    from demo_corpus.validate import read_pdf_pages, read_xlsx

    root = dataset["root"]
    for entry in dataset["entries"]:
        if entry["category"] != "planning":
            continue
        result = entry["expected_planning_result"]
        key = entry["planning_input"]["record_set"]
        expected_codes = {record["course_code"] for record in facts.RECORDS[key]}

        for locator in entry["expected_locators"]:
            rel = locator["path"]
            if rel.endswith(".pdf"):
                page = read_pdf_pages(root / rel)[locator["page_number"] - 1]
                assert "毕业总学分" in page
                assert f"{result['required_credits']:.1f}" in page
            else:
                cells = read_xlsx(root / rel)["cells"][locator["sheet_name"]]
                rows = cells[locator["row_start"] - 1:locator["row_end"]]
                located_codes = {str(cell) for row in rows for cell in row} & expected_codes
                assert located_codes == expected_codes, entry["id"]


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

