"""阶段 6 修复轮：外部 LLM 配置接线与检索阈值解析。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings

LLM_ENV_KEYS = (
    "LLM_PROVIDER",
    "LLM_BASE_URL",
    "LLM_API_KEY",
    "LLM_MODEL",
    "LLM_TIMEOUT_SECONDS",
    "LLM_ANSWER_MAX_CHARS",
    "LLM_REWRITE_MAX_CHARS",
)


def _from_env(monkeypatch, **env) -> Settings:
    for key in LLM_ENV_KEYS + ("RETRIEVAL_SCORE_THRESHOLD",):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_llm_environment_is_wired_into_settings(monkeypatch) -> None:
    settings = _from_env(
        monkeypatch,
        LLM_PROVIDER="openai_compatible",
        LLM_BASE_URL="https://llm.invalid/v1",
        LLM_API_KEY="test-key-not-real",
        LLM_MODEL="demo-model",
        LLM_TIMEOUT_SECONDS="30",
        LLM_ANSWER_MAX_CHARS="1500",
        LLM_REWRITE_MAX_CHARS="200",
    )
    assert settings.llm_provider == "openai_compatible"
    assert settings.llm_base_url == "https://llm.invalid/v1"
    assert settings.llm_api_key == "test-key-not-real"
    assert settings.llm_model == "demo-model"
    assert settings.llm_timeout_seconds == 30
    assert settings.llm_answer_max_chars == 1500
    assert settings.llm_rewrite_max_chars == 200
    assert settings.llm_configured is True


def test_llm_defaults_stay_unconfigured(monkeypatch) -> None:
    settings = _from_env(monkeypatch)
    assert settings.llm_base_url == ""
    assert settings.llm_api_key == ""
    assert settings.llm_model == ""
    assert settings.llm_timeout_seconds == 60
    assert settings.llm_answer_max_chars == 2000
    assert settings.llm_rewrite_max_chars == 300
    assert settings.llm_configured is False


def test_llm_provider_is_restricted_to_fake_and_api(monkeypatch) -> None:
    with pytest.raises(ValidationError):
        _from_env(monkeypatch, LLM_PROVIDER="local")


@pytest.mark.parametrize("raw", ["", "   "])
def test_empty_retrieval_score_threshold_resolves_to_none(monkeypatch, raw) -> None:
    settings = _from_env(monkeypatch, RETRIEVAL_SCORE_THRESHOLD=raw)
    assert settings.retrieval_score_threshold is None, "空值必须安全解析为 None"


def test_retrieval_score_threshold_absent_is_none(monkeypatch) -> None:
    assert _from_env(monkeypatch).retrieval_score_threshold is None


def test_retrieval_score_threshold_accepts_valid_float(monkeypatch) -> None:
    settings = _from_env(monkeypatch, RETRIEVAL_SCORE_THRESHOLD="0.42")
    assert settings.retrieval_score_threshold == pytest.approx(0.42)


def test_retrieval_score_threshold_rejects_invalid_value(monkeypatch) -> None:
    with pytest.raises(ValidationError):
        _from_env(monkeypatch, RETRIEVAL_SCORE_THRESHOLD="not-a-number")
