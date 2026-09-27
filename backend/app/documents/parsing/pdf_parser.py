"""PDF 解析（PyMuPDF4LLM），保留 1-based 页码与可识别标题。"""

from __future__ import annotations

import contextlib
import io
import re
from pathlib import Path

import pymupdf4llm

from app.core.errors import DOCUMENT_CORRUPT, DOCUMENT_EMPTY, ApiError
from app.documents.blocks import (
    ParsedBlock,
    key_value_row,
    normalize_text,
    section_title_from_stack,
)

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$")
TABLE_SEPARATOR_RE = re.compile(r"^:?-{2,}:?$")


def parse_pdf(path: Path) -> list[ParsedBlock]:
    """解析 PDF；加密、损坏或空内容都收敛为安全错误。"""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            pages = pymupdf4llm.to_markdown(str(path), page_chunks=True, show_progress=False)
    except ApiError:
        raise
    except Exception as error:  # noqa: BLE001 - 第三方解析异常统一收敛
        raise ApiError(DOCUMENT_CORRUPT, retryable=False) from error

    if not pages:
        raise ApiError(DOCUMENT_EMPTY)

    blocks: list[ParsedBlock] = []
    heading_stack: list[str] = []
    for page in pages:
        metadata = page.get("metadata") or {}
        page_number = int(metadata.get("page") or 1)
        blocks.extend(_page_blocks(page.get("text") or "", page_number, heading_stack))

    if not blocks:
        raise ApiError(DOCUMENT_EMPTY)
    return blocks


def _page_blocks(markdown: str, page_number: int, heading_stack: list[str]) -> list[ParsedBlock]:
    blocks: list[ParsedBlock] = []
    paragraph: list[str] = []
    table: list[str] = []

    def flush_paragraph() -> None:
        if not paragraph:
            return
        text = normalize_text(" ".join(paragraph))
        if text:
            blocks.append(
                ParsedBlock(
                    text=text,
                    block_type="paragraph",
                    page_number=page_number,
                    section_title=section_title_from_stack(heading_stack),
                )
            )
        paragraph.clear()

    def flush_table() -> None:
        if not table:
            return
        blocks.extend(_table_blocks(table, page_number, section_title_from_stack(heading_stack)))
        table.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            flush_table()
            continue

        heading = HEADING_RE.match(line)
        if heading:
            flush_paragraph()
            flush_table()
            level = len(heading.group(1))
            title = normalize_text(heading.group(2))
            if title:
                del heading_stack[level - 1:]
                heading_stack.append(title)
                blocks.append(
                    ParsedBlock(
                        text=title,
                        block_type="heading",
                        page_number=page_number,
                        section_title=section_title_from_stack(heading_stack),
                        metadata={"heading_level": level},
                    )
                )
            continue

        if line.startswith("|"):
            flush_paragraph()
            table.append(line)
            continue

        flush_table()
        paragraph.append(line)

    flush_paragraph()
    flush_table()
    return blocks


def _table_blocks(lines: list[str], page_number: int, section_title: str | None) -> list[ParsedBlock]:
    rows: list[list[str]] = []
    for line in lines:
        cells = [normalize_text(cell) for cell in line.strip().strip("|").split("|")]
        if any(cells) and all(TABLE_SEPARATOR_RE.match(cell) for cell in cells if cell):
            continue
        rows.append(cells)

    if not rows:
        return []

    header = rows[0]
    blocks = [
        ParsedBlock(
            text=" | ".join(header),
            block_type="table_header",
            page_number=page_number,
            section_title=section_title,
            metadata={"header": header},
        )
    ]
    for row in rows[1:]:
        text = key_value_row(header, row) or " | ".join(row)
        if not text:
            continue
        blocks.append(
            ParsedBlock(
                text=text,
                block_type="table_row",
                page_number=page_number,
                section_title=section_title,
                metadata={"header": header, "cells": row},
            )
        )
    return blocks
