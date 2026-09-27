"""阶段 6 聊天编排（请求契约、冲突检测、结果校验、SSE 编码与服务）。"""

from app.chat.conflict import ConflictHint, detect_conflicts
from app.chat.grounding import GroundedCompletion, parse_grounded_completion, remap_citations
from app.chat.schemas import ChatFilters, ChatMessage, ChatRequest
from app.chat.service import ChatRuntime, ChatStreamRunner, ChatTurn, citation_payload

__all__ = [
    "ChatFilters",
    "ChatMessage",
    "ChatRequest",
    "ChatRuntime",
    "ChatStreamRunner",
    "ChatTurn",
    "ConflictHint",
    "GroundedCompletion",
    "citation_payload",
    "detect_conflicts",
    "parse_grounded_completion",
    "remap_citations",
]
