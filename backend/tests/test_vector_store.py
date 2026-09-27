"""Chroma 向量库：持久化、schema 校验、幂等、清理与安全边界。"""

from __future__ import annotations

import pytest

from tests.conftest import build_settings
from app.core.errors import ApiError
from app.documents.fingerprint import VECTOR_COLLECTION_NAME, VECTOR_SCHEMA_VERSION
from app.embedding.base import descriptor_for
from app.vector.store import ChromaVectorStore, VectorRecord, sanitize_metadata


def _record(chunk_id: str, doc_id: str, index: int = 0) -> VectorRecord:
    return VectorRecord(
        chunk_id=chunk_id,
        text=f"正文-{chunk_id[:8]}",
        metadata={
            "doc_id": doc_id,
            "chunk_index": index,
            "file_name": "a.pdf",
            "file_type": "pdf",
            "page_number": None,  # 无值字段必须被省略
            "section_title": "第一章",
        },
    )


def _vector(seed: float = 0.1) -> list[float]:
    return [seed] * 1024


def test_collection_name_and_schema_metadata(vectors: ChromaVectorStore) -> None:
    collection = vectors.collection()
    assert vectors.collection_name == "campus_chunks_v1"
    assert VECTOR_COLLECTION_NAME == "campus_chunks_v1"

    metadata = dict(collection.metadata)
    assert metadata["schema_version"] == VECTOR_SCHEMA_VERSION
    assert metadata["embedding_provider"] == "fake"
    assert int(metadata["embedding_dimension"]) == 1024
    assert metadata["embedding_revision"] == vectors.descriptor.revision


def test_telemetry_is_explicitly_disabled(vectors: ChromaVectorStore) -> None:
    collection = vectors.collection()
    client = vectors._connect()  # noqa: SLF001 - 契约验证需要读取底层客户端设置

    assert client.get_settings().anonymized_telemetry is False
    assert (
        client.get_settings().chroma_product_telemetry_impl == "app.vector.telemetry.NoopTelemetry"
    )
    # 不允许 Chroma 使用默认 Embedding 函数（否则可能隐式下载其它模型）
    assert type(collection._embedding_function).__name__ == "_ForbiddenEmbeddingFunction"  # noqa: SLF001
    with pytest.raises(ApiError):
        collection._embedding_function(["x"])  # noqa: SLF001


def test_upsert_is_idempotent_and_persists_across_restart(vectors: ChromaVectorStore, settings) -> None:
    records = [_record("a" * 64, "doc-1", 0), _record("b" * 64, "doc-1", 1)]
    assert vectors.upsert(records, [_vector(0.1), _vector(0.2)]) == 2
    assert vectors.count() == 2

    # 重复 upsert 不产生重复记录
    vectors.upsert(records, [_vector(0.1), _vector(0.2)])
    assert vectors.count() == 2

    vectors.close()
    reopened = ChromaVectorStore(settings, descriptor_for(settings))
    try:
        reopened.collection()
        assert reopened.count() == 2
        assert set(reopened.vectors_for_document("doc-1")) == {"a" * 64, "b" * 64}
    finally:
        reopened.close()


def test_metadata_is_scalar_only_without_nulls(vectors: ChromaVectorStore) -> None:
    vectors.upsert([_record("c" * 64, "doc-2")], [_vector()])
    stored = vectors.vectors_for_document("doc-2")["c" * 64]

    assert None not in stored.values()
    assert "page_number" not in stored  # None 值被省略
    assert stored["section_title"] == "第一章"
    for value in stored.values():
        assert isinstance(value, (str, int, float, bool))

    with pytest.raises(ApiError) as error:
        sanitize_metadata({"doc_id": "x", "tags": ["a", "b"]})
    assert error.value.code == "DOCUMENT_INDEX_FAILED"
    assert error.value.details.get("reason") == "non_scalar_metadata"


def test_delete_document_only_removes_its_own_vectors(vectors: ChromaVectorStore) -> None:
    vectors.upsert([_record("d" * 64, "doc-a", 0)], [_vector()])
    vectors.upsert([_record("e" * 64, "doc-b", 0)], [_vector()])

    assert vectors.delete_document("doc-a") == 1
    assert vectors.vectors_for_document("doc-a") == {}
    assert set(vectors.vectors_for_document("doc-b")) == {"e" * 64}
    assert vectors.count() == 1
    assert vectors.delete_document("doc-a") == 0


def test_dimension_mismatch_fails_safely(vectors: ChromaVectorStore, settings) -> None:
    collection = vectors.collection()
    collection.modify(
        metadata={
            "schema_version": VECTOR_SCHEMA_VERSION,
            "embedding_provider": "fake",
            "embedding_model": vectors.descriptor.model,
            "embedding_revision": vectors.descriptor.revision,
            "embedding_dimension": 8,
        }
    )

    reopened = ChromaVectorStore(settings, descriptor_for(settings))
    try:
        with pytest.raises(ApiError) as error:
            reopened.collection()
        assert error.value.code == "EMBEDDING_DIMENSION_MISMATCH"
    finally:
        reopened.close()


def test_schema_identity_mismatch_fails_safely(vectors: ChromaVectorStore, settings) -> None:
    collection = vectors.collection()
    collection.modify(
        metadata={
            "schema_version": VECTOR_SCHEMA_VERSION,
            "embedding_provider": "local",
            "embedding_model": "BAAI/bge-m3",
            "embedding_revision": "deadbeef",
            "embedding_dimension": 1024,
        }
    )

    reopened = ChromaVectorStore(settings, descriptor_for(settings))
    try:
        with pytest.raises(ApiError) as error:
            reopened.collection()
        assert error.value.code == "VECTOR_COLLECTION_MISMATCH"
        assert error.value.details.get("field") == "embedding_provider"
    finally:
        reopened.close()


def test_upsert_rejects_wrong_vector_length(vectors: ChromaVectorStore) -> None:
    with pytest.raises(ApiError) as error:
        vectors.upsert([_record("f" * 64, "doc-c")], [[0.1] * 8])
    assert error.value.code == "EMBEDDING_DIMENSION_MISMATCH"


def test_store_uses_isolated_path(tmp_path, settings) -> None:
    """向量库必须落在配置的 CHROMA_PATH，不污染其它位置。"""
    store = ChromaVectorStore(settings, descriptor_for(settings))
    try:
        store.collection()
        assert (tmp_path / "chroma").exists()
    finally:
        store.close()


def test_missing_chromadb_reports_safe_error(monkeypatch, tmp_path) -> None:
    settings = build_settings(tmp_path)
    store = ChromaVectorStore(settings, descriptor_for(settings))

    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "chromadb":
            raise ModuleNotFoundError("chromadb")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    with pytest.raises(ApiError) as error:
        store.collection()
    assert error.value.code == "VECTOR_STORE_UNAVAILABLE"
    assert error.value.details.get("reason") == "chromadb_not_installed"
