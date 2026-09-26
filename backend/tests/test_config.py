"""集中式 Settings 的校验测试。"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.config import Settings, get_settings


def test_default_devices_are_cpu() -> None:
    settings = Settings()

    assert settings.embedding_device == "cpu"
    assert settings.rerank_device == "cpu"
    assert settings.cors_origin_list == ["http://localhost:5173"]


def test_auto_device_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(embedding_device="auto")


def test_cors_origins_accept_comma_separated_values() -> None:
    settings = Settings(cors_origins="http://localhost:5173, http://example.test ")

    assert settings.cors_origin_list == ["http://localhost:5173", "http://example.test"]


def test_llm_configured_requires_base_url_model_and_key() -> None:
    assert Settings().llm_configured is False
    assert Settings(llm_base_url="http://example.test", llm_model="m").llm_configured is False
    assert (
        Settings(
            llm_base_url="http://example.test",
            llm_model="m",
            llm_api_key="k",
        ).llm_configured
        is True
    )


def test_get_settings_is_cached_singleton() -> None:
    assert get_settings() is get_settings()
