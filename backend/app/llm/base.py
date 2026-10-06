"""LLM Provider 描述符与统一接口（阶段 6；明确不提供本地 LLM）。

统一接口表达三件事：**问题改写**、**受证据约束的结构化生成**、以及
descriptor / provider / model / version / loaded / close 这些生命周期信息。

LLM 是**查询时**能力，不产生任何持久索引：其描述符与指纹
**不得**进入 ``app.documents.fingerprint`` 的 ``pipeline_fingerprint``，
也不得触发 SQLite / Chroma / FTS 重建。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from app import constants
from app.config import Settings
from app.core.hashing import stable_digest
from app.core.privacy import PRIVACY_POLICY_VERSION

FAKE_LLM_MODEL_NAME = "campus-rag-fake-llm"
FAKE_LLM_REVISION = "1.0.0"
API_LLM_REVISION = "api"

# 实现 / 提示词 / 响应结构版本：变化即改变 LLM 指纹（仅用于安全诊断）
LLM_IMPLEMENTATION_VERSION = "1.1.0"
# v6：收紧模型生成阶段的回答/拒答契约——outcome↔reason_code 严格绑定、覆盖规则、
#     引用克制；服务端确定性早退 reason 不再出现在模型规则或示例中。
PROMPT_VERSION = "grounded-answer-v6"
REWRITE_PROMPT_VERSION = "query-rewrite-v1"
# 响应结构版本（v2）：outcome 与 reason_code 严格绑定——answered→null、
# refused→insufficient_evidence、conflict→version_conflict；四种服务端确定性早退
# reason 不属于模型输出契约。变化即改变 LLM 指纹（仅诊断用，不进入文档流水线指纹）。
RESPONSE_SCHEMA_VERSION = "grounded-completion-v2"


@dataclass(frozen=True)
class LLMDescriptor:
    """LLM 身份与契约版本；``fingerprint`` 仅用于安全诊断。"""

    provider: str
    model: str
    revision: str
    implementation_version: str
    prompt_version: str
    response_schema_version: str
    privacy_policy_version: str = PRIVACY_POLICY_VERSION

    def as_dict(self) -> dict[str, object]:
        return {
            "provider": self.provider,
            "model": self.model,
            "revision": self.revision,
            "implementation_version": self.implementation_version,
            "prompt_version": self.prompt_version,
            "response_schema_version": self.response_schema_version,
            "privacy_policy_version": self.privacy_policy_version,
        }

    @property
    def fingerprint(self) -> str:
        return stable_digest(self.as_dict())

    def label(self) -> str:
        """用于日志与诊断的可读标签（不含密钥、不含路径）。"""
        return f"{self.provider}:{self.model}@{self.revision}"


def descriptor_for(settings: Settings) -> LLMDescriptor:
    """从配置推导当前 Provider 描述符；不联网、不加载模型。"""
    if settings.llm_provider == constants.LLM_PROVIDER_FAKE:
        return LLMDescriptor(
            provider=constants.LLM_PROVIDER_FAKE,
            model=FAKE_LLM_MODEL_NAME,
            revision=FAKE_LLM_REVISION,
            implementation_version=LLM_IMPLEMENTATION_VERSION,
            prompt_version=PROMPT_VERSION,
            response_schema_version=RESPONSE_SCHEMA_VERSION,
        )
    return LLMDescriptor(
        provider=constants.LLM_PROVIDER_API,
        model=settings.llm_model or "unset",
        revision=API_LLM_REVISION,
        implementation_version=LLM_IMPLEMENTATION_VERSION,
        prompt_version=PROMPT_VERSION,
        response_schema_version=RESPONSE_SCHEMA_VERSION,
    )


class LLMProvider(ABC):
    """``fake`` 与 ``openai_compatible`` 共用的最小接口。"""

    descriptor: LLMDescriptor

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
        """是否已有**真实成功证据**；用于健康状态，绝不谎报。"""
        return False

    @property
    def configured(self) -> bool:
        """是否具备发起请求所需的最小配置；不联网、不加载模型。"""
        return True

    @abstractmethod
    async def complete(self, *, system: str, user: str) -> str:
        """返回模型的原始文本输出；调用方负责结构化校验。"""

    async def rewrite(self, *, system: str, user: str) -> str:
        """多轮问题改写：默认复用同一次补全调用。"""
        return await self.complete(system=system, user=user)

    async def generate(self, *, system: str, user: str) -> str:
        """受证据约束的结构化生成：默认复用同一次补全调用。"""
        return await self.complete(system=system, user=user)

    def close(self) -> None:
        """释放资源；可重复调用。"""

    async def aclose(self) -> None:
        """异步释放；可重复调用。"""
        self.close()


__all__ = [
    "API_LLM_REVISION",
    "FAKE_LLM_MODEL_NAME",
    "FAKE_LLM_REVISION",
    "LLM_IMPLEMENTATION_VERSION",
    "LLMDescriptor",
    "LLMProvider",
    "PROMPT_VERSION",
    "RESPONSE_SCHEMA_VERSION",
    "REWRITE_PROMPT_VERSION",
    "descriptor_for",
]
