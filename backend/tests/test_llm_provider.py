"""阶段 6：LLM Provider 契约测试（Fake 确定性 / API Mock / 错误边界 / 隐私 v4）。

全部测试离线：API 路径一律使用 ``httpx.AsyncClient`` 替身，不访问真实网络。
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys

import httpx
import pytest

from app import constants
from app.core.errors import (
    LLM_PROVIDER_FORBIDDEN,
    LLM_PROVIDER_UNAVAILABLE,
    MODEL_RESPONSE_INVALID,
    MODEL_TIMEOUT,
    ApiError,
)
from app.llm.api import OpenAiCompatibleLLMProvider
from app.llm.base import descriptor_for
from app.llm.fake import FakeLLMProvider
from app.llm.factory import build_llm_provider
from app.llm.prompts import EVIDENCE_MARKER, QUESTION_MARKER, SYSTEM_PROMPT, build_answer_user
from tests.conftest import build_settings

PII_LINE = "姓名 张三 学号 20260001 电话 010-12345678 课程编号 QM-CS201 学分 3 学期 2026-2027-1"


def _api_settings(tmp_path, **overrides):
    values = {
        "llm_provider": "openai_compatible",
        "llm_base_url": "https://llm.invalid/v1",
        "llm_api_key": "test-key-not-real",
        "llm_model": "demo-model",
    }
    values.update(overrides)
    return build_settings(tmp_path, **values)


def _patch_async_client(
    monkeypatch,
    *,
    status_code: int = 200,
    body: bytes | None = None,
    error: BaseException | None = None,
    module: str = "app.llm.api",
) -> tuple[list[dict], dict]:
    """把 httpx.AsyncClient 换成记录器替身；返回 (捕获的请求, 生命周期状态)。"""
    captured: list[dict] = []
    state = {"constructed": 0, "closed": 0, "streams": 0}

    class _Response:
        def __init__(self) -> None:
            self.status_code = status_code

        def raise_for_status(self) -> None:
            if status_code >= 400:
                raise httpx.HTTPStatusError("upstream error", request=None, response=None)  # type: ignore[arg-type]

        async def aiter_bytes(self):
            yield body or b""

    class _Stream:
        async def __aenter__(self):
            return _Response()

        async def __aexit__(self, *exc_info):
            state["closed"] += 1
            return False

    class _Client:
        def __init__(self, *args, **kwargs):
            state["constructed"] += 1
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc_info):
            state["closed"] += 1
            return False

        def stream(self, method, url, **kwargs):
            state["streams"] += 1
            captured.append({"method": method, "url": url, **kwargs})
            if error is not None:
                raise error
            return _Stream()

    monkeypatch.setattr(f"{module}.httpx.AsyncClient", _Client)
    return captured, state


def _chat_body(content: str) -> bytes:
    return json.dumps({"choices": [{"message": {"content": content}}]}).encode("utf-8")


def _valid_completion() -> str:
    return json.dumps(
        {
            "outcome": "answered",
            "reason_code": None,
            "answer": "依据资料 [1]。",
            "citation_indices": [1],
        },
        ensure_ascii=False,
    )


# --- 工厂与描述符 -----------------------------------------------------------


def test_fake_provider_is_forbidden_in_production(tmp_path) -> None:
    settings = build_settings(tmp_path, app_env="production", llm_provider="fake")
    with pytest.raises(ApiError) as error:
        build_llm_provider(settings)
    assert error.value.code == LLM_PROVIDER_FORBIDDEN


def test_descriptor_records_versions_without_paths(tmp_path) -> None:
    descriptor = descriptor_for(_api_settings(tmp_path))
    payload = descriptor.as_dict()
    assert payload["provider"] == constants.LLM_PROVIDER_API
    assert payload["privacy_policy_version"] == "external-privacy-v4"
    assert payload["prompt_version"]
    assert payload["response_schema_version"]
    assert "invalid" not in json.dumps(payload)


def test_llm_fingerprint_is_stable_and_does_not_touch_pipeline(tmp_path) -> None:
    from app.documents.fingerprint import pipeline_fingerprint, stage_fingerprints

    before = descriptor_for(_api_settings(tmp_path)).fingerprint
    assert before == descriptor_for(_api_settings(tmp_path)).fingerprint

    # LLM 是查询时能力：只改 LLM 配置不得改变文档流水线指纹
    base = build_settings(tmp_path, embedding_provider="fake")
    llm_changed = build_settings(
        tmp_path,
        embedding_provider="fake",
        llm_provider="openai_compatible",
        llm_base_url="https://other.invalid/v1",
        llm_model="another-model",
        llm_api_key="another-key",
    )
    assert pipeline_fingerprint(base) == pipeline_fingerprint(llm_changed)
    assert stage_fingerprints(base) == stage_fingerprints(llm_changed)


# --- Fake Provider ----------------------------------------------------------


def test_fake_provider_is_deterministic_for_same_input() -> None:
    provider = FakeLLMProvider()
    user = build_answer_user("毕业总学分是多少？", [(1, "毕业总学分：160.0 学分。")])
    first = asyncio.run(provider.generate(system=SYSTEM_PROMPT, user=user))
    second = asyncio.run(provider.generate(system=SYSTEM_PROMPT, user=user))
    assert first == second
    payload = json.loads(first)
    assert payload["outcome"] == "answered"
    assert payload["citation_indices"] == [1]
    assert "160.0" in payload["answer"]


def test_fake_provider_is_stable_across_python_hash_seeds() -> None:
    script = (
        "import asyncio, json, sys\n"
        "sys.path.insert(0, '/app')\n"
        "from app.llm.fake import FakeLLMProvider\n"
        "from app.llm.prompts import SYSTEM_PROMPT, build_answer_user\n"
        "async def main():\n"
        "    provider = FakeLLMProvider()\n"
        "    user = build_answer_user('问题', [(2, '乙证据'), (1, '甲证据'), (3, '丙证据')])\n"
        "    print(await provider.generate(system=SYSTEM_PROMPT, user=user))\n"
        "asyncio.run(main())\n"
    )
    outputs = []
    for seed in ("0", "1", "987654"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            check=True,
        )
        outputs.append(result.stdout.strip())
    assert outputs[0] == outputs[1] == outputs[2]
    assert json.loads(outputs[0])["citation_indices"] == [1, 2, 3]


def test_fake_provider_never_touches_the_network(monkeypatch) -> None:
    def _forbidden(*args, **kwargs):  # pragma: no cover - 触发即失败
        raise AssertionError("Fake Provider 不得创建 HTTP 客户端")

    monkeypatch.setattr("httpx.AsyncClient", _forbidden)
    provider = FakeLLMProvider()
    raw = asyncio.run(
        provider.generate(system=SYSTEM_PROMPT, user=build_answer_user("问题", [(1, "证据")]))
    )
    assert json.loads(raw)["outcome"] == "answered"


def test_fake_provider_does_not_hardcode_demo_answers() -> None:
    """回答文本必须由本次证据派生：换证据即换答案。"""
    provider = FakeLLMProvider()
    first = asyncio.run(
        provider.generate(system=SYSTEM_PROMPT, user=build_answer_user("问题", [(1, "甲证据")]))
    )
    second = asyncio.run(
        provider.generate(system=SYSTEM_PROMPT, user=build_answer_user("问题", [(1, "乙证据")]))
    )
    assert "甲证据" in first
    assert "乙证据" in second
    assert first != second


def test_fake_provider_rewrite_uses_history_to_become_self_contained() -> None:
    from app.llm.prompts import REWRITE_SYSTEM_PROMPT, build_rewrite_user

    provider = FakeLLMProvider()
    user = build_rewrite_user(
        [("user", "计算机科学与技术专业毕业总学分是多少？"), ("assistant", "见方案。")],
        "那专业必修呢？",
    )
    rewritten = asyncio.run(provider.rewrite(system=REWRITE_SYSTEM_PROMPT, user=user))
    assert "毕业总学分" in rewritten
    assert "专业必修" in rewritten


# --- API Provider -----------------------------------------------------------


def test_api_provider_requires_complete_configuration(tmp_path, monkeypatch) -> None:
    captured, state = _patch_async_client(monkeypatch)
    provider = OpenAiCompatibleLLMProvider(_api_settings(tmp_path, llm_api_key=""))
    assert provider.configured is False
    with pytest.raises(ApiError) as error:
        asyncio.run(provider.generate(system=SYSTEM_PROMPT, user="内容"))
    assert error.value.code == LLM_PROVIDER_UNAVAILABLE
    assert state["constructed"] == 0, "配置不完整时不得创建 HTTP 客户端"
    assert captured == []


def test_api_provider_becomes_ready_only_after_validated_response(tmp_path, monkeypatch) -> None:
    captured, state = _patch_async_client(monkeypatch, body=_chat_body(_valid_completion()))
    provider = OpenAiCompatibleLLMProvider(_api_settings(tmp_path))
    assert provider.loaded is False

    raw = asyncio.run(provider.generate(system=SYSTEM_PROMPT, user="问题"))
    assert json.loads(raw)["outcome"] == "answered"
    assert len(captured) == 1
    assert state["closed"] >= 1, "上游 response / AsyncClient 必须被关闭"
    assert provider.loaded is True

    provider.close()
    assert provider.loaded is False


def test_api_provider_sends_expected_request_shape(tmp_path, monkeypatch) -> None:
    captured, _state = _patch_async_client(monkeypatch, body=_chat_body(_valid_completion()))
    provider = OpenAiCompatibleLLMProvider(_api_settings(tmp_path))
    asyncio.run(provider.generate(system=SYSTEM_PROMPT, user="问题"))

    request = captured[0]
    assert request["method"] == "POST"
    assert request["url"] == "https://llm.invalid/v1/chat/completions"
    assert request["headers"]["Authorization"] == "Bearer test-key-not-real"
    assert request["json"]["model"] == "demo-model"
    assert [message["role"] for message in request["json"]["messages"]] == ["system", "user"]
    assert request["json"]["stream"] is False


@pytest.mark.parametrize(
    ("status_code", "body", "error", "expected"),
    [
        (200, b"not-json", None, MODEL_RESPONSE_INVALID),
        (200, json.dumps({"choices": []}).encode(), None, MODEL_RESPONSE_INVALID),
        (200, json.dumps({"choices": [{"message": {}}]}).encode(), None, MODEL_RESPONSE_INVALID),
        (500, b"{}", None, LLM_PROVIDER_UNAVAILABLE),
        (200, b"", httpx.TimeoutException("timeout"), MODEL_TIMEOUT),
        (200, b"", httpx.ConnectError("boom"), LLM_PROVIDER_UNAVAILABLE),
    ],
)
def test_api_provider_maps_upstream_failures(tmp_path, monkeypatch, status_code, body, error, expected) -> None:
    _captured, _state = _patch_async_client(
        monkeypatch, status_code=status_code, body=body, error=error
    )
    provider = OpenAiCompatibleLLMProvider(_api_settings(tmp_path))
    with pytest.raises(ApiError) as failure:
        asyncio.run(provider.generate(system=SYSTEM_PROMPT, user="问题"))
    assert failure.value.code == expected
    assert provider.loaded is False


def test_api_provider_errors_never_leak_secrets_or_bodies(tmp_path, monkeypatch) -> None:
    secret = "sk-should-never-appear"
    _patch_async_client(monkeypatch, status_code=500, body=b'{"error":"internal-detail"}')
    provider = OpenAiCompatibleLLMProvider(_api_settings(tmp_path, llm_api_key=secret))
    with pytest.raises(ApiError) as failure:
        asyncio.run(provider.generate(system=SYSTEM_PROMPT, user="用户正文不应外泄"))
    rendered = f"{failure.value!r} {failure.value.to_payload('req')}"
    assert secret not in rendered
    assert "llm.invalid" not in rendered
    assert "internal-detail" not in rendered
    assert "用户正文不应外泄" not in rendered


def test_api_provider_construction_and_configured_check_are_offline(tmp_path, monkeypatch) -> None:
    def _forbidden(*args, **kwargs):  # pragma: no cover - 触发即失败
        raise AssertionError("构造与健康检查不得联网")

    monkeypatch.setattr("httpx.AsyncClient", _forbidden)
    provider = OpenAiCompatibleLLMProvider(_api_settings(tmp_path))
    assert provider.configured is True
    assert provider.loaded is False


def test_api_provider_scrubs_personal_information_before_send(tmp_path, monkeypatch) -> None:
    captured, _state = _patch_async_client(monkeypatch, body=_chat_body(_valid_completion()))
    provider = OpenAiCompatibleLLMProvider(_api_settings(tmp_path))
    user = build_answer_user(PII_LINE, [(1, PII_LINE)])
    asyncio.run(provider.generate(system=SYSTEM_PROMPT, user=user))

    sent = json.dumps(captured[0]["json"], ensure_ascii=False)
    for leaked in ("张三", "20260001", "010-12345678"):
        assert leaked not in sent, f"外发 payload 仍包含个人信息：{leaked}"
    assert "[REDACTED_NAME]" in sent
    assert "[REDACTED_STUDENT_ID]" in sent
    assert "[REDACTED_PHONE]" in sent
    for kept in ("QM-CS201", "学分", "学期"):
        assert kept in sent, f"外发 payload 丢失学术字段：{kept}"
    # 原始对象未被改写（清洗只作用于外发副本）
    assert PII_LINE in user
    assert EVIDENCE_MARKER in user and QUESTION_MARKER in user
