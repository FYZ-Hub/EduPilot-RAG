"""9B 评测专用 Provider 审计包装器与 Judge 调用适配器。

设计要点：

- **透明委托**：包装器不改变 ``app/**`` 中 Provider 的行为，``descriptor`` / ``loaded`` /
  ``close`` / ``release`` 等接口语义原样透传（未显式覆盖的属性经 ``__getattr__`` 落到原对象）。
- **真实调用审计**：只对**真实发起**的调用累加 ``calls/ok/failed``；空 Embedding 输入、
  空 Rerank 候选**不计次、不算失败**，也不下探底层。
- **失败粘滞**：任意一次真实失败后状态永久为 ``VERIFICATION_FAILED``（复用 metrics 的四态机）。
- **LLM 与 Judge 分账**：同一个底层 Provider 被 LLM 与 Judge 复用时，分别计入 ``llm`` 与
  ``judge``，绝不重复计数。
- **Judge 适配器**：Prompt 由 ``build_judge_request`` 生成，**单次**调用 ``LLMProvider.generate``
  （零重试），输出交 ``parse_judge_output`` 严格校验；契约错误保持 ``JudgeContractError``，
  传输错误转为**已清洗**的 ``JudgeTransportError``（只带异常类名，不含正文/URL/密钥）。

本模块**不发起**任何网络请求，也不读写 ``.env``。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Coroutine

from app.core.errors import ApiError

from eval_tools.qa.judge import (
    JudgeContractError,
    JudgeVerdict,
    build_judge_request,
    parse_judge_output,
)
from eval_tools.qa.metrics import (
    PROVIDER_NAMES,
    ProviderAudit,
    new_provider_audit,
    normalize_api_failure_reason,
    normalize_exception_failure_reason,
    record_provider_call,
    rerank_should_call,
)

if TYPE_CHECKING:  # 仅类型标注，避免运行时耦合
    from eval_tools.qa.executor import JudgeRequest

PROVIDER_EMBEDDING = "embedding"
PROVIDER_RERANK = "rerank"
PROVIDER_LLM = "llm"
PROVIDER_JUDGE = "judge"


def provider_failure_reason(error: BaseException) -> str:
    """把 Provider 调用的失败异常归一为**安全**原因标签（纯函数，无 I/O）。

    - ``ApiError``：只取 ``details.reason`` 中符合**稳定小写标签**规则的值，非法归 ``unknown``；
    - 其它异常：只取**安全异常类名**（如 ``RuntimeError``），**绝不**使用 ``str(error)``。

    任何情况下都不返回异常正文、URL、请求/响应正文或密钥。
    """
    if isinstance(error, ApiError):
        raw = error.details.get("reason") if isinstance(error.details, dict) else None
        return normalize_api_failure_reason(raw)
    return normalize_exception_failure_reason(type(error).__name__)


class JudgeTransportError(RuntimeError):
    """Judge 传输层失败（已清洗）。

    ``reason`` 只保留异常类名等稳定标识，**不含**响应正文、URL、密钥或异常原文。
    与 ``JudgeContractError`` 保持可区分：前者表示「没拿到合法响应」，后者表示
    「拿到了响应但契约不合法」。
    """

    def __init__(self, reason: str = "transport_error"):
        super().__init__(reason)
        self.reason = reason


class ProviderMeter:
    """单个 Provider 的 ``calls/ok/failed`` 审计；状态机复用 metrics 的四态实现。"""

    def __init__(self, name: str, *, configured: bool = True):
        if name not in PROVIDER_NAMES:
            raise ValueError(f"未登记的 Provider 名称：{name}")
        self._audit = new_provider_audit(name, configured=configured)

    @classmethod
    def for_provider(cls, name: str, provider: object) -> "ProviderMeter":
        """按底层 Provider 的 ``configured`` 属性初始化（缺省视为已配置）。"""
        return cls(name, configured=bool(getattr(provider, "configured", True)))

    @property
    def name(self) -> str:
        return self._audit.name

    @property
    def state(self) -> str:
        return self._audit.state

    @property
    def calls(self) -> int:
        return self._audit.calls

    @property
    def ok(self) -> int:
        return self._audit.ok

    @property
    def failed(self) -> int:
        return self._audit.failed

    @property
    def verified(self) -> bool:
        return self._audit.verified

    @property
    def audit(self) -> ProviderAudit:
        return self._audit

    def record(self, *, succeeded: bool, reason: str | None = None) -> ProviderAudit:
        self._audit = record_provider_call(self._audit, succeeded=succeeded, reason=reason)
        return self._audit


class AuditRegistry:
    """四类 Provider 的审计集合，供报告层直接接线。

    **必须按真实配置初始化**：默认全部为 ``UNCONFIGURED``（缺配置不得假装已配置）。
    Judge 与 LLM 共享同一底层 Provider，故 ``judge`` 缺省沿用 ``llm`` 的配置状态。
    """

    def __init__(
        self,
        *,
        embedding: bool = False,
        rerank: bool = False,
        llm: bool = False,
        judge: bool | None = None,
    ) -> None:
        judge_configured = llm if judge is None else judge
        self.embedding = ProviderMeter(PROVIDER_EMBEDDING, configured=bool(embedding))
        self.rerank = ProviderMeter(PROVIDER_RERANK, configured=bool(rerank))
        self.llm = ProviderMeter(PROVIDER_LLM, configured=bool(llm))
        self.judge = ProviderMeter(PROVIDER_JUDGE, configured=bool(judge_configured))

    @property
    def meters(self) -> tuple[ProviderMeter, ...]:
        return (self.embedding, self.rerank, self.llm, self.judge)

    def snapshots(self) -> list[ProviderAudit]:
        """按 ``PROVIDER_NAMES`` 顺序返回快照，可直接交给门禁函数。"""
        by_name = {meter.name: meter.audit for meter in self.meters}
        return [by_name[name] for name in PROVIDER_NAMES]


class _Transparent:
    """透明委托基类：只覆盖需要计数的调用，其余属性全部落到原 Provider。"""

    def __init__(self, provider: object, meter: ProviderMeter):
        self._provider = provider
        self._meter = meter

    @property
    def provider(self) -> object:
        return self._provider

    @property
    def meter(self) -> ProviderMeter:
        return self._meter

    @property
    def descriptor(self) -> Any:
        return self._provider.descriptor  # type: ignore[attr-defined]

    @property
    def loaded(self) -> bool:
        return bool(self._provider.loaded)  # type: ignore[attr-defined]

    def close(self) -> None:
        self._provider.close()  # type: ignore[attr-defined]

    def __getattr__(self, item: str) -> Any:
        # 未显式覆盖的属性透传；下划线名称不透传，避免内部属性被误解析
        if item.startswith("_"):
            raise AttributeError(item)
        return getattr(self._provider, item)


class AuditedEmbedding(_Transparent):
    """Embedding 审计包装器：空输入不计次、不下探。"""

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        try:
            vectors = self._provider.embed_documents(texts)  # type: ignore[attr-defined]
        except Exception as error:
            self._meter.record(succeeded=False, reason=provider_failure_reason(error))
            raise
        self._meter.record(succeeded=True)
        return vectors


class AuditedReranker(_Transparent):
    """Rerank 审计包装器：空候选不计次、不算降级、不下探。"""

    def rerank(self, query: str, candidates: Sequence[str]) -> list[float]:
        if not rerank_should_call(len(candidates)):
            return []
        try:
            scores = self._provider.rerank(query, candidates)  # type: ignore[attr-defined]
        except Exception as error:
            self._meter.record(succeeded=False, reason=provider_failure_reason(error))
            raise
        self._meter.record(succeeded=True)
        return scores


class AuditedLLM(_Transparent):
    """LLM 审计包装器：``complete`` / ``generate`` / ``rewrite`` 各自计一次。

    每个方法都直接委托**底层 Provider 的同名方法**，绝不调用同包装器内的兄弟方法，
    因此底层的 ``generate → complete`` 委托只产生一次底层调用、也只计一次。
    """

    async def complete(self, *, system: str, user: str) -> str:
        return await self._counted(self._provider.complete, system=system, user=user)

    async def generate(self, *, system: str, user: str) -> str:
        return await self._counted(self._provider.generate, system=system, user=user)

    async def rewrite(self, *, system: str, user: str) -> str:
        return await self._counted(self._provider.rewrite, system=system, user=user)

    async def _counted(self, call: Callable[..., Coroutine[Any, Any, str]], **kwargs: str) -> str:
        try:
            text = await call(**kwargs)
        except Exception as error:
            self._meter.record(succeeded=False, reason=provider_failure_reason(error))
            raise
        self._meter.record(succeeded=True)
        return text

    async def aclose(self) -> None:
        closer = getattr(self._provider, "aclose", None)
        if closer is None:
            self._provider.close()  # type: ignore[attr-defined]
            return
        await closer()


class JudgeAdapter:
    """把现有 ``LLMProvider`` 适配为 executor 的 ``CitationJudge``（同步可调用）。

    - 单次调用 ``provider.generate``，**零重试**；
    - 契约错误 → ``JudgeContractError``；传输错误 → 清洗后的 ``JudgeTransportError``；
    - 该次调用只计入 ``judge`` 审计，**不**计入 ``llm``。
    """

    def __init__(
        self,
        provider: object,
        *,
        meter: ProviderMeter | None = None,
        runner: Callable[[Coroutine[Any, Any, JudgeVerdict]], JudgeVerdict] | None = None,
    ):
        self._provider = provider
        self._meter = meter if meter is not None else ProviderMeter(PROVIDER_JUDGE)
        self._runner = runner if runner is not None else asyncio.run

    @property
    def provider(self) -> object:
        return self._provider

    @property
    def meter(self) -> ProviderMeter:
        return self._meter

    async def ajudge(self, request: "JudgeRequest") -> JudgeVerdict:
        system, user = build_judge_request(
            question=request.question,
            facts=list(request.facts),
            answer=request.answer,
            evidence=list(request.evidence),
        )
        try:
            raw = await self._provider.generate(system=system, user=user)  # type: ignore[attr-defined]
        except JudgeContractError as error:
            self._meter.record(succeeded=False, reason=provider_failure_reason(error))
            raise
        except Exception as error:  # noqa: BLE001 - 只保留异常类名，绝不回显原文
            self._meter.record(succeeded=False, reason=provider_failure_reason(error))
            raise JudgeTransportError(type(error).__name__) from None

        try:
            verdict = parse_judge_output(
                raw, fact_count=len(request.facts), evidence_count=len(request.evidence)
            )
        except JudgeContractError as error:
            self._meter.record(succeeded=False, reason=provider_failure_reason(error))
            raise
        self._meter.record(succeeded=True)
        return verdict

    def __call__(self, request: "JudgeRequest") -> JudgeVerdict:
        return self._runner(self.ajudge(request))


__all__ = [
    "PROVIDER_EMBEDDING",
    "PROVIDER_JUDGE",
    "PROVIDER_LLM",
    "PROVIDER_RERANK",
    "AuditRegistry",
    "AuditedEmbedding",
    "AuditedLLM",
    "AuditedReranker",
    "JudgeAdapter",
    "JudgeTransportError",
    "ProviderMeter",
    "provider_failure_reason",
]
