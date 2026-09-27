"""Embedding Provider 描述符与统一接口。

描述符是「provider / model / revision / dimension」的单一事实来源：
指纹计算、Chroma collection 校验、chunk 元数据都只能引用它，
不得在别处重复拼装这些字段。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from app import constants
from app.config import Settings
from app.core.hashing import stable_digest

FAKE_MODEL_NAME = "campus-rag-fake-embedding"
FAKE_REVISION = "1.0.0"
# BAAI/bge-m3 的固定快照（Hugging Face commit）：绝不跟随可变的 main 分支
LOCAL_MODEL_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"
API_REVISION = "api"


@dataclass(frozen=True)
class EmbeddingDescriptor:
    """Embedding 身份；``fingerprint`` 同时用作 ``embedding_fingerprint``。"""

    provider: str
    model: str
    revision: str
    dimension: int

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "revision": self.revision,
            "dimension": self.dimension,
        }

    @property
    def fingerprint(self) -> str:
        return stable_digest(self.as_dict())

    def label(self) -> str:
        """用于日志与集合校验的可读标签（不含密钥、不含路径）。"""
        return f"{self.provider}:{self.model}@{self.revision}:{self.dimension}"


def descriptor_for(settings: Settings) -> EmbeddingDescriptor:
    """从配置推导当前 Provider 描述符；不加载模型、不访问网络。"""
    provider = settings.embedding_provider
    if provider == constants.EMBEDDING_PROVIDER_FAKE:
        return EmbeddingDescriptor(
            provider=constants.EMBEDDING_PROVIDER_FAKE,
            model=FAKE_MODEL_NAME,
            revision=FAKE_REVISION,
            dimension=constants.EMBEDDING_DIMENSION,
        )
    if provider == constants.EMBEDDING_PROVIDER_LOCAL:
        return EmbeddingDescriptor(
            provider=provider,
            model=settings.embedding_model,
            revision=settings.embedding_revision or LOCAL_MODEL_REVISION,
            dimension=settings.embedding_dimension,
        )
    return EmbeddingDescriptor(
        provider=constants.EMBEDDING_PROVIDER_API,
        model=settings.embedding_model,
        revision=settings.embedding_revision or API_REVISION,
        dimension=settings.embedding_dimension,
    )


class EmbeddingProvider(ABC):
    """``local``、``api``、``fake`` 共用的最小接口。"""

    descriptor: EmbeddingDescriptor

    @property
    def dimension(self) -> int:
        return self.descriptor.dimension

    @property
    def provider_name(self) -> str:
        return self.descriptor.provider

    @property
    def model_name(self) -> str:
        return self.descriptor.model

    @property
    def revision(self) -> str:
        return self.descriptor.revision

    @property
    def fingerprint(self) -> str:
        return self.descriptor.fingerprint

    @property
    def loaded(self) -> bool:
        """模型/远端客户端是否已实际就绪；用于健康状态，绝不谎报。"""
        return False

    @abstractmethod
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """按输入顺序返回向量；实现必须自行保证长度等于 ``dimension``。"""

    def close(self) -> None:
        """释放模型或网络资源；可重复调用。"""

    def release(self) -> None:
        """``close`` 的别名，便于表达“阶段结束后释放”。"""
        self.close()
