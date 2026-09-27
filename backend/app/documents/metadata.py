"""文档级可选过滤元数据的确定性派生。

这些字段是 Chroma 标量过滤条件的一部分。后端当前没有权威的教务数据来源，
因此只从 manifest 提供的文档标题做**确定性**派生，派生不出时**省略该字段**
（绝不写入 null 占位），后续阶段接入教务数据后再由真实来源覆盖。
"""

from __future__ import annotations

import re
from typing import Any

# 「XX大学YY专业…」→ YY
_MAJOR_RE = re.compile(r"大学(.+?)专业")
# 「2026-2027」学年
_ACADEMIC_YEAR_RE = re.compile(r"(\d{4})-(\d{4})")
# 「第一学期 / 第二学期」
_SEMESTER_RE = re.compile(r"第([一二三四五])学期")
# 课程代码：QM-CS201
_COURSE_CODE_RE = re.compile(r"\b([A-Z]{2,4}-[A-Z]{2,4}\d{3})\b")
_SEMESTER_ORDINALS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5}


def derive_document_metadata(*texts: str | None) -> dict[str, Any]:
    """从文档标题与文件名派生可选标量元数据；无值时直接省略键。

    多个文本按「标题在前、文件名在后」的顺序拼接后统一匹配，
    保证 demo（有 manifest 标题）与 upload（只有文件名）都走同一条确定性规则。
    """
    joined = " ".join(text.strip() for text in texts if text and text.strip())
    if not joined:
        return {}
    text = joined

    result: dict[str, Any] = {}

    major = _MAJOR_RE.search(text)
    if major and major.group(1).strip():
        result["major"] = major.group(1).strip()

    academic_year = _ACADEMIC_YEAR_RE.search(text)
    if academic_year:
        result["academic_year"] = f"{academic_year.group(1)}-{academic_year.group(2)}"

    semester = _SEMESTER_RE.search(text)
    if semester and academic_year:
        ordinal = _SEMESTER_ORDINALS.get(semester.group(1))
        if ordinal:
            result["semester"] = f"{result['academic_year']}-{ordinal}"

    course_code = _COURSE_CODE_RE.search(text)
    if course_code:
        result["course_code"] = course_code.group(1)

    # grade_year（入学年份）在 manifest 中没有可靠来源，故不派生
    return result


__all__ = ["derive_document_metadata"]
