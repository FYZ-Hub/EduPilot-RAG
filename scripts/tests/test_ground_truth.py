"""ground truth 验收：条数、ID、引用、定位、类别覆盖与规划期望值来源。"""

from __future__ import annotations

import json
import shutil
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
    """每个 expected_source_paths 中的路径都必须有 path 相同的 locator；无来源时定位必须为空。

    required 与 supporting 两层各自独立满足同一不变量。
    """
    for entry in dataset["entries"]:
        sources = set(entry["expected_source_paths"])
        locator_paths = {locator["path"] for locator in entry["expected_locators"]}
        assert sources == locator_paths, entry["id"]
        if not sources:
            assert entry["expected_locators"] == []
            assert entry["should_refuse"] is True

        sup_sources = set(entry["supporting_source_paths"])
        sup_locator_paths = {locator["path"] for locator in entry["supporting_locators"]}
        assert sup_sources == sup_locator_paths, entry["id"]


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
    for locator in entry["expected_locators"] + entry["supporting_locators"]:
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
        paths = {
            locator["path"]
            for locator in entry["expected_locators"] + entry["supporting_locators"]
        }
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


# ---------------------------------------------------------------------------
# required / supporting 分层契约
# ---------------------------------------------------------------------------

SUPPORTING_FIELDS = (
    "supporting_answer_facts",
    "supporting_source_paths",
    "supporting_locators",
)

REQUIRED_FIELDS_HEAD = [
    "id",
    "category",
    "question",
    "expected_answer_facts",
    "expected_source_paths",
    "expected_locators",
]


def test_every_entry_declares_supporting_fields_in_fixed_position(dataset):
    """所有记录都必须显式包含三个 supporting 字段，且键序固定。"""
    for entry in dataset["entries"]:
        keys = list(entry.keys())
        assert keys == GROUND_TRUTH_FIELDS, entry["id"]
        assert keys[:6] == REQUIRED_FIELDS_HEAD, entry["id"]
        assert keys[7:10] == list(SUPPORTING_FIELDS), entry["id"]
        for field in SUPPORTING_FIELDS:
            assert isinstance(entry[field], list), (entry["id"], field)


def test_supporting_paths_come_from_manifest(dataset):
    manifest_paths = {doc["path"] for doc in dataset["manifest"]["documents"]}
    for entry in dataset["entries"]:
        for path in entry["supporting_source_paths"]:
            assert path in manifest_paths, (entry["id"], path)
        for locator in entry["supporting_locators"]:
            assert locator["path"] in manifest_paths, (entry["id"], locator["path"])


def test_required_and_supporting_layers_are_disjoint(dataset):
    for entry in dataset["entries"]:
        assert not (
            set(entry["expected_source_paths"]) & set(entry["supporting_source_paths"])
        ), entry["id"]
        assert not (
            set(entry["expected_answer_facts"]) & set(entry["supporting_answer_facts"])
        ), entry["id"]
        required_locators = {tuple(sorted(loc.items())) for loc in entry["expected_locators"]}
        supporting_locators = {tuple(sorted(loc.items())) for loc in entry["supporting_locators"]}
        assert not (required_locators & supporting_locators), entry["id"]


def test_refusal_injection_planning_have_empty_supporting(dataset):
    for entry in dataset["entries"]:
        if entry["should_refuse"] or entry["category"] in ("prompt_injection", "planning"):
            assert entry["supporting_answer_facts"] == [], entry["id"]
            assert entry["supporting_source_paths"] == [], entry["id"]
            assert entry["supporting_locators"] == [], entry["id"]


# 迁移用例：精确锁定 required / supporting 分层（含本轮 cross-003 方案移 supporting）
MIGRATED_CASES = {
    "gt-single-003": {
        "facts": ["QM-CS201 数据结构：4.0 学分"],
        "supporting_facts": ["课程类别：专业必修"],
        "required_sources": 1,
        "supporting_sources": 0,
        "supporting_locators": 0,
    },
    "gt-conflict-001": {
        "facts": ["2025 版培养方案：155.0 学分", "2026 修订版培养方案：160.0 学分"],
        "supporting_facts": ["两个版本同时存在，须由用户确认适用版本"],
        "required_sources": 2,
        "supporting_sources": 0,
        "supporting_locators": 0,
    },
    "gt-cross-006": {
        "facts": ["2026 修订版新增专业必修：QM-CS303 计算机网络（3.0 学分）"],
        "supporting_facts": ["2025 版专业必修课程共 7 门，不含 QM-CS303"],
        "required_sources": 2,
        "supporting_sources": 0,
        "supporting_locators": 0,
    },
    "gt-cross-005": {
        "facts": [
            "可参加补考；补考仍不及格须重修",
            "补考安排在 2026-08-26 14:00-16:00，地点 QM-A210",
            "同一课程多次重修只认定一次学分",
        ],
        "supporting_facts": [],
        "required_sources": 2,
        "supporting_sources": 1,
        "supporting_locators": 1,
    },
    "gt-cross-001": {
        "facts": ["学分：4.0", "建议学期：第3学期", "先修课程：QM-CS101 程序设计基础"],
        "supporting_facts": ["课程类别：专业必修"],
        "required_sources": 1,
        "supporting_sources": 2,
        "supporting_locators": 4,
    },
    "gt-cross-003": {
        "facts": [
            "已通过专业必修：QM-CS101 程序设计基础 4.0 学分、QM-CS104 计算机导论 2.0 学分",
            "合计 6.0 学分",
        ],
        "supporting_facts": [],
        "required_sources": 1,
        "supporting_sources": 1,
        "supporting_locators": 1,
    },
}


def test_migrated_cases_lock_required_and_supporting(dataset):
    entries = {entry["id"]: entry for entry in dataset["entries"]}
    for gt_id, expected in MIGRATED_CASES.items():
        entry = entries[gt_id]
        assert entry["expected_answer_facts"] == expected["facts"], gt_id
        assert entry["supporting_answer_facts"] == expected["supporting_facts"], gt_id
        assert len(entry["expected_source_paths"]) == expected["required_sources"], gt_id
        assert len(entry["supporting_source_paths"]) == expected["supporting_sources"], gt_id
        assert len(entry["supporting_locators"]) == expected["supporting_locators"], gt_id


def test_unmigrated_entries_have_empty_supporting(dataset):
    migrated = set(MIGRATED_CASES)
    for entry in dataset["entries"]:
        if entry["id"] in migrated:
            continue
        assert entry["supporting_answer_facts"] == [], entry["id"]
        assert entry["supporting_source_paths"] == [], entry["id"]
        assert entry["supporting_locators"] == [], entry["id"]


def _validate_mutated(tmp_path, dataset, mutate) -> list[str]:
    """复制数据集、对 ground truth 施加一处变异，再跑校验。"""
    root = tmp_path / "mutated"
    shutil.copytree(dataset["root"], root)
    path = root / "ground_truth.jsonl"
    entries = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    mutate(entries)
    path.write_text(
        "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries),
        encoding="utf-8",
        newline="\n",
    )
    _, problems = validate_ground_truth(root, dataset["manifest"])
    return problems


def test_validator_rejects_illegal_supporting_type(tmp_path, dataset):
    def mutate(entries):
        entries[0]["supporting_answer_facts"] = "不是数组"

    assert any("supporting_answer_facts" in problem for problem in _validate_mutated(tmp_path, dataset, mutate))


def test_validator_rejects_unknown_supporting_path(tmp_path, dataset):
    def mutate(entries):
        entries[0]["supporting_source_paths"] = ["corpus/does-not-exist.pdf"]
        entries[0]["supporting_locators"] = [{"path": "corpus/does-not-exist.pdf", "page_number": 1}]

    problems = _validate_mutated(tmp_path, dataset, mutate)
    assert any("supporting" in problem and "manifest" in problem for problem in problems)


def test_validator_rejects_illegal_supporting_locator(tmp_path, dataset):
    def mutate(entries):
        entry = next(item for item in entries if item["expected_locators"])
        entry["supporting_source_paths"] = [entry["expected_locators"][0]["path"]]
        entry["supporting_locators"] = [
            {**entry["expected_locators"][0], "page_number": 999}
        ]

    problems = _validate_mutated(tmp_path, dataset, mutate)
    assert problems, "非法 supporting locator 必须被拒绝"


def test_validator_rejects_cross_layer_duplicate_fact(tmp_path, dataset):
    def mutate(entries):
        entry = next(item for item in entries if item["expected_answer_facts"])
        entry["supporting_answer_facts"] = list(entry["expected_answer_facts"])

    problems = _validate_mutated(tmp_path, dataset, mutate)
    assert any("不得重复" in problem for problem in problems)


def test_validator_rejects_cross_layer_duplicate_source(tmp_path, dataset):
    def mutate(entries):
        entry = next(item for item in entries if item["expected_source_paths"])
        entry["supporting_source_paths"] = list(entry["expected_source_paths"])
        entry["supporting_locators"] = list(entry["expected_locators"])

    problems = _validate_mutated(tmp_path, dataset, mutate)
    assert any("不得交叉" in problem for problem in problems)


def test_validator_rejects_missing_supporting_field(tmp_path, dataset):
    def mutate(entries):
        entries[0].pop("supporting_locators")

    problems = _validate_mutated(tmp_path, dataset, mutate)
    assert any("字段集合或顺序" in problem for problem in problems)


# ---------------------------------------------------------------------------
# required_evidence_groups：组间 AND、组内 OR
# ---------------------------------------------------------------------------

GROUP_FIELD = "required_evidence_groups"

# 迁移用例：按来源 basename 精确锁定证据组（组序、组数、组内备选数）
REQUIRED_GROUP_BASENAMES = {
    # 2026方案 / 2025方案 / CS302大纲 三选一（单组 OR）
    "gt-code-001": [
        [
            "01-培养方案-计算机科学与技术-2025版.pdf",
            "02-培养方案-计算机科学与技术-2026修订版.pdf",
            "05-课程大纲-QM-CS302-数据库系统.docx",
        ]
    ],
    # required 仅课程记录；2026 方案移 supporting
    "gt-cross-003": [["13-课程记录-匿名学生A.xlsx"]],
    # 新增“第三章 重修规则”必需 locator（单元素组）
    "gt-cross-005": [
        ["07-制度-补考重修与学分认定办法.docx"],
        ["07-制度-补考重修与学分认定办法.docx"],
        ["10-考试通知-2025-2026-2补考安排.pdf"],
    ],
    # 课表必需 AND（2026方案 OR recA row12）
    "gt-cross-008": [
        ["11-课表-计算机科学与技术-2026-2027-1.xlsx"],
        [
            "02-培养方案-计算机科学与技术-2026修订版.pdf",
            "13-课程记录-匿名学生A.xlsx",
        ],
    ],
    # 学生B记录（线性代数=公共必修）AND（2026方案三、学分要求 OR 学生B汇总）
    "gt-cross-007": [
        ["14-课程记录-匿名学生B.xlsx"],
        [
            "02-培养方案-计算机科学与技术-2026修订版.pdf",
            "14-课程记录-匿名学生B.xlsx",
        ],
    ],
    # 2026方案（8 学分）AND（学分认定办法第四章 OR 2025方案三、学分要求）
    "gt-conflict-002": [
        ["02-培养方案-计算机科学与技术-2026修订版.pdf"],
        [
            "01-培养方案-计算机科学与技术-2025版.pdf",
            "07-制度-补考重修与学分认定办法.docx",
        ],
    ],
}


def _group_basenames(entry: dict) -> list[list[str]]:
    return [
        sorted(alternative["path"].rsplit("/", 1)[-1] for alternative in group)
        for group in entry[GROUP_FIELD]
    ]


def test_required_evidence_groups_field_present_and_positioned(dataset):
    """全部记录都显式包含该字段，且键序固定。"""
    assert GROUND_TRUTH_FIELDS.index(GROUP_FIELD) == 6
    for entry in dataset["entries"]:
        assert list(entry.keys()) == GROUND_TRUTH_FIELDS, entry["id"]
        assert isinstance(entry[GROUP_FIELD], list), entry["id"]


def test_required_evidence_groups_derivation_invariants(dataset):
    """组非空且唯一、组内备选唯一、备选并集 == expected_locators、备选来源 == expected_source_paths。"""
    for entry in dataset["entries"]:
        groups = entry[GROUP_FIELD]
        if not entry["expected_source_paths"]:
            assert groups == [], entry["id"]
            continue
        assert groups, entry["id"]
        flat: list[tuple] = []
        identities: set[tuple] = set()
        group_sources: set[str] = set()
        for group in groups:
            assert group, entry["id"]
            keys = [tuple(sorted(item.items())) for item in group]
            assert len(set(keys)) == len(keys), entry["id"]
            flat.extend(keys)
            identities.add(tuple(sorted(keys)))
            group_sources.update(item["path"] for item in group)
        assert len(identities) == len(groups), entry["id"]
        expected_keys = sorted(
            tuple(sorted(item.items())) for item in entry["expected_locators"]
        )
        assert sorted(flat) == expected_keys, entry["id"]
        assert group_sources == set(entry["expected_source_paths"]), entry["id"]


def test_migrated_required_evidence_groups_are_locked(dataset):
    entries = {entry["id"]: entry for entry in dataset["entries"]}
    for gt_id, expected in REQUIRED_GROUP_BASENAMES.items():
        assert _group_basenames(entries[gt_id]) == expected, gt_id


def test_unmigrated_cases_keep_single_element_groups(dataset):
    """普通用例（未迁移）为单元素组：组数等于 locator 数，每组一个备选。"""
    locked = set(REQUIRED_GROUP_BASENAMES)
    for entry in dataset["entries"]:
        if entry["id"] in locked or not entry["expected_locators"]:
            continue
        groups = entry[GROUP_FIELD]
        assert len(groups) == len(entry["expected_locators"]), entry["id"]
        assert all(len(group) == 1 for group in groups), entry["id"]


# 两处等价证据修正的事实文字锁定（8 学分事实与“两处规定冲突”保持不变）
EQUIVALENT_EVIDENCE_FACTS = {
    "gt-conflict-002": [
        "2026 修订版培养方案：单次最多认定 8 学分",
        "另一份文件规定：交流课程单次最多认定 6 学分",
        "两处规定冲突",
    ],
    "gt-cross-007": [
        "QM-CS105 线性代数属于公共必修",
        "公共必修要求 55.0 学分（2026 修订版）",
    ],
}


def test_equivalent_evidence_facts_are_locked(dataset):
    entries = {entry["id"]: entry for entry in dataset["entries"]}
    for gt_id, facts in EQUIVALENT_EVIDENCE_FACTS.items():
        assert entries[gt_id]["expected_answer_facts"] == facts, gt_id


def test_validator_rejects_illegal_required_evidence_groups_type(tmp_path, dataset):
    def mutate(entries):
        entries[0][GROUP_FIELD] = "not-a-list"

    problems = _validate_mutated(tmp_path, dataset, mutate)
    assert any("required_evidence_groups 必须是数组" in problem for problem in problems)


def test_validator_rejects_empty_required_group(tmp_path, dataset):
    def mutate(entries):
        entry = next(item for item in entries if item[GROUP_FIELD])
        entry[GROUP_FIELD][0] = []

    problems = _validate_mutated(tmp_path, dataset, mutate)
    assert any("必须是非空数组" in problem for problem in problems)


def test_validator_rejects_non_object_alternative(tmp_path, dataset):
    def mutate(entries):
        entry = next(item for item in entries if item[GROUP_FIELD])
        entry[GROUP_FIELD][0] = ["not-an-object"]

    problems = _validate_mutated(tmp_path, dataset, mutate)
    assert any("元素必须是 locator 对象" in problem for problem in problems)


def test_validator_rejects_duplicate_alternative_in_group(tmp_path, dataset):
    def mutate(entries):
        entry = next(
            item
            for item in entries
            if len(item[GROUP_FIELD]) == 1 and len(item[GROUP_FIELD][0]) == 1
        )
        entry[GROUP_FIELD][0].append(dict(entry[GROUP_FIELD][0][0]))

    problems = _validate_mutated(tmp_path, dataset, mutate)
    assert any("组内备选重复" in problem for problem in problems)


def test_validator_rejects_duplicate_group(tmp_path, dataset):
    def mutate(entries):
        entry = next(item for item in entries if len(item[GROUP_FIELD]) >= 2)
        entry[GROUP_FIELD].append(
            [dict(alternative) for alternative in entry[GROUP_FIELD][0]]
        )

    problems = _validate_mutated(tmp_path, dataset, mutate)
    assert any("证据组重复" in problem for problem in problems)


def test_validator_rejects_group_union_mismatch(tmp_path, dataset):
    def mutate(entries):
        entry = next(
            item
            for item in entries
            if item[GROUP_FIELD] and len(item[GROUP_FIELD][0]) >= 2
        )
        entry[GROUP_FIELD][0].pop()

    problems = _validate_mutated(tmp_path, dataset, mutate)
    assert any("备选并集与 expected_locators 不一致" in problem for problem in problems)

