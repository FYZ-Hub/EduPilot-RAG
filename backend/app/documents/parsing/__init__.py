"""多格式解析分发。"""

from __future__ import annotations

from pathlib import Path

from app.constants import FILE_TYPE_DOCX, FILE_TYPE_PDF, FILE_TYPE_XLSX
from app.core.errors import DOCUMENT_INVALID_TYPE, ApiError
from app.documents.blocks import ParsedBlock
from app.documents.parsing.docx_parser import parse_docx
from app.documents.parsing.pdf_parser import parse_pdf
from app.documents.parsing.xlsx_parser import parse_xlsx

PARSERS = {
    FILE_TYPE_PDF: parse_pdf,
    FILE_TYPE_DOCX: parse_docx,
    FILE_TYPE_XLSX: parse_xlsx,
}


def parse_document(path: Path, file_type: str) -> list[ParsedBlock]:
    """按类型解析文档，返回稳定顺序的 ``DocumentBlock`` 列表。"""
    parser = PARSERS.get(file_type)
    if parser is None:
        raise ApiError(DOCUMENT_INVALID_TYPE)
    return parser(path)


__all__ = ["PARSERS", "parse_document", "parse_docx", "parse_pdf", "parse_xlsx"]
