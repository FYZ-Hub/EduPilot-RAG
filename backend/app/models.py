"""SQLAlchemy 持久化模型。

时间统一使用**无时区的 UTC**（SQLite 以字符串保存），
避免带 offset 的 datetime 在 SQLite 往返后变成 naive 导致比较错误。
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from app import constants


def utcnow() -> datetime:
    """返回无时区的 UTC 当前时间。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Document(Base):
    """文档主体；``source_type`` 决定所有权与删除语义。"""

    __tablename__ = "documents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_key: Mapped[str] = mapped_column(String(640), nullable=False)
    file_name: Mapped[str] = mapped_column(String(512), nullable=False)
    safe_storage_name: Mapped[str | None] = mapped_column(String(255))
    file_type: Mapped[str] = mapped_column(String(8), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(160), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # 仅数据库内部使用，API 与日志都不得返回
    storage_path: Mapped[str | None] = mapped_column(Text)
    doc_category: Mapped[str] = mapped_column(String(64), nullable=False, default="unknown")
    dataset_version: Mapped[str | None] = mapped_column(String(64))
    document_version: Mapped[str | None] = mapped_column(String(64))
    effective_from: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=constants.STATUS_QUEUED)
    current_stage: Mapped[str | None] = mapped_column(String(32))
    retrievable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    activation_state: Mapped[str | None] = mapped_column(String(16))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    error_retryable: Mapped[bool | None] = mapped_column(Boolean)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # worker 租约（与 demo job 相同的条件更新模式）
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime)

    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime)

    blocks: Mapped[list["DocumentBlock"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )
    pipeline_state: Mapped["DocumentPipelineState | None"] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )

    __table_args__ = (
        Index("ix_documents_source_sha256", "source_type", "sha256"),
        Index("ix_documents_source_key", "source_type", "source_key"),
        Index("ix_documents_status", "status"),
    )


class DocumentBlock(Base):
    """可追溯的解析块；``block_index`` 在同一文档内唯一且稳定。"""

    __tablename__ = "document_blocks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    doc_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    block_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    block_type: Mapped[str] = mapped_column(String(32), nullable=False)
    page_number: Mapped[int | None] = mapped_column(Integer)
    sheet_name: Mapped[str | None] = mapped_column(String(255))
    row_start: Mapped[int | None] = mapped_column(Integer)
    row_end: Mapped[int | None] = mapped_column(Integer)
    section_title: Mapped[str | None] = mapped_column(Text)
    # 数据库列名为 metadata（产品契约字段名），Python 属性避开 Declarative 保留名
    block_metadata: Mapped[dict] = mapped_column("metadata", JSON, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    document: Mapped[Document] = relationship(back_populates="blocks")

    __table_args__ = (UniqueConstraint("doc_id", "block_index", name="uq_document_blocks_doc_index"),)


class DocumentChunk(Base):
    """确定性切片结果；``id`` 为稳定 chunk_id，``chunk_index`` 在同一文档内唯一。

    ``locator``（来源定位）参与 chunk_id 计算；
    ``citation``（文档级引用字段）用于生成引用，两者都只存 SQLite。
    ``fts_rowid`` 记录该切片在 FTS5 表中的 rowid，用于精确删除与对账。
    """

    __tablename__ = "document_chunks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    doc_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    locator: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    citation: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    parser_version: Mapped[str] = mapped_column(String(32), nullable=False)
    chunker_version: Mapped[str] = mapped_column(String(32), nullable=False)
    chunker_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    # FTS5 行号；与 FTS 记录一一对应，使删除与重建可以精确定位
    # （唯一性由 init_database 的部分唯一索引保证，便于对既有数据库做增量迁移）
    fts_rowid: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)

    document: Mapped[Document] = relationship(back_populates="chunks")

    __table_args__ = (
        UniqueConstraint("doc_id", "chunk_index", name="uq_document_chunks_doc_index"),
        Index("ix_document_chunks_doc_id", "doc_id"),
    )


class DocumentPipelineState(Base):
    """跨任务保存的检查点；分阶段 fingerprint 分开存储。"""

    __tablename__ = "document_pipeline_state"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    doc_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    source_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_key: Mapped[str] = mapped_column(String(640), nullable=False)
    file_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    last_completed_stage: Mapped[str] = mapped_column(
        String(32), nullable=False, default=constants.STAGE_NONE
    )
    parser_fingerprint: Mapped[str | None] = mapped_column(String(64))
    normalization_fingerprint: Mapped[str | None] = mapped_column(String(64))
    chunker_fingerprint: Mapped[str | None] = mapped_column(String(64))
    embedding_fingerprint: Mapped[str | None] = mapped_column(String(64))
    vector_schema_fingerprint: Mapped[str | None] = mapped_column(String(64))
    fts_schema_fingerprint: Mapped[str | None] = mapped_column(String(64))
    expected_chunk_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    vector_record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    fts_record_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_stage: Mapped[str | None] = mapped_column(String(32))
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    document: Mapped[Document] = relationship(back_populates="pipeline_state")


class DemoSeedJob(Base):
    """演示导入任务。

    ``active_marker`` 与部分唯一索引共同保证全局最多一个 queued/running 任务：
    活动任务写入常量 1，终态写回 NULL。
    """

    __tablename__ = "demo_seed_jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    dataset_version: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    pipeline_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    target_stage: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=constants.JOB_QUEUED)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    imported_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    resumed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    current_stage: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    active_marker: Mapped[int | None] = mapped_column(Integer)

    documents: Mapped[list["DemoSeedJobDocument"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", passive_deletes=True
    )

    __table_args__ = (Index("ix_demo_seed_jobs_status", "status"),)


class DemoSeedJobDocument(Base):
    """每个 manifest 文件一条；只记录本次执行结果并引用检查点。"""

    __tablename__ = "demo_seed_job_documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("demo_seed_jobs.id", ondelete="CASCADE"), nullable=False
    )
    manifest_path: Mapped[str] = mapped_column(String(512), nullable=False)
    expected_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    doc_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("documents.id", ondelete="SET NULL")
    )
    pipeline_state_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("document_pipeline_state.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=constants.JOB_DOC_PENDING)
    result: Mapped[str | None] = mapped_column(String(16))
    last_completed_stage: Mapped[str] = mapped_column(
        String(32), nullable=False, default=constants.STAGE_NONE
    )
    failed_stage: Mapped[str | None] = mapped_column(String(32))
    retryable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow, onupdate=utcnow)

    job: Mapped[DemoSeedJob] = relationship(back_populates="documents")

    __table_args__ = (
        UniqueConstraint("job_id", "manifest_path", name="uq_demo_job_documents_path"),
    )


class DemoActiveDataset(Base):
    """active demo dataset 指针。

    **全局最多一行**：``active_marker`` 与部分唯一索引共同保证任何时刻只有一个
    active 数据集；切换在单个 SQLite 事务中完成（写入新指针并退役旧版本）。
    只有当前 manifest 的全部文件达到最终 ``completed`` 并通过三方对账后才允许切换。
    """

    __tablename__ = "demo_active_dataset"

    dataset_version: Mapped[str] = mapped_column(String(64), primary_key=True)
    manifest_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    pipeline_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    activated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=utcnow)
    # 活动行写常量 1；部分唯一索引只在 active_marker IS NOT NULL 时生效
    active_marker: Mapped[int | None] = mapped_column(Integer, default=1)
