"""共享的纯 HTTP 异常分类器（Rerank / Embedding 复用）。

只依据异常类型与 HTTP 状态码把请求异常归为有限稳定**类别**；纯函数、无 I/O，
绝不读取或返回 URL、响应正文、请求正文、密钥或异常原文。
调用方再把类别映射为各自稳定的 ``reason`` 标签（如 ``api_rerank_*`` / ``api_embedding_*``），
因此同一份判定顺序在多个 Provider 之间保持一致，标签前缀却互不干扰。

判定顺序（由具体到宽泛）：

1. :class:`httpx.TimeoutException` → ``timeout``；
2. :class:`httpx.HTTPStatusError` 按状态码：429 → ``rate_limited``，
   5xx → ``server_error``，401/403 → ``auth_error``，其余 → ``http_error``；
3. :class:`httpx.ConnectError` → ``connect_error``；
4. :class:`httpx.RemoteProtocolError` → ``protocol_error``；
5. 其它 :class:`httpx.RequestError` → ``transport_error``；
6. 其它任何异常 → ``unavailable``。
"""

from __future__ import annotations

import httpx

CATEGORY_TIMEOUT = "timeout"
CATEGORY_RATE_LIMITED = "rate_limited"
CATEGORY_SERVER_ERROR = "server_error"
CATEGORY_AUTH_ERROR = "auth_error"
CATEGORY_HTTP_ERROR = "http_error"
CATEGORY_CONNECT_ERROR = "connect_error"
CATEGORY_PROTOCOL_ERROR = "protocol_error"
CATEGORY_TRANSPORT_ERROR = "transport_error"
CATEGORY_UNAVAILABLE = "unavailable"

# 固定顺序，供前缀标签映射与测试遍历；分类结果只可能是其中之一。
HTTP_ERROR_CATEGORIES = (
    CATEGORY_TIMEOUT,
    CATEGORY_RATE_LIMITED,
    CATEGORY_SERVER_ERROR,
    CATEGORY_AUTH_ERROR,
    CATEGORY_HTTP_ERROR,
    CATEGORY_CONNECT_ERROR,
    CATEGORY_PROTOCOL_ERROR,
    CATEGORY_TRANSPORT_ERROR,
    CATEGORY_UNAVAILABLE,
)


def classify_http_error(error: BaseException) -> str:
    """把请求异常归类为**有限稳定类别**（纯函数，无 I/O）。

    只依据异常类型与 HTTP 状态码判断；**绝不读取或返回** URL、响应正文、请求正文、
    密钥或异常原文。返回值必属于 :data:`HTTP_ERROR_CATEGORIES`。
    """
    if isinstance(error, httpx.TimeoutException):
        return CATEGORY_TIMEOUT
    if isinstance(error, httpx.HTTPStatusError):
        status = getattr(getattr(error, "response", None), "status_code", None)
        if status == 429:
            return CATEGORY_RATE_LIMITED
        if isinstance(status, int) and 500 <= status <= 599:
            return CATEGORY_SERVER_ERROR
        if status in (401, 403):
            return CATEGORY_AUTH_ERROR
        return CATEGORY_HTTP_ERROR
    if isinstance(error, httpx.ConnectError):
        return CATEGORY_CONNECT_ERROR
    if isinstance(error, httpx.RemoteProtocolError):
        return CATEGORY_PROTOCOL_ERROR
    if isinstance(error, httpx.RequestError):
        return CATEGORY_TRANSPORT_ERROR
    return CATEGORY_UNAVAILABLE


def prefixed_labels(prefix: str) -> dict[str, str]:
    """把每个类别映射为 ``{prefix}_{category}`` 形式的稳定标签。

    Rerank 用 ``prefix="api_rerank"``、Embedding 用 ``prefix="api_embedding"``，
    从而复用同一分类口径而各自保持稳定标签。
    """
    return {category: f"{prefix}_{category}" for category in HTTP_ERROR_CATEGORIES}


__all__ = [
    "CATEGORY_AUTH_ERROR",
    "CATEGORY_CONNECT_ERROR",
    "CATEGORY_HTTP_ERROR",
    "CATEGORY_PROTOCOL_ERROR",
    "CATEGORY_RATE_LIMITED",
    "CATEGORY_SERVER_ERROR",
    "CATEGORY_TIMEOUT",
    "CATEGORY_TRANSPORT_ERROR",
    "CATEGORY_UNAVAILABLE",
    "HTTP_ERROR_CATEGORIES",
    "classify_http_error",
    "prefixed_labels",
]
