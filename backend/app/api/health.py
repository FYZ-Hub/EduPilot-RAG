"""健康检查路由。

阶段 2B 只完成上传与解析（达到 ``parsed``），尚未建立任何向量或 FTS 索引，
因此文档能力仍不得声明为 ready，整体状态继续保持 ``degraded``。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import get_settings_dep
from app.config import Settings
from app.schemas.health import Capabilities, HealthResponse, ProviderStatus, Providers

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def read_health(settings: Settings = Depends(get_settings_dep)) -> HealthResponse:
    """返回进程状态与能力信息；未接入检索前固定为 degraded。"""
    return HealthResponse(
        status="degraded",
        version=settings.app_version,
        capabilities=Capabilities(
            # 上传与解析已可用，但完整文档检索能力（向量/FTS/引用）尚未建立
            documents="unavailable",
            # 聊天能力依赖后续阶段实现与 LLM 配置
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
