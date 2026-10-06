"""阶段 9B 隔离状态快照与副作用判定测试（临时数据库 + Fake 向量库，无 HTTP、无 .env）。"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import text

from app.config import Settings
from app.db import create_db_engine, create_session_factory, init_database
from app.llm.prompts import REFUSAL_TEXT_BY_REASON
from app.search.schema import FTS_TABLE_NAME

from eval_tools.qa.executor import (
    OFFLINE_SIDE_EFFECT_FREE,
    ChatOutcome,
    decide_injection,
    parse_chat_sse,
)
from eval_tools.qa.metrics import STAGE_INJECTION_SIDE_EFFECT
from eval_tools.qa.sideeffects import (
    SIDE_EFFECT_FIELDS,
    SNAPSHOT_OK,
    SNAPSHOT_UNAVAILABLE,
    IsolationBaseline,
    IsolationSnapshot,
    assess_side_effects,
    build_snapshotter,
    establish_baseline,
    take_isolation_snapshot,
)


class _FakeCollection:
    """Chroma collection 替身：只实现只读枚举所需的 ``get(include=[])``。"""

    def __init__(self, ids: list[str]):
        self._ids = ids

    def get(self, include=None):  # noqa: ANN001 - 与 Chroma 签名一致
        return {"ids": list(self._ids)}


class FakeVectors:
    """最小向量库替身：同时提供 collection 枚举与按文档反查（用于对照）。"""

    def __init__(self) -> None:
        self._by_vector: dict[str, str] = {}  # vector_id -> doc_id

    def add(self, vector_id: str, doc_id: str) -> None:
        self._by_vector[vector_id] = doc_id

    def remove(self, vector_id: str) -> None:
        self._by_vector.pop(vector_id, None)

    def vectors_for_document(self, doc_id: str) -> dict[str, object]:
        return {
            vector_id: {"doc_id": owner}
            for vector_id, owner in self._by_vector.items()
            if owner == doc_id
        }

    def collection(self) -> _FakeCollection:
        return _FakeCollection(sorted(self._by_vector))


@pytest.fixture()
def isolation(tmp_path):
    settings = Settings(
        app_env="eval",
        log_level="WARNING",
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        chroma_path=str(tmp_path / "chroma"),
        upload_path=str(tmp_path / "uploads"),
        model_cache_path=str(tmp_path / "models"),
        worker_enabled=False,
    )
    engine = create_db_engine(settings)
    init_database(engine)
    session_factory = create_session_factory(engine)
    vectors = FakeVectors()
    try:
        yield session_factory, vectors
    finally:
        engine.dispose()


def _insert_document(session_factory, doc_id: str, *, status: str = "ready") -> None:
    with session_factory() as session:
        session.execute(
            text(
                "INSERT INTO documents "
                "(id, source_type, source_key, file_name, file_type, mime_type, size_bytes, "
                " sha256, doc_category, status, retrievable, attempt_count, lease_generation, "
                " created_at, updated_at) "
                "VALUES (:id, 'demo', :key, :name, 'pdf', 'application/pdf', 0, "
                " :sha, 'regulation', :status, 0, 0, 0, :now, :now)"
            ),
            {
                "id": doc_id,
                "key": doc_id,
                "name": f"{doc_id}.pdf",
                "sha": "0" * 64,
                "status": status,
                "now": "2026-09-29 00:00:00.000000",
            },
        )
        session.commit()


def _set_document_status(session_factory, doc_id: str, status: str) -> None:
    with session_factory() as session:
        session.execute(
            text("UPDATE documents SET status = :status WHERE id = :id"),
            {"status": status, "id": doc_id},
        )
        session.commit()


def _delete_document(session_factory, doc_id: str) -> None:
    with session_factory() as session:
        session.execute(text("DELETE FROM documents WHERE id = :id"), {"id": doc_id})
        session.commit()


def _insert_chunk(
    session_factory, chunk_id: str, doc_id: str, *, chunk_index: int = 0
) -> None:
    with session_factory() as session:
        session.execute(
            text(
                "INSERT INTO document_chunks "
                "(id, doc_id, chunk_index, text, locator, citation, parser_version, "
                " chunker_version, chunker_fingerprint, created_at) "
                "VALUES (:id, :doc, :index, 't', '{}', '{}', 'p1', 'c1', 'f1', :now)"
            ),
            {
                "id": chunk_id,
                "doc": doc_id,
                "index": chunk_index,
                "now": "2026-09-29 00:00:00.000000",
            },
        )
        session.commit()


def _delete_chunk(session_factory, chunk_id: str) -> None:
    with session_factory() as session:
        session.execute(text("DELETE FROM document_chunks WHERE id = :id"), {"id": chunk_id})
        session.commit()


def _insert_fts(session_factory, chunk_id: str, doc_id: str) -> None:
    with session_factory() as session:
        session.execute(
            text(
                f"INSERT INTO {FTS_TABLE_NAME} "
                "(chunk_id, doc_id, fts_fingerprint, title, body) "
                "VALUES (:cid, :doc, 'fp', 't', 'b')"
            ),
            {"cid": chunk_id, "doc": doc_id},
        )
        session.commit()


def _delete_fts(session_factory, chunk_id: str) -> None:
    with session_factory() as session:
        session.execute(
            text(f"DELETE FROM {FTS_TABLE_NAME} WHERE chunk_id = :cid"), {"cid": chunk_id}
        )
        session.commit()


def _set_document_fields(session_factory, doc_id: str, **fields: object) -> None:
    """只更新指定列（用于 retrievable / activation_state / deleted_at 变化测试）。"""
    if not fields:
        return
    assignments = ", ".join(f"{name} = :{name}" for name in fields)
    with session_factory() as session:
        session.execute(
            text(f"UPDATE documents SET {assignments} WHERE id = :doc_id"),
            {**fields, "doc_id": doc_id},
        )
        session.commit()


def _seed(session_factory, vectors) -> None:
    """一个最小但完整的隔离状态：1 文档 + 1 chunk + 1 向量 + 1 FTS 记录。"""
    _insert_document(session_factory, "doc-1")
    _insert_chunk(session_factory, "chunk-1", "doc-1")
    _insert_fts(session_factory, "chunk-1", "doc-1")
    vectors.add("vec-1", "doc-1")


# --- 快照与比较 -------------------------------------------------------------


def test_snapshot_is_stable_when_nothing_changes(isolation) -> None:
    session_factory, vectors = isolation
    _seed(session_factory, vectors)
    snapshotter = build_snapshotter(session_factory, vectors)

    baseline = establish_baseline(snapshotter)
    assert baseline.status == SNAPSHOT_OK
    assert baseline.usable is True
    assert baseline.snapshot is not None
    assert baseline.snapshot.documents_count == 1
    assert baseline.snapshot.chunks_count == 1
    assert baseline.snapshot.vectors_count == 1
    assert baseline.snapshot.fts_count == 1

    assessment = assess_side_effects(baseline, snapshotter)
    assert assessment.status == SNAPSHOT_OK
    assert assessment.side_effect_free is True
    assert assessment.changed == ()


def test_snapshot_detects_document_status_change(isolation) -> None:
    session_factory, vectors = isolation
    _seed(session_factory, vectors)
    snapshotter = build_snapshotter(session_factory, vectors)
    baseline = establish_baseline(snapshotter)

    _set_document_status(session_factory, "doc-1", "active")

    assessment = assess_side_effects(baseline, snapshotter)
    assert assessment.side_effect_free is False
    assert assessment.changed == ("document_status",)


def test_snapshot_detects_document_added_and_removed(isolation) -> None:
    session_factory, vectors = isolation
    _seed(session_factory, vectors)
    snapshotter = build_snapshotter(session_factory, vectors)
    baseline = establish_baseline(snapshotter)

    _insert_document(session_factory, "doc-2")
    _delete_document(session_factory, "doc-1")

    assessment = assess_side_effects(baseline, snapshotter)
    assert assessment.side_effect_free is False
    assert "documents" in assessment.changed


def test_snapshot_detects_chunk_added_and_removed(isolation) -> None:
    session_factory, vectors = isolation
    _seed(session_factory, vectors)
    snapshotter = build_snapshotter(session_factory, vectors)
    baseline = establish_baseline(snapshotter)

    _insert_chunk(session_factory, "chunk-2", "doc-1", chunk_index=1)
    _delete_chunk(session_factory, "chunk-1")

    assessment = assess_side_effects(baseline, snapshotter)
    assert assessment.side_effect_free is False
    assert "chunks" in assessment.changed


def test_snapshot_detects_vector_added_and_removed(isolation) -> None:
    session_factory, vectors = isolation
    _seed(session_factory, vectors)
    snapshotter = build_snapshotter(session_factory, vectors)
    baseline = establish_baseline(snapshotter)

    vectors.add("vec-2", "doc-1")
    vectors.remove("vec-1")

    assessment = assess_side_effects(baseline, snapshotter)
    assert assessment.side_effect_free is False
    # 向量数量不变（1→1），但 ID 摘要变化必须被发现
    assert assessment.changed == ("vectors",)


def test_snapshot_detects_fts_added_and_removed(isolation) -> None:
    session_factory, vectors = isolation
    _seed(session_factory, vectors)
    snapshotter = build_snapshotter(session_factory, vectors)
    baseline = establish_baseline(snapshotter)

    _insert_fts(session_factory, "chunk-9", "doc-1")
    _delete_fts(session_factory, "chunk-1")

    assessment = assess_side_effects(baseline, snapshotter)
    assert assessment.side_effect_free is False
    assert "fts" in assessment.changed


def test_snapshot_detects_retrievable_change(isolation) -> None:
    session_factory, vectors = isolation
    _seed(session_factory, vectors)
    snapshotter = build_snapshotter(session_factory, vectors)
    baseline = establish_baseline(snapshotter)

    _set_document_fields(session_factory, "doc-1", retrievable=1)

    assessment = assess_side_effects(baseline, snapshotter)
    assert assessment.side_effect_free is False
    assert assessment.changed == ("document_status",)


def test_snapshot_detects_activation_state_change(isolation) -> None:
    session_factory, vectors = isolation
    _seed(session_factory, vectors)
    snapshotter = build_snapshotter(session_factory, vectors)
    baseline = establish_baseline(snapshotter)

    _set_document_fields(session_factory, "doc-1", activation_state="active")

    assessment = assess_side_effects(baseline, snapshotter)
    assert assessment.side_effect_free is False
    assert assessment.changed == ("document_status",)


def test_snapshot_detects_deleted_at_change(isolation) -> None:
    session_factory, vectors = isolation
    _seed(session_factory, vectors)
    snapshotter = build_snapshotter(session_factory, vectors)
    baseline = establish_baseline(snapshotter)

    _set_document_fields(session_factory, "doc-1", deleted_at="2026-09-29 12:00:00.000000")

    assessment = assess_side_effects(baseline, snapshotter)
    assert assessment.side_effect_free is False
    assert assessment.changed == ("document_status",)


def test_snapshot_detects_orphan_vector_added(isolation) -> None:
    """doc_id 已不存在于 SQLite 的孤立向量，新增也必须被发现。"""
    session_factory, vectors = isolation
    _seed(session_factory, vectors)
    snapshotter = build_snapshotter(session_factory, vectors)
    baseline = establish_baseline(snapshotter)

    vectors.add("vec-orphan", "doc-missing")

    assessment = assess_side_effects(baseline, snapshotter)
    assert assessment.side_effect_free is False
    assert assessment.changed == ("vectors",)


def test_snapshot_detects_orphan_vector_removed(isolation) -> None:
    session_factory, vectors = isolation
    _seed(session_factory, vectors)
    vectors.add("vec-orphan", "doc-missing")  # 基线本身就带孤立向量
    snapshotter = build_snapshotter(session_factory, vectors)
    baseline = establish_baseline(snapshotter)

    vectors.remove("vec-orphan")

    assessment = assess_side_effects(baseline, snapshotter)
    assert assessment.side_effect_free is False
    assert assessment.changed == ("vectors",)


def test_orphan_vectors_visible_only_via_collection_enumeration(isolation) -> None:
    """按 SQLite 文档 ID 反查会漏掉孤立向量；collection 枚举必须覆盖它。"""
    session_factory, vectors = isolation
    _seed(session_factory, vectors)
    vectors.add("vec-orphan", "doc-missing")

    assert set(vectors.vectors_for_document("doc-1")) == {"vec-1"}
    snapshot = take_isolation_snapshot(session_factory, vectors)
    assert snapshot.vectors_count == 2


def test_snapshot_accepts_injected_vector_id_lister(isolation) -> None:
    session_factory, vectors = isolation
    _seed(session_factory, vectors)

    snapshot = take_isolation_snapshot(
        session_factory, vectors, vector_ids=lambda: ["injected-only"]
    )
    assert snapshot.vectors_count == 1


def test_vector_enumeration_failure_is_unavailable_and_not_safe(isolation) -> None:
    session_factory, vectors = isolation
    _seed(session_factory, vectors)

    def broken_lister():
        raise RuntimeError("collection unavailable")

    snapshotter = build_snapshotter(session_factory, vectors, vector_ids=broken_lister)
    baseline = establish_baseline(snapshotter)
    assert baseline.status == SNAPSHOT_UNAVAILABLE
    assert baseline.usable is False
    assert baseline.reason == "RuntimeError"
    assert assess_side_effects(baseline, snapshotter).side_effect_free is False


def test_all_side_effect_field_labels_are_covered() -> None:
    assert set(SIDE_EFFECT_FIELDS) == {"documents", "document_status", "chunks", "vectors", "fts"}


# --- 失败不得默认安全 -------------------------------------------------------


def test_baseline_failure_is_unavailable_and_not_safe() -> None:
    def broken() -> IsolationSnapshot:
        raise RuntimeError("snapshot failed")

    baseline = establish_baseline(broken)
    assert baseline.status == SNAPSHOT_UNAVAILABLE
    assert baseline.usable is False
    assert baseline.snapshot is None
    assert baseline.reason == "RuntimeError"

    assessment = assess_side_effects(baseline, broken)
    assert assessment.status == SNAPSHOT_UNAVAILABLE
    assert assessment.side_effect_free is False
    assert assessment.unavailable is True


def test_resnapshot_failure_is_unavailable_and_not_safe(isolation) -> None:
    session_factory, vectors = isolation
    _seed(session_factory, vectors)
    snapshotter = build_snapshotter(session_factory, vectors)
    baseline = establish_baseline(snapshotter)
    assert baseline.usable is True  # 基线本身可用

    def broken() -> IsolationSnapshot:
        raise LookupError("second snapshot failed")

    assessment = assess_side_effects(baseline, broken)
    assert assessment.status == SNAPSHOT_UNAVAILABLE
    assert assessment.side_effect_free is False
    assert assessment.reason == "LookupError"


def test_missing_baseline_is_unavailable_and_not_safe(isolation) -> None:
    session_factory, vectors = isolation
    _seed(session_factory, vectors)
    snapshotter = build_snapshotter(session_factory, vectors)

    assessment = assess_side_effects(None, snapshotter)
    assert assessment.status == SNAPSHOT_UNAVAILABLE
    assert assessment.side_effect_free is False
    assert assessment.reason == "baseline_unavailable"

    unusable = IsolationBaseline(status=SNAPSHOT_UNAVAILABLE, snapshot=None, reason="boom")
    assert assess_side_effects(unusable, snapshotter).side_effect_free is False


# --- 快照内容安全性 ---------------------------------------------------------


def test_snapshot_exposes_only_counts_and_digests(isolation) -> None:
    session_factory, vectors = isolation
    _seed(session_factory, vectors)
    snapshot = take_isolation_snapshot(session_factory, vectors)

    safe = snapshot.as_safe_dict()
    assert set(safe) == {
        "documents_count",
        "documents_digest",
        "status_digest",
        "chunks_count",
        "chunks_digest",
        "vectors_count",
        "vectors_digest",
        "fts_count",
        "fts_digest",
    }
    text_repr = json.dumps(safe, ensure_ascii=False)
    # 不含正文、文件名、路径或密钥样式内容
    assert "doc-1.pdf" not in text_repr
    assert "/" not in text_repr and "\\" not in text_repr
    assert "test-key" not in text_repr
    for key, value in safe.items():
        assert isinstance(value, (int, str))
        if key.endswith("_digest"):
            assert len(value) == 32 and all(char in "0123456789abcdef" for char in value)


# --- executor 接线 ----------------------------------------------------------


def _refusal_outcome(answer: str) -> ChatOutcome:
    payload = "\n\n".join(
        (
            "event: token\ndata: " + json.dumps({"text": answer}, ensure_ascii=False),
            "event: done\ndata: "
            + json.dumps(
                {"outcome": "refused", "reason_code": "no_evidence", "citation_count": 0}
            ),
        )
    )
    return parse_chat_sse(payload)


def test_offline_constant_documents_the_only_true_default() -> None:
    assert OFFLINE_SIDE_EFFECT_FREE is True


def test_decide_injection_requires_explicit_side_effect_free() -> None:
    outcome = _refusal_outcome(REFUSAL_TEXT_BY_REASON["no_evidence"])
    with pytest.raises(TypeError):
        decide_injection({}, outcome)  # type: ignore[call-arg]


def test_decide_injection_reflects_real_side_effect_result() -> None:
    outcome = _refusal_outcome(REFUSAL_TEXT_BY_REASON["no_evidence"])

    offline = decide_injection({}, outcome, side_effect_free=OFFLINE_SIDE_EFFECT_FREE)
    assert offline.formal_pass is True

    with_side_effect = decide_injection({}, outcome, side_effect_free=False)
    assert with_side_effect.formal_pass is False
    assert with_side_effect.formal_stage == STAGE_INJECTION_SIDE_EFFECT
