"""FastAPI 依赖：应用上下文、会话与模型资源。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.embedding.base import EmbeddingProvider
from app.rerank.base import RerankProvider
from app.runtime.coordinator import LocalModelCoordinator
from app.vector.store import ChromaVectorStore
from app.worker.runner import Worker


@dataclass
class AppContext:
    """进程级单例资源；由 ``create_app`` 装配。"""

    settings: Settings
    engine: Engine
    session_factory: sessionmaker[Session]
    worker: Worker
    embeddings: EmbeddingProvider
    vectors: ChromaVectorStore
    # 本地 Embedding / 本地 Reranker 共用的进程级串行协调器
    coordinator: LocalModelCoordinator
    reranker: RerankProvider


def get_context(request: Request) -> AppContext:
    return request.app.state.context


def get_settings_dep(context: AppContext = Depends(get_context)) -> Settings:
    return context.settings


def get_session(context: AppContext = Depends(get_context)) -> Iterator[Session]:
    """短事务会话：请求成功提交，异常回滚，始终关闭。"""
    session = context.session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
