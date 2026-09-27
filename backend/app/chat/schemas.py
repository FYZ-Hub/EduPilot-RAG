"""``POST /api/chat/stream`` 的请求契约（PRODUCT_SPEC 6.3）。

严格验证：条数 1–10、role 只允许 user/assistant、最后一条必须是非空 user、
单条 ≤ 4000 字符、总计 ≤ 12000 字符、filters 字段类型固定、
messages / message / filters 一律 ``extra="forbid"``（禁止未知字段）。

校验失败由全局 handler 统一转为 ``{code,message,details,request_id}`` 的 422 JSON，
``details`` 只包含字段位置与错误类型，**不回显用户原文**。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MIN_MESSAGES = 1
MAX_MESSAGES = 10
MAX_MESSAGE_CHARS = 4000
MAX_TOTAL_CHARS = 12000


class ChatMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(max_length=MAX_MESSAGE_CHARS)


class ChatFilters(BaseModel):
    """允许的过滤字段固定；未知字段必须拒绝，模型不得增删过滤条件。"""

    model_config = ConfigDict(extra="forbid")

    major: str | None = None
    grade_year: int | None = Field(default=None, strict=True)
    semester: str | None = None
    doc_category: str | None = None


class ChatRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[ChatMessage] = Field(min_length=MIN_MESSAGES, max_length=MAX_MESSAGES)
    filters: ChatFilters = Field(default_factory=ChatFilters)

    @model_validator(mode="after")
    def _validate_conversation(self) -> "ChatRequest":
        last = self.messages[-1]
        if last.role != "user":
            raise ValueError("last message must come from the user")
        if not last.content.strip():
            raise ValueError("last user message must not be empty")
        total = sum(len(message.content) for message in self.messages)
        if total > MAX_TOTAL_CHARS:
            raise ValueError("total message content is too long")
        return self

    @property
    def question(self) -> str:
        return self.messages[-1].content.strip()

    @property
    def history(self) -> list[tuple[str, str]]:
        return [(message.role, message.content) for message in self.messages[:-1]]


__all__ = [
    "MAX_MESSAGES",
    "MAX_MESSAGE_CHARS",
    "MAX_TOTAL_CHARS",
    "MIN_MESSAGES",
    "ChatFilters",
    "ChatMessage",
    "ChatRequest",
]
