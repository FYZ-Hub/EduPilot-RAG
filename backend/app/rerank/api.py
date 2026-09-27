"""ApiReranker：内部约定的 OpenAI 兼容重排接口。

OpenAI 没有统一的 Rerank 标准，本项目固定采用以下内部约定：

``POST {RERANK_BASE_URL.rstrip('/')}/rerank``

请求::

    {"model": "...", "query": "...", "documents": ["...", "..."], "top_n": <候选数量>}

响应::

    {"results": [{"index": 0, "relevance_score": 0.0}, ...]}

- 使用 Bearer 认证与显式 timeout；Base URL、model、API Key 缺一即明确失败
  （安全、稳定的错误原因，不含任何配置值）。
- 返回的 ``index`` 必须唯一、完整且在范围内；服务端乱序返回时按 ``index`` 映射回输入。
- ``relevance_score`` 必须是有限浮点数。
- **隐私边界**：外发前对 ``query`` 与候选文本**副本**执行确定性个人信息清洗
  （``app.core.privacy``）；原始 ``RetrievedChunk``、citation、quote 与库内数据不被修改。
- 只发送 ``query`` 与必要候选片段；不发送文件名、绝对路径、locator、引用元数据或整份文档。
- 错误、异常与日志不得泄漏 API Key、Base URL、请求正文、响应正文、候选全文或清洗前正文。
- ``loaded`` 采用证据式 readiness：只有一次请求与响应结构校验真正成功后才是 true；
  失败或 ``close`` 后恢复 false，绝不因为「填写了配置」而谎报远端可用。
- 基础测试只使用 httpx mock，不访问真实 API。
"""

from __future__ import annotations

import math
from collections.abc import Sequence

import httpx

from app.config import Settings
from app.core.errors import (
    RERANK_PROVIDER_UNAVAILABLE,
    RERANK_RESPONSE_INVALID,
    ApiError,
)
from app.core.privacy import scrub, scrub_texts
from app.rerank.base import RerankProvider, descriptor_for


class ApiReranker(RerankProvider):
    def __init__(self, settings: Settings):
        self.settings = settings
        self.descriptor = descriptor_for(settings)
        self._ready = False

    @property
    def loaded(self) -> bool:
        """是否已有一次真实成功的请求；不发起探测请求。"""
        return self._ready

    def close(self) -> None:
        self._ready = False

    def _require_config(self) -> None:
        if not (
            self.settings.rerank_base_url
            and self.settings.rerank_model
            and self.settings.rerank_api_key
        ):
            raise ApiError(
                RERANK_PROVIDER_UNAVAILABLE, details={"reason": "api_rerank_not_configured"}
            )

    def rerank(self, query: str, candidates: Sequence[str]) -> list[float]:
        if not candidates:
            return []
        try:
            scores = self._rerank(query, candidates)
        except Exception:
            self._ready = False
            raise
        self._ready = True
        return scores

    def _rerank(self, query: str, candidates: Sequence[str]) -> list[float]:
        self._require_config()

        endpoint = f"{self.settings.rerank_base_url.rstrip('/')}/rerank"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.settings.rerank_api_key}",
        }
        # 只清洗外发副本：数量与顺序保持不变，因此 index 仍与原始候选一一对应
        payload = {
            "model": self.settings.rerank_model,
            "query": scrub(query),
            "documents": scrub_texts(list(candidates)),
            "top_n": len(candidates),
        }

        try:
            with httpx.Client(timeout=self.settings.rerank_timeout_seconds) as client:
                response = client.post(endpoint, headers=headers, json=payload)
                response.raise_for_status()
        except ApiError:
            raise
        except httpx.TimeoutException as error:
            raise ApiError(
                RERANK_PROVIDER_UNAVAILABLE, details={"reason": "api_rerank_timeout"}
            ) from error
        except Exception as error:  # noqa: BLE001 - 网络/状态码异常统一收敛为安全错误
            raise ApiError(
                RERANK_PROVIDER_UNAVAILABLE, details={"reason": "api_rerank_unavailable"}
            ) from error

        # 响应体无法解析属于「非法返回结构」，是内部不变量错误，不得被降级吞掉
        try:
            data = response.json()
        except Exception as error:  # noqa: BLE001
            raise ApiError(RERANK_RESPONSE_INVALID) from error
        return self._parse(data, len(candidates))

    def _parse(self, payload: object, expected: int) -> list[float]:
        if not isinstance(payload, dict):
            raise ApiError(RERANK_RESPONSE_INVALID)
        results = payload.get("results")
        if not isinstance(results, list):
            raise ApiError(RERANK_RESPONSE_INVALID)

        scores: list[float | None] = [None] * expected
        for item in results:
            if not isinstance(item, dict):
                raise ApiError(RERANK_RESPONSE_INVALID)
            index = item.get("index")
            if isinstance(index, bool) or not isinstance(index, int):
                raise ApiError(RERANK_RESPONSE_INVALID)
            if not (0 <= index < expected):
                raise ApiError(RERANK_RESPONSE_INVALID)
            if scores[index] is not None:
                raise ApiError(RERANK_RESPONSE_INVALID)
            raw_score = item.get("relevance_score")
            if isinstance(raw_score, bool) or not isinstance(raw_score, (int, float)):
                raise ApiError(RERANK_RESPONSE_INVALID)
            value = float(raw_score)
            if not math.isfinite(value):
                raise ApiError(RERANK_RESPONSE_INVALID)
            scores[index] = value

        if any(value is None for value in scores):
            raise ApiError(RERANK_RESPONSE_INVALID)
        return [float(value) for value in scores]


__all__ = ["ApiReranker"]
