"""阶段 7C：确定性学业规划服务（``POST /api/academic/plan``）。

职责边界：

- **唯一计算路径**是 7A 的纯函数 ``compute_plan()``；本模块只负责「显式选择的 ID →
  数据库行 → 7A 数据类 → 真实证据」的装配，**不实现**任何学分加减、缺口、去重或类别计算；
- 两个 ID 必须由用户显式提供，绝不自动选择第一个、最新版本或默认规则；
- 可见性口径与 ``GET /api/academic/options`` **共用同一处定义**；
- 证据全部来自真实 ``DocumentChunk`` 行；学业导入文档在计算前幂等补建证据切片并回填
  ``source_chunk_id``（不建向量、不写 FTS、不建检查点）；
- 不访问 LLM、Embedding、Reranker 或网络。
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import constants
from app.academic import evidence as evidence_backfill
from app.academic.engine import compute_plan
from app.academic.options import active_demo_version, visibility_clause
from app.academic.projection import labelled_pairs
from app.academic.service import load_chunk_locators, resolve_source_chunk
from app.academic.types import (
    WARN_CATEGORY_MISMATCH,
    WARN_COURSE_NOT_IN_RULE,
    WARN_RECORD_CONTRADICTION,
    WARN_REQUIRED_COURSE_UNKNOWN,
    WARN_VERSION_CONFLICT,
    AcademicDataError,
    CourseRecord,
    DegreeRule,
    DegreeRuleSet,
    PlanningEvidence,
    PlanningResult,
    RuleCourse,
    TimeConflictHint,
    to_credit,
)
from app.config import Settings
from app.core.errors import (
    ACADEMIC_DATA_CORRUPT,
    ACADEMIC_EVIDENCE_UNAVAILABLE,
    ACADEMIC_PLAN_FAILED,
    ACADEMIC_RECORD_SET_NOT_FOUND,
    ACADEMIC_RULE_SET_NOT_FOUND,
    ACADEMIC_SOURCE_INVALID,
    ACADEMIC_VALUE_INVALID,
    ApiError,
)
from app.models import (
    AcademicRecordSet,
    AcademicRuleSet,
    CourseRecordRow,
    DegreeRuleCourse,
    DegreeRuleRow,
    Document,
    DocumentBlock,
    DocumentChunk,
)

SCHEDULE_DOC_CATEGORY = "course_schedule"
# 课表中真实重复排课的稳定文案（与演示语料的既定表述一致）
SCHEDULE_CONFLICT_MESSAGE = "课表中存在同一时间段安排两门不同课程的情况。"

_WEEKDAY_RE = re.compile(r"星期(?P<weekday>[一二三四五六日天])")
_PERIOD_RE = re.compile(r"第(?P<start>\d{1,2})\s*-\s*(?P<end>\d{1,2})节")
_CLOCK_RE = re.compile(
    r"(?P<h1>\d{1,2}):(?P<m1>\d{2})\s*-\s*(?P<h2>\d{1,2}):(?P<m2>\d{2})"
)


@dataclass(frozen=True)
class _RuleEvidence:
    header: str
    by_category: dict[str, str]
    by_course: dict[str, str]


@dataclass(frozen=True)
class TimeConflictFinding:
    """时间冲突发现：稳定文案 + 冲突双方的真实 (chunk_id, doc_id)。"""

    message: str
    chunks: tuple[tuple[str, str], ...]

    def as_hint(self) -> TimeConflictHint:
        return TimeConflictHint(
            message=self.message,
            evidence_chunk_ids=tuple(chunk_id for chunk_id, _doc_id in self.chunks),
        )


# ---------------------------------------------------------------------------
# 可见性（与 options 共用口径）
# ---------------------------------------------------------------------------


def _load_record_set(
    session: Session, record_set_id: str, active_version: str | None
) -> AcademicRecordSet:
    record_set = session.get(AcademicRecordSet, record_set_id)
    if record_set is None or record_set.status != constants.STATUS_READY:
        raise ApiError(ACADEMIC_RECORD_SET_NOT_FOUND)
    _require_valid_source(session, record_set.source_doc_id)
    if record_set.source_type == constants.SOURCE_DEMO:
        if (
            active_version is None
            or record_set.dataset_version != active_version
            or record_set.activation_state != constants.ACTIVATION_ACTIVE
        ):
            raise ApiError(ACADEMIC_RECORD_SET_NOT_FOUND)
    elif record_set.source_type != constants.SOURCE_UPLOAD:
        raise ApiError(ACADEMIC_RECORD_SET_NOT_FOUND)
    return record_set


def _load_rule_set(
    session: Session, rule_set_id: str, active_version: str | None
) -> AcademicRuleSet:
    rule_set = session.get(AcademicRuleSet, rule_set_id)
    if rule_set is None or rule_set.status != constants.STATUS_READY:
        raise ApiError(ACADEMIC_RULE_SET_NOT_FOUND)
    _require_valid_source(session, rule_set.source_doc_id)
    if rule_set.source_type == constants.SOURCE_DEMO:
        if (
            active_version is None
            or rule_set.dataset_version != active_version
            or rule_set.activation_state != constants.ACTIVATION_ACTIVE
        ):
            raise ApiError(ACADEMIC_RULE_SET_NOT_FOUND)
    elif rule_set.source_type != constants.SOURCE_UPLOAD:
        raise ApiError(ACADEMIC_RULE_SET_NOT_FOUND)
    return rule_set


def _require_valid_source(session: Session, doc_id: str) -> None:
    document = session.get(Document, doc_id)
    if document is None or document.deleted_at is not None:
        raise ApiError(ACADEMIC_SOURCE_INVALID)


def _other_rule_sets(
    session: Session, active_version: str | None, selected: AcademicRuleSet
) -> list[AcademicRuleSet]:
    """同一专业、当前同样合法的其它培养方案版本（不混用其数字，只用于版本冲突提示）。"""
    return list(
        session.scalars(
            select(AcademicRuleSet)
            .join(Document, Document.id == AcademicRuleSet.source_doc_id)
            .where(
                AcademicRuleSet.status == constants.STATUS_READY,
                AcademicRuleSet.major == selected.major,
                AcademicRuleSet.rule_version != selected.rule_version,
                Document.deleted_at.is_(None),
                visibility_clause(AcademicRuleSet, active_version),
            )
            .order_by(AcademicRuleSet.rule_version, AcademicRuleSet.id)
        ).all()
    )


# ---------------------------------------------------------------------------
# 数据装配（只读，不做任何计算）
# ---------------------------------------------------------------------------


def _require_chunk(chunk_id: str | None, reason: str) -> str:
    if not chunk_id:
        raise ApiError(ACADEMIC_EVIDENCE_UNAVAILABLE, details={"reason": reason})
    return chunk_id


def load_records(
    session: Session, record_set: AcademicRecordSet
) -> tuple[tuple[CourseRecord, ...], dict[str, tuple[str, ...]]]:
    """显式选择的课程记录，以及每个课程代码对应的真实证据切片。"""
    rows = list(
        session.scalars(
            select(CourseRecordRow)
            .where(CourseRecordRow.record_set_id == record_set.id)
            .order_by(CourseRecordRow.ordinal)
        ).all()
    )
    if not rows:
        raise ApiError(ACADEMIC_DATA_CORRUPT, details={"reason": "record_set_empty"})

    records: list[CourseRecord] = []
    by_code: dict[str, list[str]] = {}
    for row in rows:
        chunk_id = _require_chunk(row.source_chunk_id, f"record_row:{row.course_code}")
        if chunk_id not in by_code.setdefault(row.course_code, []):
            by_code[row.course_code].append(chunk_id)
        records.append(
            CourseRecord(
                course_code=row.course_code,
                course_name=row.course_name,
                credits=to_credit(row.credits),
                category=row.category,
                grade=row.grade,
                status=row.status,
                semester=row.semester,
                schedule=row.schedule,
            )
        )
    return tuple(records), {code: tuple(ids) for code, ids in by_code.items()}


def load_rule(
    session: Session, rule_set: AcademicRuleSet
) -> tuple[DegreeRuleSet, _RuleEvidence]:
    """显式选择的培养方案规则，以及各字段对应的真实证据切片。"""
    declarations = list(
        session.scalars(
            select(DegreeRuleRow)
            .where(DegreeRuleRow.rule_set_id == rule_set.id)
            .order_by(DegreeRuleRow.ordinal)
        ).all()
    )
    if not declarations:
        raise ApiError(ACADEMIC_DATA_CORRUPT, details={"reason": "rule_set_empty"})
    courses = list(
        session.scalars(
            select(DegreeRuleCourse)
            .where(DegreeRuleCourse.rule_set_id == rule_set.id)
            .order_by(DegreeRuleCourse.ordinal)
        ).all()
    )

    header = _require_chunk(rule_set.source_chunk_id, "rule_header")
    by_category: dict[str, str] = {}
    rules: list[DegreeRule] = []
    for row in declarations:
        chunk_id = _require_chunk(row.source_chunk_id, f"rule_category:{row.category}")
        by_category[row.category] = chunk_id
        rules.append(
            DegreeRule(
                major=row.major,
                admission_year=row.admission_year,
                rule_version=row.rule_version,
                category=row.category,
                minimum_credits=to_credit(row.minimum_credits),
                required_course_codes=tuple(row.required_course_codes or ()),
                effective_from=row.effective_from,
                source_doc_id=row.source_doc_id,
                source_chunk_id=chunk_id,
            )
        )

    by_course: dict[str, str] = {}
    catalog: list[RuleCourse] = []
    for row in courses:
        by_course[row.course_code] = _require_chunk(
            row.source_chunk_id, f"rule_course:{row.course_code}"
        )
        catalog.append(
            RuleCourse(
                course_code=row.course_code,
                course_name=row.course_name,
                credits=to_credit(row.credits),
                category=row.category,
            )
        )

    rule = DegreeRuleSet(
        rule_set_id=rule_set.id,
        major=rule_set.major,
        admission_year=rule_set.admission_year,
        rule_version=rule_set.rule_version,
        effective_from=rule_set.effective_from,
        required_credits=to_credit(rule_set.required_credits),
        categories=tuple(rules),
        courses=tuple(catalog),
        source_doc_id=rule_set.source_doc_id,
        source_chunk_id=header,
    )
    return rule, _RuleEvidence(header=header, by_category=by_category, by_course=by_course)


# ---------------------------------------------------------------------------
# 时间冲突（只使用可证明的确定性结构，绝不猜测）
# ---------------------------------------------------------------------------


def _schedule_interval(text: str | None) -> tuple[str, str, int, int] | None:
    """把 ``星期一第1-2节`` / ``星期二 10:00-11:40`` 解析为 (星期, 单位, 起, 止)。"""
    if not text:
        return None
    weekday = _WEEKDAY_RE.search(text)
    if weekday is None:
        return None
    period = _PERIOD_RE.search(text)
    if period is not None:
        start, end = int(period.group("start")), int(period.group("end"))
        if start <= end:
            return (weekday.group("weekday"), "period", start, end)
    clock = _CLOCK_RE.search(text)
    if clock is not None:
        start = int(clock.group("h1")) * 60 + int(clock.group("m1"))
        end = int(clock.group("h2")) * 60 + int(clock.group("m2"))
        if start <= end:
            return (weekday.group("weekday"), "clock", start, end)
    return None


def _overlaps(left: tuple[str, str, int, int], right: tuple[str, str, int, int]) -> bool:
    return (
        left[0] == right[0]
        and left[1] == right[1]
        and left[2] <= right[3]
        and right[2] <= left[3]
    )


def record_schedule_conflicts(
    records: Sequence[CourseRecord],
    chunks_by_code: Mapping[str, Sequence[str]],
    doc_id: str,
) -> TimeConflictFinding | None:
    """所选记录自身 ``schedule`` 的确定性时间重叠；无法解析即不产生冲突。"""
    entries: list[tuple[str, tuple[str, str, int, int]]] = []
    for record in records:
        interval = _schedule_interval(record.schedule)
        if interval is not None:
            entries.append((record.course_code, interval))
    entries.sort(key=lambda item: (item[1][0], item[1][2], item[1][3], item[0]))

    conflicting: set[str] = set()
    pairs: list[tuple[str, str]] = []
    for index, (code, interval) in enumerate(entries):
        for other_code, other_interval in entries[index + 1 :]:
            if code == other_code or not _overlaps(interval, other_interval):
                continue
            conflicting.update((code, other_code))
            pairs.append(tuple(sorted((code, other_code))))
    if not pairs:
        return None

    chunks: list[tuple[str, str]] = []
    for code in sorted(conflicting):
        for chunk_id in chunks_by_code.get(code, ()):
            if all(existing != chunk_id for existing, _doc in chunks):
                chunks.append((chunk_id, doc_id))
    if not chunks:
        return None
    detail = "、".join(f"{left}/{right}" for left, right in sorted(set(pairs)))
    return TimeConflictFinding(
        message=f"以下课程在所选记录的上课时间上存在重叠，请人工核对：{detail}。",
        chunks=tuple(chunks),
    )


def schedule_document_conflicts(
    session: Session, active_version: str | None
) -> TimeConflictFinding | None:
    """active demo 课表中真实存在的「同一时间段两门不同课程」；证据覆盖冲突双方来源。"""
    if active_version is None:
        return None
    documents = list(
        session.scalars(
            select(Document)
            .where(
                Document.source_type == constants.SOURCE_DEMO,
                Document.dataset_version == active_version,
                Document.activation_state == constants.ACTIVATION_ACTIVE,
                Document.doc_category == SCHEDULE_DOC_CATEGORY,
                Document.deleted_at.is_(None),
                Document.status == constants.STATUS_READY,
            )
            .order_by(Document.file_name, Document.id)
        ).all()
    )

    chunks: list[tuple[str, str]] = []
    for document in documents:
        blocks = list(
            session.scalars(
                select(DocumentBlock)
                .where(DocumentBlock.doc_id == document.id)
                .order_by(DocumentBlock.block_index)
            ).all()
        )
        locators = load_chunk_locators(session, document.id)
        slots: dict[tuple[str, str], list[tuple[str, DocumentBlock]]] = {}
        for block in blocks:
            if block.block_type != "table_row":
                continue
            pairs = labelled_pairs(block.text)
            weekday = (pairs.get("星期") or "").strip()
            period = (pairs.get("节次") or "").strip()
            code = (pairs.get("课程代码") or "").strip()
            if not weekday or not period or not code:
                continue
            slots.setdefault((weekday, period), []).append((code, block))
        for slot in sorted(slots):
            entries = slots[slot]
            if len({code for code, _ in entries}) < 2:
                continue
            for _code, block in sorted(entries, key=lambda item: (item[0], item[1].block_index)):
                chunk_id = evidence_backfill.resolve_chunk_by_block(
                    locators, block.block_index
                ) or resolve_source_chunk(
                    locators,
                    sheet_name=block.sheet_name,
                    row_start=block.row_start,
                    row_end=block.row_end,
                )
                if chunk_id and all(existing != chunk_id for existing, _doc in chunks):
                    chunks.append((chunk_id, document.id))

    if not chunks:
        return None
    return TimeConflictFinding(message=SCHEDULE_CONFLICT_MESSAGE, chunks=tuple(chunks))


# ---------------------------------------------------------------------------
# 证据装载
# ---------------------------------------------------------------------------


def load_evidence(
    session: Session, expected: Mapping[str, str]
) -> tuple[PlanningEvidence, ...]:
    """按 chunk_id 装载真实证据；缺失、跨文档或不完整定位一律安全失败。"""
    if not expected:
        return ()
    rows = session.execute(
        select(DocumentChunk, Document)
        .join(Document, Document.id == DocumentChunk.doc_id)
        .where(DocumentChunk.id.in_(sorted(expected)))
    ).all()
    collected: dict[str, PlanningEvidence] = {}
    for chunk, document in rows:
        if expected.get(chunk.id) != document.id:
            raise ApiError(ACADEMIC_EVIDENCE_UNAVAILABLE, details={"reason": "doc_mismatch"})
        locator = dict(chunk.locator or {})
        if not locator:
            raise ApiError(ACADEMIC_EVIDENCE_UNAVAILABLE, details={"reason": "locator_missing"})
        citation = dict(chunk.citation or {})
        collected[chunk.id] = PlanningEvidence(
            chunk_id=chunk.id,
            doc_id=document.id,
            file_name=str(citation.get("file_name") or document.file_name),
            document_version=citation.get("document_version") or document.document_version,
            effective_from=citation.get("effective_from") or document.effective_from,
            page_number=locator.get("page_number"),
            sheet_name=locator.get("sheet_name"),
            row_start=locator.get("row_start"),
            row_end=locator.get("row_end"),
            section_title=locator.get("section_title"),
            # quote 必须是被引用 chunk 的真实文本，不得拼接或生成
            quote=chunk.text,
        )
    if set(collected) != set(expected):
        raise ApiError(ACADEMIC_EVIDENCE_UNAVAILABLE, details={"reason": "chunk_missing"})
    return tuple(sorted(collected.values(), key=lambda item: (item.doc_id, item.chunk_id)))


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------


def build_plan(
    session: Session, settings: Settings, record_set_id: str, rule_set_id: str
) -> PlanningResult:
    """按用户**显式选择**的两个 ID 计算规划；数字只能来自 7A 的 ``compute_plan``。"""
    active_version = active_demo_version(session)
    record_set = _load_record_set(session, record_set_id, active_version)
    rule_set = _load_rule_set(session, rule_set_id, active_version)

    # 真实证据前置：学业导入集合在计算前幂等补建证据切片并回填 source_chunk_id
    if record_set.source_type == constants.SOURCE_UPLOAD:
        evidence_backfill.backfill_record_set(session, settings, record_set)
    if rule_set.source_type == constants.SOURCE_UPLOAD:
        evidence_backfill.backfill_rule_set(session, settings, rule_set)

    expected: dict[str, str] = {}

    def _register(chunk_id: str, doc_id: str) -> str:
        known = expected.get(chunk_id)
        if known is not None and known != doc_id:
            raise ApiError(ACADEMIC_EVIDENCE_UNAVAILABLE, details={"reason": "doc_mismatch"})
        expected[chunk_id] = doc_id
        return chunk_id

    try:
        records, record_chunks = load_records(session, record_set)
        rule, rule_evidence = load_rule(session, rule_set)
        other_versions = _other_rule_sets(session, active_version, rule_set)
    except ApiError:
        raise
    except AcademicDataError as error:
        raise _as_api_error(error) from error

    record_doc = record_set.source_doc_id
    for chunk_ids in record_chunks.values():
        for chunk_id in chunk_ids:
            _register(chunk_id, record_doc)
    rule_doc = rule_set.source_doc_id
    _register(rule_evidence.header, rule_doc)
    for chunk_id in rule_evidence.by_category.values():
        _register(chunk_id, rule_doc)
    for chunk_id in rule_evidence.by_course.values():
        _register(chunk_id, rule_doc)

    other_headers: list[str] = []
    for version in other_versions:
        chunk_id = version.source_chunk_id
        if not chunk_id:  # pragma: no cover - demo/import 均保证存在
            raise ApiError(
                ACADEMIC_EVIDENCE_UNAVAILABLE,
                details={"reason": f"version_header:{version.rule_version}"},
            )
        other_headers.append(_register(chunk_id, version.source_doc_id))

    findings: list[TimeConflictFinding] = []
    finding = record_schedule_conflicts(records, record_chunks, record_doc)
    if finding is not None:
        findings.append(finding)
    finding = schedule_document_conflicts(session, active_version)
    if finding is not None:
        findings.append(finding)
    for finding in findings:
        for chunk_id, doc_id in finding.chunks:
            _register(chunk_id, doc_id)
    time_conflicts = [finding.as_hint() for finding in findings]

    all_record_chunks = [chunk_id for ids in record_chunks.values() for chunk_id in ids]
    category_chunks = list(rule_evidence.by_category.values())
    required_codes = {
        code for declaration in rule.categories for code in declaration.required_course_codes
    }

    warning_evidence: dict[str, Sequence[str]] = {
        # 所选记录的**全部**真实记录行都是矛盾/类别/目录类告警的证据基础（超集，确保覆盖）
        WARN_RECORD_CONTRADICTION: all_record_chunks,
        WARN_CATEGORY_MISMATCH: all_record_chunks,
        WARN_COURSE_NOT_IN_RULE: all_record_chunks,
        WARN_REQUIRED_COURSE_UNKNOWN: category_chunks or [rule_evidence.header],
        WARN_VERSION_CONFLICT: [rule_evidence.header, *other_headers],
    }
    for code in sorted(required_codes):
        chunk_id = rule_evidence.by_course.get(code)
        if chunk_id is None:
            chunk_id = rule_evidence.by_category.get(_category_of(rule, code))
        warning_evidence[f"missing:{code}"] = [chunk_id or rule_evidence.header]

    evidence_items = load_evidence(session, expected)
    try:
        return compute_plan(
            records,
            rule,
            other_rule_versions=tuple(version.rule_version for version in other_versions),
            time_conflicts=tuple(time_conflicts),
            evidence=evidence_items,
            warning_evidence=warning_evidence,
        )
    except AcademicDataError as error:
        raise _as_api_error(error) from error
    except ApiError:
        raise
    except Exception as error:  # noqa: BLE001 - 兜底：绝不向客户端泄漏内部实现
        raise ApiError(ACADEMIC_PLAN_FAILED) from error


def _category_of(rule: DegreeRuleSet, code: str) -> str:
    for declaration in rule.categories:
        if code in declaration.required_course_codes:
            return declaration.category
    return ""


def _as_api_error(error: AcademicDataError) -> ApiError:
    """已存数据的非法取值 → 稳定的数值错误；其它结构问题 → 数据损坏。"""
    code = error.code if error.code == ACADEMIC_VALUE_INVALID else ACADEMIC_DATA_CORRUPT
    return ApiError(code, details={"reason": error.code})


__all__ = [
    "SCHEDULE_CONFLICT_MESSAGE",
    "SCHEDULE_DOC_CATEGORY",
    "TimeConflictFinding",
    "build_plan",
    "load_evidence",
    "load_records",
    "load_rule",
    "record_schedule_conflicts",
    "schedule_document_conflicts",
]
