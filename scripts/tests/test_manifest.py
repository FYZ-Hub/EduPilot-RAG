"""manifest 一致性与路径安全验收。"""

from __future__ import annotations

from pathlib import Path

from demo_corpus.validate import (
    read_docx,
    read_pdf_pages,
    read_xlsx,
    sha256_file,
    validate_dataset,
)


def test_validator_reports_no_problems(dataset):
    report = validate_dataset(dataset["root"])
    assert report["problems"] == []


def test_documents_are_sorted_by_path_and_safe(dataset):
    documents = dataset["manifest"]["documents"]
    paths = [doc["path"] for doc in documents]
    assert paths == sorted(paths)

    for path in paths:
        assert path.startswith("corpus/")
        assert "\\" not in path
        assert ".." not in path
        assert not Path(path).is_absolute()


def test_sha256_matches_files_on_disk(dataset):
    root = dataset["root"]
    for doc in dataset["manifest"]["documents"]:
        assert sha256_file(root / doc["path"]) == doc["sha256"]


def test_expected_pages_or_sheets_structure(dataset):
    root = dataset["root"]
    for doc in dataset["manifest"]["documents"]:
        value = doc["expected_pages_or_sheets"]
        if doc["file_type"] == "pdf":
            assert value["kind"] == "pages"
            assert value["count"] == len(read_pdf_pages(root / doc["path"]))
            assert doc["expected_sections"]
        elif doc["file_type"] == "xlsx":
            assert value["kind"] == "sheets"
            assert value["names"] == sorted(value["names"])
            assert value["names"] == sorted(read_xlsx(root / doc["path"])["names"])
            assert doc["expected_sections"] is None
        else:
            assert value is None
            assert doc["expected_sections"] == read_docx(root / doc["path"])["headings"]


def test_intentional_conflicts_are_recorded(dataset):
    conflicts = {
        conflict["conflict_id"]: conflict
        for doc in dataset["manifest"]["documents"]
        for conflict in doc["intentional_conflicts"]
    }
    assert set(conflicts) == {
        "degree_plan_total_credits",
        "degree_plan_required_courses",
        "course_schedule_overlap_2026_2027_1",
        "credit_recognition_cap",
    }
    for conflict in conflicts.values():
        assert conflict["field"]
        assert conflict["reason"]
        assert len(conflict["sources"]) >= 2
        values = {source["value"] for source in conflict["sources"]}
        assert len(values) >= 2
        for source in conflict["sources"]:
            assert source["path"].startswith("corpus/")
            assert source["locator"]["path"] == source["path"]


def test_version_difference_conflict_covers_both_degree_plans(dataset):
    documents = dataset["manifest"]["documents"]
    carriers = {
        doc["path"]
        for doc in documents
        if any(conflict["conflict_id"] == "degree_plan_required_courses" for conflict in doc["intentional_conflicts"])
    }
    plan_paths = {doc["path"] for doc in documents if doc["doc_category"] == "degree_plan"}
    assert len(plan_paths) == 2
    assert carriers == plan_paths
