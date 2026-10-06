"""ApiEmbeddingProvider：OpenAI 兼容 Embedding 接口。

- Base URL、模型、API Key 与超时全部显式配置；缺少任一必要项即明确失败
  （安全、稳定的错误原因，不含任何配置值）。
- **隐私边界**：外发前对文本副本执行确定性个人信息清洗（``app.core.privacy``），
  只发送回答/向量计算所需的最少片段；原始引用、SQLite、Chroma、FTS 内容不被修改。
- 错误与日志不得包含 API Key、Base URL 或原始响应正文，也不得包含清洗前正文；
  请求异常只由 :func:`classify_embedding_error` 依据**异常类型与 HTTP 状态码**附加
  ``api_embedding_*`` 稳定 reason，显式非法响应统一为 ``api_embedding_response_invalid``。
- ``loaded`` 采用与 Reranker 一致的严格语义：只有真正发出请求并成功校验响应结构
  之后才为 true；失败或 close 后恢复 false，绝不因为「填写了配置」而谎报 ready。
- 每个实例**懒加载并复用一个** ``httpx.Client``（复用连接池）；初始化与 ``close`` 均
  线程安全，``close`` 幂等且之后可再次懒加载。空输入 / 缺配置不会创建客户端。
- 基础测试不依赖外部 API（默认使用 FakeEmbeddingProvider）。
"""

from __future__ import annotations

import threading
from collections.abc import Sequence

import httpx

from app.config import Settings
from app.core.errors import (
    DOCUMENT_EMBEDDING_FAILED,
    EMBEDDING_DIMENSION_MISMATCH,
    EMBEDDING_PROVIDER_UNAVAILABLE,
    ApiError,
)
from app.core.http_errors import classify_http_error, prefixed_labels
from app.core.privacy import scrub_texts
from app.embedding.base import EmbeddingProvider, descriptor_for

# ApiEmbeddingProvider 失败原因的**有限稳定标签**（只依据异常类型与状态码分类，跨运行稳定）。
# 与 Rerank 共用 ``app.core.http_errors`` 的分类口径，只保留 ``api_embedding_*`` 前缀。
_API_EMBEDDING_LABELS = prefixed_labels("api_embedding")

API_EMBEDDING_TIMEOUT = _API_EMBEDDING_LABELS["timeout"]
API_EMBEDDING_RATE_LIMITED = _API_EMBEDDING_LABELS["rate_limited"]
API_EMBEDDING_SERVER_ERROR = _API_EMBEDDING_LABELS["server_error"]
API_EMBEDDING_AUTH_ERROR = _API_EMBEDDING_LABELS["auth_error"]
API_EMBEDDING_HTTP_ERROR = _API_EMBEDDING_LABELS["http_error"]
API_EMBEDDING_CONNECT_ERROR = _API_EMBEDDING_LABELS["connect_error"]
API_EMBEDDING_PROTOCOL_ERROR = _API_EMBEDDING_LABELS["protocol_error"]
API_EMBEDDING_TRANSPORT_ERROR = _API_EMBEDDING_LABELS["transport_error"]
API_EMBEDDING_UNAVAILABLE = _API_EMBEDDING_LABELS["unavailable"]
# 显式非法响应（无法解析或结构不符）的统一稳定原因；错误码保持 DOCUMENT_EMBEDDING_FAILED 不变
API_EMBEDDING_RESPONSE_INVALID = "api_embedding_response_invalid"


def classify_embedding_error(error: BaseException) -> str:
    """把 ApiEmbeddingProvider 的请求异常归类为**有限稳定标签**（纯函数，无 I/O）。

    复用共享分类器 :func:`app.core.http_errors.classify_http_error` 的判定顺序
    （timeout → rate_limited → server_error → auth_error → http_error → connect_error →
    protocol_error → transport_error → unavailable），再映射为 ``api_embedding_*`` 标签；
    只依据异常类型与 HTTP 状态码判断，**绝不读取或返回** URL、响应正文、请求正文、
    密钥或异常原文。
    """
    return _API_EMBEDDING_LABELS[classify_http_error(error)]


def _response_invalid() -> ApiError:
    """显式非法响应的安全错误：错误码与 retryable 与原行为一致，仅补充稳定 reason。"""
    return ApiError(
        DOCUMENT_EMBEDDING_FAILED,
        retryable=True,
        details={"reason": API_EMBEDDING_RESPONSE_INVALID},
    )



class ApiEmbeddingProvider(EmbeddingProvider):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.descriptor = descriptor_for(settings)
        self._ready = False
        # 每个实例懒加载并**复用同一个** httpx.Client（复用连接池，避免每次调用重建）；
        # 初始化与 close 都用同一把锁保护，保证线程安全。空输入 / 缺配置不会创建它。
        self._client: httpx.Client | None = None
        self._client_lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        """是否已有一次真实成功的调用；不发起探测请求。"""
        return self._ready

    def close(self) -> None:
        """真正关闭并释放复用客户端；**幂等**，并把 readiness 恢复为 false。

        关闭后再调用会重新懒加载一个新客户端。
        """
        with self._client_lock:
            client = self._client
            self._client = None
            self._ready = False
        if client is not None:
            client.close()

    def _get_client(self) -> httpx.Client:
        """线程安全的懒加载：同一实例只构造一个客户端，之后复用其连接池。"""
        with self._client_lock:
            client = self._client
            if client is None:
                client = httpx.Client(timeout=self.settings.embedding_timeout_seconds)
                self._client = client
            return client

    def _require_config(self) -> None:
        if not (
            self.settings.embedding_base_url
            and self.settings.embedding_model
            and self.settings.embedding_api_key
        ):
            raise ApiError(
                EMBEDDING_PROVIDER_UNAVAILABLE,
                details={"reason": "api_embedding_not_configured"},
            )

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            # 空输入是 no-op：不创建 HTTP Client、不发送请求，
            # 也不改变 readiness —— 空输入既不能伪造成功，也不能清除既有成功证据。
            return []
        try:
            vectors = self._embed(texts)
        except Exception:
            self._ready = False
            raise
        self._ready = True
        return vectors

    def _embed(self, texts: Sequence[str]) -> list[list[float]]:
        self._require_config()
        endpoint = f"{self.settings.embedding_base_url.rstrip('/')}/embeddings"
        headers = {"Content-Type": "application/json"}
        headers["Authorization"] = f"Bearer {self.settings.embedding_api_key}"

        batch_size = max(1, int(self.settings.embedding_batch_size))
        vectors: list[list[float]] = []
        try:
            # 复用实例级客户端（懒加载）：每个 batch 只发一次请求，无任何内部重试
            client = self._get_client()
            for start in range(0, len(texts), batch_size):
                # 只清洗外发副本，绝不修改调用方持有的文本
                batch = scrub_texts(list(texts[start : start + batch_size]))
                response = client.post(
                    endpoint,
                    headers=headers,
                    json={"model": self.settings.embedding_model, "input": batch},
                )
                response.raise_for_status()
                vectors.extend(self._parse(self._decode(response), len(batch)))
        except ApiError:
            raise
        except Exception as error:  # noqa: BLE001 - 网络/状态码异常统一收敛为安全错误
            # 只依据异常类型与状态码归类；绝不读取异常原文 / 请求正文 / 响应正文 / URL / 密钥
            raise ApiError(
                DOCUMENT_EMBEDDING_FAILED,
                retryable=True,
                details={"reason": classify_embedding_error(error)},
            ) from error
        return vectors

    @staticmethod
    def _decode(response: httpx.Response) -> object:
        """解析响应体；无法解析属于「非法返回结构」，不得降级为传输错误。"""
        try:
            return response.json()
        except Exception as error:  # noqa: BLE001
            raise _response_invalid() from error

    def _parse(self, payload: object, expected: int) -> list[list[float]]:
        if not isinstance(payload, dict):
            raise _response_invalid()
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != expected:
            raise _response_invalid()

        vectors: list[list[float]] = []
        for item in data:
            embedding = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(embedding, list) or not embedding:
                raise _response_invalid()
            if len(embedding) != self.descriptor.dimension:
                raise ApiError(
                    EMBEDDING_DIMENSION_MISMATCH,
                    details={"expected": self.descriptor.dimension, "actual": len(embedding)},
                )
            vectors.append([self._to_float(value) for value in embedding])
        return vectors

    @staticmethod
    def _to_float(value: object) -> float:
        """把向量元素转为 ``float``；无法转换的（非数值字符串 / ``None`` / 溢出）属非法响应结构。

        只捕获 :class:`TypeError` / :class:`ValueError` / :class:`OverflowError`，
        统一转为 :func:`_response_invalid`；绝不回显原始元素内容。
        可转换的数值（含可解析的数值字符串）行为保持不变。
        """
        try:
            return float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError, OverflowError) as error:
            raise _response_invalid() from error


__all__ = [
    "API_EMBEDDING_AUTH_ERROR",
    "API_EMBEDDING_CONNECT_ERROR",
    "API_EMBEDDING_HTTP_ERROR",
    "API_EMBEDDING_PROTOCOL_ERROR",
    "API_EMBEDDING_RATE_LIMITED",
    "API_EMBEDDING_RESPONSE_INVALID",
    "API_EMBEDDING_SERVER_ERROR",
    "API_EMBEDDING_TIMEOUT",
    "API_EMBEDDING_TRANSPORT_ERROR",
    "API_EMBEDDING_UNAVAILABLE",
    "ApiEmbeddingProvider",
    "classify_embedding_error",
]
