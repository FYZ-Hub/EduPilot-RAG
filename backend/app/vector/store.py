"""Chroma 持久化向量库封装。

约束（PRODUCT_SPEC 5.1 / DEMO_DATA_SPEC 6）：
- 只使用 ``PersistentClient``，位置来自 ``CHROMA_PATH``；不单独部署服务
- Collection 名称固定为 ``campus_chunks_v1``
- 显式关闭遥测
- **始终显式传入向量**，并提供禁止调用的占位 Embedding 函数，
  避免 Chroma 使用默认 Embedding 函数而隐式下载其它模型
- metadata 只允许标量；无值字段由调用方直接省略
- 启动或首次使用时校验 collection 名称 / schema 版本 / 维度 / provider 身份；
  不一致时安全失败，绝不静默新建另一套集合或截断向量
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import Settings
from app.core.errors import (
    DOCUMENT_INDEX_FAILED,
    EMBEDDING_DIMENSION_MISMATCH,
    VECTOR_COLLECTION_MISMATCH,
    VECTOR_STORE_UNAVAILABLE,
    ApiError,
)
from app.documents.fingerprint import VECTOR_COLLECTION_NAME, VECTOR_SCHEMA_VERSION
from app.embedding.base import EmbeddingDescriptor

logger = logging.getLogger("app.vector")

_SCALAR_TYPES = (str, int, float, bool)
_SCHEMA_KEYS = (
    "schema_version",
    "embedding_provider",
    "embedding_model",
    "embedding_revision",
    "embedding_dimension",
)
# Chroma 通过该路径实例化遥测组件；指向空实现即彻底关闭遥测
NOOP_TELEMETRY_IMPL = "app.vector.telemetry.NoopTelemetry"


class _ForbiddenEmbeddingFunction:
    """显式占位：任何隐式 Embedding 调用都必须失败，而不是去下载默认模型。"""

    def __call__(self, input):  # noqa: A002 - 与 Chroma 约定签名保持一致
        raise ApiError(
            VECTOR_STORE_UNAVAILABLE, details={"reason": "default_embedding_forbidden"}
        )

    def name(self) -> str:  # pragma: no cover - Chroma 仅在记录配置时读取
        return "campus-rag-forbidden"


@dataclass(frozen=True)
class VectorRecord:
    chunk_id: str
    text: str
    metadata: dict[str, Any]


def sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    """只保留标量且非 None 的字段；出现数组/嵌套对象即安全失败。"""
    cleaned: dict[str, Any] = {}
    for key, value in metadata.items():
        if value is None:
            continue
        if isinstance(value, bool) or isinstance(value, _SCALAR_TYPES):
            cleaned[key] = value
            continue
        raise ApiError(
            DOCUMENT_INDEX_FAILED,
            details={"reason": "non_scalar_metadata", "key": key},
            retryable=False,
        )
    return cleaned


class ChromaVectorStore:
    """``campus_chunks_v1`` 的读写与对账。"""

    def __init__(self, settings: Settings, descriptor: EmbeddingDescriptor):
        self.settings = settings
        self.descriptor = descriptor
        self._client: Any | None = None
        self._collection: Any | None = None

    # -- 连接与校验 -------------------------------------------------------

    @property
    def collection_name(self) -> str:
        return VECTOR_COLLECTION_NAME

    def schema_metadata(self) -> dict[str, Any]:
        return {
            "schema_version": VECTOR_SCHEMA_VERSION,
            "embedding_provider": self.descriptor.provider,
            "embedding_model": self.descriptor.model,
            "embedding_revision": self.descriptor.revision,
            "embedding_dimension": self.descriptor.dimension,
        }

    def _connect(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import chromadb
            from chromadb.config import Settings as ChromaSettings
        except ModuleNotFoundError as error:
            raise ApiError(
                VECTOR_STORE_UNAVAILABLE, details={"reason": "chromadb_not_installed"}
            ) from error

        Path(self.settings.chroma_path).mkdir(parents=True, exist_ok=True)
        try:
            self._client = chromadb.PersistentClient(
                path=self.settings.chroma_path,
                settings=ChromaSettings(
                    anonymized_telemetry=False,
                    allow_reset=False,
                    is_persistent=True,
                    # 显式替换遥测实现：不构造 PostHog 客户端、不发送任何事件
                    chroma_product_telemetry_impl=NOOP_TELEMETRY_IMPL,
                ),
            )
        except Exception as error:  # noqa: BLE001 - 统一收敛为安全错误
            raise ApiError(VECTOR_STORE_UNAVAILABLE, details={"reason": "client_init_failed"}) from error
        return self._client

    def collection(self) -> Any:
        if self._collection is not None:
            return self._collection
        client = self._connect()
        try:
            collection = client.get_or_create_collection(
                name=VECTOR_COLLECTION_NAME,
                metadata=self.schema_metadata(),
                embedding_function=_ForbiddenEmbeddingFunction(),
            )
        except ApiError:
            raise
        except Exception as error:  # noqa: BLE001
            raise ApiError(
                VECTOR_STORE_UNAVAILABLE, details={"reason": "collection_open_failed"}
            ) from error
        self._assert_schema(collection)
        self._collection = collection
        return collection

    def _assert_schema(self, collection: Any) -> None:
        metadata = dict(collection.metadata or {})
        stored = {key: metadata.get(key) for key in _SCHEMA_KEYS}
        expected = self.schema_metadata()
        for key in _SCHEMA_KEYS:
            stored_value = stored[key]
            expected_value = expected[key]
            if stored_value is None:
                continue
            if str(stored_value) != str(expected_value):
                # 维度不一致单独报错，便于定位
                if key == "embedding_dimension":
                    raise ApiError(
                        EMBEDDING_DIMENSION_MISMATCH,
                        details={"expected": expected_value, "actual": stored_value},
                    )
                raise ApiError(
                    VECTOR_COLLECTION_MISMATCH,
                    details={"field": key, "expected": expected_value, "actual": stored_value},
                )

        if collection.count() == 0:
            return
        probe = collection.get(limit=1, include=["embeddings"])
        embeddings = probe.get("embeddings")
        if embeddings is None or len(embeddings) == 0:
            return
        actual_dimension = len(embeddings[0])
        if actual_dimension != self.descriptor.dimension:
            raise ApiError(
                EMBEDDING_DIMENSION_MISMATCH,
                details={"expected": self.descriptor.dimension, "actual": actual_dimension},
            )

    # -- 读写 -------------------------------------------------------------

    def vectors_for_document(self, doc_id: str) -> dict[str, dict[str, Any]]:
        """返回 ``chunk_id -> metadata``；用于对账与恢复时复用向量。"""
        collection = self.collection()
        result = collection.get(where={"doc_id": doc_id}, include=["metadatas"])
        ids = list(result.get("ids") or [])
        metadatas = list(result.get("metadatas") or [])
        return {
            chunk_id: dict(metadata or {})
            for chunk_id, metadata in zip(ids, metadatas)
        }

    def upsert(self, records: Sequence[VectorRecord], embeddings: Sequence[Sequence[float]]) -> int:
        if not records:
            return 0
        if len(records) != len(embeddings):
            raise ApiError(
                DOCUMENT_INDEX_FAILED, details={"reason": "records_embeddings_length_mismatch"}
            )
        for vector in embeddings:
            if len(vector) != self.descriptor.dimension:
                raise ApiError(
                    EMBEDDING_DIMENSION_MISMATCH,
                    details={"expected": self.descriptor.dimension, "actual": len(vector)},
                )

        collection = self.collection()
        try:
            collection.upsert(
                ids=[record.chunk_id for record in records],
                embeddings=[list(vector) for vector in embeddings],
                documents=[record.text for record in records],
                metadatas=[sanitize_metadata(record.metadata) for record in records],
            )
        except ApiError:
            raise
        except Exception as error:  # noqa: BLE001
            raise ApiError(DOCUMENT_INDEX_FAILED, retryable=True) from error
        return len(records)

    def delete_ids(self, chunk_ids: Sequence[str]) -> int:
        if not chunk_ids:
            return 0
        collection = self.collection()
        try:
            collection.delete(ids=list(chunk_ids))
        except Exception as error:  # noqa: BLE001
            raise ApiError(DOCUMENT_INDEX_FAILED, retryable=True) from error
        return len(chunk_ids)

    def delete_document(self, doc_id: str) -> int:
        """只删除该文档的向量，绝不做整库清空。"""
        existing = self.vectors_for_document(doc_id)
        if not existing:
            return 0
        return self.delete_ids(list(existing))

    def count(self) -> int:
        return int(self.collection().count())

    def close(self) -> None:
        """释放客户端引用；Chroma 持久化数据留在磁盘上。"""
        self._collection = None
        self._client = None


__all__ = ["ChromaVectorStore", "VectorRecord", "sanitize_metadata"]
