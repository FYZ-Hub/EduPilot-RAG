"""Reranker Provider 描述符与统一接口。

描述符是「provider / model / revision / 实现版本 / 分数语义 / 最大输入长度」的
单一事实来源：安全诊断与指纹计算只能引用它，不得在别处重复拼装这些字段。

Reranker 是**查询时**能力，不产生任何持久索引：该描述符与指纹
**不得**进入 ``app.documents.fingerprint`` 的 ``pipeline_fingerprint``，
也不得触发任何 SQLite / Chroma / FTS 重建。

Provider 的输入只有 ``query`` 与**按稳定顺序排列**的候选文本；
输出必须是与输入一一对应的有限浮点分数。Provider 不得生成或重建
``chunk_id``、引用、locator 或任何业务元数据。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass

from app import constants
from app.config import Settings
from app.core.hashing import stable_digest
from app.core.privacy import PRIVACY_POLICY_VERSION

FAKE_RERANK_MODEL_NAME = "campus-rag-fake-reranker"
FAKE_RERANK_REVISION = "1.0.0"
# BAAI/bge-reranker-v2-m3 的固定快照（Hugging Face commit，2024-06-24，Apache-2.0）
LOCAL_RERANK_MODEL_REVISION = "953dc6f6f85a1b2dbfca4c34a2796e7dde08d41e"
API_RERANK_REVISION = "api"

# 实现/结构版本与分数语义：变化即改变 reranker fingerprint（仅诊断用途）
# 1.2.0：候选文本改为「原正文 + 一行白名单 JSON 元数据」（见 search.reranking）。
# 1.3.0：ApiReranker 改为按实例懒加载并复用单个 httpx.Client（连接池复用）；
#        payload / headers / timeout / 标签 / 分数语义不变。
# 1.4.0 / 1.4.1：曾尝试「重排后精确课程代码硬分区」；实测收益为负（期望 locator 新增 3、丢失 3、
#        净值 0，且正式 support 下降），已撤回。
# 1.4.2：撤回重排后的课程代码硬覆盖，恢复严格按外部 Reranker 排序取前 K 条；课程代码仍通过
#        候选白名单元数据（format_rerank_candidate）交由 Reranker 结合完整问题自行判断相关性。
# 1.5.0：仅把最终上下文硬上限与默认值 6→10（证据覆盖更完整）；外部排序、候选数（20）、
#        Provider 调用次数与分数语义均不变。
RERANK_IMPLEMENTATION_VERSION = "1.5.0"
RERANK_SCORE_KIND_FAKE = "fake-deterministic"
RERANK_SCORE_KIND_LOCAL = "crossencoder-sigmoid"
RERANK_SCORE_KIND_API = "api-relevance-score"
# 固定、有限的输入长度上限与截断策略（进入描述符）
RERANK_MAX_LENGTH = 1024


@dataclass(frozen=True)
class RerankDescriptor:
    """Reranker 身份与分数语义；``fingerprint`` 仅用于安全诊断。

    ``privacy_policy_version`` 记录外发前的个人信息清洗策略版本。
    该指纹**绝不进入** ``pipeline_fingerprint``，因此改动它不会触发任何文档重建。
    """

    provider: str
    model: str
    revision: str
    implementation_version: str
    score_kind: str
    max_length: int
    privacy_policy_version: str = PRIVACY_POLICY_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "revision": self.revision,
            "implementation_version": self.implementation_version,
            "score_kind": self.score_kind,
            "max_length": self.max_length,
            "privacy_policy_version": self.privacy_policy_version,
        }

    @property
    def fingerprint(self) -> str:
        return stable_digest(self.as_dict())

    def label(self) -> str:
        """用于日志与诊断的可读标签（不含密钥、不含路径）。"""
        return f"{self.provider}:{self.model}@{self.revision}"


def descriptor_for(settings: Settings) -> RerankDescriptor:
    """从配置推导当前 Provider 描述符；不加载模型、不访问网络。"""
    provider = settings.rerank_provider
    if provider == constants.RERANK_PROVIDER_FAKE:
        return RerankDescriptor(
            provider=constants.RERANK_PROVIDER_FAKE,
            model=FAKE_RERANK_MODEL_NAME,
            revision=FAKE_RERANK_REVISION,
            implementation_version=RERANK_IMPLEMENTATION_VERSION,
            score_kind=RERANK_SCORE_KIND_FAKE,
            max_length=RERANK_MAX_LENGTH,
        )
    if provider == constants.RERANK_PROVIDER_LOCAL:
        return RerankDescriptor(
            provider=constants.RERANK_PROVIDER_LOCAL,
            model=settings.rerank_model,
            revision=settings.rerank_revision or LOCAL_RERANK_MODEL_REVISION,
            implementation_version=RERANK_IMPLEMENTATION_VERSION,
            score_kind=RERANK_SCORE_KIND_LOCAL,
            max_length=RERANK_MAX_LENGTH,
        )
    return RerankDescriptor(
        provider=constants.RERANK_PROVIDER_API,
        model=settings.rerank_model,
        revision=settings.rerank_revision or API_RERANK_REVISION,
        implementation_version=RERANK_IMPLEMENTATION_VERSION,
        score_kind=RERANK_SCORE_KIND_API,
        max_length=RERANK_MAX_LENGTH,
    )


class RerankProvider(ABC):
    """``fake``、``local``、``api`` 共用的最小接口。"""

    descriptor: RerankDescriptor

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
    def score_kind(self) -> str:
        return self.descriptor.score_kind

    @property
    def fingerprint(self) -> str:
        return self.descriptor.fingerprint

    @property
    def loaded(self) -> bool:
        """模型/远端客户端是否已**实际**就绪；用于健康状态，绝不谎报。"""
        return False

    @abstractmethod
    def rerank(self, query: str, candidates: Sequence[str]) -> list[float]:
        """按输入顺序返回与 ``candidates`` 一一对应的有限浮点分数。"""

    def close(self) -> None:
        """释放模型或网络资源；可重复调用。"""

    def release(self) -> None:
        """``close`` 的别名，便于表达“使用窗口结束后释放”。"""
        self.close()


__all__ = [
    "API_RERANK_REVISION",
    "FAKE_RERANK_MODEL_NAME",
    "FAKE_RERANK_REVISION",
    "LOCAL_RERANK_MODEL_REVISION",
    "RERANK_IMPLEMENTATION_VERSION",
    "RERANK_MAX_LENGTH",
    "RERANK_SCORE_KIND_API",
    "RERANK_SCORE_KIND_FAKE",
    "RERANK_SCORE_KIND_LOCAL",
    "RerankDescriptor",
    "RerankProvider",
    "descriptor_for",
]
