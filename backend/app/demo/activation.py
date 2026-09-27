"""demo 数据集原子激活与旧版本退役。

只有当前 manifest 的**全部**文件都达到 ``completed``，且 SQLite / Chroma / FTS
三方 chunk_id 集合与计数完全一致，且不存在失败文档时，才允许在**单个 SQLite 事务**
中切换唯一 active 指针：新版本置为 ``ready/active/retrievable``，旧版本立即退役。

任何前置条件不满足都直接返回，不触碰旧指针 —— 旧 active 继续提供检索。
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app import constants
from app.config import Settings
from app.demo.manifest import DemoManifest
from app.documents.fingerprint import pipeline_fingerprint
from app.models import (
    DemoActiveDataset,
    Document,
    DocumentChunk,
    DocumentPipelineState,
    utcnow,
)
from app.search import fts as fts_index
from app.vector.store import ChromaVectorStore

# 前置条件不满足时的安全原因码（只用于内部判别与日志，不外泄细节）
REASON_MISSING_DOCUMENT = "missing_document"
REASON_STAGE_INCOMPLETE = "stage_incomplete"
REASON_COUNT_MISMATCH = "count_mismatch"
REASON_CHUNK_SET_MISMATCH = "chunk_set_mismatch"
REASON_VECTOR_SET_MISMATCH = "vector_set_mismatch"
REASON_FTS_SET_MISMATCH = "fts_set_mismatch"
REASON_FAILED_DOCUMENT = "failed_document"
REASON_MANIFEST_CHANGED = "manifest_changed"
REASON_PIPELINE_CHANGED = "pipeline_changed"


@dataclass(frozen=True)
class ActivationOutcome:
    activated: bool
    dataset_version: str | None
    retired_documents: int = 0
    reason: str | None = None


def evaluate_preconditions(
    session: Session,
    settings: Settings,
    manifest: DemoManifest,
    vectors: ChromaVectorStore | None,
) -> str | None:
    """返回 ``None`` 表示可以激活，否则返回安全原因码。"""
    keys = [manifest.source_key(document) for document in manifest.documents]
    documents = list(
        session.scalars(
            select(Document).where(
                Document.source_type == constants.SOURCE_DEMO,
                Document.source_key.in_(keys),
                Document.deleted_at.is_(None),
            )
        )
    )
    if len(documents) != len(manifest.documents):
        return REASON_MISSING_DOCUMENT

    new_ids = {document.id for document in documents}
    states = {
        state.doc_id: state
        for state in session.scalars(
            select(DocumentPipelineState).where(DocumentPipelineState.doc_id.in_(new_ids))
        )
    }

    for document in documents:
        if document.status == constants.STATUS_FAILED:
            return REASON_FAILED_DOCUMENT
        state = states.get(document.id)
        if state is None or state.last_completed_stage != constants.STAGE_COMPLETED:
            return REASON_STAGE_INCOMPLETE
        expected = int(state.expected_chunk_count or 0)
        if (
            expected <= 0
            or int(state.vector_record_count or 0) != expected
            or int(state.fts_record_count or 0) != expected
        ):
            return REASON_COUNT_MISMATCH

        chunk_ids = set(
            session.scalars(select(DocumentChunk.id).where(DocumentChunk.doc_id == document.id))
        )
        if len(chunk_ids) != expected:
            return REASON_CHUNK_SET_MISMATCH
        if set(fts_index.existing_ids(session, document.id)) != chunk_ids:
            return REASON_FTS_SET_MISMATCH
        if vectors is not None and set(vectors.vectors_for_document(document.id)) != chunk_ids:
            return REASON_VECTOR_SET_MISMATCH

    return None


def activate_dataset(
    session: Session,
    settings: Settings,
    manifest: DemoManifest,
    vectors: ChromaVectorStore | None = None,
) -> ActivationOutcome:
    """在单个事务中激活新版本并退役旧版本；调用方负责提交。"""
    reason = evaluate_preconditions(session, settings, manifest, vectors)
    if reason is not None:
        return ActivationOutcome(
            activated=False, dataset_version=None, reason=reason
        )

    keys = [manifest.source_key(document) for document in manifest.documents]
    now = utcnow()
    pipeline = pipeline_fingerprint(settings)

    retired = session.execute(
        update(Document)
        .where(
            Document.source_type == constants.SOURCE_DEMO,
            Document.deleted_at.is_(None),
            Document.activation_state == constants.ACTIVATION_ACTIVE,
            Document.dataset_version != manifest.dataset_version,
        )
        .values(
            activation_state=constants.ACTIVATION_INACTIVE,
            retrievable=False,
            updated_at=now,
        )
    ).rowcount

    session.execute(
        update(Document)
        .where(
            Document.source_type == constants.SOURCE_DEMO,
            Document.source_key.in_(keys),
            Document.deleted_at.is_(None),
        )
        .values(
            status=constants.STATUS_READY,
            activation_state=constants.ACTIVATION_ACTIVE,
            retrievable=True,
            current_stage=None,
            updated_at=now,
        )
    )

    # 唯一指针：先移除其它版本，再写入/更新本版本（部分唯一索引兜底）
    session.execute(
        delete(DemoActiveDataset).where(
            DemoActiveDataset.dataset_version != manifest.dataset_version
        )
    )
    pointer = session.get(DemoActiveDataset, manifest.dataset_version)
    if pointer is None:
        session.add(
            DemoActiveDataset(
                dataset_version=manifest.dataset_version,
                manifest_sha256=manifest.manifest_sha256,
                pipeline_fingerprint=pipeline,
                activated_at=now,
                active_marker=1,
            )
        )
    else:
        pointer.manifest_sha256 = manifest.manifest_sha256
        pointer.pipeline_fingerprint = pipeline
        pointer.activated_at = now
        pointer.active_marker = 1
    session.flush()

    return ActivationOutcome(
        activated=True,
        dataset_version=manifest.dataset_version,
        retired_documents=int(retired or 0),
    )


__all__ = [
    "ActivationOutcome",
    "REASON_CHUNK_SET_MISMATCH",
    "REASON_COUNT_MISMATCH",
    "REASON_FAILED_DOCUMENT",
    "REASON_FTS_SET_MISMATCH",
    "REASON_MANIFEST_CHANGED",
    "REASON_MISSING_DOCUMENT",
    "REASON_PIPELINE_CHANGED",
    "REASON_STAGE_INCOMPLETE",
    "REASON_VECTOR_SET_MISMATCH",
    "activate_dataset",
    "evaluate_preconditions",
]
