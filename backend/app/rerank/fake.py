"""FakeReranker：完全离线的确定性重排分数。

仅用于测试与显式的开发测试模式：
- 分数只由 ``(query, candidate_text)`` 通过 SHA-256 派生，绝不使用 ``hash()``；
- 相同输入在同一进程、跨进程、不同 ``PYTHONHASHSEED`` 下结果完全一致；
- 逐条调用与批量调用结果一致（每个候选独立打分，与批内位置无关）；
- 不访问网络、不读取模型缓存、不导入 torch。

只用于生产环境之外的显式配置；``production`` / ``prod`` 由工厂拒绝。
"""

from __future__ import annotations

import hashlib

from app import constants
from app.rerank.base import (
    FAKE_RERANK_MODEL_NAME,
    FAKE_RERANK_REVISION,
    RERANK_IMPLEMENTATION_VERSION,
    RERANK_MAX_LENGTH,
    RERANK_SCORE_KIND_FAKE,
    RerankDescriptor,
    RerankProvider,
)

_SCORE_BYTES = 8


class FakeReranker(RerankProvider):
    """用 SHA-256 派生的确定性分数替代真实交叉编码器输出。"""

    def __init__(self) -> None:
        self.descriptor = RerankDescriptor(
            provider=constants.RERANK_PROVIDER_FAKE,
            model=FAKE_RERANK_MODEL_NAME,
            revision=FAKE_RERANK_REVISION,
            implementation_version=RERANK_IMPLEMENTATION_VERSION,
            score_kind=RERANK_SCORE_KIND_FAKE,
            max_length=RERANK_MAX_LENGTH,
        )

    @property
    def loaded(self) -> bool:
        """Fake 始终可用（离线、无外部依赖），因此真实 ready 为 True。"""
        return True

    def rerank(self, query, candidates) -> list[float]:
        return [self._score(query, text or "") for text in candidates]

    def _score(self, query: str, text: str) -> float:
        digest = hashlib.sha256(f"{query}\x00{text}".encode("utf-8")).digest()
        raw = int.from_bytes(digest[:_SCORE_BYTES], "big")
        # 归一化到 [0, 1]，恒为有限浮点数
        return raw / float(0xFFFFFFFFFFFFFFFF)


__all__ = ["FakeReranker"]
