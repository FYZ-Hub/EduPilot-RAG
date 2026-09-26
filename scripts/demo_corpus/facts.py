"""确定性虚构事实模型（启明大学模拟资料）。

所有跨文件事实（课程代码、课程名称、学分、学期、日期、先修关系、考试安排、
匿名学生记录、培养方案规则）只在这里定义一次，生成器与校验测试共用同一份数据，
避免手工抄写导致的跨文件漂移。
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# 数据集级固定配置
# ---------------------------------------------------------------------------

DATASET_NAME = "qiming-campus-demo"
DATASET_VERSION = "2026.1"
SCHOOL_NAME = "启明大学"
FICTIONAL = True
DEFAULT_SEED = 20260925
GENERATOR_VERSION = "1.0.0"
GENERATED_AT = "2026-09-25T00:00:00Z"

FICTION_MARKER = "仅供系统演示的虚构资料"
FICTION_NOTICE = (
    f"【{FICTION_MARKER}】本文件由模拟资料生成器确定性生成，其中的学校、人员、"
    "课程、成绩与制度均为虚构，不影射任何真实机构或个人。"
)

DOC_AUTHOR = "启明大学模拟资料生成器"
DOC_CREATOR = "campus-rag-demo-corpus-generator"
DOC_PRODUCER = "campus-rag-demo-corpus-generator"

MAJOR_NAME = "计算机科学与技术"
MAJOR_CODE = "QM-CS"
ADMISSION_YEAR = 2025
SEMESTERS = ["2025-2026-1", "2025-2026-2", "2026-2027-1", "2026-2027-2"]
CATEGORIES = ["公共必修", "专业必修", "专业选修", "通识选修", "实践环节"]

TEACHER_ROLES = ["课程负责人A", "课程负责人B", "课程负责人C"]
STUDENT_A = {"key": "student_a", "label": "匿名学生A", "student_id": "QM-DEMO-A001"}
STUDENT_B = {"key": "student_b", "label": "匿名学生B", "student_id": "QM-DEMO-B002"}
STUDENTS = [STUDENT_A, STUDENT_B]

ROOMS = ["QM-A201", "QM-A203", "QM-A305", "QM-A210", "QM-A212", "QM-B102"]

# ---------------------------------------------------------------------------
# 课程（唯一事实来源）
# ---------------------------------------------------------------------------


def _course(code, name, credits, category, term, prerequisites, assessment):
    return {
        "course_code": code,
        "course_name": name,
        "credits": credits,
        "category": category,
        "suggested_term": term,
        "prerequisites": prerequisites,
        "assessment": assessment,
    }


COURSES = [
    _course("QM-CS101", "程序设计基础", 4.0, "专业必修", 1, [], "平时30%+期末70%"),
    _course("QM-CS102", "高等数学（一）", 5.0, "公共必修", 1, [], "平时30%+期末70%"),
    _course("QM-CS103", "大学英语（一）", 3.0, "公共必修", 1, [], "平时40%+期末60%"),
    _course("QM-CS104", "计算机导论", 2.0, "专业必修", 1, [], "平时40%+期末60%"),
    _course("QM-CS105", "线性代数", 3.0, "公共必修", 2, [], "平时30%+期末70%"),
    _course("QM-CS203", "大学物理", 3.5, "公共必修", 2, [], "平时30%+期末70%"),
    _course("QM-CS201", "数据结构", 4.0, "专业必修", 3, ["QM-CS101"], "平时30%+期末70%"),
    _course("QM-CS202", "离散数学", 3.0, "专业必修", 3, ["QM-CS102"], "平时30%+期末70%"),
    _course("QM-GE101", "大学写作", 2.0, "通识选修", 3, [], "平时50%+期末50%"),
    _course("QM-CS204", "计算机组成原理", 4.0, "专业必修", 4, ["QM-CS104"], "平时30%+期末70%"),
    _course("QM-CS301", "操作系统", 4.0, "专业必修", 4, ["QM-CS201"], "平时30%+期末70%"),
    _course("QM-CS302", "数据库系统", 4.0, "专业必修", 4, ["QM-CS201"], "平时30%+期末70%"),
    _course("QM-CS303", "计算机网络", 3.0, "专业必修", 5, ["QM-CS204"], "平时30%+期末70%"),
    # 软件工程不属于任何一个培养方案版本的必修列表，因此事实模型中归为专业选修
    _course("QM-CS401", "软件工程", 3.0, "专业选修", 5, ["QM-CS201"], "平时30%+期末70%"),
]

COURSE_BY_CODE = {c["course_code"]: c for c in COURSES}

# 事实模型中标记为必修的课程类别；这些课程必须出现在培养方案的必修列表里
MANDATORY_CATEGORIES = ("公共必修", "专业必修")


def term_label(index: int) -> str:
    return f"第{index}学期"


# ---------------------------------------------------------------------------
# 培养方案（两个生效版本，构成稳定的版本冲突）
# ---------------------------------------------------------------------------

DEGREE_PLAN_2025 = {
    "version": "2025.1",
    "effective_from": "2025-09-01",
    "file_name": "01-培养方案-计算机科学与技术-2025版.pdf",
    "title": "启明大学计算机科学与技术专业培养方案（2025版）",
    "total_credits": 155.0,
    "category_minimums": {
        "公共必修": 52.0,
        "专业必修": 58.0,
        "专业选修": 20.0,
        "通识选修": 15.0,
        "实践环节": 10.0,
    },
    "required_course_codes": {
        "公共必修": ["QM-CS102", "QM-CS103", "QM-CS105", "QM-CS203"],
        "专业必修": [
            "QM-CS101",
            "QM-CS104",
            "QM-CS201",
            "QM-CS202",
            "QM-CS204",
            "QM-CS301",
            "QM-CS302",
        ],
    },
    "exchange_credit_cap": 6,
}

DEGREE_PLAN_2026 = {
    "version": "2026.1",
    "effective_from": "2026-09-01",
    "file_name": "02-培养方案-计算机科学与技术-2026修订版.pdf",
    "title": "启明大学计算机科学与技术专业培养方案（2026修订版）",
    "total_credits": 160.0,
    "category_minimums": {
        "公共必修": 55.0,
        "专业必修": 60.0,
        "专业选修": 20.0,
        "通识选修": 15.0,
        "实践环节": 10.0,
    },
    "required_course_codes": {
        "公共必修": ["QM-CS102", "QM-CS103", "QM-CS105", "QM-CS203"],
        "专业必修": [
            "QM-CS101",
            "QM-CS104",
            "QM-CS201",
            "QM-CS202",
            "QM-CS204",
            "QM-CS301",
            "QM-CS302",
            "QM-CS303",
        ],
    },
    "exchange_credit_cap": 8,
}

DEGREE_PLANS = [DEGREE_PLAN_2025, DEGREE_PLAN_2026]
PLAN_BY_VERSION = {p["version"]: p for p in DEGREE_PLANS}

# 学分认定办法规定的单次认定上限（与 2026 修订版培养方案构成冲突）
POLICY_CREDIT_RECOGNITION_CAP = 6


# ---------------------------------------------------------------------------
# 培养方案课程视图（生成与校验共用，避免正文与事实模型漂移）
# ---------------------------------------------------------------------------


def plan_required_codes(plan: dict, category: str) -> list[str]:
    """方案指定类别的必修课程代码，顺序与方案定义一致。"""
    return list(plan["required_course_codes"][category])


def plan_mandatory_courses(plan: dict) -> list[tuple[dict, str]]:
    """按方案返回必修课程 (course, 方案类别)；旧版本不会出现新版新增课程。"""
    rows: list[tuple[dict, str]] = []
    for category in MANDATORY_CATEGORIES:
        for code in plan["required_course_codes"][category]:
            rows.append((COURSE_BY_CODE[code], category))
    return rows


def plan_optional_courses(plan: dict) -> list[dict]:
    """方案中非必修课程：事实模型中不属于必修类别的课程。"""
    mandatory = {code for codes in plan["required_course_codes"].values() for code in codes}
    return [
        course
        for course in COURSES
        if course["course_code"] not in mandatory and course["category"] not in MANDATORY_CATEGORIES
    ]


def plan_added_required_codes(newer: dict, older: dict, category: str = "专业必修") -> list[str]:
    """``newer`` 相对 ``older`` 新增的必修课程代码（稳定排序）。"""
    return sorted(set(plan_required_codes(newer, category)) - set(plan_required_codes(older, category)))

# ---------------------------------------------------------------------------
# 匿名学生课程记录
# ---------------------------------------------------------------------------

RECORDS = {
    "student_a": [
        {"course_code": "QM-CS101", "semester": "2025-2026-1", "grade": 85.0, "status": "passed", "record_type": "正考"},
        {"course_code": "QM-CS102", "semester": "2025-2026-1", "grade": 52.0, "status": "failed", "record_type": "正考"},
        {"course_code": "QM-CS103", "semester": "2025-2026-1", "grade": 78.0, "status": "passed", "record_type": "正考"},
        {"course_code": "QM-CS104", "semester": "2025-2026-1", "grade": 90.0, "status": "passed", "record_type": "正考"},
        {"course_code": "QM-CS102", "semester": "2025-2026-2", "grade": 76.0, "status": "passed", "record_type": "重修"},
        {"course_code": "QM-CS105", "semester": "2025-2026-2", "grade": 81.0, "status": "passed", "record_type": "正考"},
        {"course_code": "QM-CS203", "semester": "2025-2026-2", "grade": 69.0, "status": "passed", "record_type": "正考"},
        {"course_code": "QM-CS201", "semester": "2026-2027-1", "grade": None, "status": "in_progress", "record_type": "在修"},
        {"course_code": "QM-CS202", "semester": "2026-2027-1", "grade": None, "status": "in_progress", "record_type": "在修"},
        {"course_code": "QM-GE101", "semester": "2026-2027-1", "grade": None, "status": "in_progress", "record_type": "在修"},
    ],
    "student_b": [
        {"course_code": "QM-CS101", "semester": "2025-2026-1", "grade": 92.0, "status": "passed", "record_type": "正考"},
        {"course_code": "QM-CS102", "semester": "2025-2026-1", "grade": 88.0, "status": "passed", "record_type": "正考"},
        {"course_code": "QM-CS103", "semester": "2025-2026-1", "grade": 74.0, "status": "passed", "record_type": "正考"},
        {"course_code": "QM-CS104", "semester": "2025-2026-1", "grade": 66.0, "status": "passed", "record_type": "正考"},
        {"course_code": "QM-CS105", "semester": "2025-2026-2", "grade": 58.0, "status": "failed", "record_type": "正考"},
        {"course_code": "QM-CS105", "semester": "2025-2026-2", "grade": 55.0, "status": "failed", "record_type": "补考"},
        {"course_code": "QM-CS201", "semester": "2026-2027-1", "grade": None, "status": "in_progress", "record_type": "在修"},
        {"course_code": "QM-CS202", "semester": "2026-2027-1", "grade": None, "status": "in_progress", "record_type": "在修"},
        {"course_code": "QM-CS203", "semester": "2026-2027-1", "grade": None, "status": "in_progress", "record_type": "在修"},
    ],
}

# ---------------------------------------------------------------------------
# 考试安排
# ---------------------------------------------------------------------------

FINAL_EXAMS_2026_2027_1 = [
    {"course_code": "QM-CS201", "date": "2027-01-05", "time": "09:00-11:00", "room": "QM-A201"},
    {"course_code": "QM-CS202", "date": "2027-01-06", "time": "14:00-16:00", "room": "QM-A203"},
    {"course_code": "QM-GE101", "date": "2027-01-07", "time": "09:00-11:00", "room": "QM-B102"},
]

MAKEUP_EXAMS_2025_2026_2 = [
    {"course_code": "QM-CS203", "date": "2026-08-26", "time": "09:00-11:00", "room": "QM-A212"},
    {"course_code": "QM-CS105", "date": "2026-08-26", "time": "14:00-16:00", "room": "QM-A210"},
]

# ---------------------------------------------------------------------------
# 课表（2026-2027 第一学期含一处稳定时间冲突）
# ---------------------------------------------------------------------------

SCHEDULE_HEADER = ["星期", "节次", "时间", "课程代码", "课程名称", "任课教师", "教室", "周次"]

SCHEDULE_2026_2027_1 = [
    ["星期一", "第1-2节", "08:00-09:40", "QM-CS201", "数据结构", "课程负责人A", "QM-A201", "1-16周"],
    ["星期一", "第3-4节", "10:00-11:40", "QM-CS202", "离散数学", "课程负责人B", "QM-A203", "1-16周"],
    ["星期二", "第3-4节", "10:00-11:40", "QM-GE101", "大学写作", "课程负责人C", "QM-B102", "1-16周"],
    ["星期二", "第3-4节", "10:00-11:40", "QM-CS201", "数据结构", "课程负责人A", "QM-A201", "1-16周"],
    ["星期三", "第1-2节", "08:00-09:40", "QM-CS201", "数据结构", "课程负责人A", "QM-A201", "1-16周"],
    ["星期三", "第5-6节", "14:00-15:40", "QM-CS202", "离散数学", "课程负责人B", "QM-A203", "1-16周"],
]

SCHEDULE_2026_2027_2 = [
    ["星期一", "第1-2节", "08:00-09:40", "QM-CS301", "操作系统", "课程负责人A", "QM-A201", "1-16周"],
    ["星期一", "第3-4节", "10:00-11:40", "QM-CS302", "数据库系统", "课程负责人B", "QM-A305", "1-16周"],
    ["星期二", "第1-2节", "08:00-09:40", "QM-CS204", "计算机组成原理", "课程负责人C", "QM-A203", "1-16周"],
    ["星期三", "第3-4节", "10:00-11:40", "QM-CS301", "操作系统", "课程负责人A", "QM-A201", "1-16周"],
    ["星期四", "第1-2节", "08:00-09:40", "QM-CS302", "数据库系统", "课程负责人B", "QM-A305", "1-16周"],
    ["星期五", "第3-4节", "10:00-11:40", "QM-CS204", "计算机组成原理", "课程负责人C", "QM-A203", "1-16周"],
]

# 课表冲突定位：表内行号在生成时确定，这里给出用于定位的键
SCHEDULE_CONFLICT_SLOT = ("星期二", "第3-4节")
SCHEDULE_CONFLICT_CODES = ["QM-GE101", "QM-CS201"]

# ---------------------------------------------------------------------------
# 校历（2026-2027 学年）
# ---------------------------------------------------------------------------

CALENDAR_HEADER = ["事件", "开始日期", "结束日期", "备注"]
CALENDAR_ROWS = [
    ["第一学期报到注册", "2026-08-29", "2026-08-30", "老生返校"],
    ["第一学期开课", "2026-09-01", "2026-09-01", "第1教学周开始"],
    ["国庆假期", "2026-10-01", "2026-10-07", "调休安排另行通知"],
    ["第一学期期末考试周", "2027-01-04", "2027-01-15", "含公共课与专业课"],
    ["寒假", "2027-01-18", "2027-02-21", "共5周"],
    ["第二学期开课", "2027-02-22", "2027-02-22", "第1教学周开始"],
    ["第二学期期末考试周", "2027-06-21", "2027-07-02", "含公共课与专业课"],
    ["暑假", "2027-07-05", "2027-08-29", "共8周"],
]


# ---------------------------------------------------------------------------
# 确定性学业规划规则
# ---------------------------------------------------------------------------


def _round(value: float) -> float:
    return round(float(value) + 0.0, 1)


def compute_planning_result(records, plan, evidence):
    """由课程记录与培养方案规则确定性计算学分缺口。

    同一课程多次成绩（不及格、补考、重修）只按“是否已通过”计入一次学分；
    在修课程计入 in_progress_credits，不与已修学分重复。
    """
    passed = {}
    in_progress = {}
    for record in records:
        code = record["course_code"]
        course = COURSE_BY_CODE[code]
        if record["status"] == "passed":
            passed[code] = course
        elif record["status"] == "in_progress" and code not in passed:
            in_progress[code] = course

    for code in list(in_progress):
        if code in passed:
            del in_progress[code]

    def credits_of(mapping, category):
        return _round(sum(c["credits"] for c in mapping.values() if c["category"] == category))

    category_gaps = []
    completed_total = 0.0
    in_progress_total = 0.0
    for category in CATEGORIES:
        required = _round(plan["category_minimums"][category])
        completed = credits_of(passed, category)
        ongoing = credits_of(in_progress, category)
        remaining = _round(max(required - completed - ongoing, 0.0))
        completed_total += completed
        in_progress_total += ongoing
        category_gaps.append(
            {
                "category": category,
                "required_credits": required,
                "completed_credits": completed,
                "in_progress_credits": ongoing,
                "remaining_credits": remaining,
            }
        )

    missing_required_courses = []
    for category in ("公共必修", "专业必修"):
        for code in plan["required_course_codes"][category]:
            if code in passed or code in in_progress:
                continue
            course = COURSE_BY_CODE[code]
            missing_required_courses.append(
                {
                    "course_code": code,
                    "course_name": course["course_name"],
                    "credits": _round(course["credits"]),
                    "category": course["category"],
                    "evidence_chunk_ids": [],
                }
            )

    conflict_warnings = []
    if evidence.get("rules_alt"):
        conflict_warnings.append(
            {
                "code": "DEGREE_PLAN_VERSION_CONFLICT",
                "message": (
                    f"{MAJOR_NAME}专业同时存在 {plan['version']} 与 "
                    f"{evidence['rules_alt']['document_version']} 两个培养方案版本，"
                    "毕业总学分与专业必修要求不一致，须由用户确认适用版本。"
                ),
                "severity": "warning",
                "evidence_chunk_ids": [],
            }
        )
    if evidence.get("schedule_conflict"):
        conflict_warnings.append(
            {
                "code": "COURSE_TIME_CONFLICT",
                "message": "课表中存在同一时间段安排两门不同课程的情况。",
                "severity": "warning",
                "evidence_chunk_ids": [],
            }
        )

    evidence_items = [evidence[name] for name in ("rules", "records", "schedule") if evidence.get(name)]
    if evidence.get("rules_alt"):
        evidence_items.append(evidence["rules_alt"])
    if evidence.get("schedule_conflict"):
        evidence_items.append(evidence["schedule_conflict"])

    return {
        "rule_version": plan["version"],
        "major": MAJOR_NAME,
        "admission_year": ADMISSION_YEAR,
        "required_credits": _round(plan["total_credits"]),
        "completed_credits": _round(completed_total),
        "in_progress_credits": _round(in_progress_total),
        "remaining_credits": _round(max(plan["total_credits"] - completed_total - in_progress_total, 0.0)),
        "missing_required_courses": missing_required_courses,
        "category_gaps": category_gaps,
        "conflict_warnings": conflict_warnings,
        "evidence": evidence_items,
    }
