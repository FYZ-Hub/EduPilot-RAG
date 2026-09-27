"""阶段 7A：演示/上传资料的学业投影（接口 + 纯函数）。

职责边界（用户明确要求）：

- ``CourseRecord`` / ``DegreeRule`` 的业务字段**只**从正式解析后的 ``DocumentBlock``
  提取；
- ``DocumentChunk`` 只用于建立 ``source_chunk_id`` 与 ``PlanningEvidence`` 定位，
  **禁止**从带重叠的 chunk 文本重复生成课程记录（否则会造成重复计分）；
- XLSX 按 ``sheet_name`` / ``row_start`` / ``row_end`` / 表头 + 完整数据行确定性映射；
- 不读取 ``facts.py`` / ``ground_truth.jsonl`` / manifest 冲突答案。

7A 只定义这里的接口、数据结构与纯函数（用合成 block 即可测试）；
真实演示资料的投影落库在 7B，plan API 与真实证据映射在 7C。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from app.academic.types import (
    COURSE_STATUSES,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    STATUS_PASSED,
    AcademicDataError,
    CourseRecord,
    DegreeRule,
    DegreeRuleSet,
    RuleCourse,
    credit_text,
    to_credit,
)
from app.core.hashing import stable_digest

# 只允许「正式解析后的表格块」参与业务字段提取；chunk 文本一律拒绝
TABLE_BLOCK_TYPES = ("table", "table_row", "sheet")

DEFAULT_COLUMN_ALIASES: dict[str, str] = {
    "课程代码": "course_code",
    "课程编号": "course_code",
    "课程名称": "course_name",
    "学分": "credits",
    "课程类别": "category",
    "类别": "category",
    "成绩": "grade",
    "分数": "grade",
    "状态": "status",
    "修读状态": "status",
    "学期": "semester",
    "上课时间": "schedule",
    "时间": "schedule",
}

DEFAULT_STATUS_ALIASES: dict[str, str] = {
    "通过": STATUS_PASSED,
    "已通过": STATUS_PASSED,
    "已修": STATUS_PASSED,
    "已修读": STATUS_PASSED,
    "及格": STATUS_PASSED,
    "在修": STATUS_IN_PROGRESS,
    "在修读": STATUS_IN_PROGRESS,
    "修读中": STATUS_IN_PROGRESS,
    "进行中": STATUS_IN_PROGRESS,
    "未通过": STATUS_FAILED,
    "不及格": STATUS_FAILED,
    "未及格": STATUS_FAILED,
    "挂科": STATUS_FAILED,
    "失败": STATUS_FAILED,
}


@dataclass(frozen=True)
class SourceBlock:
    """``DocumentBlock`` 的只读投影；不含路径、校验和等内部字段。"""

    block_id: str
    doc_id: str
    block_type: str
    text: str
    # 仅用于建立来源定位，不参与业务字段提取
    chunk_ids: tuple[str, ...] = ()
    sheet_name: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    page_number: int | None = None
    section_title: str | None = None


@dataclass(frozen=True)
class RecordSetProjection:
    """一个课程记录集合的投影产物（7B 负责落库）。"""

    display_name: str
    major: str | None
    admission_year: int | None
    records: tuple[CourseRecord, ...]
    source_doc_id: str
    source_chunk_id: str | None
    content_hash: str


@dataclass(frozen=True)
class RuleSetProjection:
    """一个培养方案规则集合的投影产物（7B 负责落库）。"""

    display_name: str
    rule_set: DegreeRuleSet
    content_hash: str


def projection_fingerprint(parts: Sequence[str]) -> str:
    """内容指纹：同一来源的相同内容必须得到同一指纹（幂等导入的依据）。"""
    return stable_digest(list(parts))


def _require_table_block(block: SourceBlock) -> None:
    if block.block_type not in TABLE_BLOCK_TYPES:
        raise AcademicDataError(
            "course records must be projected from parsed table blocks, not chunk text"
        )


def project_course_records(
    block: SourceBlock,
    *,
    header: Sequence[str],
    rows: Sequence[Sequence[str]],
    column_aliases: Mapping[str, str] | None = None,
    status_aliases: Mapping[str, str] | None = None,
    row_offset: int = 0,
) -> tuple[CourseRecord, ...]:
    """从表格块确定性提取课程记录；空行跳过，非法取值直接报错（不猜测）。"""
    _require_table_block(block)
    aliases = dict(column_aliases or DEFAULT_COLUMN_ALIASES)
    statuses = dict(status_aliases or DEFAULT_STATUS_ALIASES)

    columns: dict[str, int] = {}
    for position, label in enumerate(header):
        field = aliases.get((label or "").strip())
        if field and field not in columns:
            columns[field] = position
    missing = {"course_code", "course_name", "credits", "category", "status"} - set(columns)
    if missing:
        raise AcademicDataError(f"missing required columns: {sorted(missing)}")

    def _cell(row: Sequence[str], field: str) -> str:
        position = columns.get(field)
        if position is None or position >= len(row):
            return ""
        return (row[position] or "").strip()

    records: list[CourseRecord] = []
    for row in rows:
        cells = [str(cell or "").strip() for cell in row]
        if not any(cells):
            continue
        code = _cell(row, "course_code")
        if not code:
            raise AcademicDataError("course_code must not be empty")
        raw_status = _cell(row, "status")
        status = statuses.get(raw_status, raw_status)
        if status not in COURSE_STATUSES:
            raise AcademicDataError(f"unknown course status: {raw_status!r}")
        records.append(
            CourseRecord(
                course_code=code,
                course_name=_cell(row, "course_name") or code,
                credits=to_credit(_cell(row, "credits")),
                category=_cell(row, "category") or "未分类",
                grade=_cell(row, "grade") or None,
                status=status,
                semester=_cell(row, "semester") or None,
                schedule=_cell(row, "schedule") or None,
            )
        )
    if not records:
        raise AcademicDataError("no usable course records found")
    _ = row_offset  # 行号信息由调用方用于来源定位，不参与业务字段
    return tuple(records)


def build_rule_set(
    *,
    rule_set_id: str,
    major: str,
    admission_year: int,
    rule_version: str,
    effective_from: str | None,
    required_credits: object,
    categories: Sequence[DegreeRule],
    courses: Sequence[RuleCourse],
    source_doc_id: str,
    source_chunk_id: str | None = None,
) -> DegreeRuleSet:
    """构造并校验规则集合：类别不重复、课程代码不冲突、学分合法。"""
    if not rule_set_id or not major or not rule_version:
        raise AcademicDataError("rule set identity must not be empty")
    if not isinstance(admission_year, int) or isinstance(admission_year, bool):
        raise AcademicDataError("admission_year must be an integer")

    seen_categories: set[str] = set()
    for declaration in categories:
        if declaration.category in seen_categories:
            raise AcademicDataError(f"duplicate rule category: {declaration.category}")
        seen_categories.add(declaration.category)
        to_credit(declaration.minimum_credits)

    catalog: dict[str, RuleCourse] = {}
    for course in courses:
        to_credit(course.credits)
        existing = catalog.get(course.course_code)
        if existing is not None and (existing.credits, existing.category) != (
            course.credits,
            course.category,
        ):
            raise AcademicDataError(f"conflicting rule course: {course.course_code}")
        catalog.setdefault(course.course_code, course)

    return DegreeRuleSet(
        rule_set_id=rule_set_id,
        major=major,
        admission_year=admission_year,
        rule_version=rule_version,
        effective_from=effective_from,
        required_credits=to_credit(required_credits),
        categories=tuple(categories),
        courses=tuple(sorted(catalog.values(), key=lambda item: item.course_code)),
        source_doc_id=source_doc_id,
        source_chunk_id=source_chunk_id,
    )


def rule_set_fingerprint(rule_set: DegreeRuleSet) -> str:
    """规则集合的内容指纹：用于同来源幂等导入。"""
    parts = [
        rule_set.major,
        str(rule_set.admission_year),
        rule_set.rule_version,
        rule_set.effective_from or "",
        credit_text(rule_set.required_credits),
    ]
    parts += [
        f"{item.category}:{credit_text(item.minimum_credits)}:{','.join(item.required_course_codes)}"
        for item in rule_set.categories
    ]
    parts += [
        f"{item.course_code}:{item.course_name}:{credit_text(item.credits)}:{item.category}"
        for item in rule_set.courses
    ]
    return projection_fingerprint(parts)


def record_set_fingerprint(records: Sequence[CourseRecord], display_name: str) -> str:
    """记录集合的内容指纹（不包含任何身份字段）。"""
    parts = [display_name]
    parts += [
        f"{item.course_code}:{item.status}:{credit_text(item.credits)}:{item.category}:{item.semester or ''}"
        for item in records
    ]
    return projection_fingerprint(parts)


__all__ = [
    "DEFAULT_COLUMN_ALIASES",
    "DEFAULT_STATUS_ALIASES",
    "TABLE_BLOCK_TYPES",
    "RecordSetProjection",
    "RuleSetProjection",
    "SourceBlock",
    "build_rule_set",
    "project_course_records",
    "projection_fingerprint",
    "record_set_fingerprint",
    "rule_set_fingerprint",
]
