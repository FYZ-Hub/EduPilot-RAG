"""DOCX 解析（python-docx），保留标题路径、段落与表格的稳定顺序。"""

from __future__ import annotations

import re
from pathlib import Path

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from app.core.errors import DOCUMENT_CORRUPT, DOCUMENT_EMPTY, ApiError
from app.documents.blocks import (
    ParsedBlock,
    key_value_row,
    normalize_text,
    section_title_from_stack,
)

HEADING_STYLE_RE = re.compile(r"^Heading\s+(\d+)$")
TITLE_STYLE_LEVELS = {"Title": 1, "Subtitle": 2}


def parse_docx(path: Path) -> list[ParsedBlock]:
    """按 body 子元素顺序解析，段落与表格不会被拆散或重排。"""
    try:
        document = Document(str(path))
    except Exception as error:  # noqa: BLE001 - 第三方解析异常统一收敛
        raise ApiError(DOCUMENT_CORRUPT, retryable=False) from error

    blocks: list[ParsedBlock] = []
    heading_stack: list[str] = []
    paragraph_index = 0
    table_index = 0

    for child in document.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            paragraph = Paragraph(child, document)
            text = normalize_text(paragraph.text)
            if text:
                style = paragraph.style.name if paragraph.style is not None else ""
                level = _heading_level(style)
                if level:
                    del heading_stack[level - 1:]
                    heading_stack.append(text)
                    blocks.append(
                        ParsedBlock(
                            text=text,
                            block_type="heading",
                            section_title=section_title_from_stack(heading_stack),
                            metadata={
                                "heading_level": level,
                                "style": style,
                                "paragraph_index": paragraph_index,
                            },
                        )
                    )
                else:
                    block_type = "list_item" if style.startswith("List") else "paragraph"
                    blocks.append(
                        ParsedBlock(
                            text=text,
                            block_type=block_type,
                            section_title=section_title_from_stack(heading_stack),
                            metadata={"style": style, "paragraph_index": paragraph_index},
                        )
                    )
            paragraph_index += 1
        elif tag == "tbl":
            table = Table(child, document)
            blocks.extend(_table_blocks(table, table_index, section_title_from_stack(heading_stack)))
            table_index += 1

    if not blocks:
        raise ApiError(DOCUMENT_EMPTY)
    return blocks


def _heading_level(style: str) -> int | None:
    match = HEADING_STYLE_RE.match(style or "")
    if match:
        return int(match.group(1))
    return TITLE_STYLE_LEVELS.get((style or "").strip())


def _row_values(row) -> list[str]:
    """按稳定列序提取单元格文本；合并单元格只取一次。"""
    values: list[str] = []
    previous = None
    for cell in row.cells:
        if cell._tc is previous:
            continue
        previous = cell._tc
        values.append(normalize_text(cell.text))
    return values


def _table_blocks(table: Table, table_index: int, section_title: str | None) -> list[ParsedBlock]:
    rows = table.rows
    if not rows:
        return []

    header = _row_values(rows[0])
    blocks = [
        ParsedBlock(
            text=" | ".join(header),
            block_type="table_header",
            section_title=section_title,
            metadata={"table_index": table_index, "header": header, "row_index": 1},
        )
    ]
    for row_index, row in enumerate(rows[1:], start=2):
        values = _row_values(row)
        text = key_value_row(header, values) or " | ".join(values)
        if not text:
            continue
        blocks.append(
            ParsedBlock(
                text=text,
                block_type="table_row",
                section_title=section_title,
                metadata={
                    "table_index": table_index,
                    "header": header,
                    "cells": values,
                    "row_index": row_index,
                },
            )
        )
    return blocks
