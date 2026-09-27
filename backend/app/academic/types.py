"""阶段 7A：学业规划的规范化数据结构与固定精度工具。

本模块是**纯数据**层：不访问数据库、文件、网络、LLM、Embedding 或 Reranker。
对外输出统一为「一位小数」；内部一律使用 ``Decimal``，**禁止**二进制浮点累加漂移，
并拒绝 NaN / Infinity / 负学分 / 未知状态。

字段严格对应 ``docs/PRODUCT_SPEC.md`` 5.2，不增删字段。
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from app.core.errors import ACADEMIC_FIELD_MISSING, ACADEMIC_VALUE_INVALID

# 对外固定一位小数；上限用于拒绝明显异常值（防注入式超大数字）
CREDIT_QUANT = Decimal("0.1")
MAX_CREDIT = Decimal("999.9")

STATUS_PASSED = "passed"
STATUS_IN_PROGRESS = "in_progress"
STATUS_FAILED = "failed"
COURSE_STATUSES = (STATUS_PASSED, STATUS_IN_PROGRESS, STATUS_FAILED)

SEVERITY_WARNING = "warning"
SEVERITY_BLOCKING = "blocking"
SEVERITIES = (SEVERITY_WARNING, SEVERITY_BLOCKING)

# 稳定的 warning code（与 ground truth 的 oracle 口径一致）
WARN_VERSION_CONFLICT = "DEGREE_PLAN_VERSION_CONFLICT"
WARN_TIME_CONFLICT = "COURSE_TIME_CONFLICT"
WARN_CATEGORY_MISMATCH = "COURSE_CATEGORY_MISMATCH"
WARN_RECORD_CONTRADICTION = "COURSE_RECORD_CONTRADICTION"
WARN_COURSE_NOT_IN_RULE = "COURSE_NOT_IN_RULE_CATALOG"
WARN_REQUIRED_COURSE_UNKNOWN = "REQUIRED_COURSE_UNKNOWN"


class AcademicDataError(ValueError):
    """规范化数据非法；调用方必须返回稳定错误，不得猜测或静默修正。

    ``code`` 是可直接映射为 HTTP 错误体的稳定错误码，默认「缺少必要字段」；
    学分 / 状态非法与规则冲突在抛出点显式指定各自的错误码。
    """

    def __init__(self, message: str, code: str = ACADEMIC_FIELD_MISSING) -> None:
        super().__init__(message)
        self.code = code


def to_credit(value: object) -> Decimal:
    """把外部取值转换为受限 ``Decimal``；非法值一律抛出 ``AcademicDataError``。"""
    if value is None or isinstance(value, bool):
        raise AcademicDataError("credit must be a finite number", ACADEMIC_VALUE_INVALID)
    try:
        amount = Decimal(str(value).strip())
    except (InvalidOperation, ValueError, AttributeError, ArithmeticError) as error:
        raise AcademicDataError(
            "credit must be a finite number", ACADEMIC_VALUE_INVALID
        ) from error
    if not amount.is_finite():
        raise AcademicDataError("credit must be finite", ACADEMIC_VALUE_INVALID)
    if amount < 0:
        raise AcademicDataError("credit must not be negative", ACADEMIC_VALUE_INVALID)
    if amount > MAX_CREDIT:
        raise AcademicDataError("credit out of range", ACADEMIC_VALUE_INVALID)
    return amount


def quantize_credit(value: Decimal) -> Decimal:
    """四舍五入到一位小数（计算内部使用，避免漂移）。"""
    return value.quantize(CREDIT_QUANT, rounding=ROUND_HALF_UP)


def credit_text(value: Decimal) -> str:
    """持久化用的一位小数字符串（SQLite 中不使用浮点列）。"""
    return str(quantize_credit(value))


def credit_number(value: Decimal) -> float:
    """API 边界输出一位小数。"""
    return float(quantize_credit(value))


@dataclass(frozen=True)
class CourseRecord:
    """PRODUCT_SPEC 5.2 ``CourseRecord``。"""

    course_code: str
    course_name: str
    credits: Decimal
    category: str
    grade: str | None
    status: str
    semester: str | None
    schedule: str | None


@dataclass(frozen=True)
class DegreeRule:
    """PRODUCT_SPEC 5.2 ``DegreeRule``（一个课程类别一条）。"""

    major: str
    admission_year: int
    rule_version: str
    category: str
    minimum_credits: Decimal
    required_course_codes: tuple[str, ...]
    effective_from: str | None
    source_doc_id: str
    source_chunk_id: str | None


@dataclass(frozen=True)
class RuleCourse:
    """规则课程目录条目：课程代码 → 名称 / 学分 / 类别。"""

    course_code: str
    course_name: str
    credits: Decimal
    category: str


@dataclass(frozen=True)
class DegreeRuleSet:
    """一个培养方案版本的完整规则（用户显式选择的最小计算单元）。"""

    rule_set_id: str
    major: str
    admission_year: int
    rule_version: str
    effective_from: str | None
    # 毕业总学分（``required_credits`` 的来源）
    required_credits: Decimal
    categories: tuple[DegreeRule, ...]
    courses: tuple[RuleCourse, ...]
    source_doc_id: str
    source_chunk_id: str | None

    @property
    def course_by_code(self) -> dict[str, RuleCourse]:
        return {course.course_code: course for course in self.courses}


@dataclass(frozen=True)
class TimeConflictHint:
    """由调用方（7C 从证据中）给出的上课时间冲突提示，引擎只负责转成 warning。"""

    message: str
    evidence_chunk_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class MissingRequiredCourse:
    course_code: str
    course_name: str
    credits: Decimal
    category: str
    evidence_chunk_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class CategoryGap:
    category: str
    required_credits: Decimal
    completed_credits: Decimal
    in_progress_credits: Decimal
    remaining_credits: Decimal


@dataclass(frozen=True)
class ConflictWarning:
    code: str
    message: str
    severity: str
    evidence_chunk_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class PlanningEvidence:
    """PRODUCT_SPEC 5.2 ``PlanningEvidence``；只允许白名单元数据与真实 chunk 文本。"""

    chunk_id: str
    doc_id: str
    file_name: str
    document_version: str | None
    effective_from: str | None
    page_number: int | None
    sheet_name: str | None
    row_start: int | None
    row_end: int | None
    section_title: str | None
    quote: str


@dataclass(frozen=True)
class PlanningResult:
    """PRODUCT_SPEC 5.2 ``PlanningResult``。

    字段集**严格固定为 8 项**，不得增删：所选培养方案的展示信息（专业 / 规则版本）
    不属于本结构，应由外层响应元数据或 options 数据承担。
    """

    required_credits: Decimal
    completed_credits: Decimal
    in_progress_credits: Decimal
    remaining_credits: Decimal
    missing_required_courses: tuple[MissingRequiredCourse, ...]
    category_gaps: tuple[CategoryGap, ...]
    conflict_warnings: tuple[ConflictWarning, ...]
    evidence: tuple[PlanningEvidence, ...]


def plan_evidence_payload(item: PlanningEvidence) -> dict[str, object]:
    return {
        "chunk_id": item.chunk_id,
        "doc_id": item.doc_id,
        "file_name": item.file_name,
        "document_version": item.document_version,
        "effective_from": item.effective_from,
        "page_number": item.page_number,
        "sheet_name": item.sheet_name,
        "row_start": item.row_start,
        "row_end": item.row_end,
        "section_title": item.section_title,
        "quote": item.quote,
    }


def planning_result_payload(result: PlanningResult) -> dict[str, object]:
    """稳定序列化：顶层 key 严格等于 PRODUCT_SPEC 5.2 的 8 项，
    数字一位小数、数组顺序固定；相同结果产生逐字节一致的 JSON。
    """
    return {
        "required_credits": credit_number(result.required_credits),
        "completed_credits": credit_number(result.completed_credits),
        "in_progress_credits": credit_number(result.in_progress_credits),
        "remaining_credits": credit_number(result.remaining_credits),
        "missing_required_courses": [
            {
                "course_code": item.course_code,
                "course_name": item.course_name,
                "credits": credit_number(item.credits),
                "category": item.category,
                "evidence_chunk_ids": list(item.evidence_chunk_ids),
            }
            for item in result.missing_required_courses
        ],
        "category_gaps": [
            {
                "category": item.category,
                "required_credits": credit_number(item.required_credits),
                "completed_credits": credit_number(item.completed_credits),
                "in_progress_credits": credit_number(item.in_progress_credits),
                "remaining_credits": credit_number(item.remaining_credits),
            }
            for item in result.category_gaps
        ],
        "conflict_warnings": [
            {
                "code": item.code,
                "message": item.message,
                "severity": item.severity,
                "evidence_chunk_ids": list(item.evidence_chunk_ids),
            }
            for item in result.conflict_warnings
        ],
        "evidence": [plan_evidence_payload(item) for item in result.evidence],
    }


__all__ = [
    "COURSE_STATUSES",
    "CREDIT_QUANT",
    "MAX_CREDIT",
    "SEVERITIES",
    "SEVERITY_BLOCKING",
    "SEVERITY_WARNING",
    "STATUS_FAILED",
    "STATUS_IN_PROGRESS",
    "STATUS_PASSED",
    "WARN_CATEGORY_MISMATCH",
    "WARN_COURSE_NOT_IN_RULE",
    "WARN_RECORD_CONTRADICTION",
    "WARN_REQUIRED_COURSE_UNKNOWN",
    "WARN_TIME_CONFLICT",
    "WARN_VERSION_CONFLICT",
    "AcademicDataError",
    "CategoryGap",
    "ConflictWarning",
    "CourseRecord",
    "DegreeRule",
    "DegreeRuleSet",
    "MissingRequiredCourse",
    "PlanningEvidence",
    "PlanningResult",
    "RuleCourse",
    "TimeConflictHint",
    "credit_number",
    "credit_text",
    "plan_evidence_payload",
    "planning_result_payload",
    "quantize_credit",
    "to_credit",
]
