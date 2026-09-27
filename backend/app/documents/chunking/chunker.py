"""确定性 DocumentChunker。

输入是阶段 2B 已持久化的 ``DocumentBlock``；输出是带完整来源定位的 chunk。
设计要点：
- 只使用固定的遍历顺序、NFC 规范化与固定空白折叠，不依赖时间或随机数
- 优先在语义边界切分：标题 / 段落 / 表格行 / 工作表 / 中文句读；最后才按安全字符边界切分
- 绝不把定位不同的块无约束合并：``(page_number, sheet_name, section_title)`` 变化即断章
- 表格行是原子单位，且每个含表格行的 chunk 都会带上该表的表头
- 不停留在课程代码、学分值、日期时间等 token 中间
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from app.core.errors import DOCUMENT_CHUNK_FAILED, ApiError
from app.documents.blocks import normalize_text

# 句读终止符：中文句号、问号、感叹号、分号 + ASCII 对应
SENTENCE_TERMINATORS = "。！？；!?;"
# 次级断点：仅在长句必须硬切时使用
SECONDARY_BREAKS = "，,、）)】」》〉】…：:"
# 不允许被切断的 token 字符（课程代码 QM-CS201、日期 2026-09-01、学分 4.0 等）
_TOKEN_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._%-")

_ATOMIC_BLOCK_TYPES = frozenset({"heading", "caption", "table_header", "table_row"})
_TABLE_ROW_TYPES = frozenset({"table_row"})
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

CHUNKER_NAME = "campus-rag-chunker"


@dataclass(frozen=True)
class ChunkSourceBlock:
    """切片输入：与持久化层解耦的最小字段集合。"""

    block_index: int
    text: str
    block_type: str
    page_number: int | None = None
    sheet_name: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    section_title: str | None = None


@dataclass(frozen=True)
class ChunkUnit:
    """原子切片单位：整块（标题/表格行）或一个句子。"""

    text: str
    block_index: int
    block_type: str
    page_number: int | None = None
    sheet_name: str | None = None
    row_start: int | None = None
    row_end: int | None = None
    section_title: str | None = None
    table_header: "ChunkUnit | None" = None

    @property
    def group_key(self) -> tuple[int | None, str | None, str | None]:
        return (self.page_number, self.sheet_name, self.section_title)


@dataclass(frozen=True)
class ChunkDraft:
    """切片结果；``locator`` 参与 chunk_id 计算，必须稳定。"""

    chunk_index: int
    text: str
    locator: dict[str, Any] = field(default_factory=dict)


def normalize_chunk_text(text: str) -> str:
    """NFC + 控制字符清理 + 固定空白折叠；与解析层保持同一规则。"""
    if not text:
        return ""
    return normalize_text(_CONTROL_RE.sub("", unicodedata.normalize("NFC", text)))


def chunk_blocks(
    blocks: Sequence[ChunkSourceBlock],
    *,
    target_chars: int,
    overlap_chars: int,
) -> list[ChunkDraft]:
    """把解析块切成确定性 chunk；相同输入必定得到相同结果。"""
    target = max(1, int(target_chars))
    overlap = max(0, min(int(overlap_chars), target - 1))

    units = _build_units(blocks, target)
    if not units:
        raise ApiError(DOCUMENT_CHUNK_FAILED, details={"reason": "no_chunkable_text"})

    drafts: list[ChunkDraft] = []
    current: list[ChunkUnit] = []
    current_length = 0

    def flush() -> None:
        nonlocal current, current_length
        if current:
            drafts.append(_draft(len(drafts), current))
            current = []
            current_length = 0

    for unit in units:
        if not current:
            current = [unit]
            current_length = len(unit.text)
            continue

        same_group = unit.group_key == current[0].group_key
        # 表格行可能需要在 chunk 内补上表头，长度预算必须把表头算进去
        overhead = _header_overhead(current, unit) if same_group else 0
        if same_group and current_length + 1 + len(unit.text) + overhead <= target:
            current.append(unit)
            current_length += 1 + len(unit.text)
            continue

        previous = current
        flush()
        carried = _tail_units(previous, overlap) if same_group else []
        candidate = [*carried, unit]
        candidate_length = (
            sum(len(item.text) for item in candidate)
            + len(candidate)
            - 1
            + _header_overhead(carried, unit)
        )
        if candidate_length > target:
            # 丢弃重叠上下文；单个原子单位仍然超预算时保持完整（表格行不得拆散）
            carried = []
            candidate = [unit]
            candidate_length = len(unit.text) + _header_overhead([], unit)
        current = candidate
        current_length = candidate_length

    flush()
    return drafts


def _header_overhead(present: Sequence[ChunkUnit], unit: ChunkUnit) -> int:
    """新加入表格行时，若当前 chunk 还没有该表头，需要额外计入表头长度。"""
    header = unit.table_header
    if header is None:
        return 0
    if any(existing.block_index == header.block_index for existing in present):
        return 0
    return len(header.text) + 1


def _build_units(blocks: Iterable[ChunkSourceBlock], target: int) -> list[ChunkUnit]:
    units: list[ChunkUnit] = []
    active_header: ChunkUnit | None = None
    header_group: tuple[int | None, str | None, str | None] | None = None

    for block in blocks:
        text = normalize_chunk_text(block.text)
        if not text:
            continue

        base = ChunkUnit(
            text=text,
            block_index=block.block_index,
            block_type=block.block_type,
            page_number=block.page_number,
            sheet_name=block.sheet_name,
            row_start=block.row_start,
            row_end=block.row_end,
            section_title=block.section_title,
        )

        if block.block_type not in _ATOMIC_BLOCK_TYPES:
            # 段落 / 列表项：先按句读切句，必要时再按安全字符边界切分
            pieces = _split_sentences(text, target)
            if len(pieces) > 1:
                units.extend(
                    replace(base, text=piece, row_start=None, row_end=None) for piece in pieces
                )
            else:
                units.append(base)
            continue

        unit = base
        if block.block_type == "table_header":
            active_header = unit
            header_group = unit.group_key
        elif block.block_type in _TABLE_ROW_TYPES and active_header is not None:
            if header_group == unit.group_key:
                unit = replace(unit, table_header=active_header)
        units.append(unit)

    return units


def _split_sentences(text: str, target: int) -> list[str]:
    """先按句读切句；仍超长的句子再按安全字符边界切分。"""
    pieces: list[str] = []
    buffer = ""
    for char in text:
        buffer += char
        if char in SENTENCE_TERMINATORS:
            stripped = buffer.strip()
            if stripped:
                pieces.append(stripped)
            buffer = ""
    tail = buffer.strip()
    if tail:
        pieces.append(tail)

    result: list[str] = []
    for piece in pieces:
        result.extend(_split_long_piece(piece, target))
    return result


def _split_long_piece(text: str, target: int) -> list[str]:
    if len(text) <= target:
        return [text]
    result: list[str] = []
    remaining = text
    while len(remaining) > target:
        cut = _safe_cut(remaining, target)
        result.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        result.append(remaining)
    return [item for item in result if item]


def _safe_cut(text: str, limit: int) -> int:
    """返回不超过 ``limit`` 且不切断 token 的切点。"""
    for index in range(limit, max(limit // 2, 1) - 1, -1):
        if text[index - 1] in SECONDARY_BREAKS:
            return index
    cut = limit
    if cut < len(text) and text[cut - 1] in _TOKEN_CHARS and text[cut] in _TOKEN_CHARS:
        index = cut
        while index > 1 and text[index - 1] in _TOKEN_CHARS:
            index -= 1
        cut = index if index > 0 else limit
    return cut


def _tail_units(units: Sequence[ChunkUnit], overlap: int) -> list[ChunkUnit]:
    """取上一 chunk 的尾部单位作为重叠上下文；标题不参与重叠。"""
    if overlap <= 0:
        return []
    selected: list[ChunkUnit] = []
    total = 0
    for unit in reversed(units):
        if unit.block_type in {"heading", "caption"}:
            break
        addition = len(unit.text) + 1
        if total + addition > overlap:
            break
        selected.append(unit)
        total += addition
    return list(reversed(selected))


def _draft(index: int, units: Sequence[ChunkUnit]) -> ChunkDraft:
    first = units[0]
    contributing: list[ChunkUnit] = []
    for unit in units:
        if unit.table_header is not None and unit.table_header not in contributing:
            contributing.append(unit.table_header)
        contributing.append(unit)

    block_indexes = [unit.block_index for unit in contributing]
    row_starts = [unit.row_start for unit in contributing if unit.row_start is not None]
    row_ends = [unit.row_end for unit in contributing if unit.row_end is not None]

    text = _join_units(contributing)

    locator: dict[str, Any] = {
        "page_number": first.page_number,
        "sheet_name": first.sheet_name,
        "row_start": min(row_starts) if row_starts else None,
        "row_end": max(row_ends) if row_ends else None,
        "section_title": first.section_title,
        "block_start": min(block_indexes),
        "block_end": max(block_indexes),
    }
    return ChunkDraft(chunk_index=index, text=text, locator=locator)


def _join_units(units: Sequence[ChunkUnit]) -> str:
    """同一来源块内用空格连接，跨块用换行，保持可读且确定。"""
    pieces: list[str] = []
    previous_index: int | None = None
    for unit in units:
        if previous_index is not None:
            pieces.append(" " if unit.block_index == previous_index else "\n")
        pieces.append(unit.text)
        previous_index = unit.block_index
    return "".join(pieces).strip()


__all__ = [
    "CHUNKER_NAME",
    "ChunkDraft",
    "ChunkSourceBlock",
    "ChunkUnit",
    "chunk_blocks",
    "normalize_chunk_text",
]
