"""阶段 7C：学业导入文档的真实证据切片。

学业导入（7B-2）只保存 ``DocumentBlock``，不建立切片、不建立 ``DocumentPipelineState``、
不进入 RAG worker。但 ``PlanningEvidence`` 必须引用**真实存在的 chunk** 与真实定位，
因此本模块为学业导入文档确定性生成「证据切片」：

- 复用正式的确定性切片组件（``chunk_blocks`` + ``build_chunk_records`` + ``compute_chunk_id``），
  绝不使用随机 ID；
- 证据切片使用**独立的证据切片剖面版本**，因此其 chunk_id 永远不会与同一文件的
  RAG 切片碰撞（同一份文件可以同时存在于普通上传通道与学业导入通道）；
- 只写入 ``document_chunks``：**不建立向量、不写 FTS（``fts_rowid`` 保持 NULL）、
  不创建 ``DocumentPipelineState``、不改 ``retrievable``**，所以 RAG worker 仍然不会认领它；
- 幂等：重复执行只补缺失行，不删除、不改变既有 ID。
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import constants
from app.academic.projection import (
    SourceBlockView,
    project_rule_set_blocks,
    rule_set_fingerprint,
)
from app.academic.service import load_block_views, load_chunk_locators, resolve_source_chunk
from app.academic.types import AcademicDataError
from app.config import Settings
from app.core.errors import (
    ACADEMIC_DATA_CORRUPT,
    ACADEMIC_EVIDENCE_UNAVAILABLE,
    ACADEMIC_SOURCE_INVALID,
    ApiError,
)
from app.core.hashing import stable_digest
from app.documents.chunking import ChunkSourceBlock, chunk_blocks
from app.documents.fingerprint import CHUNKER_VERSION, PARSER_VERSION
from app.documents.service import build_chunk_records
from app.models import (
    AcademicRecordSet,
    AcademicRuleSet,
    CourseRecordRow,
    DegreeRuleCourse,
    DegreeRuleRow,
    Document,
    DocumentBlock,
    DocumentChunk,
    DocumentPipelineState,
)

# 证据切片剖面：与 RAG 切片共用一个公式，但版本不同 -> chunk_id 与 RAG 切片必然不同
EVIDENCE_PROFILE_VERSION = "academic-evidence-v1"
EVIDENCE_CHUNKER_VERSION = f"{CHUNKER_VERSION}+{EVIDENCE_PROFILE_VERSION}"


def _evidence_chunker_fingerprint(settings: Settings) -> str:
    return stable_digest(
        {
            "chunker_version": EVIDENCE_CHUNKER_VERSION,
            "target_chars": settings.chunk_target_chars,
            "overlap_chars": settings.chunk_overlap_chars,
        }
    )


def _document_or_fail(session: Session, doc_id: str) -> Document:
    document = session.get(Document, doc_id)
    if document is None or document.deleted_at is not None:
        raise ApiError(ACADEMIC_SOURCE_INVALID)
    if document.source_type != constants.SOURCE_UPLOAD:
        raise ApiError(ACADEMIC_SOURCE_INVALID)
    state = session.scalar(
        select(DocumentPipelineState.doc_id).where(DocumentPipelineState.doc_id == doc_id)
    )
    if state is not None:
        # 有检查点的文档属于普通上传通道，其切片由 RAG 流水线拥有，不得在此改造
        raise ApiError(ACADEMIC_SOURCE_INVALID)
    return document


def ensure_evidence_chunks(session: Session, settings: Settings, document: Document) -> int:
    """为学业导入文档确定性生成缺失的证据切片；返回本次新增行数。"""
    document = _document_or_fail(session, document.id)
    existing = {
        str(chunk_id)
        for chunk_id in session.scalars(
            select(DocumentChunk.id).where(DocumentChunk.doc_id == document.id)
        ).all()
    }
    blocks = list(
        session.scalars(
            select(DocumentBlock)
            .where(DocumentBlock.doc_id == document.id)
            .order_by(DocumentBlock.block_index)
        ).all()
    )
    if not blocks:
        raise ApiError(ACADEMIC_EVIDENCE_UNAVAILABLE, details={"reason": "no_blocks"})

    try:
        drafts = chunk_blocks(
            [
                ChunkSourceBlock(
                    block_index=block.block_index,
                    text=block.text,
                    block_type=block.block_type,
                    page_number=block.page_number,
                    sheet_name=block.sheet_name,
                    row_start=block.row_start,
                    row_end=block.row_end,
                    section_title=block.section_title,
                )
                for block in blocks
            ],
            target_chars=settings.chunk_target_chars,
            overlap_chars=settings.chunk_overlap_chars,
        )
    except ApiError as error:
        raise ApiError(ACADEMIC_EVIDENCE_UNAVAILABLE, details={"reason": error.code}) from error

    records = build_chunk_records(
        document,
        drafts,
        parser_version=PARSER_VERSION,
        chunker_version=EVIDENCE_CHUNKER_VERSION,
        chunker_fingerprint=_evidence_chunker_fingerprint(settings),
        title=document.file_name,
    )
    created = 0
    for record in records:
        if record["id"] in existing:
            continue
        session.add(DocumentChunk(fts_rowid=None, **record))
        created += 1
    if created:
        session.flush()
    return created


def resolve_chunk_by_block(
    locators: Sequence[tuple[str, int, dict]], block_index: int
) -> str | None:
    """选取覆盖指定 block 的证据切片。

    排序与阶段 7B-1 一致：完全覆盖 → 覆盖范围最窄 → ``chunk_index`` → ``chunk_id``。
    """
    candidates: list[tuple[int, int, int, str]] = []
    for chunk_id, chunk_index, locator in locators:
        start = locator.get("block_start")
        end = locator.get("block_end")
        if not isinstance(start, int) or not isinstance(end, int):
            continue
        if not (start <= block_index <= end):
            continue
        exact = 1 if (start == block_index and end == block_index) else 0
        candidates.append((exact, end - start, chunk_index, chunk_id))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1], item[2], item[3]))
    return candidates[0][3]


def resolve_block_chunk(
    locators: Sequence[tuple[str, int, dict]], block: SourceBlockView | None
) -> str | None:
    """按块的**真实 locator** 解析覆盖它的证据切片（XLSX 按工作表/行，PDF/DOCX 按页/标题）。"""
    if block is None:
        return None
    chunk_id = resolve_chunk_by_block(locators, block.block_index)
    if chunk_id is not None:
        return chunk_id
    if block.sheet_name is not None and block.row_start is not None:
        return resolve_source_chunk(
            locators,
            sheet_name=block.sheet_name,
            row_start=block.row_start,
            row_end=block.row_end,
        )
    return resolve_source_chunk(
        locators, page_number=block.page_number, section_title=block.section_title
    )


def _block_at(blocks: Sequence[SourceBlockView], index: int) -> SourceBlockView | None:
    for block in blocks:
        if block.block_index == index:
            return block
    return None


def _require_chunk(chunk_id: str | None, *, reason: str) -> str:
    if not chunk_id:
        raise ApiError(ACADEMIC_EVIDENCE_UNAVAILABLE, details={"reason": reason})
    return chunk_id


def backfill_record_set(
    session: Session, settings: Settings, record_set: AcademicRecordSet
) -> int:
    """为课程记录集合幂等补建证据切片并回填 ``source_chunk_id``。"""
    document = _document_or_fail(session, record_set.source_doc_id)
    ensure_evidence_chunks(session, settings, document)
    locators = load_chunk_locators(session, document.id)

    updated = 0
    rows = list(
        session.scalars(
            select(CourseRecordRow)
            .where(CourseRecordRow.record_set_id == record_set.id)
            .order_by(CourseRecordRow.ordinal)
        ).all()
    )
    for row in rows:
        if row.source_chunk_id:
            continue
        chunk_id = resolve_source_chunk(
            locators,
            sheet_name=row.sheet_name,
            row_start=row.row_start,
            row_end=row.row_end,
        )
        if chunk_id is None and row.source_block_id:
            try:
                chunk_id = resolve_chunk_by_block(locators, int(row.source_block_id))
            except ValueError:  # pragma: no cover - 非法来源块编号
                chunk_id = None
        row.source_chunk_id = _require_chunk(
            chunk_id, reason=f"record_row:{row.course_code}"
        )
        updated += 1
    if updated:
        session.flush()
    return updated


def backfill_rule_set(session: Session, settings: Settings, rule_set: AcademicRuleSet) -> int:
    """为培养方案规则集合幂等补建证据切片并回填 ``source_chunk_id``。

    业务字段**不重新落库**：这里只在已持久化的 ``DocumentBlock`` 上重新推导
    「字段/类别/课程 → block」的定位映射（与导入时同一套确定性提取），并用它定位真实切片。
    """
    document = _document_or_fail(session, rule_set.source_doc_id)
    ensure_evidence_chunks(session, settings, document)
    locators = load_chunk_locators(session, document.id)
    blocks = load_block_views(session, document.id)

    try:
        draft = project_rule_set_blocks(blocks, doc_id=document.id)
    except AcademicDataError as error:
        raise ApiError(ACADEMIC_DATA_CORRUPT, details={"reason": error.code}) from error

    if rule_set_fingerprint(draft.rule_set) != rule_set.content_hash:
        # 已存业务字段与文档块推导结果不一致：属于数据损坏，绝不静默沿用
        raise ApiError(ACADEMIC_DATA_CORRUPT, details={"reason": "rule_content_mismatch"})

    updated = 0
    if not rule_set.source_chunk_id:
        rule_set.source_chunk_id = _require_chunk(
            resolve_block_chunk(locators, _block_at(blocks, draft.header_block_index)),
            reason="rule_header",
        )
        updated += 1

    category_blocks = dict(draft.category_blocks)
    for row in session.scalars(
        select(DegreeRuleRow)
        .where(DegreeRuleRow.rule_set_id == rule_set.id)
        .order_by(DegreeRuleRow.ordinal)
    ).all():
        if row.source_chunk_id:
            continue
        index = category_blocks.get(row.category)
        row.source_chunk_id = _require_chunk(
            resolve_block_chunk(locators, _block_at(blocks, index) if index is not None else None),
            reason=f"rule_category:{row.category}",
        )
        updated += 1

    course_blocks = dict(draft.course_blocks)
    for row in session.scalars(
        select(DegreeRuleCourse)
        .where(DegreeRuleCourse.rule_set_id == rule_set.id)
        .order_by(DegreeRuleCourse.ordinal)
    ).all():
        if row.source_chunk_id:
            continue
        index = course_blocks.get(row.course_code)
        row.source_chunk_id = _require_chunk(
            resolve_block_chunk(locators, _block_at(blocks, index) if index is not None else None),
            reason=f"rule_course:{row.course_code}",
        )
        updated += 1

    if updated:
        session.flush()
    return updated


__all__ = [
    "EVIDENCE_CHUNKER_VERSION",
    "EVIDENCE_PROFILE_VERSION",
    "backfill_record_set",
    "backfill_rule_set",
    "ensure_evidence_chunks",
    "resolve_block_chunk",
    "resolve_chunk_by_block",
]
