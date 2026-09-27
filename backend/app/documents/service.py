"""文档业务服务：创建/查询/状态投影/预览/删除。

``storage_path`` 只用于数据库与删除逻辑，任何 API 响应都不得返回它。
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session

from app import constants
from app.config import Settings
from app.core.errors import (
    DOCUMENT_NOT_FOUND,
    DOCUMENT_RETRY_NOT_ALLOWED,
    ApiError,
)
from app.documents.fingerprint import stage_fingerprints
from app.models import (
    DemoSeedJobDocument,
    Document,
    DocumentBlock,
    DocumentPipelineState,
    utcnow,
)
from app.documents.upload import PendingUpload

PREVIEW_MAX_BLOCKS = 100


def isoformat_utc(value: datetime | None) -> str | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


def next_status_for_stage(stage: str | None) -> str:
    """检查点 → 公共处理状态。

    阶段 2B 达到 ``parsed`` 后表示等待阶段 3/4，因此投影为 ``queued``；
    绝不返回 ``ready``，也不伪造正在执行的阶段。
    """
    if stage in (None, constants.STAGE_NONE, constants.STAGE_PARSED):
        return constants.STATUS_QUEUED
    if stage == constants.STAGE_VALIDATED:
        return constants.STATUS_STORING
    if stage == constants.STAGE_STORED:
        return constants.STATUS_PARSING
    if stage == constants.STAGE_CHUNKED:
        return constants.STATUS_EMBEDDING
    if stage == constants.STAGE_VECTOR_INDEXED:
        return constants.STATUS_KEYWORD_INDEXING
    if stage == constants.STAGE_KEYWORD_INDEXED:
        return constants.STATUS_QUEUED
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


def serialize_list_item(document: Document, block_count: int) -> dict:
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
        "chunk_count": 0,
        "block_count": block_count,
        "locator_types": locator_types(document, block_count),
        "created_at": isoformat_utc(document.created_at),
        "updated_at": isoformat_utc(document.updated_at),
        "error": error_object(document),
    }


def serialize_detail(document: Document, block_count: int) -> dict:
    payload = serialize_list_item(document, block_count)
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
    counts = block_counts(session, [document.id for document in documents])
    items = [serialize_list_item(document, counts.get(document.id, 0)) for document in documents]
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


def status_payload(document: Document, block_count: int) -> dict:
    return serialize_list_item(document, block_count)


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
    """取得或创建检查点，并写入当前分阶段指纹。"""
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
    for key, value in stage_fingerprints(settings).items():
        setattr(state, key, value)
    return state


def find_existing_upload(session: Session, sha256: str) -> Document | None:
    return session.scalar(
        select(Document)
        .where(
            Document.source_type == constants.SOURCE_UPLOAD,
            Document.sha256 == sha256,
            Document.deleted_at.is_(None),
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


def delete_document(session: Session, document: Document, settings: Settings) -> None:
    """按来源删除运行时文件、块、检查点与关联；demo 源文件保持只读不变。"""
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
