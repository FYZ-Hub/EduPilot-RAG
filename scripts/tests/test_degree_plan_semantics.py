"""培养方案版本语义回归：正文必须按各版本的 required_course_codes 渲染。"""

from __future__ import annotations

from demo_corpus import facts
from demo_corpus.validate import read_pdf_pages, validate_degree_plan_semantics


def _plan_text(root, manifest, version):
    for doc in manifest["documents"]:
        if doc["doc_category"] == "degree_plan" and doc["version"] == version:
            return "\n".join(read_pdf_pages(root / doc["path"]))
    raise AssertionError(f"缺少培养方案版本 {version}")


def _plan_doc(manifest, version):
    for doc in manifest["documents"]:
        if doc["doc_category"] == "degree_plan" and doc["version"] == version:
            return doc
    raise AssertionError(f"缺少培养方案版本 {version}")


def test_degree_plan_semantics_validator_passes(dataset):
    assert validate_degree_plan_semantics(dataset["root"], dataset["manifest"]) == []


def test_2025_plan_does_not_include_newer_required_course(dataset):
    older = facts.DEGREE_PLAN_2025
    text = _plan_text(dataset["root"], dataset["manifest"], older["version"])
    added = facts.plan_added_required_codes(facts.DEGREE_PLAN_2026, older)
    assert added == ["QM-CS303"]
    for code in added:
        assert code not in text
    professional = facts.plan_required_codes(older, "专业必修")
    assert f"专业必修课程共 {len(professional)} 门。" in text
    assert f"公共必修课程共 {len(facts.plan_required_codes(older, '公共必修'))} 门。" in text
    for code in professional:
        assert code in text


def test_2026_plan_includes_newer_required_course(dataset):
    newer = facts.DEGREE_PLAN_2026
    text = _plan_text(dataset["root"], dataset["manifest"], newer["version"])
    for code in facts.plan_added_required_codes(newer, facts.DEGREE_PLAN_2025):
        assert code in text
    professional = facts.plan_required_codes(newer, "专业必修")
    assert f"专业必修课程共 {len(professional)} 门。" in text
    for code in professional:
        assert code in text


def test_both_plans_render_optional_courses_as_non_mandatory(dataset):
    root = dataset["root"]
    optional = {course["course_code"] for course in facts.plan_optional_courses(facts.DEGREE_PLAN_2026)}
    assert optional == {"QM-CS401", "QM-GE101"}
    for version in ("2025.1", "2026.1"):
        text = _plan_text(root, dataset["manifest"], version)
        assert "（二）专业选修、通识选修与实践环节课程" in text
        for code in optional:
            assert code in text


def test_software_engineering_is_not_mandatory_anywhere():
    course = facts.COURSE_BY_CODE["QM-CS401"]
    assert course["category"] == "专业选修"
    assert course["category"] not in facts.MANDATORY_CATEGORIES
    for plan in (facts.DEGREE_PLAN_2025, facts.DEGREE_PLAN_2026):
        required = {code for codes in plan["required_course_codes"].values() for code in codes}
        assert "QM-CS401" not in required


def test_mandatory_categories_match_plan_required_lists():
    plans = (facts.DEGREE_PLAN_2025, facts.DEGREE_PLAN_2026)
    all_required = {
        code for plan in plans for codes in plan["required_course_codes"].values() for code in codes
    }
    for course in facts.COURSES:
        is_mandatory = course["category"] in facts.MANDATORY_CATEGORIES
        assert is_mandatory == (course["course_code"] in all_required), course["course_code"]


def test_version_difference_conflict_is_registered_on_both_plans(dataset):
    manifest = dataset["manifest"]
    plan_2025 = _plan_doc(manifest, "2025.1")
    plan_2026 = _plan_doc(manifest, "2026.1")
    conflicts = {}
    for doc in (plan_2025, plan_2026):
        conflict = next(
            (item for item in doc["intentional_conflicts"] if item["conflict_id"] == "degree_plan_required_courses"),
            None,
        )
        assert conflict is not None, doc["path"]
        conflicts[doc["version"]] = conflict
        assert conflict["field"]
        assert conflict["reason"]
        assert {source["path"] for source in conflict["sources"]} == {plan_2025["path"], plan_2026["path"]}
        for source in conflict["sources"]:
            assert source["locator"]["path"] == source["path"]
            assert source["locator"]["section_title"] == "四、课程设置与先修关系"

    values = {
        source["path"]: source["value"] for source in conflicts["2025.1"]["sources"]
    }
    assert values[plan_2025["path"]] == "7 门 / 58.0 学分"
    assert values[plan_2026["path"]] == "8 门 / 60.0 学分"


def test_version_difference_conflict_is_covered_by_ground_truth(dataset):
    entries = {entry["id"]: entry for entry in dataset["entries"]}
    entry = entries["gt-conflict-003"]
    assert entry["conflict_expected"] is True
    plan_paths = {
        doc["path"] for doc in dataset["manifest"]["documents"] if doc["doc_category"] == "degree_plan"
    }
    assert set(entry["expected_source_paths"]) == plan_paths
    assert {locator["path"] for locator in entry["expected_locators"]} == plan_paths
    facts_text = " ".join(entry["expected_answer_facts"])
    assert "QM-CS303" in facts_text
    assert "7 门" in facts_text and "8 门" in facts_text
