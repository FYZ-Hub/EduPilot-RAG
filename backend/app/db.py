"""SQLite 引擎、会话工厂与幂等初始化。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import Base

# 部分唯一索引：保证全局最多一个 queued/running 演示任务。
# 用 ``active_marker``（活动任务=1，终态=NULL）而不是 status，避免出现两个不同 status 的活动行。
ACTIVE_JOB_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_demo_seed_jobs_single_active "
    "ON demo_seed_jobs(active_marker) WHERE active_marker IS NOT NULL"
)

_SQLITE_PREFIX = "sqlite:///"


def sqlite_file_path(database_url: str) -> Path | None:
    """从 ``sqlite:///`` URL 解析文件路径；内存库返回 None。"""
    if not database_url.startswith(_SQLITE_PREFIX):
        return None
    raw = database_url[len(_SQLITE_PREFIX):]
    if not raw or raw == ":memory:":
        return None
    return Path(raw)


def ensure_database_directory(database_url: str) -> None:
    path = sqlite_file_path(database_url)
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)


def create_db_engine(settings: Settings) -> Engine:
    """创建引擎并固定 SQLite PRAGMA（WAL / busy_timeout / foreign_keys）。"""
    ensure_database_directory(settings.database_url)
    engine = create_engine(
        settings.database_url,
        connect_args={
            "check_same_thread": False,
            "timeout": settings.sqlite_busy_timeout_ms / 1000,
        },
        future=True,
    )
    busy_timeout = int(settings.sqlite_busy_timeout_ms)

    @event.listens_for(engine, "connect")
    def _configure_sqlite(dbapi_connection, _record) -> None:  # pragma: no cover - 驱动回调
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute(f"PRAGMA busy_timeout={busy_timeout}")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

    return engine


def init_database(engine: Engine) -> None:
    """幂等创建表与索引（重复调用不报错）。"""
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(ACTIVE_JOB_INDEX_SQL)


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """短事务：正常提交，异常回滚，始终关闭。"""
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
