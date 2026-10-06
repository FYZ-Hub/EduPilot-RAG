"""真实检索链装配：隔离 SQLite + Chroma + FTS5，Embedding / Reranker 使用 Fake Provider。

与生产运行的区别只有两处，且都在配置层：

1. 数据库、Chroma、上传与模型缓存目录全部指向调用方给定的隔离目录；
2. ``embedding_provider`` / ``rerank_provider`` 固定为 ``fake``（离线、确定性）。

解析、切片、向量化、FTS5 索引、RRF 融合与重排全部复用生产实现，未做任何简化。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import TYPE_CHECKING, Iterator

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.db import create_db_engine, create_session_factory, init_database
from app.demo import service as demo_service
from app.embedding.base import EmbeddingProvider
from app.llm.factory import build_llm_provider
from app.rerank.base import RerankProvider
from app.rerank.factory import build_rerank_provider
from app.runtime.coordinator import LocalModelCoordinator
from app.search.dense import DenseRetriever
from app.search.eligibility import ELIGIBILITY_FROM, build_where
from app.search.hybrid import HybridRetriever
from app.search.hydrate import build_scope
from app.search.keyword import KeywordRetriever
from app.search.reranking import RERANK_MAX_CANDIDATES, RerankingRetriever
from app.search.types import RetrievalFilters
from app.vector.store import ChromaVectorStore
from app.worker.runner import Worker

from eval_tools.matching import RetrievedSource
from eval_tools.qa.providers import (
    AuditRegistry,
    AuditedEmbedding,
    AuditedLLM,
    AuditedReranker,
    JudgeAdapter,
)
from eval_tools.qa.sideeffects import (
    IsolationBaseline,
    IsolationSnapshotter,
    build_snapshotter,
    establish_baseline,
)
from eval_tools.runner import RankedTrace, RouteProbe

if TYPE_CHECKING:  # 仅类型标注，避免与 executor 形成运行时循环导入
    from eval_tools.qa.executor import CitationJudge


def build_eval_settings(
    work_dir: Path,
    *,
    demo_dataset_path: Path,
    demo_dataset_version: str,
) -> Settings:
    """构造隔离评测配置：临时目录 + Fake Provider + 关闭后台线程。"""
    work_dir.mkdir(parents=True, exist_ok=True)
    return Settings(
        app_env="eval",
        log_level="WARNING",
        database_url=f"sqlite:///{work_dir / 'app.db'}",
        chroma_path=str(work_dir / "chroma"),
        upload_path=str(work_dir / "uploads"),
        model_cache_path=str(work_dir / "models"),
        demo_dataset_path=str(demo_dataset_path),
        demo_dataset_version=demo_dataset_version,
        embedding_provider="fake",
        rerank_provider="fake",
        llm_provider="fake",
        worker_enabled=False,
        worker_poll_seconds=0.05,
        demo_job_poll_seconds=1,
        demo_job_lease_seconds=60,
    )


def _identity(source_key: str, file_name: str) -> str:
    return source_key or file_name


# --- semantic_api 装配 -------------------------------------------------------

# semantic profile 要求的 Provider kind（任一不符即视为 profile 选择错误）
SEMANTIC_PROVIDER_KINDS = {
    "embedding": "api",
    "rerank": "api",
    "llm": "openai_compatible",
}


class SemanticConfigError(RuntimeError):
    """semantic profile 配置不合法。

    只携带**配置键名**与稳定 reason，绝不携带任何配置值（base_url / api_key / model）。
    """

    def __init__(self, reason: str, keys: tuple[str, ...] = ()):
        super().__init__(reason)
        self.reason = reason
        self.keys = keys


@dataclass(frozen=True)
class SemanticConfigStatus:
    """三组 Provider 的**配置齐备性**（纯布尔，不携带任何配置值）。"""

    embedding: bool
    rerank: bool
    llm: bool
    missing: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.embedding and self.rerank and self.llm


def build_semantic_settings(base: Settings, work_dir: Path) -> Settings:
    """构造 semantic_api 的隔离配置。

    - **不读取 ``.env``**：``base`` 由调用方自行创建并传入；
    - **强制** SQLite / Chroma / uploads / models 全部落在 ``work_dir`` 下（覆盖 ``base`` 的任何取值）；
    - 关闭后台轮询线程并固定轮询参数（评测要求确定性、不引入后台写库）；
    - 其余字段（含真实 Provider 配置）原样保留；``base`` 本身不被修改。
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    return base.model_copy(
        update={
            "database_url": f"sqlite:///{work_dir / 'app.db'}",
            "chroma_path": str(work_dir / "chroma"),
            "upload_path": str(work_dir / "uploads"),
            "model_cache_path": str(work_dir / "models"),
            "worker_enabled": False,
            "demo_job_poll_seconds": 1,
            "demo_job_lease_seconds": 60,
        }
    )


def _triple_present(settings: Settings, prefix: str, missing: list[str]) -> bool:
    present = True
    for suffix in ("base_url", "model", "api_key"):
        key = f"{prefix}_{suffix}"
        if not str(getattr(settings, key, "") or "").strip():
            present = False
            missing.append(key)
    return present


def validate_semantic_config(settings: Settings) -> SemanticConfigStatus:
    """校验 semantic profile 的 Provider kind 与三组凭据齐备性。

    - **kind 不符**（embedding≠api / rerank≠api / llm≠openai_compatible）→ 抛
      ``SemanticConfigError``：这是 profile 选择错误，必须显式失败而不是静默回退；
    - **凭据缺失**（base_url / model / api_key 任一为空）→ 对应 Provider 记 ``False``，
      审计据此初始化为 ``UNCONFIGURED``，**不得默认已配置**。
    """
    mismatched = tuple(
        f"{name}_provider"
        for name, required in SEMANTIC_PROVIDER_KINDS.items()
        if str(getattr(settings, f"{name}_provider", "") or "") != required
    )
    if mismatched:
        raise SemanticConfigError("provider_kind_mismatch", mismatched)

    missing: list[str] = []
    embedding = _triple_present(settings, "embedding", missing)
    rerank = _triple_present(settings, "rerank", missing)
    llm = _triple_present(settings, "llm", missing)
    return SemanticConfigStatus(
        embedding=embedding,
        rerank=rerank,
        llm=llm,
        missing=tuple(missing),
    )


def indexed_source_identities(
    session_factory: sessionmaker[Session], settings: Settings
) -> frozenset[str]:
    """当前索引中「可检索」的来源标识集合（与检索资格同一套 SQL 条件）。"""
    with session_factory() as session:
        scope = build_scope(session, settings)
        where_sql, params = build_where(scope, RetrievalFilters())
        rows = session.execute(
            text(
                "SELECT DISTINCT d.source_key, d.file_name "
                f"{ELIGIBILITY_FROM} WHERE 1 = 1 {where_sql}"
            ),
            params,
        ).all()
    return frozenset(_identity(row[0] or "", row[1] or "") for row in rows)


class PipelineRetrievalChain:
    """基于真实生产检索实现的评测链。"""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        vectors: ChromaVectorStore,
        embeddings: EmbeddingProvider,
        reranker: RerankProvider,
        coordinator: LocalModelCoordinator | None = None,
    ):
        self.session_factory = session_factory
        self.settings = settings
        self.vectors = vectors
        self.embeddings = embeddings
        self.reranker = reranker
        self.coordinator = coordinator

    def indexed_identities(self) -> frozenset[str]:
        return indexed_source_identities(self.session_factory, self.settings)

    def ranked(self, query: str, top_k: int) -> RankedTrace:
        started = perf_counter()
        with self.session_factory() as session:
            results, diagnostics = RerankingRetriever(
                session,
                self.settings,
                self.vectors,
                self.embeddings,
                self.reranker,
                self.coordinator,
            ).search(query, RetrievalFilters(), top_k=top_k)
        elapsed_ms = (perf_counter() - started) * 1000.0
        ranked = tuple(
            RetrievedSource(source_key=item.chunk.source_key, file_name=item.chunk.file_name)
            for item in results
        )
        return RankedTrace(
            ranked=ranked,
            elapsed_ms=elapsed_ms,
            rerank_applied=diagnostics.rerank_applied,
            degraded_reason=diagnostics.degraded_reason,
        )

    def probe(self, query: str) -> RouteProbe:
        with self.session_factory() as session:
            dense = DenseRetriever(
                session,
                self.settings,
                self.vectors,
                self.embeddings,
                self.coordinator,
            ).search(query, RetrievalFilters())
            keyword, _mode = KeywordRetriever(session, self.settings).search(
                query, RetrievalFilters()
            )
            fused, _diag = HybridRetriever(
                session,
                self.settings,
                self.vectors,
                self.embeddings,
                self.coordinator,
            ).search(query, RetrievalFilters(), top_k=RERANK_MAX_CANDIDATES)
        return RouteProbe(
            dense=tuple(
                RetrievedSource(chunk.source_key, chunk.file_name) for chunk in dense
            ),
            keyword=tuple(
                RetrievedSource(chunk.source_key, chunk.file_name) for chunk in keyword
            ),
            fused=tuple(
                RetrievedSource(item.chunk.source_key, item.chunk.file_name)
                for item in fused
            ),
        )


@dataclass
class EvalEnvironment:
    settings: Settings
    session_factory: sessionmaker[Session]
    worker: Worker
    reranker: RerankProvider
    chain: PipelineRetrievalChain
    # semantic_api 专用：审计集合、Judge 适配器与已包装的 LLM；offline_fake 下均为 None
    audit: AuditRegistry | None = None
    judge: "CitationJudge | None" = None
    llm: object | None = None
    # semantic_api 专用：隔离快照器与 demo 导入后建立的基线；offline_fake 下均为 None
    snapshotter: IsolationSnapshotter | None = None
    isolation: IsolationBaseline | None = None

    def rebuild_chain(self) -> None:
        """用当前的 ``worker.embeddings`` 与 ``reranker`` 重建检索链（包装后必须调用）。"""
        self.chain = PipelineRetrievalChain(
            self.session_factory,
            self.settings,
            self.worker.vectors,
            self.worker.embeddings,
            self.reranker,
            self.worker.coordinator,
        )

    @property
    def embedding_provider(self) -> str:
        return self.worker.embeddings.descriptor.provider

    @property
    def rerank_provider(self) -> str:
        return self.reranker.descriptor.provider

    @property
    def rerank_score_kind(self) -> str:
        """Reranker 分数种类；决定质量门禁落在融合层还是最终层。"""
        return self.reranker.descriptor.score_kind

    def ingest_demo(self) -> None:
        """执行一次完整演示导入（真实解析/切片/索引），幂等复用生产实现。"""
        with self.session_factory() as session:
            job, _reused = demo_service.seed_job(session, self.settings)
            session.commit()
            job_id = job.id
        self.worker.run_once()
        with self.session_factory() as session:
            demo_service.serialize_job(
                session, demo_service.get_job(session, job_id), self.settings
            )
        if self.snapshotter is not None:
            # demo 导入完成后建立安全快照基线；失败即 unavailable，不得默认安全
            self.isolation = establish_baseline(self.snapshotter)


@contextmanager
def open_eval_environment(settings: Settings) -> Iterator[EvalEnvironment]:
    """打开隔离评测环境；退出时关闭 Reranker、Worker、向量库与数据库连接。"""
    engine = create_db_engine(settings)
    init_database(engine)
    session_factory = create_session_factory(engine)
    worker = Worker(settings, session_factory, worker_id="eval-worker")
    if not worker.prepare():
        engine.dispose()
        raise RuntimeError("无法获取评测 worker 进程锁")
    reranker = build_rerank_provider(settings, worker.coordinator)
    chain = PipelineRetrievalChain(
        session_factory, settings, worker.vectors, worker.embeddings, reranker, worker.coordinator
    )
    environment = EvalEnvironment(
        settings=settings,
        session_factory=session_factory,
        worker=worker,
        reranker=reranker,
        chain=chain,
    )
    try:
        yield environment
    finally:
        reranker.close()
        worker.stop()
        engine.dispose()


@contextmanager
def open_semantic_eval_environment(
    base_settings: Settings,
    work_dir: Path,
    *,
    demo_dataset_path: Path | None = None,
    demo_dataset_version: str | None = None,
) -> Iterator[EvalEnvironment]:
    """打开 semantic_api 评测环境：隔离目录 + 真实 Provider 审计包装。

    - 路径由 :func:`build_semantic_settings` 强制落在 ``work_dir``；
    - Provider kind 与凭据由 :func:`validate_semantic_config` 校验（kind 不符直接抛错，
      凭据缺失则审计为 ``UNCONFIGURED``）；
    - Worker Embedding、Reranker、LLM 全部替换为审计包装器；
    - Judge 与生成**共享同一个底层 LLM**，但分别计入 ``judge`` 与 ``llm`` 审计。

    本函数**不读取 ``.env``**，也**不发起任何请求**：首次真实调用发生在评测执行阶段。
    """
    settings = build_semantic_settings(base_settings, work_dir)
    updates: dict[str, str] = {}
    if demo_dataset_path is not None:
        updates["demo_dataset_path"] = str(demo_dataset_path)
    if demo_dataset_version is not None:
        updates["demo_dataset_version"] = demo_dataset_version
    if updates:
        settings = settings.model_copy(update=updates)

    status = validate_semantic_config(settings)
    audit = AuditRegistry(
        embedding=status.embedding, rerank=status.rerank, llm=status.llm
    )
    raw_llm = build_llm_provider(settings)
    try:
        with open_eval_environment(settings) as environment:
            environment.audit = audit
            environment.worker.embeddings = AuditedEmbedding(
                environment.worker.embeddings, audit.embedding
            )
            environment.reranker = AuditedReranker(environment.reranker, audit.rerank)
            environment.llm = AuditedLLM(raw_llm, audit.llm)
            environment.judge = JudgeAdapter(raw_llm, meter=audit.judge)
            environment.snapshotter = build_snapshotter(
                environment.session_factory, environment.worker.vectors
            )
            environment.rebuild_chain()
            yield environment
    finally:
        raw_llm.close()


__all__ = [
    "EvalEnvironment",
    "PipelineRetrievalChain",
    "SEMANTIC_PROVIDER_KINDS",
    "SemanticConfigError",
    "SemanticConfigStatus",
    "build_eval_settings",
    "build_semantic_settings",
    "indexed_source_identities",
    "open_eval_environment",
    "open_semantic_eval_environment",
    "validate_semantic_config",
]
