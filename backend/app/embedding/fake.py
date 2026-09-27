"""FakeEmbeddingProvider：确定性伪向量。

仅用于测试与显式的开发测试模式：
- 固定算法，同一文本永远得到相同向量
- 维度固定 1024
- 不访问网络、不读取模型缓存、不下载模型
"""

from __future__ import annotations

import hashlib
import math

from app import constants
from app.embedding.base import (
    FAKE_MODEL_NAME,
    FAKE_REVISION,
    EmbeddingDescriptor,
    EmbeddingProvider,
)

_BLOCK_SIZE = 32  # SHA-256 摘要字节数
_WORD_SIZE = 4


class FakeEmbeddingProvider(EmbeddingProvider):
    """用 SHA-256 派生的确定性单位向量替代真实模型输出。"""

    def __init__(self, dimension: int = constants.EMBEDDING_DIMENSION):
        self.descriptor = EmbeddingDescriptor(
            provider=constants.EMBEDDING_PROVIDER_FAKE,
            model=FAKE_MODEL_NAME,
            revision=FAKE_REVISION,
            dimension=dimension,
        )

    def embed_documents(self, texts) -> list[list[float]]:
        return [self._vector(text or "") for text in texts]

    def _vector(self, text: str) -> list[float]:
        dimension = self.descriptor.dimension
        seed = hashlib.sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        counter = 0
        while len(values) < dimension:
            block = hashlib.sha256(seed + counter.to_bytes(_WORD_SIZE, "big")).digest()
            for offset in range(0, _BLOCK_SIZE, _WORD_SIZE):
                if len(values) >= dimension:
                    break
                raw = int.from_bytes(block[offset : offset + _WORD_SIZE], "big")
                values.append(raw / 0xFFFFFFFF * 2.0 - 1.0)
            counter += 1
        norm = math.sqrt(sum(value * value for value in values)) or 1.0
        return [value / norm for value in values]
