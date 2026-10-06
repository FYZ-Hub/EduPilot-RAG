"""阶段 9B Provider 审计包装器与 Judge 适配器测试（全程 Fake，无 HTTP、无 .env）。"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.core.errors import ApiError
from eval_tools.qa.executor import JudgeRequest
from eval_tools.qa.judge import JudgeContractError
from eval_tools.qa.metrics import (
    PROVIDER_CONFIGURED_UNVERIFIED,
    PROVIDER_NAMES,
    PROVIDER_UNCONFIGURED,
    PROVIDER_VERIFICATION_FAILED,
    PROVIDER_VERIFIED,
    new_provider_audit,
    providers_allow_formal_metrics,
    record_provider_call,
)
from eval_tools.qa.providers import (
    AuditRegistry,
    AuditedEmbedding,
    AuditedLLM,
    AuditedReranker,
    JudgeAdapter,
    JudgeTransportError,
    ProviderMeter,
    provider_failure_reason,
)

VALID_JUDGE_JSON = json.dumps(
    {
        "fact_results": [
            {
                "fact_index": 1,
                "answer_expresses": True,
                "evidence_supports": True,
                "evidence_indices": [1],
            }
        ],
        "verdict": "supported",
    },
    ensure_ascii=False,
)


def _request(facts: tuple[str, ...] = ("某虚构课程的学分为 3 学分。",)) -> JudgeRequest:
    return JudgeRequest(
        case_id="gt-synth-001",
        question="合成问题：某虚构课程的学分为多少？",
        facts=facts,
        answer="依据资料：[1] 学分为 3 学分。",
        evidence=((1, "合成引用原文", {"page_number": 1}),),
    )


class FakeEmbedding:
    def __init__(self, *, dimension: int = 4, fail_remaining: int = 0):
        self.descriptor = {"provider": "fake-embed", "model": "fake-embed-model"}
        self.dimension = dimension
        self.fail_remaining = fail_remaining
        self.calls = 0
        self.closed = 0
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    def embed_documents(self, texts):
        self.calls += 1
        if self.fail_remaining > 0:
            self.fail_remaining -= 1
            raise RuntimeError("embedding backend unavailable")
        self._loaded = True
        return [[0.25] * self.dimension for _ in texts]

    def close(self) -> None:
        self.closed += 1


class FakeReranker:
    def __init__(self, *, fail_remaining: int = 0):
        self.descriptor = {"provider": "fake-rerank", "model": "fake-rerank-model"}
        self.score_kind = "fake-deterministic"
        self.fail_remaining = fail_remaining
        self.calls = 0
        self.closed = 0
        self._loaded = False

    @property
    def loaded(self) -> bool:
        return self._loaded

    def rerank(self, query, candidates):
        self.calls += 1
        if self.fail_remaining > 0:
            self.fail_remaining -= 1
            raise RuntimeError("rerank backend unavailable")
        self._loaded = True
        return [0.5 for _ in candidates]

    def close(self) -> None:
        self.closed += 1


class FakeLLM:
    def __init__(self, *, outputs=(), fail_remaining: int = 0):
        self.descriptor = {"provider": "fake-llm", "model": "fake-llm-model"}
        self.configured = True
        self._outputs = list(outputs)
        self.fail_remaining = fail_remaining
        self.calls = 0
        self.complete_calls = 0
        self.closed = 0
        self.aclosed = 0

    @property
    def loaded(self) -> bool:
        return self.calls > 0

    async def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        self.complete_calls += 1
        if self.fail_remaining > 0:
            self.fail_remaining -= 1
            raise RuntimeError("HTTP 500 https://api.example.invalid sk-secret-key")
        return self._outputs.pop(0) if self._outputs else "{}"

    async def generate(self, *, system: str, user: str) -> str:
        return await self.complete(system=system, user=user)

    async def rewrite(self, *, system: str, user: str) -> str:
        return await self.complete(system=system, user=user)

    def close(self) -> None:
        self.closed += 1

    async def aclose(self) -> None:
        self.aclosed += 1


# --- 透明委托 ---------------------------------------------------------------


def test_audited_embedding_is_transparent() -> None:
    provider = FakeEmbedding(dimension=6)
    meter = ProviderMeter("embedding")
    wrapped = AuditedEmbedding(provider, meter)

    assert wrapped.descriptor is provider.descriptor
    assert wrapped.loaded is False
    assert wrapped.dimension == 6  # 经 __getattr__ 透传
    assert wrapped.provider is provider
    assert wrapped.meter is meter

    vectors = wrapped.embed_documents(["a", "b"])
    assert vectors == [[0.25] * 6, [0.25] * 6]
    assert wrapped.loaded is True
    assert wrapped.close is not None
    wrapped.close()
    assert provider.closed == 1


def test_audited_reranker_is_transparent() -> None:
    provider = FakeReranker()
    meter = ProviderMeter("rerank")
    wrapped = AuditedReranker(provider, meter)

    assert wrapped.descriptor is provider.descriptor
    assert wrapped.score_kind == "fake-deterministic"
    assert wrapped.rerank("q", ["a", "b"]) == [0.5, 0.5]
    assert wrapped.loaded is True


def test_audited_llm_is_transparent() -> None:
    provider = FakeLLM(outputs=["回答"])
    meter = ProviderMeter("llm")
    wrapped = AuditedLLM(provider, meter)

    assert wrapped.descriptor is provider.descriptor
    assert wrapped.configured is True
    assert asyncio.run(wrapped.complete(system="s", user="u")) == "回答"
    assert wrapped.loaded is True
    wrapped.close()
    asyncio.run(wrapped.aclose())
    assert (provider.closed, provider.aclosed) == (1, 1)


# --- 计数与粘滞失败 ---------------------------------------------------------


def test_audited_embedding_counts_success_and_sticky_failure() -> None:
    provider = FakeEmbedding(fail_remaining=1)
    meter = ProviderMeter("embedding")
    wrapped = AuditedEmbedding(provider, meter)

    assert (meter.calls, meter.ok, meter.failed) == (0, 0, 0)
    assert meter.state == PROVIDER_CONFIGURED_UNVERIFIED

    with pytest.raises(RuntimeError):
        wrapped.embed_documents(["a"])
    assert (meter.calls, meter.ok, meter.failed) == (1, 0, 1)
    assert meter.state == PROVIDER_VERIFICATION_FAILED

    # 失败粘滞：后续成功也不能回到 VERIFIED
    wrapped.embed_documents(["a"])
    assert (meter.calls, meter.ok, meter.failed) == (2, 1, 1)
    assert meter.state == PROVIDER_VERIFICATION_FAILED
    assert meter.verified is False


def test_audited_embedding_empty_input_is_not_counted() -> None:
    provider = FakeEmbedding()
    meter = ProviderMeter("embedding")
    wrapped = AuditedEmbedding(provider, meter)

    assert wrapped.embed_documents([]) == []
    assert (meter.calls, meter.ok, meter.failed) == (0, 0, 0)
    assert meter.state == PROVIDER_CONFIGURED_UNVERIFIED
    assert provider.calls == 0  # 不下探底层


def test_audited_reranker_zero_candidates_is_not_counted() -> None:
    provider = FakeReranker()
    meter = ProviderMeter("rerank")
    wrapped = AuditedReranker(provider, meter)

    assert wrapped.rerank("q", []) == []
    assert (meter.calls, meter.ok, meter.failed) == (0, 0, 0)
    assert provider.calls == 0
    assert meter.state == PROVIDER_CONFIGURED_UNVERIFIED

    assert wrapped.rerank("q", ["a"]) == [0.5]
    assert (meter.calls, meter.ok, meter.failed) == (1, 1, 0)
    assert meter.state == PROVIDER_VERIFIED


def test_audited_reranker_failure_is_sticky() -> None:
    meter = ProviderMeter("rerank")
    wrapped = AuditedReranker(FakeReranker(fail_remaining=1), meter)

    with pytest.raises(RuntimeError):
        wrapped.rerank("q", ["a"])
    assert meter.state == PROVIDER_VERIFICATION_FAILED
    assert (meter.calls, meter.ok, meter.failed) == (1, 0, 1)


def test_audited_llm_via_generate_counts_exactly_once() -> None:
    """底层 ``generate → complete`` 委托只能产生一次底层调用、一次计数。"""
    provider = FakeLLM(outputs=["回答"])
    meter = ProviderMeter("llm")
    wrapped = AuditedLLM(provider, meter)

    assert asyncio.run(wrapped.generate(system="s", user="u")) == "回答"
    assert provider.complete_calls == 1
    assert (meter.calls, meter.ok, meter.failed) == (1, 1, 0)
    assert meter.state == PROVIDER_VERIFIED


def test_audited_llm_failure_is_sticky_and_counts_once() -> None:
    provider = FakeLLM(fail_remaining=1)
    meter = ProviderMeter("llm")
    wrapped = AuditedLLM(provider, meter)

    with pytest.raises(RuntimeError):
        asyncio.run(wrapped.generate(system="s", user="u"))
    assert provider.calls == 1  # 零重试
    assert (meter.calls, meter.ok, meter.failed) == (1, 0, 1)
    assert meter.state == PROVIDER_VERIFICATION_FAILED


# --- Judge 适配器 -----------------------------------------------------------


def test_judge_adapter_returns_strict_verdict() -> None:
    provider = FakeLLM(outputs=[VALID_JUDGE_JSON])
    meter = ProviderMeter("judge")
    adapter = JudgeAdapter(provider, meter=meter)

    verdict = adapter(_request())
    assert verdict.all_supported is True
    assert verdict.verdict == "supported"
    assert verdict.fact_results[0].evidence_indices == (1,)
    assert (meter.calls, meter.ok, meter.failed) == (1, 1, 0)
    assert meter.state == PROVIDER_VERIFIED


def test_judge_adapter_contract_error_is_distinct_and_no_leak() -> None:
    raw = "这不是 JSON：SENTINEL_SHOULD_NOT_APPEAR"
    provider = FakeLLM(outputs=[raw])
    meter = ProviderMeter("judge")
    adapter = JudgeAdapter(provider, meter=meter)

    with pytest.raises(JudgeContractError) as error:
        adapter(_request())
    assert error.value.reason == "not_json"
    assert "SENTINEL_SHOULD_NOT_APPEAR" not in str(error.value)
    assert (meter.calls, meter.ok, meter.failed) == (1, 0, 1)
    assert meter.state == PROVIDER_VERIFICATION_FAILED


def test_judge_adapter_transport_error_is_sanitized() -> None:
    provider = FakeLLM(fail_remaining=1)
    meter = ProviderMeter("judge")
    adapter = JudgeAdapter(provider, meter=meter)

    with pytest.raises(JudgeTransportError) as error:
        adapter(_request())
    # 只保留异常类名；不含正文、URL、密钥
    assert error.value.reason == "RuntimeError"
    assert "api.example.invalid" not in str(error.value)
    assert "sk-secret-key" not in str(error.value)
    # 与契约错误可区分
    assert not isinstance(error.value, JudgeContractError)
    assert (meter.calls, meter.ok, meter.failed) == (1, 0, 1)


def test_judge_adapter_does_not_retry() -> None:
    provider = FakeLLM(fail_remaining=3)
    adapter = JudgeAdapter(provider, meter=ProviderMeter("judge"))

    with pytest.raises(JudgeTransportError):
        adapter(_request())
    assert provider.calls == 1


def test_judge_adapter_async_path_is_available() -> None:
    provider = FakeLLM(outputs=[VALID_JUDGE_JSON])
    adapter = JudgeAdapter(provider, meter=ProviderMeter("judge"))

    verdict = asyncio.run(adapter.ajudge(_request()))
    assert verdict.all_supported is True
    assert provider.calls == 1


# --- 分账与注册表 -----------------------------------------------------------


def test_llm_and_judge_are_accounted_separately() -> None:
    registry = AuditRegistry()
    provider = FakeLLM(outputs=["直接回答", VALID_JUDGE_JSON])
    llm = AuditedLLM(provider, registry.llm)
    judge = JudgeAdapter(provider, meter=registry.judge)

    assert asyncio.run(llm.generate(system="s", user="u")) == "直接回答"
    verdict = judge(_request())
    assert verdict.all_supported is True

    assert (registry.llm.calls, registry.llm.ok, registry.llm.failed) == (1, 1, 0)
    assert (registry.judge.calls, registry.judge.ok, registry.judge.failed) == (1, 1, 0)
    assert provider.calls == 2  # 底层真实调用总数，无重复计数


def test_audit_registry_defaults_to_unconfigured() -> None:
    """缺配置不得默认已配置：默认构造一律 UNCONFIGURED。"""
    registry = AuditRegistry()
    assert [meter.state for meter in registry.meters] == [PROVIDER_UNCONFIGURED] * 4
    assert all(meter.calls == 0 for meter in registry.meters)


def test_audit_registry_initialises_from_real_config() -> None:
    registry = AuditRegistry(embedding=True, rerank=True, llm=True)
    assert [meter.state for meter in registry.meters] == [
        PROVIDER_CONFIGURED_UNVERIFIED,
        PROVIDER_CONFIGURED_UNVERIFIED,
        PROVIDER_CONFIGURED_UNVERIFIED,
        PROVIDER_CONFIGURED_UNVERIFIED,  # judge 缺省沿用 llm
    ]

    partial = AuditRegistry(embedding=True, rerank=False, llm=True)
    assert partial.rerank.state == PROVIDER_UNCONFIGURED
    assert partial.embedding.state == PROVIDER_CONFIGURED_UNVERIFIED

    split = AuditRegistry(llm=True, judge=False)
    assert split.llm.state == PROVIDER_CONFIGURED_UNVERIFIED
    assert split.judge.state == PROVIDER_UNCONFIGURED


def test_registry_snapshots_follow_provider_name_order() -> None:
    registry = AuditRegistry(embedding=True, rerank=True, llm=True)
    for meter in registry.meters:
        meter.record(succeeded=True)

    snapshots = registry.snapshots()
    assert [audit.name for audit in snapshots] == list(PROVIDER_NAMES)
    assert providers_allow_formal_metrics(
        snapshots, rerank_degraded=False, side_effect_free=True
    ) is True
    assert providers_allow_formal_metrics(
        snapshots, rerank_degraded=True, side_effect_free=True
    ) is False


def test_registry_snapshots_block_gate_when_any_provider_unconfigured() -> None:
    registry = AuditRegistry(embedding=True, rerank=False, llm=True)
    for meter in (registry.embedding, registry.llm, registry.judge):
        meter.record(succeeded=True)
    assert providers_allow_formal_metrics(
        registry.snapshots(), rerank_degraded=False, side_effect_free=True
    ) is False


def test_provider_meter_rejects_unknown_name() -> None:
    with pytest.raises(ValueError):
        ProviderMeter("unknown")


def test_provider_meter_for_provider_reads_configured_flag() -> None:
    class Unconfigured:
        configured = False

    meter = ProviderMeter.for_provider("llm", Unconfigured())
    assert meter.state == PROVIDER_UNCONFIGURED
    assert meter.calls == 0


# --- 全局失败原因审计（failure_reason_counts） --------------------------------


class _ApiFailingEmbedding:
    """抛 ApiError 的 Embedding 替身：reason 取自 ``details.reason``。"""

    descriptor = {"provider": "api-fail", "model": "m"}

    def __init__(self, reason: str | None = "api_embedding_timeout"):
        self._reason = reason
        self.calls = 0

    def embed_documents(self, texts):
        self.calls += 1
        details = {} if self._reason is None else {"reason": self._reason}
        raise ApiError("DOCUMENT_EMBEDDING_FAILED", retryable=True, details=details)


def _counts(meter: ProviderMeter) -> dict[str, int]:
    return dict(meter.audit.failure_reason_counts)


def test_provider_failure_reason_only_accepts_safe_values() -> None:
    """ApiError 只接受稳定小写标签；其它异常只取安全类名；恶意值一律 unknown。"""
    assert (
        provider_failure_reason(
            ApiError("DOCUMENT_EMBEDDING_FAILED", details={"reason": "api_embedding_timeout"})
        )
        == "api_embedding_timeout"
    )
    for raw in (
        "TimeoutError",
        "Upper_Reason",
        "https://evil.example/x?sk=abc",
        "sk-live-1",
        "x" * 80,
        "",
        None,
    ):
        assert (
            provider_failure_reason(
                ApiError("DOCUMENT_EMBEDDING_FAILED", details={"reason": raw})
            )
            == "unknown"
        )
    # 非 ApiError：只取异常类名，绝不使用 str(error)
    assert (
        provider_failure_reason(RuntimeError("HTTP 500 https://api.example.invalid sk-secret-key"))
        == "RuntimeError"
    )
    assert provider_failure_reason(TimeoutError("boom")) == "TimeoutError"


def test_audited_embedding_failure_reason_counts_and_sticky_state() -> None:
    """导入式先失败后成功：状态仍为 FAILED，原因计数正确且与 failed 一致。"""
    meter = ProviderMeter("embedding")
    wrapped = AuditedEmbedding(FakeEmbedding(fail_remaining=1), meter)

    with pytest.raises(RuntimeError):
        wrapped.embed_documents(["a"])
    wrapped.embed_documents(["a"])  # 后一次成功（模拟文档级重试）

    assert (meter.calls, meter.ok, meter.failed) == (2, 1, 1)
    assert meter.state == PROVIDER_VERIFICATION_FAILED
    assert _counts(meter) == {"RuntimeError": 1}
    assert sum(_counts(meter).values()) == meter.audit.failed


def test_failure_reason_counts_accumulate_duplicate_reasons() -> None:
    """同一原因重复失败必须累计，且不改变四态语义。"""
    meter = ProviderMeter("embedding")
    wrapped = AuditedEmbedding(_ApiFailingEmbedding("api_embedding_rate_limited"), meter)
    for _ in range(3):
        with pytest.raises(ApiError):
            wrapped.embed_documents(["a"])

    assert (meter.calls, meter.ok, meter.failed) == (3, 0, 3)
    assert _counts(meter) == {"api_embedding_rate_limited": 3}


def test_malicious_failure_reason_never_stored() -> None:
    """恶意正文/URL/密钥作为 reason 也不得落盘，统一 unknown。"""
    meter = ProviderMeter("embedding")
    wrapped = AuditedEmbedding(
        _ApiFailingEmbedding("https://evil.example/x?sk=SECRET_SENTINEL"), meter
    )
    with pytest.raises(ApiError):
        wrapped.embed_documents(["a"])

    assert _counts(meter) == {"unknown": 1}
    blob = json.dumps(_counts(meter), ensure_ascii=False)
    for leaked in ("evil.example", "sk-", "SECRET_SENTINEL", "https://"):
        assert leaked not in blob


def test_four_wrappers_record_reason_without_extra_calls() -> None:
    """四类包装器各记录一次失败原因，不增加调用/重试，也不改变异常传播。"""
    emb = ProviderMeter("embedding")
    with pytest.raises(RuntimeError):
        AuditedEmbedding(FakeEmbedding(fail_remaining=1), emb).embed_documents(["a"])
    assert (emb.calls, emb.failed, _counts(emb)) == (1, 1, {"RuntimeError": 1})

    rrk = ProviderMeter("rerank")
    with pytest.raises(RuntimeError):
        AuditedReranker(FakeReranker(fail_remaining=1), rrk).rerank("q", ["a"])
    assert (rrk.calls, rrk.failed, _counts(rrk)) == (1, 1, {"RuntimeError": 1})

    llm = ProviderMeter("llm")
    with pytest.raises(RuntimeError):
        asyncio.run(AuditedLLM(FakeLLM(fail_remaining=1), llm).generate(system="s", user="u"))
    assert (llm.calls, llm.failed, _counts(llm)) == (1, 1, {"RuntimeError": 1})

    judge = ProviderMeter("judge")
    adapter = JudgeAdapter(FakeLLM(fail_remaining=1), meter=judge)
    with pytest.raises(JudgeTransportError):
        adapter(_request())
    assert (judge.calls, judge.failed, _counts(judge)) == (1, 1, {"RuntimeError": 1})


def test_judge_adapter_contract_failure_records_safe_reason() -> None:
    """Judge 契约错误只记录安全类名，不含任何原文。"""
    judge = ProviderMeter("judge")
    adapter = JudgeAdapter(FakeLLM(outputs=["这不是 JSON：SENTINEL_SHOULD_NOT_APPEAR"]), meter=judge)

    with pytest.raises(JudgeContractError):
        adapter(_request())

    assert (judge.calls, judge.failed) == (1, 1)
    assert _counts(judge) == {"JudgeContractError": 1}
    assert "SENTINEL" not in json.dumps(_counts(judge), ensure_ascii=False)


def test_registry_snapshots_carry_failure_reason_counts() -> None:
    """AuditRegistry 快照透出全局失败原因计数；未失败的 Provider 保持空。"""
    registry = AuditRegistry(embedding=True, rerank=True, llm=True)
    with pytest.raises(RuntimeError):
        AuditedEmbedding(FakeEmbedding(fail_remaining=1), registry.embedding).embed_documents(["a"])

    snapshots = {audit.name: audit for audit in registry.snapshots()}
    assert snapshots["embedding"].failed == 1
    assert dict(snapshots["embedding"].failure_reason_counts) == {"RuntimeError": 1}
    assert snapshots["rerank"].failure_reason_counts == ()


def test_failure_without_reason_counts_as_unknown() -> None:
    """未提供 reason 的失败按 unknown 计数，保持 sum(counts) == failed。"""
    audit = record_provider_call(new_provider_audit("llm", configured=True), succeeded=False)
    assert (audit.calls, audit.ok, audit.failed) == (1, 0, 1)
    assert dict(audit.failure_reason_counts) == {"unknown": 1}
