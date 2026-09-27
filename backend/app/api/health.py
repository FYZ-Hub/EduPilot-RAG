"""健康检查路由。

阶段 5 已完成混合检索与 Reranker，但整体仍为 ``degraded``：Chat 尚未实现、
LLM 未配置、学业规划未实现。``documents`` 能力只由“是否真的有可检索文档”决定。

``embedding.ready`` / ``reranker.ready`` 只能反映 Provider **真实**的 loaded 状态：
本地模型权重没有加载、API Provider 没有连通性探测之前，一律返回 false。
健康检查本身不加载模型、不访问网络、不下载权重。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from app import constants
from app.api.deps import AppContext, get_context, get_settings_dep
from app.config import Settings
from app.db import session_scope
from app.models import Document, DocumentPipelineState
from app.schemas.health import Capabilities, HealthResponse, ProviderStatus, Providers

router = APIRouter(tags=["health"])


def _retrievable_documents(context: AppContext) -> int:
    """统计真正可检索的文档数；失败时按“不可用”处理，绝不谎报 ready。"""
    try:
        with session_scope(context.session_factory) as session:
            return int(
                session.scalar(
                    select(func.count(Document.id))
                    .join(DocumentPipelineState, DocumentPipelineState.doc_id == Document.id)
                    .where(
                        Document.deleted_at.is_(None),
                        Document.status == constants.STATUS_READY,
                        Document.retrievable.is_(True),
                        DocumentPipelineState.last_completed_stage
                        == constants.STAGE_COMPLETED,
                    )
                )
                or 0
            )
    except Exception:  # noqa: BLE001 - 健康检查不得因数据库问题抛出
        return 0


@router.get("/health", response_model=HealthResponse)
def read_health(
    settings: Settings = Depends(get_settings_dep),
    context: AppContext = Depends(get_context),
) -> HealthResponse:
    """返回进程状态与能力信息；阶段 5 完成后整体仍为 degraded。"""
    # 只有 Provider 真的加载了模型/远端客户端才报告 ready，绝不谎报
    embedding_ready = bool(context.embeddings.loaded)
    # Reranker 是查询时能力：读取 Provider 真实的 loaded 状态，而不是配置是否填写
    reranker_ready = bool(context.reranker.loaded)
    # documents 能力取决于「是否真的有可检索文档」，而不是阶段编号
    retrievable = _retrievable_documents(context)
    return HealthResponse(
        status="degraded",
        version=settings.app_version,
        capabilities=Capabilities(
            documents="ready" if retrievable else "unavailable",
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
                ready=reranker_ready,
            ),
            llm=ProviderStatus(
                provider=settings.llm_provider,
                device=None,
                ready=settings.llm_configured,
            ),
        ),
    )
