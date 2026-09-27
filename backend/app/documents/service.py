"""文档业务服务：创建/查询/状态投影/预览/删除。

``storage_path`` 只用于数据库与删除逻辑，任何 API 响应都不得返回它。
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app import constants
from app.config import Settings
from app.core.errors import (
    DOCUMENT_NOT_FOUND,
    DOCUMENT_RETRY_NOT_ALLOWED,
    ApiError,
)
from app.documents.metadata import derive_document_metadata
from app.documents.chunking import ChunkDraft, compute_chunk_id, document_checksum
from app.embedding.base import EmbeddingDescriptor
from app.models import (
    DemoSeedJobDocument,
    Document,
    DocumentBlock,
    DocumentChunk,
    DocumentPipelineState,
    utcnow,
)
from app.documents.upload import PendingUpload
from app.search import fts as fts_index

if TYPE_CHECKING:  # pragma: no cover - 仅用于类型标注，避免运行期循环导入
    from app.vector.store import ChromaVectorStore

PREVIEW_MAX_BLOCKS = 100


def isoformat_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def next_status_for_stage(stage: str | None) -> str:
    """检查点 → 公共处理状态。

    阶段 3 的 target_stage 是 ``vector_indexed``，其后仍需阶段 4 建立 FTS 与混合检索，
    因此达到 ``parsed``/``chunked``/``vector_indexed`` 后一律安全投影为 ``queued``：
    既不返回 ``ready``，也不把 ``keyword_indexing`` 谎报成正在执行的阶段。
    """
    if stage in (
        None,
        constants.STAGE_NONE,
        constants.STAGE_PARSED,
        constants.STAGE_CHUNKED,
        constants.STAGE_VECTOR_INDEXED,
        constants.STAGE_KEYWORD_INDEXED,
    ):
        return constants.STATUS_QUEUED
    if stage == constants.STAGE_VALIDATED:
        return constants.STATUS_STORING
    if stage == constants.STAGE_STORED:
        return constants.STATUS_PARSING
    if stage == constants.STAGE_COMPLETED:
        return constants.STATUS_READY
    return constants.STATUS_QUEUED


def error_object(document: Document) -> dict | None:
    if not document.error_code:
        return None
    return {
        "code": document.error_code,
        "message": document.error_message,
        "retryable": bool(document.error_retryable),
    }


def locator_types(document: Document, block_count: int) -> list[str]:
    if block_count <= 0:
        return []
    if document.file_type == constants.FILE_TYPE_PDF:
        return ["page_number", "section_title"]
    if document.file_type == constants.FILE_TYPE_DOCX:
        return ["section_title"]
    return ["sheet_name", "row_start", "row_end"]


def block_counts(session: Session, doc_ids: list[str]) -> dict[str, int]:
    if not doc_ids:
        return {}
    rows = session.execute(
        select(DocumentBlock.doc_id, func.count(DocumentBlock.id))
        .where(DocumentBlock.doc_id.in_(doc_ids))
        .group_by(DocumentBlock.doc_id)
    ).all()
    return {doc_id: count for doc_id, count in rows}


def chunk_counts(session: Session, doc_ids: list[str]) -> dict[str, int]:
    if not doc_ids:
        return {}
    rows = session.execute(
        select(DocumentChunk.doc_id, func.count(DocumentChunk.id))
        .where(DocumentChunk.doc_id.in_(doc_ids))
        .group_by(DocumentChunk.doc_id)
    ).all()
    return {doc_id: count for doc_id, count in rows}


def serialize_list_item(document: Document, block_count: int, chunk_count: int = 0) -> dict:
    return {
        "id": document.id,
        "file_name": document.file_name,
        "file_type": document.file_type,
        "doc_category": document.doc_category,
        "source_type": document.source_type,
        "dataset_version": document.dataset_version,
        "status": document.status,
        "retrievable": bool(document.retrievable),
        "activation_state": document.activation_state,
        "current_stage": document.current_stage,
        "chunk_count": chunk_count,
        "block_count": block_count,
        "locator_types": locator_types(document, block_count),
        "created_at": isoformat_utc(document.created_at),
        "updated_at": isoformat_utc(document.updated_at),
        "error": error_object(document),
    }


def serialize_detail(document: Document, block_count: int, chunk_count: int = 0) -> dict:
    payload = serialize_list_item(document, block_count, chunk_count)
    payload.update(
        {
            "document_version": document.document_version,
            "effective_from": document.effective_from,
            "checksum": document.sha256,
        }
    )
    return payload


def get_document(session: Session, document_id: str) -> Document:
    document = session.scalar(
        select(Document).where(Document.id == document_id, Document.deleted_at.is_(None))
    )
    if document is None:
        raise ApiError(DOCUMENT_NOT_FOUND)
    return document


def list_documents_payload(session: Session) -> dict:
    documents = list(
        session.scalars(
            select(Document).where(Document.deleted_at.is_(None)).order_by(Document.created_at, Document.id)
        )
    )
    doc_ids = [document.id for document in documents]
    blocks = block_counts(session, doc_ids)
    chunks = chunk_counts(session, doc_ids)
    items = [
        serialize_list_item(
            document, blocks.get(document.id, 0), chunks.get(document.id, 0)
        )
        for document in documents
    ]
    return {
        "items": items,
        "total": len(items),
        "counts": {
            "ready": sum(1 for item in items if item["status"] == constants.STATUS_READY),
            "retrievable": sum(1 for item in items if item["retrievable"]),
            "processing": sum(
                1 for item in items if item["status"] in constants.ACTIVE_PUBLIC_STATUSES
            ),
            "failed": sum(1 for item in items if item["status"] == constants.STATUS_FAILED),
        },
    }


def status_payload(document: Document, block_count: int, chunk_count: int = 0) -> dict:
    return serialize_list_item(document, block_count, chunk_count)


def build_chunk_records(
    document: Document,
    drafts: Sequence[ChunkDraft],
    *,
    parser_version: str,
    chunker_version: str,
    chunker_fingerprint: str,
    title: str | None = None,
) -> list[dict]:
    """把切片草稿变成可持久化的 chunk 记录（含稳定 chunk_id 与引用元数据）。

    ``chunk_id`` 只由「文档校验值 + 规范定位 + chunk_index + parser/chunker 版本」决定，
    因此重试或重复导入必定得到同一 ID 集合，绝不产生重复切片。
    """
    citation = {
        "file_name": document.file_name,
        "file_type": document.file_type,
        "doc_category": document.doc_category,
        "document_version": document.document_version,
        "effective_from": document.effective_from,
        "dataset_version": document.dataset_version,
    }
    citation.update(derive_document_metadata(title, document.file_name))

    # 绑定来源身份：demo 与 upload 即使文件 SHA 相同也必须得到不同 chunk_id
    checksum = document_checksum(
        source_type=document.source_type,
        source_key=document.source_key,
        file_sha256=document.sha256,
    )

    records: list[dict] = []
    for draft in drafts:
        locator = dict(draft.locator)
        records.append(
            {
                "id": compute_chunk_id(
                    document_checksum=checksum,
                    locator=locator,
                    chunk_index=draft.chunk_index,
                    parser_version=parser_version,
                    chunker_version=chunker_version,
                ),
                "doc_id": document.id,
                "chunk_index": draft.chunk_index,
                "text": draft.text,
                "locator": locator,
                "citation": citation,
                "parser_version": parser_version,
                "chunker_version": chunker_version,
                "chunker_fingerprint": chunker_fingerprint,
            }
        )
    return records


def chunk_vector_metadata(
    document: Document,
    chunk: DocumentChunk,
    descriptor: EmbeddingDescriptor,
    *,
    pipeline_fingerprint_value: str,
    vector_schema_fingerprint: str,
) -> dict:
    """Chroma 标量 metadata：无值字段直接省略，绝不写 null / 数组 / 嵌套对象。

    ``embedding_fingerprint`` 与 ``vector_schema_fingerprint`` 决定向量能否被复用，
    因此必须随记录一起保存；缺失或过期的记录会被重建。
    """
    metadata: dict = {
        "doc_id": document.id,
        "source_type": document.source_type,
        "source_key": document.source_key,
        "file_name": document.file_name,
        "file_type": document.file_type,
        "doc_category": document.doc_category,
        "checksum": document.sha256,
        "chunk_index": chunk.chunk_index,
        "parser_version": chunk.parser_version,
        "chunker_version": chunk.chunker_version,
        "embedding_provider": descriptor.provider,
        "embedding_model": descriptor.model,
        "embedding_revision": descriptor.revision,
        "embedding_fingerprint": descriptor.fingerprint,
        "vector_schema_fingerprint": vector_schema_fingerprint,
        "pipeline_fingerprint": pipeline_fingerprint_value,
    }
    metadata.update({key: value for key, value in (chunk.citation or {}).items() if value is not None})
    metadata.update({key: value for key, value in (chunk.locator or {}).items() if value is not None})
    return metadata


def preview_payload(session: Session, document: Document) -> dict:
    total = session.scalar(
        select(func.count(DocumentBlock.id)).where(DocumentBlock.doc_id == document.id)
    ) or 0
    blocks = list(
        session.scalars(
            select(DocumentBlock)
            .where(DocumentBlock.doc_id == document.id)
            .order_by(DocumentBlock.block_index)
            .limit(PREVIEW_MAX_BLOCKS)
        )
    )
    return {
        "document_id": document.id,
        "file_name": document.file_name,
        "file_type": document.file_type,
        "status": document.status,
        "total_blocks": total,
        "returned_blocks": len(blocks),
        "truncated": total > len(blocks),
        "blocks": [
            {
                "block_index": block.block_index,
                "block_type": block.block_type,
                "text": block.text,
                "page_number": block.page_number,
                "sheet_name": block.sheet_name,
                "row_start": block.row_start,
                "row_end": block.row_end,
                "section_title": block.section_title,
                "metadata": block.block_metadata,
            }
            for block in blocks
        ],
    }


def ensure_pipeline_state(session: Session, document: Document, settings: Settings) -> DocumentPipelineState:
    """取得或创建检查点；**不覆写分阶段指纹**。

    分阶段指纹必须描述“生产当前产物时实际用到的组件版本”，因此只能在该阶段
    成功完成时由 worker 写入（``_persist_blocks`` / ``_persist_chunks`` / ``_index_vectors``）。
    若在这里提前刷新，指纹失效检测将永远匹配，定向重建会静默失效。
    """
    state = session.scalar(
        select(DocumentPipelineState).where(DocumentPipelineState.doc_id == document.id)
    )
    if state is None:
        state = DocumentPipelineState(
            doc_id=document.id,
            source_type=document.source_type,
            source_key=document.source_key,
            file_sha256=document.sha256,
            last_completed_stage=constants.STAGE_NONE,
        )
        session.add(state)
        session.flush()
    state.file_sha256 = document.sha256
    return state


def find_existing_upload(session: Session, sha256: str) -> Document | None:
    return session.scalar(
        select(Document)
        .where(
            Document.source_type == constants.SOURCE_UPLOAD,
            Document.sha256 == sha256,
            Document.deleted_at.is_(None),
            # 学业导入文档是独立的证据来源，不能被通用上传接口复用
            Document.doc_category.notin_(constants.ACADEMIC_DOC_CATEGORIES),
        )
        .order_by(Document.created_at.desc())
    )


def register_upload(
    session: Session, pending: PendingUpload, settings: Settings, committer
) -> tuple[Document, str]:
    """按 upload 来源内的 SHA-256 幂等登记上传，返回 (document, disposition)。

    ``committer`` 只在需要新建文档时才被调用，因此重复上传不会留下多余文件。
    """
    existing = find_existing_upload(session, pending.sha256)
    if existing is not None:
        if existing.status == constants.STATUS_READY:
            return existing, constants.DISPOSITION_EXISTING_READY
        if existing.status == constants.STATUS_FAILED:
            if not existing.error_retryable:
                raise ApiError(DOCUMENT_RETRY_NOT_ALLOWED)
            existing.status = constants.STATUS_QUEUED
            existing.current_stage = None
            existing.error_code = None
            existing.error_message = None
            existing.error_retryable = None
            existing.attempt_count = 0
            existing.lease_owner = None
            existing.lease_expires_at = None
            existing.updated_at = utcnow()
            return existing, constants.DISPOSITION_RETRY_STARTED
        return existing, constants.DISPOSITION_ATTACHED

    stored = committer(pending)
    document = Document(
        source_type=constants.SOURCE_UPLOAD,
        source_key=f"upload:{stored.sha256}",
        file_name=stored.file_name,
        safe_storage_name=stored.storage_name,
        file_type=stored.file_type,
        mime_type=stored.mime_type,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        storage_path=stored.storage_path,
        doc_category="unknown",
        status=constants.STATUS_QUEUED,
        retrievable=False,
        activation_state=None,
    )
    session.add(document)
    session.flush()
    ensure_pipeline_state(session, document, settings)
    return document, constants.DISPOSITION_CREATED


def _safe_unlink(path_value: str | None, root: Path) -> None:
    """只删除位于上传目录之内的文件，绝不触碰只读演示目录。"""
    if not path_value:
        return
    try:
        target = Path(path_value).resolve()
        root_resolved = root.resolve()
    except OSError:  # pragma: no cover - 路径异常时保守跳过
        return
    if target == root_resolved or root_resolved not in target.parents:
        return
    target.unlink(missing_ok=True)


def delete_document(
    session: Session,
    document: Document,
    settings: Settings,
    vector_store: "ChromaVectorStore | None" = None,
) -> None:
    """按来源删除运行时文件、切片、块、检查点与关联，并清理该文档的 Chroma 向量。

    - 只作用于该 ``doc_id``：不做整库清空，也不触碰另一来源的同 SHA 数据
    - demo 来源绝不修改只读 ``/app/demo`` 源文件
    """
    if vector_store is not None:
        # 先清理外部向量：失败时向上抛出，避免出现「文档已消失但向量残留」
        vector_store.delete_document(document.id)

    # FTS 行号记录在 chunk 行上，必须先按登记的 rowid 精确删除 FTS 记录
    fts_index.delete_document_rows(session, document.id)
    session.execute(delete(DocumentChunk).where(DocumentChunk.doc_id == document.id))
    session.execute(delete(DocumentBlock).where(DocumentBlock.doc_id == document.id))
    session.execute(
        delete(DocumentPipelineState).where(DocumentPipelineState.doc_id == document.id)
    )
    session.execute(
        update(DemoSeedJobDocument)
        .where(DemoSeedJobDocument.doc_id == document.id)
        .values(doc_id=None, pipeline_state_id=None)
    )

    if document.source_type == constants.SOURCE_UPLOAD:
        _safe_unlink(document.storage_path, Path(settings.upload_path))

    document.storage_path = None
    document.retrievable = False
    document.current_stage = None
    document.lease_owner = None
    document.lease_expires_at = None
    document.deleted_at = utcnow()
    document.updated_at = utcnow()
