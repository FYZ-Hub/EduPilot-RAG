"""健康检查路由。

阶段 1 尚未实现任何业务能力，因此必须如实降级：不得谎报 ready，
也不得返回 Base URL、密钥、环境变量值或宿主机路径。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.config import Settings, get_settings
from app.schemas.health import Capabilities, HealthResponse, ProviderStatus, Providers

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def read_health(settings: Settings = Depends(get_settings)) -> HealthResponse:
    """返回进程状态与能力信息；阶段 1 固定为 degraded。"""
    return HealthResponse(
        status="degraded",
        version=settings.app_version,
        capabilities=Capabilities(
            # 文档、规划能力在阶段 1 尚未实现。
            documents="unavailable",
            # 聊天能力依赖后续阶段实现与 LLM 配置，阶段 1 固定未配置。
            chat="unconfigured",
            planning="unavailable",
        ),
        providers=Providers(
            embedding=ProviderStatus(
                provider=settings.embedding_provider,
                device=settings.embedding_device,
                ready=False,
            ),
            reranker=ProviderStatus(
                provider=settings.rerank_provider,
                device=settings.rerank_device,
                ready=False,
            ),
            llm=ProviderStatus(
                provider=settings.llm_provider,
                device=None,
                ready=settings.llm_configured,
            ),
        ),
    )
