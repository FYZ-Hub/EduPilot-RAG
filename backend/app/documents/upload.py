"""安全上传：流式落盘到受控临时区、SHA-256、容器校验与原子提交。

去重判断在“暂存 + 校验”之后、真正移动到上传目录之前完成，
重复上传不会在上传目录留下多余文件。
"""

from __future__ import annotations

import hashlib
import os
import uuid
from dataclasses import dataclass
from email.message import Message
from pathlib import Path

from fastapi import UploadFile

from app.config import Settings
from app.constants import GENERIC_MIME_TYPES, MIME_TYPES, SUPPORTED_EXTENSIONS
from app.core.errors import (
    DOCUMENT_CONTENT_TYPE_MISMATCH,
    DOCUMENT_EMPTY,
    DOCUMENT_INVALID_TYPE,
    DOCUMENT_TOO_LARGE,
    ApiError,
)
from app.documents.container import validate_container
from app.documents.naming import build_storage_name, split_extension, validate_display_name

READ_CHUNK_SIZE = 1024 * 1024


@dataclass(frozen=True)
class PendingUpload:
    """已校验但尚未进入上传目录的文件（位于受控临时区）。"""

    file_name: str
    file_type: str
    mime_type: str
    size_bytes: int
    sha256: str
    storage_name: str
    temp_path: Path


@dataclass(frozen=True)
class StoredUpload:
    """已原子移动到上传目录的文件。"""

    file_name: str
    file_type: str
    mime_type: str
    size_bytes: int
    sha256: str
    storage_name: str
    storage_path: str


def resolve_file_type(file_name: str, declared_mime: str | None) -> str:
    """同时校验扩展名与声明 MIME；不得只相信浏览器传入的 MIME。"""
    _, extension = split_extension(file_name)
    file_type = SUPPORTED_EXTENSIONS.get(extension)
    if file_type is None:
        raise ApiError(DOCUMENT_INVALID_TYPE, details={"extension": extension or "none"})

    normalized = (declared_mime or "").split(";")[0].strip().lower()
    allowed = {MIME_TYPES[file_type]} | set(GENERIC_MIME_TYPES)
    if normalized not in allowed:
        raise ApiError(
            DOCUMENT_CONTENT_TYPE_MISMATCH,
            details={"declared": normalized, "expected": MIME_TYPES[file_type]},
        )
    return file_type


def _raw_header_filename(upload: UploadFile) -> str | None:
    """从 ``Content-Disposition`` 头取原始文件名（未做 IE6 绝对路径归一化）。

    ``python-multipart`` 会把 ``C:\\path\\a.pdf`` 与 ``\\\\server\\a.pdf`` 静默改写为
    纯文件名，导致服务端看不到危险的盘符/UNC 前缀。这里读取原始头字节，确保这类
    输入被显式阻止，而不是被悄悄接受。
    """
    header_bytes: bytes | None = None
    for key, value in upload.headers.raw:
        if key.lower() == b"content-disposition":
            header_bytes = value
            break
    if not header_bytes:
        return None
    try:
        header = header_bytes.decode("utf-8")
    except UnicodeDecodeError:
        header = header_bytes.decode("latin-1")

    message = Message()
    message["content-disposition"] = header
    for key, value in message.get_params(header="content-disposition")[1:]:
        if key != "filename":
            continue
        if isinstance(value, tuple):  # RFC 2231 编码（filename*）
            value = value[-1]
        return value
    return None


async def ingest_upload(upload: UploadFile, settings: Settings) -> PendingUpload:
    """流式写入受控临时文件并完成全部校验；超限立即安全失败。"""
    display_name = validate_display_name(upload.filename)
    # 原始头中的危险形态（盘符/UNC/绝对路径）会被解析器静默归一化，必须单独拒绝
    raw_name = _raw_header_filename(upload)
    if raw_name is not None and raw_name != display_name:
        validate_display_name(raw_name)
    file_type = resolve_file_type(display_name, upload.content_type)

    limit = settings.max_upload_bytes
    if upload.size is not None and upload.size > limit:
        raise ApiError(DOCUMENT_TOO_LARGE, details={"max_mb": settings.max_upload_mb})

    tmp_dir = Path(settings.upload_tmp_path)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    # 临时文件保留真实扩展名：部分解析器（如 openpyxl）按扩展名拒绝打开
    temp_path = tmp_dir / f"upload-{uuid.uuid4().hex}.{file_type}"

    digest = hashlib.sha256()
    size = 0
    try:
        with open(temp_path, "wb") as handle:
            while True:
                chunk = await upload.read(READ_CHUNK_SIZE)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise ApiError(DOCUMENT_TOO_LARGE, details={"max_mb": settings.max_upload_mb})
                digest.update(chunk)
                handle.write(chunk)

        if size == 0:
            raise ApiError(DOCUMENT_EMPTY)

        validate_container(temp_path, file_type)
        sha256 = digest.hexdigest()
    except BaseException:
        temp_path.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()

    return PendingUpload(
        file_name=display_name,
        file_type=file_type,
        mime_type=MIME_TYPES[file_type],
        size_bytes=size,
        sha256=sha256,
        storage_name=build_storage_name(sha256, file_type),
        temp_path=temp_path,
    )


def commit_upload(pending: PendingUpload, settings: Settings) -> StoredUpload:
    """把已校验的临时文件原子移动到上传目录（同一数据卷内的 rename）。"""
    upload_dir = Path(settings.upload_path)
    upload_dir.mkdir(parents=True, exist_ok=True)
    final_path = upload_dir / pending.storage_name
    os.replace(pending.temp_path, final_path)
    return StoredUpload(
        file_name=pending.file_name,
        file_type=pending.file_type,
        mime_type=pending.mime_type,
        size_bytes=pending.size_bytes,
        sha256=pending.sha256,
        storage_name=pending.storage_name,
        storage_path=str(final_path),
    )


def discard_upload(pending: PendingUpload) -> None:
    """丢弃未被采用的临时文件；已提交时是空操作。"""
    pending.temp_path.unlink(missing_ok=True)
