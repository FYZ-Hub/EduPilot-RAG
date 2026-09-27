"""Chat SSE 路由（阶段 6）。

``POST /api/chat/stream`` 返回 ``text/event-stream``。开流前的已知错误（请求 schema、
Provider 未配置、Fake 在生产环境被禁用）走统一 JSON 4xx/5xx；响应头一旦发出，
流生成器自行收敛为唯一 SSE ``error``。

``request_id`` 必须在**创建 StreamingResponse 之前**从 ``request.state`` 读取并显式
传入流生成器：``RequestContextMiddleware`` 会在 ``call_next`` 返回后重置 ContextVar，
流生命周期内不得再依赖 ``get_request_id()``。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.responses import StreamingResponse

from app.api.deps import AppContext, get_context
from app.chat.schemas import ChatRequest
from app.chat.service import ChatRuntime, ChatStreamRunner, ChatTurn
from app.core.errors import LLM_PROVIDER_UNAVAILABLE, ApiError
from app.search.types import RetrievalFilters

router = APIRouter(tags=["chat"])

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    # 明确禁止代理/浏览器缓存与内容嗅探
    "X-Content-Type-Options": "nosniff",
}


@router.post("/chat/stream")
async def chat_stream(request: Request, payload: ChatRequest, context: AppContext = Depends(get_context)) -> StreamingResponse:
    """建立 SSE 问答流；检索在流内以线程池 + 短 Session 执行。"""
    # 开流前检查：Provider 配置不完整时直接返回统一 JSON，而不是开流后再报错
    if not context.llm.configured:
        raise ApiError(LLM_PROVIDER_UNAVAILABLE, details={"reason": "llm_not_configured"})

    turn = ChatTurn(
        request_id=str(getattr(request.state, "request_id", "") or "-"),
        question=payload.question,
        history=tuple(payload.history),
        filters=RetrievalFilters(
            major=payload.filters.major,
            grade_year=payload.filters.grade_year,
            semester=payload.filters.semester,
            doc_category=payload.filters.doc_category,
        ),
    )
    runtime = ChatRuntime(
        settings=context.settings,
        session_factory=context.session_factory,
        vectors=context.vectors,
        embeddings=context.embeddings,
        reranker=context.reranker,
        coordinator=context.coordinator,
        llm=context.llm,
    )
    runner = ChatStreamRunner(runtime, turn)
    return StreamingResponse(
        runner.stream(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


__all__ = ["SSE_HEADERS", "router"]
