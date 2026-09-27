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

import re
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
from app.core.privacy import scrub

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


# ===========================================================================
# 阶段 7B-1：从**真实 DocumentBlock** 确定性投影演示学业资料
#
# 业务字段（专业、招生年份、规则版本、生效日期、毕业总学分、类别最低学分、
# 必修课程代码、课程目录、成绩记录）**只**来自 DocumentBlock；
# DocumentChunk 仅用于建立 source_chunk_id 与后续证据定位。
# ===========================================================================

ACADEMIC_PROJECTION_VERSION = "academic-projection-v1"

BLOCK_TABLE_HEADER = "table_header"
BLOCK_TABLE_ROW = "table_row"

# 成绩记录行必须同时具备这些标签，否则不是课程记录行
RECORD_REQUIRED_LABELS = ("课程代码", "课程名称", "学分", "课程类别", "状态")
# 培养方案必需字段的标签
RULE_REQUIRED_LABELS = ("专业名称", "招生年份", "文档版本", "生效日期")

_COURSE_CODE_RE = re.compile(r"^[A-Za-z]{2,5}-[A-Za-z]{2,5}\d{2,5}$")
_NUMERIC_RE = re.compile(r"^\d+(?:\.\d+)?$")
_TOTAL_CREDITS_RE = re.compile(r"毕业总学分[：:]\s*(?P<credits>\d+(?:\.\d+)?)\s*学分")
_CATEGORY_MINIMUM_RE = re.compile(
    r"^[·•\-]?\s*(?P<category>[^\s：:；;]{2,12})[：:]\s*(?P<credits>\d+(?:\.\d+)?)\s*学分"
)
_TOTAL_CREDITS_LABEL = "毕业总学分"
_LABEL_SPLIT_RE = re.compile(r"[；;]")
_LABEL_PAIR_RE = re.compile(r"(?P<label>[^\s：:；;]{1,20})[：:]\s*(?P<value>[^\s：:；;]+)")
_UNSAFE_DISPLAY_RE = re.compile(r"[\\/:*?\"<>|\x00-\x1f\x7f]")
_DIGIT_PREFIX_RE = re.compile(r"^\d+$")


@dataclass(frozen=True)
class SourceBlockView:
    """``DocumentBlock`` 的只读视图；不含路径、校验和等内部字段。"""

    block_index: int
    block_type: str
    text: str
    page_number: int | None = None
    sheet_name: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    section_title: str | None = None


@dataclass(frozen=True)
class RecordDraft:
    """一条课程记录草稿 + 其真实来源块定位。"""

    record: CourseRecord
    block_index: int
    sheet_name: str | None
    row_start: int | None
    row_end: int | None


@dataclass(frozen=True)
class RuleSetDraft:
    """一个培养方案规则草稿 + 各字段的真实来源块定位。"""

    rule_set: DegreeRuleSet
    header_block_index: int
    category_blocks: tuple[tuple[str, int], ...] = ()
    course_blocks: tuple[tuple[str, int], ...] = ()


def labelled_pairs(text: str) -> dict[str, str]:
    """解析 ``键: 值；键: 值`` 与 ``键：值 键：值`` 两种真实形态。"""
    pairs: dict[str, str] = {}
    parts = [part for part in _LABEL_SPLIT_RE.split(text or "") if part.strip()]
    if len(parts) >= 2:
        for part in parts:
            for separator in ("：", ":"):
                if separator in part:
                    label, _, value = part.partition(separator)
                    pairs.setdefault(label.strip(), value.strip())
                    break
        if pairs:
            return pairs
    for match in _LABEL_PAIR_RE.finditer(text or ""):
        pairs.setdefault(match.group("label").strip(), match.group("value").strip())
    return pairs


def safe_display_name(candidate: str, max_length: int = 80) -> str:
    """清洗显示名：拒绝路径字符与控制字符，并移除已明确标注的个人信息。"""
    cleaned = _UNSAFE_DISPLAY_RE.sub(" ", (candidate or "").strip())
    cleaned = " ".join(cleaned.split())[:max_length]
    if not cleaned:
        raise AcademicDataError("display name must not be empty")
    return scrub(cleaned)


def record_set_display_name(file_stem: str) -> str:
    """从**安全显示文件名**推导记录集合名称，例如 ``匿名学生A · 课程记录``。"""
    parts = [part for part in re.split(r"[-_]", file_stem or "") if part.strip()]
    if parts and _DIGIT_PREFIX_RE.match(parts[0]):
        parts = parts[1:]
    if len(parts) >= 2:
        return safe_display_name(f"{parts[-1]} · {parts[0]}")
    return safe_display_name(file_stem)


def rule_set_display_name(major: str, rule_version: str) -> str:
    """培养方案显示名只用真实提取到的专业与版本，不含文件名或路径。"""
    return safe_display_name(f"{major}培养方案 {rule_version}")


def project_record_rows(blocks: Sequence[SourceBlockView]) -> tuple[RecordDraft, ...]:
    """从表格块确定性提取课程记录；非课程记录行（如汇总表）自然跳过。"""
    headers = [block for block in blocks if block.block_type == BLOCK_TABLE_HEADER]
    rows = [block for block in blocks if block.block_type == BLOCK_TABLE_ROW]
    if not headers or not rows:
        raise AcademicDataError("record set requires table_header and table_row blocks")

    header_labels: set[str] = set()
    for header in headers:
        for cell in header.text.split("|"):
            header_labels.add(cell.strip())
    missing = [label for label in RECORD_REQUIRED_LABELS if label not in header_labels]
    if missing:
        raise AcademicDataError(f"missing required record columns: {sorted(missing)}")

    drafts: list[RecordDraft] = []
    for block in rows:
        pairs = labelled_pairs(block.text)
        if "课程代码" not in pairs:
            continue  # 汇总表等非课程记录行
        absent = [label for label in RECORD_REQUIRED_LABELS if label not in pairs]
        if absent:
            raise AcademicDataError(f"record row missing labels: {sorted(absent)}")
        raw_status = pairs["状态"]
        status = DEFAULT_STATUS_ALIASES.get(raw_status, raw_status)
        if status not in COURSE_STATUSES:
            raise AcademicDataError(f"unknown course status: {raw_status!r}")
        drafts.append(
            RecordDraft(
                record=CourseRecord(
                    course_code=pairs["课程代码"],
                    course_name=pairs["课程名称"],
                    credits=to_credit(pairs["学分"]),
                    category=pairs["课程类别"],
                    grade=pairs.get("成绩") or None,
                    status=status,
                    semester=pairs.get("学期") or None,
                    schedule=pairs.get("上课时间") or None,
                ),
                block_index=block.block_index,
                sheet_name=block.sheet_name,
                row_start=block.row_start,
                row_end=block.row_end,
            )
        )
    if not drafts:
        raise AcademicDataError("no usable course records found")
    return tuple(drafts)


def project_rule_set_blocks(
    blocks: Sequence[SourceBlockView], *, doc_id: str
) -> RuleSetDraft:
    """从培养方案的 heading / paragraph 块提取规则与课程目录。"""
    labels: dict[str, str] = {}
    label_blocks: dict[str, int] = {}
    for block in blocks:
        for label, value in labelled_pairs(block.text).items():
            if label in RULE_REQUIRED_LABELS and label not in labels and value:
                labels[label] = value
                label_blocks[label] = block.block_index

    missing = [label for label in RULE_REQUIRED_LABELS if label not in labels]
    if missing:
        raise AcademicDataError(f"missing required rule labels: {sorted(missing)}")

    major = labels["专业名称"]
    admission_year = _parse_year(labels["招生年份"])
    rule_version = labels["文档版本"]
    effective_from = labels["生效日期"]

    required_credits = None
    required_credits_block: int | None = None
    minimums: list[tuple[str, str, int]] = []
    catalog: list[tuple[str, str, str, str, int]] = []
    categories_seen: list[str] = []

    for block in blocks:
        if block.block_type not in ("heading", "paragraph"):
            continue
        text = (block.text or "").strip()
        if required_credits is None:
            total = _TOTAL_CREDITS_RE.search(text)
            if total:
                required_credits = total.group("credits")
                required_credits_block = block.block_index
        minimum = _CATEGORY_MINIMUM_RE.match(text)
        if minimum and minimum.group("category") != _TOTAL_CREDITS_LABEL:
            category = minimum.group("category")
            if category not in categories_seen:
                categories_seen.append(category)
                minimums.append((category, minimum.group("credits"), block.block_index))
        entry = _parse_catalog_line(text, categories_seen)
        if entry is not None:
            code, name, credits, category = entry
            catalog.append((code, name, credits, category, block.block_index))

    if required_credits is None or required_credits_block is None:
        raise AcademicDataError("missing 毕业总学分 in the plan document")
    if not minimums:
        raise AcademicDataError("missing category minimum credits in the plan document")
    if not catalog:
        raise AcademicDataError("missing course catalog in the plan document")

    catalog_by_code: dict[str, tuple[str, str, str, int]] = {}
    for code, name, credits, category, block_index in catalog:
        if category not in categories_seen:
            raise AcademicDataError(f"catalog category not declared: {category!r}")
        existing = catalog_by_code.get(code)
        if existing is not None and (existing[0], existing[1], existing[2]) != (
            name,
            credits,
            category,
        ):
            raise AcademicDataError(f"conflicting catalog entry: {code}")
        catalog_by_code.setdefault(code, (name, credits, category, block_index))

    required_by_category: dict[str, list[str]] = {category: [] for category in categories_seen}
    for code, (name, credits, category, _block_index) in sorted(catalog_by_code.items()):
        if category.endswith("必修"):
            required_by_category[category].append(code)

    rules: list[DegreeRule] = []
    category_blocks: list[tuple[str, int]] = []
    for category, credits, block_index in minimums:
        rules.append(
            DegreeRule(
                major=major,
                admission_year=admission_year,
                rule_version=rule_version,
                category=category,
                minimum_credits=to_credit(credits),
                required_course_codes=tuple(required_by_category[category]),
                effective_from=effective_from,
                source_doc_id=doc_id,
                source_chunk_id=None,
            )
        )
        category_blocks.append((category, block_index))

    courses = tuple(
        RuleCourse(course_code=code, course_name=name, credits=to_credit(credits), category=category)
        for code, (name, credits, category, _block_index) in sorted(catalog_by_code.items())
    )

    rule_set = build_rule_set(
        rule_set_id=f"draft-{doc_id}",
        major=major,
        admission_year=admission_year,
        rule_version=rule_version,
        effective_from=effective_from,
        required_credits=required_credits,
        categories=tuple(rules),
        courses=courses,
        source_doc_id=doc_id,
        source_chunk_id=None,
    )
    return RuleSetDraft(
        rule_set=rule_set,
        header_block_index=required_credits_block,
        category_blocks=tuple(category_blocks),
        course_blocks=tuple(
            (code, entry[3]) for code, entry in sorted(catalog_by_code.items())
        ),
    )


def _parse_year(value: str) -> int:
    match = re.search(r"(\d{4})", value or "")
    if not match:
        raise AcademicDataError(f"invalid admission year: {value!r}")
    return int(match.group(1))


def _parse_catalog_line(
    text: str, categories: Sequence[str]
) -> tuple[str, str, str, str] | None:
    """解析 ``QM-CS102 高等数学（一） 5.0 公共必修 第1学期 无`` 形态的课程目录行。"""
    tokens = (text or "").split()
    if len(tokens) < 4 or not _COURSE_CODE_RE.match(tokens[0]):
        return None
    for position in range(1, len(tokens) - 1):
        if not _NUMERIC_RE.match(tokens[position]):
            continue
        category = tokens[position + 1]
        if category not in categories:
            continue
        name = " ".join(tokens[1:position]).strip()
        if not name:
            continue
        return tokens[0], name, tokens[position], category
    return None


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
    "ACADEMIC_PROJECTION_VERSION",
    "BLOCK_TABLE_HEADER",
    "BLOCK_TABLE_ROW",
    "DEFAULT_COLUMN_ALIASES",
    "DEFAULT_STATUS_ALIASES",
    "RECORD_REQUIRED_LABELS",
    "RULE_REQUIRED_LABELS",
    "TABLE_BLOCK_TYPES",
    "RecordDraft",
    "RecordSetProjection",
    "RuleSetDraft",
    "RuleSetProjection",
    "SourceBlock",
    "SourceBlockView",
    "build_rule_set",
    "labelled_pairs",
    "project_course_records",
    "project_record_rows",
    "project_rule_set_blocks",
    "projection_fingerprint",
    "record_set_display_name",
    "record_set_fingerprint",
    "rule_set_display_name",
    "rule_set_fingerprint",
    "safe_display_name",
]
