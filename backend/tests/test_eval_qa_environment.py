"""阶段 9B semantic_api 环境装配测试：隔离路径、配置校验、审计包装与 LLM/Judge 分账。

全程使用 Fake Provider（monkeypatch 工厂），**不发起任何真实 HTTP**，也不读取 ``.env``。
"""

from __future__ import annotations

import asyncio
import dataclasses
import json

import pytest

import eval_tools.pipeline as pipeline
from app.config import Settings
from eval_tools.pipeline import (
    EvalEnvironment,
    SemanticConfigError,
    build_semantic_settings,
    open_semantic_eval_environment,
    validate_semantic_config,
)
from eval_tools.qa.executor import JudgeRequest, resolve_eval_judge, resolve_eval_llm
from eval_tools.qa.metrics import (
    PROVIDER_CONFIGURED_UNVERIFIED,
    PROVIDER_UNCONFIGURED,
    PROVIDER_VERIFICATION_FAILED,
)
from eval_tools.qa.providers import (
    AuditedEmbedding,
    AuditedLLM,
    AuditedReranker,
    JudgeAdapter,
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


class FakeEmbedding:
    def __init__(self, *, dimension: int = 4, fail_remaining: int = 0):
        self.descriptor = {"provider": "api-embed", "model": "fake-embed"}
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
            raise RuntimeError("embedding transport failed")
        self._loaded = True
        return [[0.1] * self.dimension for _ in texts]

    def close(self) -> None:
        self.closed += 1


class FakeReranker:
    def __init__(self, *, fail_remaining: int = 0):
        self.descriptor = {"provider": "api-rerank", "model": "fake-rerank"}
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
            raise RuntimeError("rerank transport failed")
        self._loaded = True
        return [0.5 for _ in candidates]

    def close(self) -> None:
        self.closed += 1


class FakeLLM:
    def __init__(self, *, outputs=(), fail_remaining: int = 0):
        self.descriptor = {"provider": "openai_compatible", "model": "fake-llm"}
        self.configured = True
        self._outputs = list(outputs)
        self.fail_remaining = fail_remaining
        self.calls = 0
        self.closed = 0

    @property
    def loaded(self) -> bool:
        return self.calls > 0

    async def complete(self, *, system: str, user: str) -> str:
        self.calls += 1
        if self.fail_remaining > 0:
            self.fail_remaining -= 1
            raise RuntimeError("llm transport failed")
        return self._outputs.pop(0) if self._outputs else "{}"

    async def generate(self, *, system: str, user: str) -> str:
        return await self.complete(system=system, user=user)

    async def rewrite(self, *, system: str, user: str) -> str:
        return await self.complete(system=system, user=user)

    def close(self) -> None:
        self.closed += 1

    async def aclose(self) -> None:
        self.closed += 1


def _semantic_base(tmp_path, **overrides) -> Settings:
    """合成 semantic 配置；域名使用保留 TLD ``.invalid``，物理上不可能发起真实请求。"""
    base = dict(
        app_env="eval",
        log_level="WARNING",
        embedding_provider="api",
        embedding_base_url="https://embedding.invalid/v1",
        embedding_model="fake-embed",
        embedding_api_key="test-key-embedding",
        rerank_provider="api",
        rerank_base_url="https://rerank.invalid/v1",
        rerank_model="fake-rerank",
        rerank_api_key="test-key-rerank",
        llm_provider="openai_compatible",
        llm_base_url="https://llm.invalid/v1",
        llm_model="fake-llm",
        llm_api_key="test-key-llm",
        demo_dataset_path=str(tmp_path / "demo"),
        demo_dataset_version="0.1.0",
    )
    base.update(overrides)
    return Settings(**base)


def _patch_provider_factories(
    monkeypatch, *, embedding, reranker, llm
) -> None:
    monkeypatch.setattr(
        "app.worker.runner.build_embedding_provider",
        lambda settings, coordinator: embedding,
    )
    monkeypatch.setattr(
        pipeline, "build_rerank_provider", lambda settings, coordinator: reranker
    )
    monkeypatch.setattr(pipeline, "build_llm_provider", lambda settings: llm)


def _judge_request() -> JudgeRequest:
    return JudgeRequest(
        case_id="gt-synth-001",
        question="合成问题：某虚构课程的学分为多少？",
        facts=("某虚构课程的学分为 3 学分。",),
        answer="依据资料：[1] 学分为 3 学分。",
        evidence=((1, "合成引用原文", {"page_number": 1}),),
    )


# --- 隔离配置 ---------------------------------------------------------------


def test_build_semantic_settings_forces_isolated_paths(tmp_path) -> None:
    base = _semantic_base(tmp_path)
    work = tmp_path / "run-001"
    settings = build_semantic_settings(base, work)

    assert settings.database_url == f"sqlite:///{work / 'app.db'}"
    assert settings.chroma_path == str(work / "chroma")
    assert settings.upload_path == str(work / "uploads")
    assert settings.model_cache_path == str(work / "models")
    assert settings.worker_enabled is False
    assert work.is_dir()

    # 真实 Provider 配置原样保留
    assert settings.embedding_provider == "api"
    assert settings.rerank_provider == "api"
    assert settings.llm_provider == "openai_compatible"
    assert settings.embedding_base_url == base.embedding_base_url
    assert settings.llm_api_key == base.llm_api_key

    # 传入的 base 未被就地改写（说明未读取/改写外部配置源）
    assert str(work) not in base.chroma_path
    assert base.database_url != settings.database_url


# --- 配置校验 ---------------------------------------------------------------


@pytest.mark.parametrize("field", ["embedding", "rerank", "llm"])
def test_validate_semantic_config_rejects_wrong_kind(tmp_path, field: str) -> None:
    base = _semantic_base(tmp_path, **{f"{field}_provider": "fake"})
    with pytest.raises(SemanticConfigError) as error:
        validate_semantic_config(base)
    assert error.value.reason == "provider_kind_mismatch"
    assert error.value.keys == (f"{field}_provider",)
    # 只带键名，绝不回显配置值
    assert "https://" not in str(error.value)
    assert "test-key" not in str(error.value)


def test_validate_semantic_config_reports_missing_keys_without_values(tmp_path) -> None:
    base = _semantic_base(tmp_path, llm_api_key="", rerank_model="")
    status = validate_semantic_config(base)

    assert status.embedding is True
    assert status.rerank is False
    assert status.llm is False
    assert status.ok is False
    assert set(status.missing) == {"rerank_model", "llm_api_key"}
    assert all("test-key" not in key and "https" not in key for key in status.missing)


def test_validate_semantic_config_accepts_complete_config(tmp_path) -> None:
    status = validate_semantic_config(_semantic_base(tmp_path))
    assert status.ok is True
    assert status.missing == ()


# --- offline_fake 兼容 ------------------------------------------------------


def test_eval_environment_semantic_fields_default_to_none() -> None:
    """offline_fake 环境默认不带 Judge / 审计 / 包装 LLM。"""
    defaults = {field.name: field.default for field in dataclasses.fields(EvalEnvironment)}
    assert defaults["audit"] is None
    assert defaults["judge"] is None
    assert defaults["llm"] is None


class _StubEnvironment:
    def __init__(self, *, llm=None, judge=None, settings=None):
        self.llm = llm
        self.judge = judge
        self.settings = settings


def test_resolve_eval_llm_prefers_injected_then_environment() -> None:
    injected = object()
    from_env = object()

    llm, owns = resolve_eval_llm(_StubEnvironment(llm=from_env), injected)
    assert llm is injected
    assert owns is False

    llm, owns = resolve_eval_llm(_StubEnvironment(llm=from_env))
    assert llm is from_env
    assert owns is False


def test_resolve_eval_llm_builds_and_owns_when_environment_has_none(monkeypatch) -> None:
    import eval_tools.qa.executor as executor_module

    built = FakeLLM(outputs=["x"])
    monkeypatch.setattr(executor_module, "build_llm_provider", lambda settings: built)

    llm, owns = resolve_eval_llm(_StubEnvironment(settings=object()))
    assert llm is built
    assert owns is True


def test_resolve_eval_judge_prefers_injected_then_environment() -> None:
    injected = object()
    from_env = object()

    assert resolve_eval_judge(_StubEnvironment(judge=from_env), injected) is injected
    assert resolve_eval_judge(_StubEnvironment(judge=from_env)) is from_env
    assert resolve_eval_judge(_StubEnvironment()) is None


# --- semantic 环境装配 ------------------------------------------------------


def test_open_semantic_eval_environment_wraps_and_splits_accounting(
    tmp_path, monkeypatch
) -> None:
    embedding = FakeEmbedding()
    reranker = FakeReranker()
    llm = FakeLLM(outputs=["回答", VALID_JUDGE_JSON])
    _patch_provider_factories(
        monkeypatch, embedding=embedding, reranker=reranker, llm=llm
    )

    base = _semantic_base(tmp_path)
    work = tmp_path / "run-002"
    with open_semantic_eval_environment(base, work) as environment:
        assert isinstance(environment.worker.embeddings, AuditedEmbedding)
        assert isinstance(environment.reranker, AuditedReranker)
        assert isinstance(environment.llm, AuditedLLM)
        assert isinstance(environment.judge, JudgeAdapter)
        assert environment.audit is not None

        # Judge 与生成共享同一个底层 LLM
        assert environment.judge.provider is environment.llm.provider is llm

        # 审计按真实配置初始化（三组齐备 → 已配置未验证）
        assert [meter.state for meter in environment.audit.meters] == [
            PROVIDER_CONFIGURED_UNVERIFIED
        ] * 4

        # 隔离路径
        assert str(work) in environment.settings.database_url
        assert environment.settings.chroma_path == str(work / "chroma")

        # 检索链已用包装后的 Provider 重建
        assert environment.chain.embeddings is environment.worker.embeddings
        assert environment.chain.reranker is environment.reranker

        # 空候选：不计次、不算降级
        assert environment.reranker.rerank("q", []) == []
        assert environment.audit.rerank.calls == 0

        # 分账：一次生成 + 一次 Judge
        assert asyncio.run(environment.llm.generate(system="s", user="u")) == "回答"
        verdict = environment.judge(_judge_request())
        assert verdict.all_supported is True

        assert (environment.audit.llm.calls, environment.audit.llm.ok) == (1, 1)
        assert (environment.audit.judge.calls, environment.audit.judge.ok) == (1, 1)
        assert llm.calls == 2  # 底层真实调用总数，无重复计数

    assert llm.closed >= 1  # 退出时关闭底层 LLM


def test_open_semantic_eval_environment_marks_unconfigured_without_credentials(
    tmp_path, monkeypatch
) -> None:
    _patch_provider_factories(
        monkeypatch, embedding=FakeEmbedding(), reranker=FakeReranker(), llm=FakeLLM()
    )
    base = _semantic_base(tmp_path, rerank_api_key="", llm_api_key="")

    with open_semantic_eval_environment(base, tmp_path / "run-003") as environment:
        assert environment.audit is not None
        assert environment.audit.embedding.state == PROVIDER_CONFIGURED_UNVERIFIED
        assert environment.audit.rerank.state == PROVIDER_UNCONFIGURED
        # Judge 与 LLM 共享 Provider，配置状态一并继承
        assert environment.audit.llm.state == PROVIDER_UNCONFIGURED
        assert environment.audit.judge.state == PROVIDER_UNCONFIGURED


def test_open_semantic_eval_environment_raises_on_kind_mismatch(tmp_path) -> None:
    base = _semantic_base(tmp_path, embedding_provider="fake")
    with pytest.raises(SemanticConfigError):
        with open_semantic_eval_environment(base, tmp_path / "run-004"):
            pass
    # 未创建隔离目录内容之外的副作用即可接受；重点是不进入评测执行
    assert not (tmp_path / "run-004" / "chroma").exists()


def test_provider_failure_marks_verification_failed_in_semantic_env(
    tmp_path, monkeypatch
) -> None:
    reranker = FakeReranker(fail_remaining=1)
    _patch_provider_factories(
        monkeypatch, embedding=FakeEmbedding(), reranker=reranker, llm=FakeLLM()
    )
    base = _semantic_base(tmp_path)

    with open_semantic_eval_environment(base, tmp_path / "run-005") as environment:
        with pytest.raises(RuntimeError):
            environment.reranker.rerank("q", ["a"])
        assert environment.audit is not None
        assert environment.audit.rerank.state == PROVIDER_VERIFICATION_FAILED
        assert (environment.audit.rerank.calls, environment.audit.rerank.failed) == (1, 1)
