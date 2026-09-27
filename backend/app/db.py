"""SQLite 引擎、会话工厂与幂等初始化。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.dialects import sqlite as sqlite_dialect_module
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateIndex, CreateTable

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

# 学业表外键的期望删除语义由 ``Base.metadata`` 唯一决定（见 models.py 的 ondelete）。
# 阶段 7A 建库时这些外键是 NO ACTION，且 ``create_all`` 不会修改既有外键，
# 因此启动时必须按下面顺序幂等重建，期望语义从模型元数据实时读取、不写第二份定义。
ACADEMIC_MIGRATION_TABLES: tuple[str, ...] = (
    "academic_record_sets",
    "course_records",
    "academic_rule_sets",
    "degree_rules",
    "degree_rule_courses",
    "academic_projections",
)

_SQLITE_DIALECT = sqlite_dialect_module.dialect()


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
    # 外键删除语义无法用 ALTER TABLE 修改，必须在独立连接上重建表
    _migrate_academic_foreign_keys(engine)


def _ensure_additive_columns(connection) -> None:
    """为既有表补齐新增的可加列（只做 ALTER TABLE ADD COLUMN，不删不改）。"""
    for table, column, ddl_type in ADDITIVE_COLUMNS:
        rows = connection.exec_driver_sql(f"PRAGMA table_info({table})").all()
        if not rows:  # 表尚未创建
            continue
        existing = {row[1] for row in rows}
        if column not in existing:
            connection.exec_driver_sql(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")


def _expected_ondelete(table) -> dict[str, str]:
    """从模型元数据读取一张表**期望的**外键删除语义。"""
    expected: dict[str, str] = {}
    for column in table.columns:
        for key in column.foreign_keys:
            expected[column.name] = (key.ondelete or "NO ACTION").upper()
    return expected


def _current_ondelete(connection, table_name: str) -> dict[str, str]:
    """读取既有表的实际外键删除语义（``PRAGMA foreign_key_list``）。"""
    rows = connection.exec_driver_sql(f'PRAGMA foreign_key_list("{table_name}")').all()
    return {row[3]: str(row[6]).upper() for row in rows}


def _academic_tables_needing_rebuild(engine: Engine) -> list:
    """返回外键删除语义与当前模型不一致、需要重建的学业表（不存在则跳过）。"""
    pending: list = []
    with engine.connect() as connection:
        existing = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).all()
        }
        for name in ACADEMIC_MIGRATION_TABLES:
            if name not in existing:
                continue
            table = Base.metadata.tables[name]
            if _current_ondelete(connection, name) != _expected_ondelete(table):
                pending.append(table)
    return pending


def _rebuild_academic_table(cursor, table) -> None:
    """按 SQLite 官方推荐流程重建单张表，保留全部行与索引。

    ``legacy_alter_table`` 必须打开：否则 ``RENAME`` 会把**其它表**中指向本表的
    外键改写到临时名，造成悬空引用。
    """
    name = table.name
    legacy_name = f"{name}__legacy"
    columns = ", ".join(f'"{column.name}"' for column in table.columns)
    cursor.execute(f'ALTER TABLE "{name}" RENAME TO "{legacy_name}"')
    cursor.execute(str(CreateTable(table).compile(dialect=_SQLITE_DIALECT)))
    cursor.execute(f'INSERT INTO "{name}" ({columns}) SELECT {columns} FROM "{legacy_name}"')
    # 先删旧表（同时释放其索引名），再重建索引，避免同名索引冲突
    cursor.execute(f'DROP TABLE "{legacy_name}"')
    for index in table.indexes:
        cursor.execute(str(CreateIndex(index).compile(dialect=_SQLITE_DIALECT)))


def _migrate_academic_foreign_keys(engine: Engine) -> None:
    """把阶段 7A 旧库的学业表外键删除语义幂等迁移到当前模型。

    SQLite 无法用 ``ALTER TABLE`` 修改既有外键，只能重建表：关闭外键 →
    改旧表名 → 按当前模型建表 → 拷数据 → 删旧表 → 重建索引。整个过程在**单个事务**
    内完成：先校验 ``PRAGMA foreign_key_check`` 无结果再提交，否则回滚，绝不删除
    数据库、绝不丢弃既有 record / rule / projection 数据。
    """
    if engine.dialect.name != "sqlite":
        return
    pending = _academic_tables_needing_rebuild(engine)
    if not pending:
        return

    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        try:
            cursor.execute("PRAGMA foreign_keys=OFF")
            cursor.execute("PRAGMA legacy_alter_table=ON")
            cursor.execute("BEGIN")
            try:
                for table in pending:
                    _rebuild_academic_table(cursor, table)
                violations = cursor.execute("PRAGMA foreign_key_check").fetchall()
                if violations:
                    raise RuntimeError(f"外键迁移后存在悬空引用：{violations[:5]}")
                cursor.execute("COMMIT")
            except Exception:
                try:
                    cursor.execute("ROLLBACK")
                except Exception:  # 事务可能已随错误结束，回滚失败不掩盖原始异常
                    pass
                raise
            finally:
                cursor.execute("PRAGMA legacy_alter_table=OFF")
                cursor.execute("PRAGMA foreign_keys=ON")
        finally:
            cursor.close()
    finally:
        raw.close()


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
