"""``GET /api/health`` 的响应模型（契约来自 PRODUCT_SPEC 6.1）。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel

ServiceStatus = Literal["healthy", "degraded"]
CapabilityState = Literal["ready", "unconfigured", "unavailable"]


class Capabilities(BaseModel):
    documents: CapabilityState
    chat: CapabilityState
    planning: CapabilityState


class ProviderStatus(BaseModel):
    provider: str
    device: str | None
    ready: bool


class Providers(BaseModel):
    embedding: ProviderStatus
    reranker: ProviderStatus
    llm: ProviderStatus


class HealthResponse(BaseModel):
    """只包含可安全公开的进程状态与能力信息。"""

    status: ServiceStatus
    version: str
    capabilities: Capabilities
    providers: Providers
