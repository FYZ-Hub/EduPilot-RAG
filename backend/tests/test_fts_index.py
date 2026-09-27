"""FTS5 索引层：结构、中文规范化、安全 MATCH 构造与精确对账。"""

from __future__ import annotations

import sqlite3

import pytest
from sqlalchemy import select, text

from tests.conftest import build_settings
from app.core.errors import ApiError
from app.db import create_db_engine, create_session_factory, init_database
from app.documents.chunking import chunk_blocks
from app.documents.fingerprint import (
    pipeline_fingerprint,
    stage_fingerprints,
)
from app.models import Document, DocumentChunk, DocumentPipelineState
from app.search import fts as fts_index
from app.search.schema import (
    FTS_DDL,
    FTS_NGRAM_VERSION,
    FTS_NORMALIZATION_VERSION,
    FTS_SCHEMA_VERSION,
    FTS_TABLE_NAME,
    FTS_TOKENIZER,
)
from app.search.text import (
    MAX_QUERY_CHARS,
    build_match_expressions,
    cjk_bigrams,
    quote_token,
    split_query_terms,
    term_sub_tokens,
)


# --- FTS5 可用性与版本 -------------------------------------------------------


def test_fts5_is_available_with_versioned_schema(settings) -> None:
    engine = create_db_engine(settings)
    init_database(engine)
    with engine.begin() as connection:
        rows = connection.exec_driver_sql(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
            (FTS_TABLE_NAME,),
        ).all()
        assert rows, "FTS5 虚拟表必须存在"
        # 幂等：重复初始化不报错
        connection.exec_driver_sql(FTS_DDL)
    engine.dispose()

    assert FTS_SCHEMA_VERSION
    assert FTS_NORMALIZATION_VERSION
    assert FTS_NGRAM_VERSION == "cjk-bigram-v1"
    assert "unicode61" in FTS_TOKENIZER


def test_fts_schema_fingerprint_tracks_fts_versions(tmp_path) -> None:
    settings = build_settings(tmp_path)
    payload = stage_fingerprints(settings)
    assert payload["fts_schema_fingerprint"]
    assert payload["fts_schema_fingerprint"] != payload["vector_schema_fingerprint"]
    assert pipeline_fingerprint(settings)


# --- 中文规范化 --------------------------------------------------------------


def test_cjk_bigrams_are_deterministic_and_versioned() -> None:
    assert cjk_bigrams("学分认定") == "学分 分认 认定"
    assert cjk_bigrams("重修") == "重修"
    assert cjk_bigrams("A") == ""
    # 单字 CJK 保留自身
    assert cjk_bigrams("学") == "学"
    # 确定性：同输入同输出
    assert cjk_bigrams("补考与重修管理办法") == cjk_bigrams("补考与重修管理办法")


def test_query_tokens_are_stripped_of_fts_syntax() -> None:
    assert split_query_terms("QM-CS201") == ["qm", "cs201"]
    assert split_query_terms('学分认定" OR "x') == ["学分认定", "or", "x"]
    assert term_sub_tokens("学分认定") == ["学分", "分认", "认定"]
    assert term_sub_tokens("qm-cs201") == ["qm", "cs201"]
    # 所有 token 都被引号包裹 ⇒ 语法字符不可能注入
    assert quote_token('a"b') == '"a""b"'


def test_match_expression_is_quoted_and_bounded() -> None:
    and_expression, or_expression = build_match_expressions('QM-CS201')
    assert and_expression == '"qm" AND "cs201"'
    assert or_expression == '"qm" OR "cs201"'

    # 短中文词用 AND（精确）
    and_cjk, _ = build_match_expressions("学分认定")
    assert and_cjk == '"学分" AND "分认" AND "认定"'

    # 空查询安全返回空
    assert build_match_expressions("   ") == ("", "")
    assert build_match_expressions("!!!") == ("", "")


def test_fts_special_characters_do_not_break_query() -> None:
    """引号、括号、减号、星号、冒号、NEAR 等只能被当作普通文本。"""
    for raw in (
        "QM-CS201",
        '"quoted"',
        "(paren)",
        "star*",
        "colon:field",
        "NEAR(a b)",
        "a OR b",
        "a NOT b",
        "^caret",
        "-minus",
    ):
        and_expression, or_expression = build_match_expressions(raw)
        assert and_expression
        assert "NEAR(" not in and_expression or '"' in and_expression
        # 每个片段都必须是带引号的字面量
        for fragment in and_expression.replace(" AND ", " ").replace(" OR ", " ").split():
            assert fragment.startswith('"') and fragment.endswith('"')


def test_unquoted_course_code_would_be_a_syntax_error() -> None:
    """证明为什么必须引号包裹：未加引号的 QM-CS201 会被当成列名。"""
    connection = sqlite3.connect(":memory:")
    connection.execute(FTS_DDL)
    connection.execute(
        f"INSERT INTO {FTS_TABLE_NAME}(chunk_id, body) VALUES ('c1', 'QM-CS201 数据结构')"
    )
    with pytest.raises(sqlite3.OperationalError):
        connection.execute(
            f"SELECT chunk_id FROM {FTS_TABLE_NAME} WHERE {FTS_TABLE_NAME} MATCH ?",
            ("QM-CS201",),
        ).fetchall()
    rows = connection.execute(
        f"SELECT chunk_id FROM {FTS_TABLE_NAME} WHERE {FTS_TABLE_NAME} MATCH ?",
        ('"qm" AND "cs201"',),
    ).fetchall()
    assert rows == [("c1",)]
    connection.close()


# --- 索引写入与精确对账 ------------------------------------------------------


def _seed_chunks(session, document: Document) -> list[DocumentChunk]:
    from app.documents.chunking import ChunkSourceBlock
    from app.documents.service import build_chunk_records

    drafts = chunk_blocks(
        [
            ChunkSourceBlock(
                block_index=0,
                text="第一章 总则 学分认定与转换 上限 6 学分。",
                block_type="paragraph",
                page_number=1,
                section_title="第一章 总则",
            )
        ],
        target_chars=550,
        overlap_chars=100,
    )
    records = build_chunk_records(
        document,
        drafts,
        parser_version="1.0.0",
        chunker_version="1.0.0",
        chunker_fingerprint="f" * 64,
    )
    chunks = [DocumentChunk(**record) for record in records]
    session.add_all(chunks)
    session.flush()
    return chunks


def test_reconcile_inserts_updates_and_deletes_precisely(settings) -> None:
    engine = create_db_engine(settings)
    init_database(engine)
    factory = create_session_factory(engine)
    fingerprint = "a" * 64

    with factory() as session:
        document = Document(
            source_type="upload",
            source_key="upload:test",
            file_name="t.pdf",
            file_type="pdf",
            mime_type="application/pdf",
            sha256="b" * 64,
            doc_category="academic_policy",
            status="ready",
        )
        session.add(document)
        session.flush()
        chunks = _seed_chunks(session, document)
        values = {
            chunk.id: fts_index.build_row_values(document, chunk, fingerprint) for chunk in chunks
        }
        first = fts_index.reconcile_document(session, document.id, values, fingerprint)
        session.commit()

    assert first.inserted == len(chunks)
    assert first.deleted == 0

    with factory() as session:
        assert fts_index.existing_ids(session, document.id) == {chunk.id for chunk in chunks}
        assert len(fts_index.orphan_rowids(session)) == 0

        # 重复对账：不新增、不删除
        document = session.scalar(select(Document))
        stored = list(session.scalars(select(DocumentChunk)))
        values = {
            chunk.id: fts_index.build_row_values(document, chunk, fingerprint)
            for chunk in stored
        }
        again = fts_index.reconcile_document(session, document.id, values, fingerprint)
        assert again.inserted == 0 and again.deleted == 0 and again.kept == len(stored)
        session.commit()

    # 只补缺失
    with factory() as session:
        victim = session.scalar(select(DocumentChunk).order_by(DocumentChunk.id))
        fts_index.delete_rowids(session, [victim.fts_rowid])
        victim.fts_rowid = None
        session.commit()
        assert len(fts_index.existing_ids(session, document.id)) == len(stored) - 1

        document = session.scalar(select(Document))
        values = {
            chunk.id: fts_index.build_row_values(document, chunk, fingerprint)
            for chunk in session.scalars(select(DocumentChunk))
        }
        repaired = fts_index.reconcile_document(session, document.id, values, fingerprint)
        session.commit()
        assert repaired.inserted == 1 and repaired.deleted == 0
        assert len(fts_index.existing_ids(session, document.id)) == len(stored)

    # fingerprint 变化：只重建受影响记录，不新增也不整表清空
    with factory() as session:
        document = session.scalar(select(Document))
        values = {
            chunk.id: fts_index.build_row_values(document, chunk, "c" * 64)
            for chunk in session.scalars(select(DocumentChunk))
        }
        rebuilt = fts_index.reconcile_document(session, document.id, values, "c" * 64)
        session.commit()
        assert rebuilt.inserted == len(stored)
        assert rebuilt.deleted == len(stored)
        assert fts_index.count_rows(session) == len(stored)
    engine.dispose()


def test_empty_query_returns_no_rows(settings) -> None:
    from app.search.types import RetrievalFilters, RetrievalScope

    engine = create_db_engine(settings)
    init_database(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        scope = RetrievalScope(pipeline_fingerprint="p")
        hits, mode = fts_index.search(
            session, scope=scope, filters=RetrievalFilters(), query="   ", limit=10
        )
    engine.dispose()

    assert hits == []
    assert mode == "empty"


def test_overlong_query_is_rejected(settings) -> None:
    from app.search.types import RetrievalFilters, RetrievalScope

    engine = create_db_engine(settings)
    init_database(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        with pytest.raises(ApiError) as error:
            fts_index.search(
                session,
                scope=RetrievalScope(pipeline_fingerprint="p"),
                filters=RetrievalFilters(),
                query="x" * (MAX_QUERY_CHARS + 1),
                limit=10,
            )
    engine.dispose()
    assert error.value.code == "RETRIEVAL_QUERY_INVALID"


def test_sql_injection_payload_is_treated_as_text(settings) -> None:
    from app.search.types import RetrievalFilters, RetrievalScope

    engine = create_db_engine(settings)
    init_database(engine)
    factory = create_session_factory(engine)
    with factory() as session:
        scope = RetrievalScope(pipeline_fingerprint="p")
        for payload in (
            "'; DROP TABLE chunk_fts; --",
            '" OR 1=1 --',
            "a') UNION SELECT 1 --",
        ):
            hits, _mode = fts_index.search(
                session, scope=scope, filters=RetrievalFilters(), query=payload, limit=5
            )
            assert hits == []
        # 表仍然存在
        assert session.execute(text(f"SELECT count(*) FROM {FTS_TABLE_NAME}")).scalar() == 0
    engine.dispose()
