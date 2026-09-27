"""ApiEmbeddingProvider：OpenAI 兼容 Embedding 接口。

- Base URL、模型、认证与超时全部显式配置；缺少任一必要项即明确失败。
- 错误与日志不得包含 API Key、Base URL 或原始响应正文。
- 基础测试不依赖外部 API（默认使用 FakeEmbeddingProvider）。
"""

from __future__ import annotations

from collections.abc import Sequence

import httpx

from app.config import Settings
from app.core.errors import (
    DOCUMENT_EMBEDDING_FAILED,
    EMBEDDING_DIMENSION_MISMATCH,
    EMBEDDING_PROVIDER_UNAVAILABLE,
    ApiError,
)
from app.embedding.base import EmbeddingProvider, descriptor_for


class ApiEmbeddingProvider(EmbeddingProvider):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.descriptor = descriptor_for(settings)

    @property
    def loaded(self) -> bool:
        return bool(self.settings.embedding_base_url and self.settings.embedding_model)

    def _require_config(self) -> None:
        if not (self.settings.embedding_base_url and self.settings.embedding_model):
            raise ApiError(
                EMBEDDING_PROVIDER_UNAVAILABLE,
                details={"reason": "api_embedding_not_configured"},
            )

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self._require_config()
        endpoint = f"{self.settings.embedding_base_url.rstrip('/')}/embeddings"
        headers = {"Content-Type": "application/json"}
        if self.settings.embedding_api_key:
            headers["Authorization"] = f"Bearer {self.settings.embedding_api_key}"

        batch_size = max(1, int(self.settings.embedding_batch_size))
        vectors: list[list[float]] = []
        try:
            with httpx.Client(timeout=self.settings.embedding_timeout_seconds) as client:
                for start in range(0, len(texts), batch_size):
                    batch = list(texts[start : start + batch_size])
                    response = client.post(
                        endpoint,
                        headers=headers,
                        json={"model": self.settings.embedding_model, "input": batch},
                    )
                    response.raise_for_status()
                    vectors.extend(self._parse(response.json(), len(batch)))
        except ApiError:
            raise
        except Exception as error:  # noqa: BLE001 - 第三方/网络异常统一收敛为安全错误
            raise ApiError(DOCUMENT_EMBEDDING_FAILED, retryable=True) from error
        return vectors

    def _parse(self, payload: object, expected: int) -> list[list[float]]:
        if not isinstance(payload, dict):
            raise ApiError(DOCUMENT_EMBEDDING_FAILED, retryable=True)
        data = payload.get("data")
        if not isinstance(data, list) or len(data) != expected:
            raise ApiError(DOCUMENT_EMBEDDING_FAILED, retryable=True)

        vectors: list[list[float]] = []
        for item in data:
            embedding = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(embedding, list) or not embedding:
                raise ApiError(DOCUMENT_EMBEDDING_FAILED, retryable=True)
            if len(embedding) != self.descriptor.dimension:
                raise ApiError(
                    EMBEDDING_DIMENSION_MISMATCH,
                    details={"expected": self.descriptor.dimension, "actual": len(embedding)},
                )
            vectors.append([float(value) for value in embedding])
        return vectors
