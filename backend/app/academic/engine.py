"""阶段 7A：确定性学分计算引擎（纯函数核心）。

输入只有**已规范化的** ``CourseRecord`` 与 ``DegreeRuleSet``；不访问数据库、文件、
网络、LLM、Embedding 或 Reranker，因此可以直接单元测试，并在相同输入下产生
逐字节一致的 ``PlanningResult``。

确定性规则（严格实现规格第九节）：

1. ``passed`` 计入 ``completed_credits``；``in_progress`` 计入 ``in_progress_credits``；
2. ``failed`` 不计学分；
3. 同一 ``course_code`` 多次正考 / 补考 / 重修**只计一次**；
4. ``passed`` 与 ``in_progress`` 互斥，``passed`` 优先；
5. 学分与类别优先取用户**显式选择**的 rule set（课程目录），不与其它版本混用；
6. 记录类别与所选规则不一致时按规则类别计分（不双重计分）并产生 warning；
7. 各类别与总缺口一律 ``max(required - completed - in_progress, 0)``，绝不出现负数；
8. 必修课已修或在修即不列入 missing（``failed`` 仍列入）；
9. 无法映射的课程代码、未知状态、非法学分一律报错或产生明确 warning，绝不猜测。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal

from app.academic.types import (
    COURSE_STATUSES,
    SEVERITY_WARNING,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    STATUS_PASSED,
    WARN_CATEGORY_MISMATCH,
    WARN_COURSE_NOT_IN_RULE,
    WARN_RECORD_CONTRADICTION,
    WARN_REQUIRED_COURSE_UNKNOWN,
    WARN_TIME_CONFLICT,
    WARN_VERSION_CONFLICT,
    AcademicDataError,
    CategoryGap,
    ConflictWarning,
    CourseRecord,
    DegreeRuleSet,
    MissingRequiredCourse,
    PlanningEvidence,
    PlanningResult,
    TimeConflictHint,
    quantize_credit,
    to_credit,
)

ZERO = Decimal("0")


def _total(values: Iterable[Decimal]) -> Decimal:
    """Decimal 精确累加后统一量化到一位小数，避免二进制浮点漂移。"""
    return quantize_credit(sum(values, ZERO))


def _dedupe_evidence(evidence: Sequence[PlanningEvidence]) -> tuple[PlanningEvidence, ...]:
    """按 chunk_id 去重（保留首次出现），再按 (doc_id, chunk_id) 稳定排序。"""
    unique: dict[str, PlanningEvidence] = {}
    for item in evidence:
        if not item.chunk_id:
            raise AcademicDataError("evidence chunk_id must not be empty")
        unique.setdefault(item.chunk_id, item)
    return tuple(sorted(unique.values(), key=lambda entry: (entry.doc_id, entry.chunk_id)))


def compute_plan(
    records: Sequence[CourseRecord],
    rule: DegreeRuleSet,
    *,
    other_rule_versions: Sequence[str] = (),
    time_conflicts: Sequence[TimeConflictHint] = (),
    evidence: Sequence[PlanningEvidence] = (),
    warning_evidence: Mapping[str, Sequence[str]] | None = None,
) -> PlanningResult:
    """由课程记录与**显式选择**的规则集合确定性计算学分缺口。"""
    hints = dict(warning_evidence or {})

    def _hint(key: str) -> tuple[str, ...]:
        return tuple(hints.get(key, ()))

    # --- 规则校验 ---------------------------------------------------------
    required_credits = to_credit(rule.required_credits)
    if not rule.rule_version:
        raise AcademicDataError("rule_version must not be empty")
    seen_categories: set[str] = set()
    for declaration in rule.categories:
        if declaration.category in seen_categories:
            raise AcademicDataError(f"duplicate rule category: {declaration.category}")
        seen_categories.add(declaration.category)
        to_credit(declaration.minimum_credits)

    # --- 记录规范化（同一课程只计一次） -----------------------------------
    passed: dict[str, CourseRecord] = {}
    in_progress: dict[str, CourseRecord] = {}
    failed_codes: set[str] = set()
    for record in records:
        code = (record.course_code or "").strip()
        if not code:
            raise AcademicDataError("course_code must not be empty")
        if record.status not in COURSE_STATUSES:
            raise AcademicDataError(f"unknown course status: {record.status!r}")
        # 非法学分（NaN / Infinity / 负数）必须在这里被拒绝，绝不静默修正
        to_credit(record.credits)
        if record.status == STATUS_PASSED:
            passed.setdefault(code, record)
        elif record.status == STATUS_IN_PROGRESS:
            in_progress.setdefault(code, record)
        else:
            failed_codes.add(code)

    # 冲突信号必须在删除之前采集：passed 与 in_progress 并存同样是矛盾记录
    passed_and_in_progress = set(passed) & set(in_progress)
    # passed 优先：已修与在修互斥，重复通过 / 重复在修都只保留一次
    for code in list(in_progress):
        if code in passed:
            del in_progress[code]

    # 矛盾记录覆盖三种组合：failed+passed、failed+in_progress、passed+in_progress
    contradictory = (failed_codes & (set(passed) | set(in_progress))) | passed_and_in_progress
    contradictions = sorted(contradictory)

    # --- 学分与类别以所选规则为准 -----------------------------------------
    catalog = rule.course_by_code
    resolved: dict[str, tuple[str, Decimal]] = {}
    mismatches: list[str] = []
    unlisted: list[str] = []
    for code in sorted(set(passed) | set(in_progress)):
        record = passed.get(code) or in_progress[code]
        course = catalog.get(code)
        if course is None:
            resolved[code] = (record.category or "", to_credit(record.credits))
            unlisted.append(code)
            continue
        resolved[code] = (course.category, to_credit(course.credits))
        if record.category and record.category != course.category:
            mismatches.append(code)

    completed_credits = _total(resolved[code][1] for code in sorted(passed))
    in_progress_credits = _total(resolved[code][1] for code in sorted(in_progress))
    remaining_credits = max(required_credits - completed_credits - in_progress_credits, ZERO)

    # --- 类别缺口 ---------------------------------------------------------
    category_gaps: list[CategoryGap] = []
    for declaration in rule.categories:
        category = declaration.category
        completed = _total(
            credits
            for code, (item_category, credits) in sorted(resolved.items())
            if item_category == category and code in passed
        )
        ongoing = _total(
            credits
            for code, (item_category, credits) in sorted(resolved.items())
            if item_category == category and code in in_progress
        )
        category_gaps.append(
            CategoryGap(
                category=category,
                required_credits=to_credit(declaration.minimum_credits),
                completed_credits=completed,
                in_progress_credits=ongoing,
                remaining_credits=max(
                    to_credit(declaration.minimum_credits) - completed - ongoing, ZERO
                ),
            )
        )

    # --- 未满足的必修课程 -------------------------------------------------
    missing: list[MissingRequiredCourse] = []
    unknown_required: list[str] = []
    for declaration in rule.categories:
        for code in declaration.required_course_codes:
            if code in passed or code in in_progress:
                continue
            course = catalog.get(code)
            if course is None:
                unknown_required.append(code)
                missing.append(
                    MissingRequiredCourse(
                        course_code=code,
                        course_name=code,
                        credits=ZERO,
                        category=declaration.category,
                        evidence_chunk_ids=_hint(f"missing:{code}"),
                    )
                )
                continue
            missing.append(
                MissingRequiredCourse(
                    course_code=code,
                    course_name=course.course_name,
                    credits=to_credit(course.credits),
                    category=course.category,
                    evidence_chunk_ids=_hint(f"missing:{code}"),
                )
            )

    # --- 冲突与一致性 warning（顺序固定） ---------------------------------
    warnings: list[ConflictWarning] = []
    other_versions = sorted(
        {version for version in other_rule_versions if version and version != rule.rule_version}
    )
    if other_versions:
        warnings.append(
            ConflictWarning(
                code=WARN_VERSION_CONFLICT,
                message=(
                    f"{rule.major}专业同时存在 {rule.rule_version} 与 "
                    f"{'、'.join(other_versions)} 等多个培养方案版本，"
                    "毕业总学分与课程要求不一致，须由用户确认适用版本。"
                ),
                severity=SEVERITY_WARNING,
                evidence_chunk_ids=_hint(WARN_VERSION_CONFLICT),
            )
        )
    for hint in time_conflicts:
        warnings.append(
            ConflictWarning(
                code=WARN_TIME_CONFLICT,
                message=hint.message,
                severity=SEVERITY_WARNING,
                evidence_chunk_ids=tuple(hint.evidence_chunk_ids),
            )
        )
    if mismatches:
        warnings.append(
            ConflictWarning(
                code=WARN_CATEGORY_MISMATCH,
                message=(
                    "以下课程在记录中的类别与所选培养方案规则不一致，已按规则类别计分以避免重复计分："
                    + "、".join(mismatches)
                ),
                severity=SEVERITY_WARNING,
                evidence_chunk_ids=_hint(WARN_CATEGORY_MISMATCH),
            )
        )
    if contradictions:
        warnings.append(
            ConflictWarning(
                code=WARN_RECORD_CONTRADICTION,
                message=(
                    "以下课程同时存在通过与未通过记录，已按「通过优先」计分，请人工核对："
                    + "、".join(contradictions)
                ),
                severity=SEVERITY_WARNING,
                evidence_chunk_ids=_hint(WARN_RECORD_CONTRADICTION),
            )
        )
    if unlisted:
        warnings.append(
            ConflictWarning(
                code=WARN_COURSE_NOT_IN_RULE,
                message=(
                    "以下课程不在所选培养方案的课程目录中，已按记录自身学分与类别计分："
                    + "、".join(unlisted)
                ),
                severity=SEVERITY_WARNING,
                evidence_chunk_ids=_hint(WARN_COURSE_NOT_IN_RULE),
            )
        )
    if unknown_required:
        warnings.append(
            ConflictWarning(
                code=WARN_REQUIRED_COURSE_UNKNOWN,
                message=(
                    "以下必修课程缺少可用的学分与名称信息，无法确认其详情："
                    + "、".join(sorted(set(unknown_required)))
                ),
                severity=SEVERITY_WARNING,
                evidence_chunk_ids=_hint(WARN_REQUIRED_COURSE_UNKNOWN),
            )
        )

    return PlanningResult(
        required_credits=required_credits,
        completed_credits=completed_credits,
        in_progress_credits=in_progress_credits,
        remaining_credits=remaining_credits,
        missing_required_courses=tuple(missing),
        category_gaps=tuple(category_gaps),
        conflict_warnings=tuple(warnings),
        evidence=_dedupe_evidence(evidence),
    )


__all__ = ["compute_plan"]
