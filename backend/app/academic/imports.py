"""阶段 7B-2：上传学业资料的确定性导入（课程记录 / 培养方案规则）。

设计要点：

- **复用正式安全组件**：显示文件名、扩展名、声明 MIME、文件头 / OOXML 容器、
  ZIP 条目路径、压缩炸弹、宏与嵌入对象、外部引用等校验全部走
  ``app.documents.upload`` 与 ``app.documents.container``，不新写弱化版校验器。
- **复用正式解析组件**：``app.documents.parsing.parse_document`` 生成块；业务字段
  **只**从这些块（表格结构）提取，绝不从 ``DocumentChunk`` 正文提取。
- **先校验后落盘**：文件安全校验、解析与投影全部通过后，才把文件提交到上传目录并写入
  数据库；写入在**单个事务**内完成。任一环节失败都不产生可见集合，也不留下部分子项。
- **检索隔离**：导入建立的 ``Document`` 只作为学业证据来源，``retrievable`` 恒为
  ``False``，不进入 RAG 检索语料，也不伪造向量 / FTS 完成状态，更不污染 demo 数据集。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import constants
from app.academic.projection import (
    SourceBlockView,
    project_record_rows,
    project_rule_set_blocks,
    record_content_fingerprint,
    record_set_display_name,
    rule_set_display_name,
    rule_set_fingerprint,
    safe_display_name,
)
from app.academic.service import PROJECTION_READY, RECORD_DOC_CATEGORY, RULE_DOC_CATEGORY
from app.academic.types import AcademicDataError, credit_text
from app.config import Settings
from app.core.errors import (
    ACADEMIC_FILE_TYPE_UNSUPPORTED,
    ACADEMIC_PARSE_FAILED,
    ACADEMIC_PERSIST_FAILED,
    ACADEMIC_SOURCE_UNAVAILABLE,
    ApiError,
)
from app.documents.naming import validate_display_name
from app.documents.parsing import parse_document
from app.documents.upload import (
    StoredUpload,
    commit_upload,
    discard_upload,
    ingest_upload,
)
from app.models import (
    AcademicRecordSet,
    AcademicRuleSet,
    CourseRecordRow,
    DegreeRuleCourse,
    DegreeRuleRow,
    Document,
    DocumentBlock,
    new_uuid,
)

logger = logging.getLogger("app.academic")

RECORDS_FILE_TYPES = (constants.FILE_TYPE_XLSX,)
RULES_FILE_TYPES = (
    constants.FILE_TYPE_PDF,
    constants.FILE_TYPE_DOCX,
    constants.FILE_TYPE_XLSX,
)

_ACCEPTED_LABELS = {constants.FILE_TYPE_XLSX: "xlsx"}


@dataclass(frozen=True)
class ImportResult:
    """导入成功响应；字段严格为 ``{id, status, warnings}``。"""

    set_id: str
    status: str
    warnings: tuple[str, ...] = ()

    def payload(self) -> dict:
        return {"id": self.set_id, "status": self.status, "warnings": list(self.warnings)}


# ---------------------------------------------------------------------------
# 文件名与显示名
# ---------------------------------------------------------------------------


def _requested_display_name(name: str | None) -> str | None:
    """可选 ``name`` 的安全校验：拒绝控制字符、路径分隔符与伪装路径。"""
    if name is None or not name.strip():
        return None
    return safe_display_name(validate_display_name(name))


def _file_stem(file_name: str) -> str:
    stem, _, _ = file_name.rpartition(".")
    return stem or file_name


def _ensure_accepted(file_type: str, accepted: tuple[str, ...]) -> None:
    if file_type not in accepted:
        raise ApiError(
            ACADEMIC_FILE_TYPE_UNSUPPORTED,
            details={"accepted": [_ACCEPTED_LABELS.get(item, item) for item in accepted]},
        )


# ---------------------------------------------------------------------------
# 解析与块视图（业务字段只来自 DocumentBlock 结构）
# ---------------------------------------------------------------------------


def _as_api_error(error: AcademicDataError) -> ApiError:
    """领域校验错误 → 稳定 HTTP 错误体；内部诊断信息不对外泄漏。"""
    logger.info("academic_import_rejected code=%s", error.code)
    return ApiError(error.code)


def _parse_upload(pending) -> tuple:
    try:
        blocks = tuple(parse_document(pending.temp_path, pending.file_type))
    except ApiError as error:
        raise ApiError(ACADEMIC_PARSE_FAILED, details={"reason": error.code}) from error
    if not blocks:
        raise ApiError(ACADEMIC_PARSE_FAILED, details={"reason": "empty_document"})
    return blocks


def _block_views(blocks) -> tuple[SourceBlockView, ...]:
    return tuple(
        SourceBlockView(
            block_index=index,
            block_type=block.block_type,
            text=block.text,
            page_number=block.page_number,
            sheet_name=block.sheet_name,
            row_start=block.row_start,
            row_end=block.row_end,
            section_title=block.section_title,
        )
        for index, block in enumerate(blocks)
    )


# ---------------------------------------------------------------------------
# 幂等查询
# ---------------------------------------------------------------------------


def _existing_set(session: Session, model, category: str, sha256: str):
    """同来源空间、同内容、来源文档仍有效的既有集合（与显示名无关）。"""
    return session.scalars(
        select(model)
        .join(Document, Document.id == model.source_doc_id)
        .where(
            model.source_type == constants.SOURCE_UPLOAD,
            Document.doc_category == category,
            Document.sha256 == sha256,
            Document.deleted_at.is_(None),
        )
        .order_by(model.created_at, model.id)
    ).first()


# ---------------------------------------------------------------------------
# 落库（单事务）
# ---------------------------------------------------------------------------


def _new_document(stored: StoredUpload, category: str, document_id: str) -> Document:
    return Document(
        id=document_id,
        source_type=constants.SOURCE_UPLOAD,
        source_key=f"upload:{stored.sha256}",
        file_name=stored.file_name,
        safe_storage_name=stored.storage_name,
        file_type=stored.file_type,
        mime_type=stored.mime_type,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        storage_path=stored.storage_path,
        doc_category=category,
        # 学业证据来源：文档状态与「学业集合是否可选」是两件事，这里只表示尚未进入
        # RAG 索引流水线，绝不冒充 ready，也不写入任何向量 / FTS 完成标记。
        status=constants.STATUS_QUEUED,
        current_stage=None,
        retrievable=False,
        activation_state=None,
    )


def _persist_blocks(session: Session, document_id: str, blocks) -> None:
    session.add_all(
        [
            DocumentBlock(
                doc_id=document_id,
                block_index=index,
                text=block.text,
                block_type=block.block_type,
                page_number=block.page_number,
                sheet_name=block.sheet_name,
                row_start=block.row_start,
                row_end=block.row_end,
                section_title=block.section_title,
                block_metadata=dict(block.metadata),
            )
            for index, block in enumerate(blocks)
        ]
    )


def _remove_stored_file(storage_path: str | None, root: Path) -> None:
    """只清理本次新建的文件；存储名含随机后缀，复用中的既有文件不受影响。"""
    if not storage_path:
        return
    try:
        target = Path(storage_path).resolve()
        root_resolved = root.resolve()
    except OSError:  # pragma: no cover - 路径异常时保守跳过
        return
    if target.parent != root_resolved:
        return
    target.unlink(missing_ok=True)


def _persist_import(
    session: Session,
    settings: Settings,
    pending,
    *,
    category: str,
    document_id: str,
    blocks,
    build_set,
    existing_lookup,
) -> ImportResult:
    """提交文件并单事务写入 Document / DocumentBlock / 集合与子项。

    失败时回滚事务并删除**本次新建**的文件，不留下孤立文件、部分子项或悬空外键。
    并发相同导入时唯一约束会拒绝后来的写入：回滚后按内容重新读取赢家结果并返回同一 ID。
    """
    try:
        stored = commit_upload(pending, settings)
    except OSError as error:
        # 原始文件无法安全落盘：不能建立可追溯的真实来源
        discard_upload(pending)
        raise ApiError(ACADEMIC_SOURCE_UNAVAILABLE) from error
    try:
        document = _new_document(stored, category, document_id)
        session.add(document)
        # 先落父文档，保证集合的 source_doc_id 指向真实存在的 Document
        # （跨 mapper 的插入顺序不能依赖 unit of work 自行推断）
        session.flush()
        _persist_blocks(session, document_id, blocks)
        session.flush()
        created = build_set(document)
        session.commit()
    except IntegrityError as error:
        session.rollback()
        _remove_stored_file(stored.storage_path, Path(settings.upload_path))
        existing = existing_lookup()
        if existing is None:
            raise ApiError(ACADEMIC_PERSIST_FAILED) from error
        return ImportResult(existing.id, existing.status)
    except BaseException:
        session.rollback()
        _remove_stored_file(stored.storage_path, Path(settings.upload_path))
        raise
    return ImportResult(created.id, created.status)


# ---------------------------------------------------------------------------
# 公开入口
# ---------------------------------------------------------------------------


async def import_record_set(
    upload: UploadFile, *, name: str | None, settings: Settings, session: Session
) -> ImportResult:
    """导入课程记录（仅 XLSX）。"""
    pending = await ingest_upload(upload, settings)
    try:
        _ensure_accepted(pending.file_type, RECORDS_FILE_TYPES)
        existing = _existing_set(session, AcademicRecordSet, RECORD_DOC_CATEGORY, pending.sha256)
        if existing is not None:
            discard_upload(pending)
            return ImportResult(existing.id, existing.status)

        blocks = _parse_upload(pending)
        try:
            drafts = project_record_rows(_block_views(blocks))
        except AcademicDataError as error:
            raise _as_api_error(error) from error
        display_name = _requested_display_name(name) or record_set_display_name(
            _file_stem(pending.file_name)
        )
        content_hash = record_content_fingerprint([draft.record for draft in drafts])
    except BaseException:
        discard_upload(pending)
        raise

    document_id = new_uuid()

    def build_set(document: Document) -> AcademicRecordSet:
        record_set = AcademicRecordSet(
            source_type=constants.SOURCE_UPLOAD,
            source_key=document.source_key,
            dataset_version=None,
            activation_state=constants.ACTIVATION_ACTIVE,
            status=PROJECTION_READY,
            display_name=display_name,
            major=None,
            record_count=len(drafts),
            content_hash=content_hash,
            source_doc_id=document.id,
        )
        session.add(record_set)
        session.flush()
        for ordinal, draft in enumerate(drafts):
            record = draft.record
            session.add(
                CourseRecordRow(
                    record_set_id=record_set.id,
                    ordinal=ordinal,
                    course_code=record.course_code,
                    course_name=record.course_name,
                    credits=credit_text(record.credits),
                    category=record.category,
                    grade=record.grade,
                    status=record.status,
                    semester=record.semester,
                    schedule=record.schedule,
                    source_doc_id=document.id,
                    # 学业导入文档不建立切片，因此来源定位只能留空，绝不伪造 chunk_id
                    source_chunk_id=None,
                    source_block_id=str(draft.block_index),
                    sheet_name=draft.sheet_name,
                    row_start=draft.row_start,
                    row_end=draft.row_end,
                )
            )
        session.flush()
        return record_set

    def existing_lookup() -> AcademicRecordSet | None:
        return _existing_set(
            session, AcademicRecordSet, RECORD_DOC_CATEGORY, pending.sha256
        )

    return _persist_import(
        session,
        settings,
        pending,
        category=RECORD_DOC_CATEGORY,
        document_id=document_id,
        blocks=blocks,
        build_set=build_set,
        existing_lookup=existing_lookup,
    )


async def import_rule_set(
    upload: UploadFile, *, name: str | None, settings: Settings, session: Session
) -> ImportResult:
    """导入培养方案规则（PDF / DOCX / XLSX）。"""
    pending = await ingest_upload(upload, settings)
    try:
        _ensure_accepted(pending.file_type, RULES_FILE_TYPES)
        existing = _existing_set(session, AcademicRuleSet, RULE_DOC_CATEGORY, pending.sha256)
        if existing is not None:
            discard_upload(pending)
            return ImportResult(existing.id, existing.status)

        blocks = _parse_upload(pending)
        document_id = new_uuid()
        try:
            draft = project_rule_set_blocks(_block_views(blocks), doc_id=document_id)
        except AcademicDataError as error:
            raise _as_api_error(error) from error
        rule_set = draft.rule_set
        display_name = _requested_display_name(name) or rule_set_display_name(
            rule_set.major, rule_set.rule_version
        )
        content_hash = rule_set_fingerprint(rule_set)
        warnings = draft.warnings
    except BaseException:
        discard_upload(pending)
        raise

    def build_set(document: Document) -> AcademicRuleSet:
        if document.id != rule_set.source_doc_id:  # pragma: no cover - 内部不变量
            raise ApiError(ACADEMIC_SOURCE_UNAVAILABLE)
        stored = AcademicRuleSet(
            source_type=constants.SOURCE_UPLOAD,
            source_key=document.source_key,
            dataset_version=None,
            activation_state=constants.ACTIVATION_ACTIVE,
            status=PROJECTION_READY,
            display_name=display_name,
            major=rule_set.major,
            admission_year=rule_set.admission_year,
            rule_version=rule_set.rule_version,
            effective_from=rule_set.effective_from,
            required_credits=credit_text(rule_set.required_credits),
            category_order=[declaration.category for declaration in rule_set.categories],
            course_count=len(rule_set.courses),
            content_hash=content_hash,
            source_doc_id=document.id,
            # 规则集合不写入未验证的切片引用
            source_chunk_id=None,
        )
        session.add(stored)
        session.flush()
        for ordinal, declaration in enumerate(rule_set.categories):
            session.add(
                DegreeRuleRow(
                    rule_set_id=stored.id,
                    ordinal=ordinal,
                    major=declaration.major,
                    admission_year=declaration.admission_year,
                    rule_version=declaration.rule_version,
                    category=declaration.category,
                    minimum_credits=credit_text(declaration.minimum_credits),
                    required_course_codes=list(declaration.required_course_codes),
                    effective_from=declaration.effective_from,
                    source_doc_id=document.id,
                    source_chunk_id=None,
                )
            )
        for ordinal, course in enumerate(rule_set.courses):
            session.add(
                DegreeRuleCourse(
                    rule_set_id=stored.id,
                    ordinal=ordinal,
                    course_code=course.course_code,
                    course_name=course.course_name,
                    credits=credit_text(course.credits),
                    category=course.category,
                    source_doc_id=document.id,
                    source_chunk_id=None,
                )
            )
        session.flush()
        return stored

    def existing_lookup() -> AcademicRuleSet | None:
        return _existing_set(session, AcademicRuleSet, RULE_DOC_CATEGORY, pending.sha256)

    result = _persist_import(
        session,
        settings,
        pending,
        category=RULE_DOC_CATEGORY,
        document_id=document_id,
        blocks=blocks,
        build_set=build_set,
        existing_lookup=existing_lookup,
    )
    return ImportResult(result.set_id, result.status, warnings)


__all__ = [
    "ImportResult",
    "RECORDS_FILE_TYPES",
    "RULES_FILE_TYPES",
    "import_record_set",
    "import_rule_set",
]
