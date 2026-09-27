"""健康检查路由。

阶段 3 已完成确定性切片与 Chroma 向量索引，但仍缺少 FTS5 与混合检索，
因此文档能力仍不得声明为 ready，``embedding.ready`` 也不得谎报为 true：
本地模型权重没有加载、API Provider 没有连通性探测之前，一律返回 false。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.deps import AppContext, get_context, get_settings_dep
from app.config import Settings
from app.schemas.health import Capabilities, HealthResponse, ProviderStatus, Providers

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
def read_health(
    settings: Settings = Depends(get_settings_dep),
    context: AppContext = Depends(get_context),
) -> HealthResponse:
    """返回进程状态与能力信息；未接入检索前固定为 degraded。"""
    # 只有 Provider 真的加载了模型/远端客户端才报告 ready，绝不谎报
    embedding_ready = bool(context.embeddings.loaded)
    return HealthResponse(
        status="degraded",
        version=settings.app_version,
        capabilities=Capabilities(
            # 切片与向量已就绪，但完整文档检索能力（FTS/混合检索/引用）尚未建立
            documents="unavailable",
            # 聊天能力依赖后续阶段实现与 LLM 配置
            chat="unconfigured",
            planning="unavailable",
        ),
        providers=Providers(
            embedding=ProviderStatus(
                provider=settings.embedding_provider,
                device=settings.embedding_device,
                ready=embedding_ready,
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
