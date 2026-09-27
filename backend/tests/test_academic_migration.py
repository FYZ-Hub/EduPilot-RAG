"""阶段 7B-1 收尾修复轮：旧库外键迁移（BUG-7B-04）。

测试会**先构造阶段 7A 时代的旧结构**（外键为 ``NO ACTION``），再调用
``init_database``，验证幂等迁移到 ``CASCADE`` / ``SET NULL`` 且数据零丢失。
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.dialects import sqlite
from sqlalchemy.schema import CreateIndex, CreateTable

from app.db import create_db_engine, create_session_factory, init_database
from app.models import Base
from tests.conftest import build_settings

# 旧结构涉及的表（按「先子后父」的删除顺序排列）
ACADEMIC_TABLES = (
    "academic_record_sets",
    "course_records",
    "academic_rule_sets",
    "degree_rules",
    "degree_rule_courses",
    "academic_projections",
)

# 每张表**期望**的外键删除语义，与 models.py 的 ondelete 一一对应
EXPECTED_FOREIGN_KEYS: dict[str, dict[str, str]] = {
    "academic_record_sets": {"source_doc_id": "CASCADE", "source_chunk_id": "SET NULL"},
    "course_records": {
        "record_set_id": "CASCADE",
        "source_doc_id": "CASCADE",
        "source_chunk_id": "SET NULL",
    },
    "academic_rule_sets": {"source_doc_id": "CASCADE", "source_chunk_id": "SET NULL"},
    "degree_rules": {
        "rule_set_id": "CASCADE",
        "source_doc_id": "CASCADE",
        "source_chunk_id": "SET NULL",
    },
    "degree_rule_courses": {
        "rule_set_id": "CASCADE",
        "source_doc_id": "CASCADE",
        "source_chunk_id": "SET NULL",
    },
    # 投影是幂等账本：集合被删只置空引用，绝不随文档删除
    "academic_projections": {"record_set_id": "SET NULL", "rule_set_id": "SET NULL"},
}

_DIALECT = sqlite.dialect()

DOCUMENT_ID = "doc-0000-0000-0000-0000-000000000001"
CHUNK_ID = "chunk-0000-0000-0000-0000-000000000001"
RECORD_SET_ID = "11111111-1111-1111-1111-111111111111"
RULE_SET_ID = "22222222-2222-2222-2222-222222222222"
PROJECTION_ID = "33333333-3333-3333-3333-333333333333"


def _legacy_ddl(table_name: str) -> str:
    """用当前模型 DDL 去掉删除语义，得到阶段 7A 的旧结构。"""
    table = Base.metadata.tables[table_name]
    ddl = str(CreateTable(table).compile(dialect=_DIALECT))
    return ddl.replace("ON DELETE CASCADE", "ON DELETE NO ACTION").replace(
        "ON DELETE SET NULL", "ON DELETE NO ACTION"
    )


def _downgrade_to_legacy(engine) -> None:
    """把库降级为 7A 旧结构（外键为 NO ACTION），保留其余表。"""
    raw = engine.raw_connection()
    try:
        cursor = raw.cursor()
        cursor.execute("PRAGMA foreign_keys=OFF")
        for name in reversed(ACADEMIC_TABLES):
            cursor.execute(f'DROP TABLE IF EXISTS "{name}"')
        for name in ACADEMIC_TABLES:
            cursor.execute(_legacy_ddl(name))
        for name in ACADEMIC_TABLES:
            for index in Base.metadata.tables[name].indexes:
                cursor.execute(str(CreateIndex(index).compile(dialect=_DIALECT)))
        cursor.execute("PRAGMA foreign_keys=ON")
        raw.commit()
    finally:
        raw.close()


def _seed_rows(engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "INSERT INTO documents (id, source_type, source_key, file_name, file_type,"
            " mime_type, size_bytes, sha256, doc_category, status, retrievable,"
            " attempt_count, lease_generation, created_at, updated_at)"
            " VALUES (:id, 'demo', 'demo:key', 'records.xlsx', 'xlsx', 'application/x', 1,"
            " :sha, 'course_records', 'ready', 1, 0, 0, '2026-01-01', '2026-01-01')",
            {"id": DOCUMENT_ID, "sha": "a" * 64},
        )
        connection.exec_driver_sql(
            "INSERT INTO document_chunks (id, doc_id, chunk_index, text, locator, citation,"
            " parser_version, chunker_version, chunker_fingerprint, created_at)"
            " VALUES (:id, :doc, 0, '正文', '{}', '{}', 'p1', 'c1', :fp, '2026-01-01')",
            {"id": CHUNK_ID, "doc": DOCUMENT_ID, "fp": "b" * 64},
        )
        connection.exec_driver_sql(
            "INSERT INTO academic_record_sets (id, source_type, source_key, status,"
            " display_name, record_count, content_hash, source_doc_id, source_chunk_id,"
            " created_at, updated_at)"
            " VALUES (:id, 'demo', 'demo:key', 'ready', '匿名学生A · 课程记录', 1, 'h1',"
            " :doc, :chunk, '2026-01-01', '2026-01-01')",
            {"id": RECORD_SET_ID, "doc": DOCUMENT_ID, "chunk": CHUNK_ID},
        )
        connection.exec_driver_sql(
            "INSERT INTO course_records (id, record_set_id, ordinal, course_code, course_name,"
            " credits, category, status, source_doc_id, source_chunk_id)"
            " VALUES ('rec-1', :set, 0, 'QM-CS101', '程序设计基础', '4.0', '专业必修',"
            " 'passed', :doc, :chunk)",
            {"set": RECORD_SET_ID, "doc": DOCUMENT_ID, "chunk": CHUNK_ID},
        )
        connection.exec_driver_sql(
            "INSERT INTO academic_rule_sets (id, source_type, source_key, status, display_name,"
            " major, admission_year, rule_version, required_credits, category_order,"
            " course_count, content_hash, source_doc_id, source_chunk_id, created_at, updated_at)"
            " VALUES (:id, 'demo', 'demo:key:rule', 'ready', '计算机科学与技术培养方案 2026.1',"
            " '计算机科学与技术', 2025, '2026.1', '160.0', '[]', 1, 'h2', :doc, :chunk,"
            " '2026-01-01', '2026-01-01')",
            {"id": RULE_SET_ID, "doc": DOCUMENT_ID, "chunk": CHUNK_ID},
        )
        connection.exec_driver_sql(
            "INSERT INTO degree_rules (id, rule_set_id, ordinal, major, admission_year,"
            " rule_version, category, minimum_credits, required_course_codes, source_doc_id,"
            " source_chunk_id)"
            " VALUES ('rule-1', :set, 0, '计算机科学与技术', 2025, '2026.1', '专业必修', '60.0',"
            " '[]', :doc, :chunk)",
            {"set": RULE_SET_ID, "doc": DOCUMENT_ID, "chunk": CHUNK_ID},
        )
        connection.exec_driver_sql(
            "INSERT INTO degree_rule_courses (id, rule_set_id, ordinal, course_code,"
            " course_name, credits, category, source_doc_id, source_chunk_id)"
            " VALUES ('course-1', :set, 0, 'QM-CS101', '程序设计基础', '4.0', '专业必修',"
            " :doc, :chunk)",
            {"set": RULE_SET_ID, "doc": DOCUMENT_ID, "chunk": CHUNK_ID},
        )
        connection.exec_driver_sql(
            "INSERT INTO academic_projections (id, source_type, source_key, dataset_version,"
            " projection_fingerprint, record_set_id, rule_set_id, status, created_at, updated_at)"
            " VALUES (:id, 'demo', 'demo:key', '2026.1', 'fp', :rec, :rule, 'ready',"
            " '2026-01-01', '2026-01-01')",
            {"id": PROJECTION_ID, "rec": RECORD_SET_ID, "rule": RULE_SET_ID},
        )


def _foreign_keys(engine, table: str) -> dict[str, str]:
    with engine.connect() as connection:
        rows = connection.exec_driver_sql(f'PRAGMA foreign_key_list("{table}")').all()
    return {row[3]: row[6] for row in rows}


def _row_counts(engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: connection.exec_driver_sql(f'SELECT COUNT(*) FROM "{table}"').scalar()
            for table in ACADEMIC_TABLES
        }


def _legacy_database(tmp_path):
    settings = build_settings(tmp_path)
    engine = create_db_engine(settings)
    init_database(engine)
    _downgrade_to_legacy(engine)
    _seed_rows(engine)
    return settings, engine


# --- 修复前必须失败：旧库外键仍为 NO ACTION ---------------------------------


def test_legacy_tables_start_with_no_action_foreign_keys(tmp_path) -> None:
    """前提确认：降级后的旧库确实是 NO ACTION（否则后面的断言没有意义）。"""
    _settings, engine = _legacy_database(tmp_path)
    try:
        for table in ACADEMIC_TABLES:
            keys = _foreign_keys(engine, table)
            for column in keys:
                assert keys[column] == "NO ACTION", f"{table}.{column} 应为旧结构 NO ACTION"
    finally:
        engine.dispose()


def test_init_database_migrates_legacy_foreign_keys(tmp_path) -> None:
    _settings, engine = _legacy_database(tmp_path)
    try:
        init_database(engine)
        for table in ACADEMIC_TABLES:
            assert _foreign_keys(engine, table) == EXPECTED_FOREIGN_KEYS[table], (
                f"{table} 的外键删除语义未迁移到期望值"
            )
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        engine.dispose()


def test_migration_preserves_existing_rows(tmp_path) -> None:
    _settings, engine = _legacy_database(tmp_path)
    try:
        before = _row_counts(engine)
        init_database(engine)
        assert _row_counts(engine) == before
        with engine.connect() as connection:
            assert (
                connection.exec_driver_sql(
                    "SELECT source_doc_id, source_chunk_id FROM academic_record_sets"
                ).one()
                == (DOCUMENT_ID, CHUNK_ID)
            )
    finally:
        engine.dispose()


def test_migration_is_idempotent(tmp_path) -> None:
    _settings, engine = _legacy_database(tmp_path)
    try:
        before = _row_counts(engine)
        init_database(engine)
        first = {table: _foreign_keys(engine, table) for table in ACADEMIC_TABLES}
        init_database(engine)
        second = {table: _foreign_keys(engine, table) for table in ACADEMIC_TABLES}
        assert first == second
        assert _row_counts(engine) == before
        with engine.connect() as connection:
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
    finally:
        engine.dispose()


def test_migrated_chunk_delete_sets_academic_source_chunk_to_null(tmp_path) -> None:
    _settings, engine = _legacy_database(tmp_path)
    try:
        init_database(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "DELETE FROM document_chunks WHERE id = :id", {"id": CHUNK_ID}
            )
        with engine.connect() as connection:
            for table in ("academic_record_sets", "course_records", "academic_rule_sets",
                          "degree_rules", "degree_rule_courses"):
                rows = connection.exec_driver_sql(
                    f'SELECT source_chunk_id FROM "{table}"'
                ).all()
                assert rows, f"{table} 应有数据"
                assert all(row[0] is None for row in rows), f"{table}.source_chunk_id 应被置空"
    finally:
        engine.dispose()


def test_migrated_document_delete_cascades_academic_sets(tmp_path) -> None:
    _settings, engine = _legacy_database(tmp_path)
    try:
        init_database(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql("DELETE FROM documents WHERE id = :id", {"id": DOCUMENT_ID})
            connection.exec_driver_sql("DELETE FROM document_chunks WHERE doc_id = :id", {"id": DOCUMENT_ID})
        with engine.connect() as connection:
            counts = {
                table: connection.exec_driver_sql(f'SELECT COUNT(*) FROM "{table}"').scalar()
                for table in ACADEMIC_TABLES
            }
        assert counts["academic_record_sets"] == 0
        assert counts["course_records"] == 0
        assert counts["academic_rule_sets"] == 0
        assert counts["degree_rules"] == 0
        assert counts["degree_rule_courses"] == 0
        assert counts["academic_projections"] == 1, "投影记录不随文档删除，只置空引用"
        with engine.connect() as connection:
            row = connection.exec_driver_sql(
                "SELECT record_set_id, rule_set_id FROM academic_projections"
            ).one()
        assert row == (None, None)
    finally:
        engine.dispose()


def test_fresh_and_migrated_schemas_are_identical(tmp_path) -> None:
    """新建数据库与旧库升级后的最终结构必须一致。"""
    _settings, upgraded = _legacy_database(tmp_path / "upgraded")
    fresh_settings = build_settings(tmp_path / "fresh")
    fresh = create_db_engine(fresh_settings)
    try:
        init_database(upgraded)
        init_database(fresh)
        for table in ACADEMIC_TABLES:
            assert _foreign_keys(upgraded, table) == _foreign_keys(fresh, table)
            with upgraded.connect() as left, fresh.connect() as right:
                assert left.exec_driver_sql(f'PRAGMA table_info("{table}")').all() == (
                    right.exec_driver_sql(f'PRAGMA table_info("{table}")').all()
                )
                assert left.exec_driver_sql(f'PRAGMA index_list("{table}")').all() == (
                    right.exec_driver_sql(f'PRAGMA index_list("{table}")').all()
                )
    finally:
        upgraded.dispose()
        fresh.dispose()


def test_migration_keeps_connection_usable_for_orm(tmp_path) -> None:
    settings, engine = _legacy_database(tmp_path)
    try:
        init_database(engine)
        factory = create_session_factory(engine)
        with factory() as session:
            assert session.execute(text("SELECT COUNT(*) FROM academic_record_sets")).scalar() == 1
    finally:
        engine.dispose()
