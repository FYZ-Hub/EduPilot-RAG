"""阶段 7A：确定性学分规则引擎（纯数据 + 纯计算 + 投影接口）。

7B 将在此基础上实现：演示资料投影落库、records/rules 导入与 ``GET /api/academic/options``。
7C 将实现：``POST /api/academic/plan``、真实证据映射、冲突 warnings 与 health planning 状态。
"""

from app.academic.engine import compute_plan
from app.academic.projection import (
    RecordSetProjection,
    RuleSetProjection,
    SourceBlock,
    build_rule_set,
    project_course_records,
    record_set_fingerprint,
    rule_set_fingerprint,
)
from app.academic.types import (
    AcademicDataError,
    CategoryGap,
    ConflictWarning,
    CourseRecord,
    DegreeRule,
    DegreeRuleSet,
    MissingRequiredCourse,
    PlanningEvidence,
    PlanningResult,
    RuleCourse,
    TimeConflictHint,
    credit_number,
    credit_text,
    planning_result_payload,
    to_credit,
)

__all__ = [
    "AcademicDataError",
    "CategoryGap",
    "ConflictWarning",
    "CourseRecord",
    "DegreeRule",
    "DegreeRuleSet",
    "MissingRequiredCourse",
    "PlanningEvidence",
    "PlanningResult",
    "RecordSetProjection",
    "RuleCourse",
    "RuleSetProjection",
    "SourceBlock",
    "TimeConflictHint",
    "build_rule_set",
    "compute_plan",
    "credit_number",
    "credit_text",
    "planning_result_payload",
    "project_course_records",
    "record_set_fingerprint",
    "rule_set_fingerprint",
    "to_credit",
]
