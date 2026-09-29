"""评测数据集加载、严格校验与「检索正例」口径。

正例口径（可复现、写死在报告中）：

1. ``should_refuse = true`` 的用例**排除**：它们衡量的是拒答，不是召回（无答案
   与提示注入类都是负例）；
2. ``category = "planning"`` 的用例**排除**：它们由确定性规则引擎给出结果，
   需要 ``planning_input`` 与规则版本，不属于「按问题召回证据」；
3. ``expected_source_paths`` 为空的用例**排除**：没有可计算的正例集合。

其余用例全部纳入，且**不做任何答案/来源改写**。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXCLUDED_CATEGORIES = ("planning",)

REASON_SHOULD_REFUSE = "should_refuse"
REASON_EXCLUDED_CATEGORY = "excluded_category"


class EvaluationDataError(ValueError):
    """评测数据非法：缺字段、类型错误、重复 id 或空数据集。"""


@dataclass(frozen=True)
class GroundTruthCase:
    case_id: str
    category: str
    question: str
    expected_source_paths: tuple[str, ...]
    should_refuse: bool
    conflict_expected: bool


@dataclass(frozen=True)
class ExcludedCase:
    case: GroundTruthCase
    reason: str


@dataclass(frozen=True)
class SelectionResult:
    included: tuple[GroundTruthCase, ...]
    excluded: tuple[ExcludedCase, ...]

    def excluded_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.excluded:
            counts[item.reason] = counts.get(item.reason, 0) + 1
        return dict(sorted(counts.items()))


def _require(payload: dict[str, Any], key: str, case_id: str) -> Any:
    if key not in payload:
        raise EvaluationDataError(f"用例 {case_id} 缺少字段 {key}")
    return payload[key]


def parse_case(payload: Any, *, line_number: int) -> GroundTruthCase:
    """把一行 JSON 解析为用例；任何类型问题都显式报错。"""
    if not isinstance(payload, dict):
        raise EvaluationDataError(f"第 {line_number} 行不是 JSON 对象")
    raw_id = payload.get("id")
    if not isinstance(raw_id, str) or not raw_id.strip():
        raise EvaluationDataError(f"第 {line_number} 行缺少合法的字符串 id")
    case_id = raw_id.strip()

    category = _require(payload, "category", case_id)
    if not isinstance(category, str) or not category.strip():
        raise EvaluationDataError(f"用例 {case_id} 的 category 必须是非空字符串")

    question = _require(payload, "question", case_id)
    if not isinstance(question, str) or not question.strip():
        raise EvaluationDataError(f"用例 {case_id} 的 question 必须是非空字符串")

    paths = _require(payload, "expected_source_paths", case_id)
    if not isinstance(paths, list) or any(
        not isinstance(item, str) or not item.strip() for item in paths
    ):
        raise EvaluationDataError(f"用例 {case_id} 的 expected_source_paths 必须是字符串数组")

    should_refuse = _require(payload, "should_refuse", case_id)
    if not isinstance(should_refuse, bool):
        raise EvaluationDataError(f"用例 {case_id} 的 should_refuse 必须是布尔值")

    conflict_expected = payload.get("conflict_expected", False)
    if not isinstance(conflict_expected, bool):
        raise EvaluationDataError(f"用例 {case_id} 的 conflict_expected 必须是布尔值")

    return GroundTruthCase(
        case_id=case_id,
        category=category.strip(),
        question=question.strip(),
        expected_source_paths=tuple(path.strip() for path in paths),
        should_refuse=should_refuse,
        conflict_expected=conflict_expected,
    )


def load_ground_truth(path: Path) -> tuple[GroundTruthCase, ...]:
    """读取 JSONL；重复 id、空文件与非法行都会显式失败。"""
    if not path.is_file():
        raise EvaluationDataError(f"评测数据集不存在：{path.name}")
    cases: list[GroundTruthCase] = []
    seen: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line_number, raw in enumerate(handle, start=1):
            stripped = raw.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError as error:
                raise EvaluationDataError(
                    f"第 {line_number} 行不是合法 JSON：{error.msg}"
                ) from error
            case = parse_case(payload, line_number=line_number)
            if case.case_id in seen:
                raise EvaluationDataError(f"用例 id 重复：{case.case_id}")
            seen.add(case.case_id)
            cases.append(case)
    if not cases:
        raise EvaluationDataError("评测数据集为空")
    return tuple(cases)


def select_cases(cases: tuple[GroundTruthCase, ...]) -> SelectionResult:
    """按正例口径拆分纳入/排除；排除原因固定且可复现。"""
    included: list[GroundTruthCase] = []
    excluded: list[ExcludedCase] = []
    for case in cases:
        if case.should_refuse:
            excluded.append(ExcludedCase(case=case, reason=REASON_SHOULD_REFUSE))
        elif case.category in EXCLUDED_CATEGORIES:
            excluded.append(ExcludedCase(case=case, reason=REASON_EXCLUDED_CATEGORY))
        elif not case.expected_source_paths:
            excluded.append(ExcludedCase(case=case, reason="no_expected_sources"))
        else:
            included.append(case)
    return SelectionResult(included=tuple(included), excluded=tuple(excluded))


__all__ = [
    "EXCLUDED_CATEGORIES",
    "EvaluationDataError",
    "ExcludedCase",
    "GroundTruthCase",
    "REASON_EXCLUDED_CATEGORY",
    "REASON_SHOULD_REFUSE",
    "SelectionResult",
    "load_ground_truth",
    "parse_case",
    "select_cases",
]
