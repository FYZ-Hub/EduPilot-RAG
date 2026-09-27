"""阶段 7B-1：active demo 学业资料的确定性投影落库。

流程严格分两段：

1. **采集与校验（无写入）**：只读取唯一 active demo dataset 下、状态符合正式资格
   （``ready`` 且可检索）的文档，从**真实 DocumentBlock** 投影出 2 份匿名课程记录与
   2 份培养方案规则，全部在内存中完成并校验；
2. **落库（单事务）**：四份投影全部通过后才写入 record/rule set 及其子项，
   任一步失败即整体回滚，绝不留下部分可选集合。

``DocumentChunk`` 只用于建立真实 ``source_chunk_id``；业务字段绝不从 chunk 正文提取。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import constants
from app.academic.projection import (
    ACADEMIC_PROJECTION_VERSION,
    RecordDraft,
    RuleSetDraft,
    SourceBlockView,
    project_record_rows,
    project_rule_set_blocks,
    projection_fingerprint,
    record_set_display_name,
    record_set_fingerprint,
    rule_set_display_name,
    rule_set_fingerprint,
)
from app.academic.types import AcademicDataError, credit_text
from app.config import Settings
from app.models import (
    AcademicProjection,
    AcademicRecordSet,
    AcademicRuleSet,
    CourseRecordRow,
    DegreeRuleCourse,
    DegreeRuleRow,
    DemoActiveDataset,
    Document,
    DocumentBlock,
    DocumentChunk,
)

logger = logging.getLogger("app.academic")

DEMO_SOURCE_TYPE = "demo"
RECORD_DOC_CATEGORY = constants.DOC_CATEGORY_COURSE_RECORDS
RULE_DOC_CATEGORY = constants.DOC_CATEGORY_DEGREE_PLAN
DOCUMENT_READY = "ready"
PROJECTION_READY = "ready"
# 集合的激活状态：只有当前 active dataset 的 demo 集合为 active，其它 demo 版本一律 inactive
ACTIVATION_ACTIVE = "active"
ACTIVATION_INACTIVE = "inactive"


@dataclass(frozen=True)
class ProjectionReport:
    """投影结果摘要；不含任何隐私或路径信息。"""

    dataset_version: str
    record_sets: int = 0
    rule_sets: int = 0
    skipped: int = 0


@dataclass(frozen=True)
class _RecordProjection:
    document: Document
    blocks: tuple[SourceBlockView, ...]
    drafts: tuple[RecordDraft, ...]
    display_name: str
    content_hash: str
    fingerprint: str


@dataclass(frozen=True)
class _RuleProjection:
    document: Document
    blocks: tuple[SourceBlockView, ...]
    draft: RuleSetDraft
    display_name: str
    content_hash: str
    fingerprint: str


# ---------------------------------------------------------------------------
# chunk 映射（确定性排序，绝不依赖数据库未排序的“第一条”）
# ---------------------------------------------------------------------------


def load_chunk_locators(session: Session, doc_id: str) -> list[tuple[str, int, dict]]:
    """读取同一文档下全部切片的 (chunk_id, chunk_index, locator)，顺序无关。

    来源定位（page_number / sheet_name / row_start / row_end / section_title）保存在
    ``DocumentChunk.locator``；``citation`` 只存文档级引用字段，不能用于定位。
    """
    rows = session.execute(
        select(DocumentChunk.id, DocumentChunk.chunk_index, DocumentChunk.locator).where(
            DocumentChunk.doc_id == doc_id
        )
    ).all()
    return [(str(row[0]), int(row[1]), dict(row[2] or {})) for row in rows]


def resolve_source_chunk(
    locators: Sequence[tuple[str, int, dict]],
    *,
    sheet_name: str | None = None,
    row_start: int | None = None,
    row_end: int | None = None,
    page_number: int | None = None,
    section_title: str | None = None,
) -> str | None:
    """按固定排序选取唯一的来源切片：完全匹配 → 覆盖范围最窄 → chunk_index → chunk_id。"""
    candidates: list[tuple[int, int, int, str]] = []
    for chunk_id, chunk_index, locator in locators:
        if sheet_name is not None:
            if locator.get("sheet_name") != sheet_name or row_start is None:
                continue
            covered_start = locator.get("row_start")
            covered_end = locator.get("row_end")
            if covered_start is None or covered_end is None:
                continue
            target_end = row_end if row_end is not None else row_start
            if not (covered_start <= row_start and target_end <= covered_end):
                continue
            exact = 1 if (covered_start == row_start and covered_end == target_end) else 0
            span = covered_end - covered_start
        else:
            if page_number is None or locator.get("page_number") != page_number:
                continue
            exact = 1 if (section_title and locator.get("section_title") == section_title) else 0
            span = 0
        candidates.append((exact, span, chunk_index, chunk_id))
    if not candidates:
        return None
    candidates.sort(key=lambda item: (-item[0], item[1], item[2], item[3]))
    return candidates[0][3]


# ---------------------------------------------------------------------------
# 第一段：采集与内存校验（只读）
# ---------------------------------------------------------------------------


def active_dataset_version(session: Session) -> str:
    """返回唯一 active demo dataset 版本；没有唯一 active 指针即明确失败。"""
    versions = [
        str(value)
        for value in session.scalars(
            select(DemoActiveDataset.dataset_version).where(
                DemoActiveDataset.active_marker.is_not(None)
            )
        ).all()
    ]
    if len(versions) != 1:
        raise AcademicDataError(f"expected exactly one active demo dataset, found {len(versions)}")
    return versions[0]


def load_block_views(session: Session, doc_id: str) -> tuple[SourceBlockView, ...]:
    """读取文档的解析块视图（业务字段的唯一来源）。"""
    blocks = session.scalars(
        select(DocumentBlock)
        .where(DocumentBlock.doc_id == doc_id)
        .order_by(DocumentBlock.block_index)
    ).all()
    return tuple(
        SourceBlockView(
            block_index=block.block_index,
            block_type=block.block_type,
            text=block.text,
            page_number=block.page_number,
            sheet_name=block.sheet_name,
            row_start=block.row_start,
            row_end=block.row_end,
            section_title=block.section_title,
        )
        for block in blocks
    )


def _eligible_documents(session: Session, dataset_version: str) -> list[Document]:
    """只取当前 active dataset 下、状态符合正式资格的学业文档。"""
    return list(
        session.scalars(
            select(Document)
            .where(
                Document.source_type == DEMO_SOURCE_TYPE,
                Document.dataset_version == dataset_version,
                Document.doc_category.in_((RECORD_DOC_CATEGORY, RULE_DOC_CATEGORY)),
                Document.status == DOCUMENT_READY,
                Document.retrievable.is_(True),
            )
            .order_by(Document.file_name)
        ).all()
    )


def _block_evidence(blocks: Sequence[SourceBlockView]) -> list[str]:
    return [
        f"{block.block_index}|{block.block_type}|{block.text}|{block.sheet_name}"
        f"|{block.row_start}|{block.row_end}|{block.page_number}"
        for block in blocks
    ]


def _collect_record_projection(session: Session, document: Document) -> _RecordProjection:
    blocks = load_block_views(session, document.id)
    drafts = project_record_rows(blocks)
    display_name = record_set_display_name(document.file_name.rsplit(".", 1)[0])
    content_hash = record_set_fingerprint([draft.record for draft in drafts], display_name)
    fingerprint = projection_fingerprint(
        [ACADEMIC_PROJECTION_VERSION, document.sha256, *_block_evidence(blocks), content_hash]
    )
    return _RecordProjection(
        document=document,
        blocks=blocks,
        drafts=drafts,
        display_name=display_name,
        content_hash=content_hash,
        fingerprint=fingerprint,
    )


def _collect_rule_projection(session: Session, document: Document) -> _RuleProjection:
    blocks = load_block_views(session, document.id)
    draft = project_rule_set_blocks(blocks, doc_id=document.id)
    display_name = rule_set_display_name(draft.rule_set.major, draft.rule_set.rule_version)
    content_hash = rule_set_fingerprint(draft.rule_set)
    fingerprint = projection_fingerprint(
        [ACADEMIC_PROJECTION_VERSION, document.sha256, *_block_evidence(blocks), content_hash]
    )
    return _RuleProjection(
        document=document,
        blocks=blocks,
        draft=draft,
        display_name=display_name,
        content_hash=content_hash,
        fingerprint=fingerprint,
    )


def collect_demo_projections(
    session: Session, settings: Settings
) -> tuple[str, list[_RecordProjection], list[_RuleProjection]]:
    """在内存中完成全部四份投影并校验；不做任何写入。"""
    del settings  # 当前投影只依赖 active 指针与已持久化文档，不需要额外配置
    dataset_version = active_dataset_version(session)
    documents = _eligible_documents(session, dataset_version)
    record_documents = [doc for doc in documents if doc.doc_category == RECORD_DOC_CATEGORY]
    rule_documents = [doc for doc in documents if doc.doc_category == RULE_DOC_CATEGORY]
    if not record_documents:
        raise AcademicDataError("active demo dataset has no eligible course records document")
    if not rule_documents:
        raise AcademicDataError("active demo dataset has no eligible degree plan document")

    record_projections = [
        _collect_record_projection(session, document) for document in record_documents
    ]
    rule_projections = [
        _collect_rule_projection(session, document) for document in rule_documents
    ]
    _validate_sources(session, record_projections, rule_projections)
    return dataset_version, record_projections, rule_projections


def _validate_sources(
    session: Session,
    record_projections: Sequence[_RecordProjection],
    rule_projections: Sequence[_RuleProjection],
) -> None:
    """校验每条记录与每个规则字段都能映射到**同一文档内真实存在**的切片。"""
    for projection in record_projections:
        document = projection.document
        locators = load_chunk_locators(session, document.id)
        if not locators:
            raise AcademicDataError("course records document has no chunks")
        for draft in projection.drafts:
            chunk_id = resolve_source_chunk(
                locators,
                sheet_name=draft.sheet_name,
                row_start=draft.row_start,
                row_end=draft.row_end,
            )
            if chunk_id is None:
                raise AcademicDataError(
                    f"no chunk covers record block {draft.block_index} in the same document"
                )

    for projection in rule_projections:
        document = projection.document
        locators = load_chunk_locators(session, document.id)
        if not locators:
            raise AcademicDataError("degree plan document has no chunks")
        header = _block_by_index(projection.blocks, projection.draft.header_block_index)
        if header is None:
            raise AcademicDataError("degree plan header block is missing")
        if (
            resolve_source_chunk(
                locators, page_number=header.page_number, section_title=header.section_title
            )
            is None
        ):
            raise AcademicDataError("no chunk covers the degree plan 毕业总学分 block")
        by_category = {name: index for name, index in projection.draft.category_blocks}
        for declaration in projection.draft.rule_set.categories:
            block = _block_by_index(projection.blocks, by_category.get(declaration.category, -1))
            if block is None or resolve_source_chunk(
                locators, page_number=block.page_number, section_title=block.section_title
            ) is None:
                raise AcademicDataError(
                    f"no chunk covers rule category {declaration.category!r}"
                )


def _block_by_index(
    blocks: Sequence[SourceBlockView], block_index: int
) -> SourceBlockView | None:
    for block in blocks:
        if block.block_index == block_index:
            return block
    return None


# ---------------------------------------------------------------------------
# 第二段：单事务落库（指纹感知的幂等）
# ---------------------------------------------------------------------------


def project_active_demo(session: Session, settings: Settings) -> ProjectionReport:
    """采集并落库；调用方负责事务边界（异常时整体回滚）。"""
    dataset_version, record_projections, rule_projections = collect_demo_projections(
        session, settings
    )

    created_records = 0
    created_rules = 0
    skipped = 0
    for projection in record_projections:
        if _persist_record_projection(session, dataset_version, projection):
            created_records += 1
        else:
            skipped += 1
    for projection in rule_projections:
        if _persist_rule_projection(session, dataset_version, projection):
            created_rules += 1
        else:
            skipped += 1
    # 状态对账与投影落库处于同一事务；即使本次全部 skipped 也必须执行
    _sync_demo_activation(session, dataset_version)
    session.flush()
    return ProjectionReport(
        dataset_version=dataset_version,
        record_sets=created_records,
        rule_sets=created_rules,
        skipped=skipped,
    )


def _sync_demo_activation(session: Session, dataset_version: str) -> None:
    """把 active dataset 的 demo 集合写为 active，其它 demo 版本统一退役为 inactive。

    只处理 ``source_type == "demo"``：upload 来源自有其激活语义，完全不参与本次对账。
    不删除任何旧集合，只切换状态；对同一输入是幂等的。
    """
    for model in (AcademicRecordSet, AcademicRuleSet):
        rows = session.scalars(
            select(model).where(model.source_type == DEMO_SOURCE_TYPE)
        ).all()
        for row in rows:
            desired = (
                ACTIVATION_ACTIVE
                if row.dataset_version == dataset_version
                else ACTIVATION_INACTIVE
            )
            if row.activation_state != desired:
                row.activation_state = desired


def _existing_projection(
    session: Session, document: Document, dataset_version: str
) -> AcademicProjection | None:
    return session.scalars(
        select(AcademicProjection).where(
            AcademicProjection.source_type == document.source_type,
            AcademicProjection.source_key == document.source_key,
            AcademicProjection.dataset_version == dataset_version,
        )
    ).first()


def _drop_previous(
    session: Session,
    existing: AcademicProjection,
) -> None:
    """指纹变化时先原子移除旧投影及其集合，避免继续提供陈旧规则。"""
    if existing.record_set_id:
        previous = session.get(AcademicRecordSet, existing.record_set_id)
        if previous is not None:
            session.delete(previous)
    if existing.rule_set_id:
        previous = session.get(AcademicRuleSet, existing.rule_set_id)
        if previous is not None:
            session.delete(previous)
    session.delete(existing)
    session.flush()


def _persist_record_projection(
    session: Session, dataset_version: str, projection: _RecordProjection
) -> bool:
    document = projection.document
    existing = _existing_projection(session, document, dataset_version)
    if existing is not None:
        if existing.projection_fingerprint == projection.fingerprint:
            if existing.status != PROJECTION_READY:
                existing.status = PROJECTION_READY
            return False
        _drop_previous(session, existing)

    locators = load_chunk_locators(session, document.id)
    record_set = AcademicRecordSet(
        source_type=document.source_type,
        source_key=document.source_key,
        dataset_version=dataset_version,
        status=PROJECTION_READY,
        display_name=projection.display_name,
        major=None,
        record_count=len(projection.drafts),
        content_hash=projection.content_hash,
        source_doc_id=document.id,
    )
    session.add(record_set)
    session.flush()
    for ordinal, draft in enumerate(projection.drafts):
        chunk_id = resolve_source_chunk(
            locators,
            sheet_name=draft.sheet_name,
            row_start=draft.row_start,
            row_end=draft.row_end,
        )
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
                source_chunk_id=chunk_id,
                source_block_id=str(draft.block_index),
                sheet_name=draft.sheet_name,
                row_start=draft.row_start,
                row_end=draft.row_end,
            )
        )
    session.add(
        AcademicProjection(
            source_type=document.source_type,
            source_key=document.source_key,
            dataset_version=dataset_version,
            projection_fingerprint=projection.fingerprint,
            record_set_id=record_set.id,
            status=PROJECTION_READY,
        )
    )
    return True


def _persist_rule_projection(
    session: Session, dataset_version: str, projection: _RuleProjection
) -> bool:
    document = projection.document
    existing = _existing_projection(session, document, dataset_version)
    if existing is not None:
        if existing.projection_fingerprint == projection.fingerprint:
            if existing.status != PROJECTION_READY:
                existing.status = PROJECTION_READY
            return False
        _drop_previous(session, existing)

    locators = load_chunk_locators(session, document.id)
    rule_set = projection.draft.rule_set
    header = _block_by_index(projection.blocks, projection.draft.header_block_index)
    header_chunk = (
        resolve_source_chunk(
            locators, page_number=header.page_number, section_title=header.section_title
        )
        if header is not None
        else None
    )
    category_chunks = {name: index for name, index in projection.draft.category_blocks}
    course_chunks = {code: index for code, index in projection.draft.course_blocks}

    stored = AcademicRuleSet(
        source_type=document.source_type,
        source_key=document.source_key,
        dataset_version=dataset_version,
        status=PROJECTION_READY,
        display_name=projection.display_name,
        major=rule_set.major,
        admission_year=rule_set.admission_year,
        rule_version=rule_set.rule_version,
        effective_from=rule_set.effective_from,
        required_credits=credit_text(rule_set.required_credits),
        category_order=[declaration.category for declaration in rule_set.categories],
        course_count=len(rule_set.courses),
        content_hash=projection.content_hash,
        source_doc_id=document.id,
        source_chunk_id=header_chunk,
    )
    session.add(stored)
    session.flush()

    for ordinal, declaration in enumerate(rule_set.categories):
        block = _block_by_index(projection.blocks, category_chunks.get(declaration.category, -1))
        chunk_id = (
            resolve_source_chunk(
                locators, page_number=block.page_number, section_title=block.section_title
            )
            if block is not None
            else None
        )
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
                source_chunk_id=chunk_id,
            )
        )

    for ordinal, course in enumerate(rule_set.courses):
        block = _block_by_index(projection.blocks, course_chunks.get(course.course_code, -1))
        chunk_id = (
            resolve_source_chunk(
                locators, page_number=block.page_number, section_title=block.section_title
            )
            if block is not None
            else None
        )
        session.add(
            DegreeRuleCourse(
                rule_set_id=stored.id,
                ordinal=ordinal,
                course_code=course.course_code,
                course_name=course.course_name,
                credits=credit_text(course.credits),
                category=course.category,
                source_doc_id=document.id,
                source_chunk_id=chunk_id,
            )
        )
    session.add(
        AcademicProjection(
            source_type=document.source_type,
            source_key=document.source_key,
            dataset_version=dataset_version,
            projection_fingerprint=projection.fingerprint,
            rule_set_id=stored.id,
            status=PROJECTION_READY,
        )
    )
    return True


def reconcile_academic_projection(session: Session, settings: Settings) -> ProjectionReport:
    """幂等对账入口：重复 seed（含全部 skipped）后仍可补建缺失投影。"""
    return project_active_demo(session, settings)


__all__ = [
    "DEMO_SOURCE_TYPE",
    "DOCUMENT_READY",
    "PROJECTION_READY",
    "RECORD_DOC_CATEGORY",
    "RULE_DOC_CATEGORY",
    "ProjectionReport",
    "active_dataset_version",
    "collect_demo_projections",
    "load_block_views",
    "load_chunk_locators",
    "project_active_demo",
    "reconcile_academic_projection",
    "resolve_source_chunk",
]
