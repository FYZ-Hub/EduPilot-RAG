"""健康检查路由。

阶段 7 完成后：Chat 与学业规划均已实现，因此 ``capabilities.chat`` 为
``unconfigured`` / ``unavailable`` / ``ready`` 三态，``capabilities.planning`` 为
``ready``（规划路由与确定性引擎已注册、学业数据结构可用）。

语义要点：

- ``capabilities.documents`` 表示**文档子系统是否可用**：文档结构与文档查询可正常执行即为
  ``ready``，**空库同样是 ready**。它与「当前是否已有可检索文档」是两件事 ——
  若把空库判成 ``unavailable``，前端按 UI_SPEC 2.4 会禁用上传与 seed
  （BUG-8-PRE-01 的启动死锁）；只有文档表缺失或相关查询确实失败时才为 ``unavailable``。
- 可检索文档**数量**只用于 ``capabilities.chat`` 是否具备检索语料；
  ``_retrievable_documents()`` 返回 ``int | None`` 以区分「查询成功但为 0」与「查询失败」。
- ``capabilities.chat`` 表示**前端是否可以发起 Chat 请求**：LLM 配置完整、Chat 已实现
  且存在当前可检索文档时为 ``ready``。它**不绑定** ``providers.llm.loaded``，
  否则会死锁：chat != ready 时前端不允许建立 SSE，而没有第一次 SSE 调用
  就永远无法把 ``loaded`` 变成 true。
- ``capabilities.planning`` 只表示「规划能力是否可用」，**不依赖**库里是否已经有可选的
  record / rule set（空库同样是 ready，``POST /api/academic/plan`` 对不存在的 ID 正常返回 4xx）。
- ``status`` 由**能力前置条件**动态计算（``_service_status``）：三项能力全部 ``ready``
  才是 ``healthy``，否则 ``degraded``。它**不参与**判定 Provider 是否已成功调用，
  因此 API Provider 在首次问答前 ``ready=false`` 不会把服务锁死在 ``degraded``；
  反过来，``healthy`` 也**只**表示前置条件满足，**不代表**端到端模型调用或模型质量
  已经验证通过。
- ``providers.*.ready`` 只反映 Provider **真实**的成功证据：API Provider 在首次成功
  调用前为 false，失败或 close 后恢复 false；本地模型权重未加载时一律 false。
- 可检索文档统计复用检索侧 ``RetrievalScope`` + eligibility 口径，
  candidate / inactive / 旧 pipeline 指纹文档**都不算**可检索。
- 健康检查本身不加载模型、不访问网络、不下载权重，也不运行任何规划计算。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text

from app.api.deps import AppContext, get_context, get_settings_dep
from app.config import Settings
from app.db import session_scope
from app.schemas.health import (
    Capabilities,
    HealthResponse,
    ProviderStatus,
    Providers,
    ServiceStatus,
)
from app.search.eligibility import DOC_ALIAS, ELIGIBILITY_FROM, build_eligibility
from app.search.hydrate import build_scope

router = APIRouter(tags=["health"])


def _retrievable_documents(context: AppContext) -> int | None:
    """统计真正可检索的文档数。

    必须区分两种「0 之外」的语义：

    - ``0``：查询**成功**，只是当前没有可检索文档；
    - ``None``：查询**失败**，无法判断。

    绝不把「空库」当成「子系统不可用」，也绝不谎报 ready。
    """
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
        return None


def _documents_capability(context: AppContext, retrievable: int | None) -> str:
    """文档**子系统**是否可用：文档结构与文档查询可正常执行即为 ``ready``。

    这与「当前是否已有可检索文档」是两件事：空库同样是 ``ready``，
    否则前端按 UI_SPEC 2.4 会禁用上传与 seed，用户永远无法添加第一份文档。
    只有当文档表缺失或相关查询确实失败时才降级为 ``unavailable``。
    """
    if retrievable is None:
        return "unavailable"
    try:
        with session_scope(context.session_factory) as session:
            session.execute(text("SELECT 1 FROM documents LIMIT 1")).all()
        return "ready"
    except Exception:  # noqa: BLE001 - 健康检查不得因数据库问题抛出
        return "unavailable"


def _chat_capability(context: AppContext, retrievable: int | None) -> str:
    """Chat 是否允许前端发起请求；**不依赖** llm.loaded，避免首次调用死锁。"""
    if not context.llm.configured:
        return "unconfigured"
    if not retrievable:
        # 0（没有检索语料）或 None（无法判断）都不允许发起 Chat
        return "unavailable"
    return "ready"


def _planning_capability(context: AppContext) -> str:
    """规划能力可用性：只做一次轻量的学业结构探测。

    规划路由与确定性引擎在导入期即已注册；这里只确认学业数据结构可读。
    **不执行**任何规划计算、不加载数据集、不下载模型、不访问网络；
    空库（没有任何可选集合）同样是 ``ready``。
    """
    try:
        with session_scope(context.session_factory) as session:
            session.execute(text("SELECT 1 FROM academic_record_sets LIMIT 1")).all()
            session.execute(text("SELECT 1 FROM academic_rule_sets LIMIT 1")).all()
        return "ready"
    except Exception:  # noqa: BLE001 - 健康检查不得因数据库问题抛出
        return "unavailable"


def _service_status(capabilities: Capabilities) -> ServiceStatus:
    """按能力**前置条件**汇总服务状态：三项能力全部 ``ready`` 才是 ``healthy``。

    只看能力前置条件，**不看** Provider 是否已经发生过成功调用：

    - API Provider 在第一次问答之前 ``ready=false`` 属正常现象，用它判定 ``degraded``
      会把服务锁死，等于阻止第一次问答；
    - 因此 ``healthy`` 只表示「文档子系统可用 + LLM 已配置且存在可检索语料 +
      学业结构可读」三项前置条件成立，**不代表**端到端模型调用、模型质量或引用
      正确性已经验证通过。

    空知识库时 ``chat`` 为 ``unavailable``（没有可检索语料），因此状态为 ``degraded``，
    但这**不影响**文档功能与上传：``capabilities.documents`` 仍为 ``ready``。
    """
    states = (capabilities.documents, capabilities.chat, capabilities.planning)
    return "healthy" if all(state == "ready" for state in states) else "degraded"


@router.get("/health", response_model=HealthResponse)
def read_health(
    settings: Settings = Depends(get_settings_dep),
    context: AppContext = Depends(get_context),
) -> HealthResponse:
    """返回进程状态与能力信息。

    ``status`` 由能力前置条件动态计算（``healthy`` / ``degraded``），不加载模型、
    不访问网络、不运行规划计算。
    """
    # 只有 Provider 真的加载了模型/取得过真实成功证据才报告 ready，绝不谎报
    embedding_ready = bool(context.embeddings.loaded)
    reranker_ready = bool(context.reranker.loaded)
    llm_ready = bool(context.llm.loaded)
    # 阶段 8 前置：documents 表示**子系统可用性**（空库同样 ready），
    # 可检索数量只用于 chat 是否具备检索语料
    retrievable = _retrievable_documents(context)
    capabilities = Capabilities(
        documents=_documents_capability(context, retrievable),
        chat=_chat_capability(context, retrievable),
        planning=_planning_capability(context),
    )
    return HealthResponse(
        status=_service_status(capabilities),
        version=settings.app_version,
        capabilities=capabilities,
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
