"""请求 ID 生成与统一错误响应中间件。"""

from __future__ import annotations

import logging
import uuid
from contextvars import ContextVar

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from app.core.errors import INTERNAL_ERROR, ApiError

REQUEST_ID_HEADER = "X-Request-ID"

_logger = logging.getLogger("app.request")

_request_id: ContextVar[str] = ContextVar("request_id", default="")


def new_request_id() -> str:
    return uuid.uuid4().hex


def get_request_id() -> str:
    """返回当前请求 ID；日志与错误体统一使用该值。"""
    return _request_id.get() or "-"


def error_response(error: ApiError, request_id: str) -> JSONResponse:
    return JSONResponse(status_code=int(error.status_code or 500), content=error.to_payload(request_id))


class RequestContextMiddleware(BaseHTTPMiddleware):
    """为每个请求生成 request_id，并把未预期异常收敛为安全错误体。"""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = new_request_id()
        request.state.request_id = request_id
        token = _request_id.set(request_id)
        try:
            response = await call_next(request)
        except ApiError as error:  # 路由内直接抛出时兜底
            response = error_response(error, request_id)
        except Exception:  # noqa: BLE001 - 兜底：绝不向客户端泄漏堆栈
            _logger.exception("unhandled_error request_id=%s path=%s", request_id, request.url.path)
            response = error_response(ApiError(INTERNAL_ERROR), request_id)
        finally:
            _request_id.reset(token)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
