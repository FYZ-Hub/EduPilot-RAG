"""OpenAiCompatibleLLMProvider：OpenAI 兼容的 ``/chat/completions`` 客户端。

- 使用现有 ``httpx``（异步），**不引入 OpenAI SDK**；
- ``POST {LLM_BASE_URL.rstrip('/')}/chat/completions``，Bearer 认证，显式 timeout；
- 请求固定 ``temperature=0.0`` 贪婪采样（implementation 1.1.0），消除服务商默认
  随机采样导致相同证据仍产生不同结果；不发送 seed、不重试、不二次调用；
- Base URL / model / API Key 缺一即安全失败（固定 reason，不回显配置值）；
- 构造、健康检查与 ``configured`` 判断**都不联网、不加载模型**；
- **隐私边界**：外发前用共享的 ``app.core.privacy.scrub`` 清洗 user 文本副本，
  只发送回答问题所需的最少证据；原始对象、引用与库内数据零改动；
- ``loaded`` 采用证据式语义：真实请求且响应结构校验成功后为 true，
  失败或 ``close`` 后恢复 false；
- 错误、异常与日志绝不泄漏 API Key、Base URL、请求正文、响应正文或内部异常文本；
- 取消/异常路径由 ``async with`` 保证关闭上游 response 与 AsyncClient。
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from app.config import Settings
from app.core.errors import (
    LLM_PROVIDER_UNAVAILABLE,
    MODEL_RESPONSE_INVALID,
    MODEL_TIMEOUT,
    ApiError,
)
from app.core.privacy import scrub
from app.llm.base import LLMProvider, descriptor_for

# 单次响应体的硬上限，避免无界缓冲
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class OpenAiCompatibleLLMProvider(LLMProvider):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.descriptor = descriptor_for(settings)
        self._ready = False

    @property
    def loaded(self) -> bool:
        """是否已有一次真实成功的调用；不发起探测请求。"""
        return self._ready

    @property
    def configured(self) -> bool:
        """配置是否完整（仅检查配置，不联网、不加载模型）。"""
        return bool(
            self.settings.llm_base_url and self.settings.llm_model and self.settings.llm_api_key
        )

    def close(self) -> None:
        self._ready = False

    async def complete(self, *, system: str, user: str) -> str:
        try:
            content = await self._request(system=system, user=user)
        except Exception:
            self._ready = False
            raise
        self._ready = True
        return content

    # -- 内部实现 ---------------------------------------------------------

    def _require_config(self) -> None:
        if not self.configured:
            raise ApiError(
                LLM_PROVIDER_UNAVAILABLE, details={"reason": "api_llm_not_configured"}
            )

    async def _request(self, *, system: str, user: str) -> str:
        self._require_config()
        endpoint = f"{self.settings.llm_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.settings.llm_api_key}",
        }
        payload = {
            "model": self.settings.llm_model,
            "messages": [
                {"role": "system", "content": system},
                # 只清洗外发副本：请求正文中的用户数据绝不原样外发
                {"role": "user", "content": scrub(user)},
            ],
            "stream": False,
            # 固定贪婪采样：消除服务商默认随机采样带来的跨次不一致
            "temperature": 0.0,
        }

        try:
            async with httpx.AsyncClient(
                timeout=self.settings.llm_timeout_seconds
            ) as client:
                async with client.stream(
                    "POST", endpoint, headers=headers, json=payload
                ) as response:
                    response.raise_for_status()
                    body = await self._read_bounded(response)
        except ApiError:
            raise
        except httpx.TimeoutException as error:
            raise ApiError(MODEL_TIMEOUT) from error
        except Exception as error:  # noqa: BLE001 - 网络/状态码异常统一收敛为安全错误
            raise ApiError(
                LLM_PROVIDER_UNAVAILABLE, details={"reason": "api_llm_unavailable"}
            ) from error

        try:
            data = json.loads(body)
        except Exception as error:  # noqa: BLE001
            raise ApiError(MODEL_RESPONSE_INVALID) from error
        return self._extract_content(data)

    @staticmethod
    async def _read_bounded(response: httpx.Response) -> str:
        chunks: list[bytes] = []
        total = 0
        async for chunk in response.aiter_bytes():
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise ApiError(MODEL_RESPONSE_INVALID)
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="strict")

    @staticmethod
    def _extract_content(data: Any) -> str:
        if not isinstance(data, dict):
            raise ApiError(MODEL_RESPONSE_INVALID)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise ApiError(MODEL_RESPONSE_INVALID)
        first = choices[0]
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            raise ApiError(MODEL_RESPONSE_INVALID)
        return content


__all__ = ["MAX_RESPONSE_BYTES", "OpenAiCompatibleLLMProvider"]
