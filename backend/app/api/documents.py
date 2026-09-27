"""文档接口：上传、列表、详情、状态、预览与删除。

响应绝不包含 ``storage_path`` 等宿主机路径。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Response, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app import constants
from app.api.deps import AppContext, get_context, get_session
from app.documents import service
from app.documents.upload import StoredUpload, commit_upload, discard_upload, ingest_upload

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("")
async def create_document(
    file: UploadFile = File(...),
    context: AppContext = Depends(get_context),
    session: Session = Depends(get_session),
) -> JSONResponse:
    """接收单个文件；新建或续跑返回 202，命中已就绪返回 200。"""
    settings = context.settings
    pending = await ingest_upload(file, settings)
    committed = False

    def committer(item) -> StoredUpload:
        nonlocal committed
        stored = commit_upload(item, settings)
        committed = True
        return stored

    try:
        document, disposition = service.register_upload(session, pending, settings, committer)
    finally:
        if not committed:
            discard_upload(pending)

    status_code = 200 if disposition == constants.DISPOSITION_EXISTING_READY else 202
    return JSONResponse(
        status_code=status_code,
        content={
            "document_id": document.id,
            "status": document.status,
            "status_url": f"/api/documents/{document.id}/status",
            "deduplicated": disposition != constants.DISPOSITION_CREATED,
            "disposition": disposition,
        },
    )


@router.get("")
def list_documents(session: Session = Depends(get_session)) -> dict:
    return service.list_documents_payload(session)


@router.get("/{document_id}")
def read_document(document_id: str, session: Session = Depends(get_session)) -> dict:
    document = service.get_document(session, document_id)
    counts = service.block_counts(session, [document.id])
    chunks = service.chunk_counts(session, [document.id])
    return service.serialize_detail(
        document, counts.get(document.id, 0), chunks.get(document.id, 0)
    )


@router.get("/{document_id}/status")
def read_document_status(document_id: str, session: Session = Depends(get_session)) -> dict:
    document = service.get_document(session, document_id)
    counts = service.block_counts(session, [document.id])
    chunks = service.chunk_counts(session, [document.id])
    return service.status_payload(
        document, counts.get(document.id, 0), chunks.get(document.id, 0)
    )


@router.get("/{document_id}/preview")
def read_document_preview(document_id: str, session: Session = Depends(get_session)) -> dict:
    document = service.get_document(session, document_id)
    return service.preview_payload(session, document)


@router.delete("/{document_id}", status_code=204)
def delete_document(
    document_id: str,
    context: AppContext = Depends(get_context),
    session: Session = Depends(get_session),
) -> Response:
    document = service.get_document(session, document_id)
    # 同步清理该文档的 Chroma 向量；只作用于本 doc_id，不做整库清空
    service.delete_document(session, document, context.settings, context.vectors)
    return Response(status_code=204)
