"""健康检查路由。

阶段 6 完成后：Chat 已实现，因此 ``capabilities.chat`` 为
``unconfigured`` / ``unavailable`` / ``ready`` 三态；整体 ``status`` 仍为 ``degraded``
（学业规划规则引擎尚未接入）。

语义要点：

- ``capabilities.chat`` 表示**前端是否可以发起 Chat 请求**：LLM 配置完整、Chat 已实现
  且存在当前可检索文档时为 ``ready``。它**不绑定** ``providers.llm.loaded``，
  否则会死锁：chat != ready 时前端不允许建立 SSE，而没有第一次 SSE 调用
  就永远无法把 ``loaded`` 变成 true。
- ``providers.*.ready`` 只反映 Provider **真实**的成功证据：API Provider 在首次成功
  调用前为 false，失败或 close 后恢复 false；本地模型权重未加载时一律 false。
- 可检索文档统计复用检索侧 ``RetrievalScope`` + eligibility 口径，
  candidate / inactive / 旧 pipeline 指纹文档**都不算**可检索。
- 健康检查本身不加载模型、不访问网络、不下载权重。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.api.deps import AppContext, get_context, get_settings_dep
from app.config import Settings
from app.db import session_scope
from app.schemas.health import Capabilities, HealthResponse, ProviderStatus, Providers
from app.search.eligibility import DOC_ALIAS, ELIGIBILITY_FROM, build_eligibility
from app.search.hydrate import build_scope

router = APIRouter(tags=["health"])


def _retrievable_documents(context: AppContext) -> int:
    """统计真正可检索的文档数；失败时按“不可用”处理，绝不谎报 ready。"""
    try:
        with session_scope(context.session_factory) as session:
            scope = build_scope(session, context.settings)
            clause, params = build_eligibility(scope)
            total = session.execute(
                text(f"SELECT COUNT(DISTINCT {DOC_ALIAS}.id) {ELIGIBILITY_FROM} WHERE {clause}"),
                params,
            ).scalar()
            return int(total or 0)
    except Exception:  # noqa: BLE001 - 健康检查不得因数据库问题抛出
        return 0


def _chat_capability(context: AppContext, retrievable: int) -> str:
    """Chat 是否允许前端发起请求；**不依赖** llm.loaded，避免首次调用死锁。"""
    if not context.llm.configured:
        return "unconfigured"
    if retrievable == 0:
        return "unavailable"
    return "ready"


@router.get("/health", response_model=HealthResponse)
def read_health(
    settings: Settings = Depends(get_settings_dep),
    context: AppContext = Depends(get_context),
) -> HealthResponse:
    """返回进程状态与能力信息；planning 未实现时整体仍为 degraded。"""
    # 只有 Provider 真的加载了模型/取得过真实成功证据才报告 ready，绝不谎报
    embedding_ready = bool(context.embeddings.loaded)
    reranker_ready = bool(context.reranker.loaded)
    llm_ready = bool(context.llm.loaded)
    # documents 能力取决于「是否真的有可检索文档」，而不是阶段编号
    retrievable = _retrievable_documents(context)
    return HealthResponse(
        status="degraded",
        version=settings.app_version,
        capabilities=Capabilities(
            documents="ready" if retrievable else "unavailable",
            chat=_chat_capability(context, retrievable),
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
                ready=llm_ready,
            ),
        ),
    )
