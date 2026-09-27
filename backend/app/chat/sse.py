"""SSE 帧编码（阶段 6 公共契约）。

只允许四种事件：``token`` / ``citation`` / ``done`` / ``error``。
标准帧：``event: <name>\\ndata: <单行JSON>\\n\\n``，UTF-8 编码。
JSON 使用安全序列化并强制单行，正文中的换行不会破坏 framing。
保活（若实现）只能使用 SSE comment（``: keep-alive``），不得新增事件名。
"""

from __future__ import annotations

import json
from typing import Any

TOKEN_EVENT = "token"
CITATION_EVENT = "citation"
DONE_EVENT = "done"
ERROR_EVENT = "error"
SSE_EVENTS = (TOKEN_EVENT, CITATION_EVENT, DONE_EVENT, ERROR_EVENT)

# 正文切分粒度（字符）；确定性、覆盖完整、不丢字
TOKEN_CHUNK_CHARS = 24

# Product Spec 6.3 规定的引用字段（严格顺序，不含分数、路径或诊断）
CITATION_FIELDS = (
    "citation_index",
    "chunk_id",
    "doc_id",
    "file_name",
    "document_version",
    "effective_from",
    "dataset_version",
    "page_number",
    "sheet_name",
    "row_start",
    "row_end",
    "section_title",
    "quote",
)


def encode_event(name: str, payload: dict[str, Any]) -> bytes:
    """编码一个 SSE 帧；``data`` 始终是单行 JSON。"""
    if name not in SSE_EVENTS:  # pragma: no cover - 契约守卫
        raise ValueError(f"unsupported sse event: {name}")
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    data = data.replace("\r", "\\r").replace("\n", "\\n")
    return f"event: {name}\ndata: {data}\n\n".encode("utf-8")


def iter_answer_tokens(answer: str) -> list[str]:
    """把回答按固定字符粒度切分为稳定、完整覆盖的 token 序列。"""
    text = answer or ""
    return [
        text[start : start + TOKEN_CHUNK_CHARS]
        for start in range(0, len(text), TOKEN_CHUNK_CHARS)
    ]


__all__ = [
    "CITATION_EVENT",
    "CITATION_FIELDS",
    "DONE_EVENT",
    "ERROR_EVENT",
    "SSE_EVENTS",
    "TOKEN_CHUNK_CHARS",
    "TOKEN_EVENT",
    "encode_event",
    "iter_answer_tokens",
]
