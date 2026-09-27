"""SQLite 引擎、会话工厂与幂等初始化。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from app.config import Settings
from app.models import Base
from app.search.schema import ACTIVE_DATASET_INDEX_SQL, FTS_DDL

# 部分唯一索引：保证全局最多一个 queued/running 演示任务。
# 用 ``active_marker``（活动任务=1，终态=NULL）而不是 status，避免出现两个不同 status 的活动行。
ACTIVE_JOB_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_demo_seed_jobs_single_active "
    "ON demo_seed_jobs(active_marker) WHERE active_marker IS NOT NULL"
)

# fts_rowid 的部分唯一索引：与 FTS 行一一对应（NULL 不参与唯一性）
FT_SROWID_INDEX_SQL = (
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_document_chunks_fts_rowid "
    "ON document_chunks(fts_rowid) WHERE fts_rowid IS NOT NULL"
)

INDEX_STATEMENTS = (
    ACTIVE_JOB_INDEX_SQL,
    ACTIVE_DATASET_INDEX_SQL,
    FT_SROWID_INDEX_SQL,
)

# 可加列：旧数据库启动时幂等补齐，避免因缺失列导致初始化崩溃
ADDITIVE_COLUMNS = (
    ("demo_active_dataset", "active_marker", "INTEGER"),
    ("document_chunks", "fts_rowid", "INTEGER"),
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
    """幂等创建表、索引与 FTS5 虚拟表（重复调用不报错）。

    ``create_all`` **不会**给已存在的表补列，因此对新增的**可加列**做一次
    幂等增量迁移，否则沿用旧数据库启动时会因为索引引用缺失列而直接崩溃。
    """
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        _ensure_additive_columns(connection)
        for statement in INDEX_STATEMENTS:
            connection.exec_driver_sql(statement)
        # FTS5 索引结构
        connection.exec_driver_sql(FTS_DDL)


def _ensure_additive_columns(connection) -> None:
    """为既有表补齐新增的可加列（只做 ALTER TABLE ADD COLUMN，不删不改）。"""
    for table, column, ddl_type in ADDITIVE_COLUMNS:
        rows = connection.exec_driver_sql(f"PRAGMA table_info({table})").all()
        if not rows:  # 表尚未创建
            continue
        existing = {row[1] for row in rows}
        if column not in existing:
            connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")


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
