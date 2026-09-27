"""上传文件的容器级安全检查（PDF 头、加密、OOXML 宏/嵌入/外部引用、ZIP 炸弹）。"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from app.constants import FILE_TYPE_DOCX, FILE_TYPE_PDF, FILE_TYPE_XLSX
from app.core.errors import (
    DOCUMENT_CONTENT_TYPE_MISMATCH,
    DOCUMENT_CORRUPT,
    DOCUMENT_EMPTY,
    DOCUMENT_UNSAFE_CONTAINER,
    ApiError,
)

PDF_MAGIC = b"%PDF-"
PDF_MAGIC_SCAN_BYTES = 1024

MAX_ZIP_ENTRIES = 2048
MAX_ZIP_UNCOMPRESSED_BYTES = 256 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 200
RATIO_CHECK_MIN_SIZE = 1024 * 1024

CONTENT_TYPE_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"
CONTENT_TYPE_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"

# 宏 / 嵌入对象 / 外部链接相关标记（大小写不敏感）
FORBIDDEN_CONTENT_TYPE_MARKERS = (
    "macroenabled",
    "vbaproject",
    "ms-word.document.macro",
    "ms-excel.sheet.macro",
)

FORBIDDEN_ENTRY_PREFIXES = (
    "word/vbaproject",
    "xl/vbaproject",
    "ppt/vbaproject",
    "word/activex/",
    "xl/activex/",
    "ppt/activex/",
    "word/embeddings/",
    "xl/embeddings/",
    "ppt/embeddings/",
    "xl/externallinks/",
)

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


def _unsafe(reason: str) -> ApiError:
    return ApiError(DOCUMENT_UNSAFE_CONTAINER, details={"reason": reason})


def _assert_safe_entry_name(name: str) -> None:
    if (
        not name
        or name.startswith("/")
        or "\\" in name
        or ".." in name
        or _DRIVE_RE.match(name)
    ):
        raise _unsafe("unsafe_zip_entry")


def inspect_pdf(path: Path) -> None:
    """校验 PDF 文件头、加密状态与可解析性。"""
    with open(path, "rb") as handle:
        head = handle.read(PDF_MAGIC_SCAN_BYTES)
    if PDF_MAGIC not in head:
        raise ApiError(DOCUMENT_CONTENT_TYPE_MISMATCH, details={"expected": FILE_TYPE_PDF})

    import pymupdf

    try:
        document = pymupdf.open(str(path))
    except Exception as error:  # noqa: BLE001 - 第三方解析异常统一收敛
        raise ApiError(DOCUMENT_CORRUPT, retryable=False) from error
    try:
        if document.needs_pass or document.is_encrypted:
            raise _unsafe("encrypted_pdf")
        if document.page_count <= 0:
            raise ApiError(DOCUMENT_EMPTY)
    finally:
        document.close()


def detect_ooxml_type(content_types_xml: str) -> str | None:
    if CONTENT_TYPE_XLSX in content_types_xml:
        return FILE_TYPE_XLSX
    if CONTENT_TYPE_DOCX in content_types_xml:
        return FILE_TYPE_DOCX
    return None


def inspect_ooxml(path: Path, expected_type: str) -> None:
    """校验 OOXML 容器：条目安全、无宏、无嵌入对象、无外部关系、非 ZIP 炸弹。"""
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as error:
        raise ApiError(DOCUMENT_CORRUPT, retryable=False) from error

    with archive:
        infos = archive.infolist()
        if len(infos) > MAX_ZIP_ENTRIES:
            raise _unsafe("too_many_zip_entries")

        total_uncompressed = 0
        for info in infos:
            if info.flag_bits & 0x1:
                raise _unsafe("encrypted_zip_entry")
            _assert_safe_entry_name(info.filename)
            total_uncompressed += info.file_size
            if total_uncompressed > MAX_ZIP_UNCOMPRESSED_BYTES:
                raise _unsafe("zip_bomb_total_size")
            if info.file_size >= RATIO_CHECK_MIN_SIZE and info.compress_size > 0:
                if info.file_size / info.compress_size > MAX_ZIP_COMPRESSION_RATIO:
                    raise _unsafe("zip_bomb_ratio")

        names = [info.filename for info in infos]
        if "[Content_Types].xml" not in names:
            raise ApiError(DOCUMENT_CONTENT_TYPE_MISMATCH, details={"expected": "ooxml"})

        lowered_names = [name.casefold() for name in names]
        for prefix in FORBIDDEN_ENTRY_PREFIXES:
            if any(name.startswith(prefix) for name in lowered_names):
                raise _unsafe("macro_or_embedded_object")

        content_types = archive.read("[Content_Types].xml").decode("utf-8", "ignore")
        lowered_types = content_types.casefold()
        for marker in FORBIDDEN_CONTENT_TYPE_MARKERS:
            if marker in lowered_types:
                raise _unsafe("macro_enabled_document")

        for name, lowered in zip(names, lowered_names):
            if lowered.endswith(".rels"):
                body = archive.read(name).decode("utf-8", "ignore").casefold()
                if 'targetmode="external"' in body:
                    raise _unsafe("external_relationship")

        detected = detect_ooxml_type(content_types)
        if detected is None:
            raise ApiError(DOCUMENT_CONTENT_TYPE_MISMATCH, details={"expected": expected_type})
        if detected != expected_type:
            raise ApiError(
                DOCUMENT_CONTENT_TYPE_MISMATCH,
                details={"expected": expected_type, "detected": detected},
            )


def assert_parseable(path: Path, file_type: str) -> None:
    """用真实解析器确认文件可打开；失败统一视为损坏。"""
    try:
        if file_type == FILE_TYPE_PDF:
            inspect_pdf(path)
            return
        if file_type == FILE_TYPE_DOCX:
            import docx

            docx.Document(str(path))
            return
        if file_type == FILE_TYPE_XLSX:
            import openpyxl

            workbook = openpyxl.load_workbook(str(path), data_only=False, read_only=True)
            workbook.close()
            return
    except ApiError:
        raise
    except Exception as error:  # noqa: BLE001 - 第三方解析异常统一收敛
        raise ApiError(DOCUMENT_CORRUPT, retryable=False) from error
    raise ApiError(DOCUMENT_CONTENT_TYPE_MISMATCH, details={"expected": file_type})


def validate_container(path: Path, file_type: str) -> None:
    """按扩展名分派容器校验。"""
    if file_type == FILE_TYPE_PDF:
        inspect_pdf(path)
    elif file_type in (FILE_TYPE_DOCX, FILE_TYPE_XLSX):
        inspect_ooxml(path, file_type)
    else:  # pragma: no cover - 上传入口已先做扩展名白名单
        raise ApiError(DOCUMENT_CONTENT_TYPE_MISMATCH, details={"expected": file_type})
    assert_parseable(path, file_type)
