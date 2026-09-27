"""阶段 7C：``demo/ground_truth.jsonl`` 中全部 planning 条目的验收。

期望值只作为**测试 oracle**；生产代码不读取 ground truth，也不允许用
``compute_plan`` 生成期望值再与 ``compute_plan`` 比较（避免自证循环）。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.academic.types import WARN_RECORD_CONTRADICTION
from tests.conftest import demo_file  # noqa: F401 - 统一测试助手来源

GROUND_TRUTH_PATH = Path("/app/demo/ground_truth.jsonl")
PLAN_ENDPOINT = "/api/academic/plan"

# ground truth 的 record_set 键 -> options 中的显示名
RECORD_SET_NAMES = {
    "student_a": "匿名学生A · 课程记录",
    "student_b": "匿名学生B · 课程记录",
}
# ground truth 的 rule_set 键 -> 真实 rule_version
RULE_VERSIONS = {
    "QM-CS-2025.1": "2025.1",
    "QM-CS-2026.1": "2026.1",
}

# 规格要求（第七节）必须继续由 7A 引擎产生的告警：ground truth 生成早于 BUG-7A-02，
# 因此它没列出「正考不及格 + 重修通过」这一真实矛盾；这里显式允许为**新增**告警。
ALLOWED_EXTRA_WARNINGS = {
    WARN_RECORD_CONTRADICTION: "7A 要求 failed+passed 必须告警（BUG-7A-02）",
}


def _planning_entries() -> list[dict]:
    entries = [
        json.loads(line)
        for line in GROUND_TRUTH_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return [entry for entry in entries if entry.get("planning_input")]


def _plan(client, record_set_id: str, rule_set_id: str) -> dict:
    response = client.post(
        PLAN_ENDPOINT, json={"record_set_id": record_set_id, "rule_set_id": rule_set_id}
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture
def demo_ready(ingest_demo, context, worker):
    ingest_demo()
    return context


@pytest.fixture
def planning_entries() -> list[dict]:
    entries = _planning_entries()
    assert len(entries) >= 6, "ground truth 必须包含 planning 条目"
    return entries


def _ids(client) -> tuple[dict[str, str], dict[str, str]]:
    options = client.get("/api/academic/options").json()
    records = {item["name"]: item["id"] for item in options["record_sets"]}
    rules = {item["rule_version"]: item["id"] for item in options["rule_sets"]}
    return records, rules


def test_ground_truth_planning_entries_are_all_verified(client, demo_ready, planning_entries) -> None:
    records, rules = _ids(client)
    verified = 0
    for entry in planning_entries:
        plan_input = entry["planning_input"]
        expected = entry["expected_planning_result"]
        record_set_id = records[RECORD_SET_NAMES[plan_input["record_set"]]]
        rule_set_id = rules[RULE_VERSIONS[plan_input["rule_set"]]]

        payload = _plan(client, record_set_id, rule_set_id)
        label = f"{entry['id']} ({plan_input['record_set']}/{plan_input['rule_set']})"

        # 1) 固定 8 字段：不得重新加入 oracle 里的 major / rule_version / admission_year
        assert set(payload) == {
            "required_credits",
            "completed_credits",
            "in_progress_credits",
            "remaining_credits",
            "missing_required_courses",
            "category_gaps",
            "conflict_warnings",
            "evidence",
        }, label
        for forbidden in ("major", "rule_version", "admission_year", "record_set_id", "rule_set_id"):
            assert forbidden not in payload, f"{label}: 不得返回 {forbidden}"

        # 2) 数字逐字段相等
        for key in (
            "required_credits",
            "completed_credits",
            "in_progress_credits",
            "remaining_credits",
        ):
            assert payload[key] == expected[key], f"{label}: {key}"

        # 3) 缺失必修逐项相等
        assert [
            (item["course_code"], item["course_name"], item["credits"], item["category"])
            for item in payload["missing_required_courses"]
        ] == [
            (item["course_code"], item["course_name"], item["credits"], item["category"])
            for item in expected["missing_required_courses"]
        ], label

        # 4) 类别缺口逐项相等
        assert payload["category_gaps"] == expected["category_gaps"], label

        # 5) 告警：oracle 期望的 code 必须出现且顺序一致；只允许规格要求的新增告警
        produced = [item["code"] for item in payload["conflict_warnings"]]
        wanted = [item["code"] for item in expected["conflict_warnings"]]
        assert produced[: len(wanted)] == wanted, f"{label}: {produced}"
        extra = set(produced) - set(wanted)
        assert extra <= set(ALLOWED_EXTRA_WARNINGS), f"{label}: 出现未预期告警 {extra}"
        assert all(item["severity"] == "warning" for item in payload["conflict_warnings"])
        assert all(item["message"] for item in payload["conflict_warnings"])

        # 6) 证据：真实、去重、稳定排序，且所有被引用 ID 都在顶层 evidence 中
        evidence = payload["evidence"]
        ids = [item["chunk_id"] for item in evidence]
        assert all(ids), label
        assert len(ids) == len(set(ids)), label
        assert [(item["doc_id"], item["chunk_id"]) for item in evidence] == sorted(
            (item["doc_id"], item["chunk_id"]) for item in evidence
        ), label
        known = set(ids)
        for warning in payload["conflict_warnings"]:
            assert warning["evidence_chunk_ids"], f"{label}: 告警必须有证据"
            assert set(warning["evidence_chunk_ids"]) <= known, f"{label}: {warning['code']}"
        for item in payload["missing_required_courses"]:
            assert set(item["evidence_chunk_ids"]) <= known, f"{label}: {item['course_code']}"

        # 7) 拒绝语义：oracle 明确不应拒答
        assert entry.get("should_refuse") is False, label
        verified += 1

    assert verified == len(planning_entries)


def test_ground_truth_covers_required_scenarios(client, demo_ready, planning_entries) -> None:
    """覆盖：A/B、两个规则版本、已修/在修/剩余、重修只计一次、缺失必修、类别缺口、
    版本冲突、时间冲突、以及「总学分已满但仍缺必修」。"""
    records, rules = _ids(client)
    versions = {entry["planning_input"]["rule_set"] for entry in planning_entries}
    students = {entry["planning_input"]["record_set"] for entry in planning_entries}
    assert versions == {"QM-CS-2025.1", "QM-CS-2026.1"}
    assert students == {"student_a", "student_b"}

    payload = _plan(client, records["匿名学生A · 课程记录"], rules["2026.1"])
    codes = {item["code"] for item in payload["conflict_warnings"]}
    assert "DEGREE_PLAN_VERSION_CONFLICT" in codes
    assert "COURSE_TIME_CONFLICT" in codes
    # 重修只计一次：QM-CS102 在记录中出现两次（正考不及格 + 重修通过）但只计 5.0
    assert payload["completed_credits"] == 20.5
    assert payload["in_progress_credits"] == 9.0
    assert all(gap["remaining_credits"] >= 0 for gap in payload["category_gaps"])
