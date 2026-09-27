"""文档类别 → 中文标签（稳定 value + 可读 label）。

只用于对外展示，不参与过滤匹配；未知类别原样返回 value 作为 label，
保证新增类别时不会因为缺少映射而丢数据。
"""

from __future__ import annotations

DOC_CATEGORY_LABELS: dict[str, str] = {
    "degree_plan": "培养方案",
    "course_syllabus": "课程大纲",
    "academic_policy": "学籍与教学管理规定",
    "academic_calendar": "校历",
    "exam_notice": "考试通知",
    "course_schedule": "课表",
    "course_records": "课程记录",
    "security_test": "安全测试样本",
}


def category_label(value: str) -> str:
    return DOC_CATEGORY_LABELS.get(value, value)


__all__ = ["DOC_CATEGORY_LABELS", "category_label"]
