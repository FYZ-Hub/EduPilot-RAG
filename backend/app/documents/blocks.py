"""解析产物：统一的 ``DocumentBlock`` 结构与文本规范化。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

_WHITESPACE_RE = re.compile(r"[ \t\u3000]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")


def normalize_text(text: str) -> str:
    """折叠空白但保留紧凑结构，供解析块与后续切片共用。"""
    if not text:
        return ""
    collapsed = _WHITESPACE_RE.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n"))
    lines = [line.strip() for line in collapsed.split("\n")]
    joined = "\n".join(line for line in lines if line)
    return _MULTI_NEWLINE_RE.sub("\n\n", joined).strip()


@dataclass(frozen=True)
class ParsedBlock:
    """可追溯的解析块；``block_index`` 由持久化层按顺序赋值。"""

    text: str
    block_type: str
    page_number: int | None = None
    sheet_name: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    section_title: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


def section_title_from_stack(stack: list[str]) -> str | None:
    """把标题栈拼成可追溯的标题路径。"""
    filtered = [item for item in stack if item]
    return " > ".join(filtered) if filtered else None


def cell_text(value: Any) -> str:
    """把单元格值转成稳定文本；公式保留原文，不执行计算。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        return repr(value)
    return str(value).strip()


def key_value_row(header: list[str], values: list[str]) -> str:
    """把「表头 + 完整数据行」拼成可检索文本，跳过空单元格。"""
    parts: list[str] = []
    for index, value in enumerate(values):
        if not value:
            continue
        name = header[index] if index < len(header) and header[index] else f"第{index + 1}列"
        parts.append(f"{name}: {value}")
    return "；".join(parts)
