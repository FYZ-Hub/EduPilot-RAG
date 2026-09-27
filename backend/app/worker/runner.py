"""应用内单 worker：SQLite 持久队列、租约、检查点恢复、向量索引与逐文档重试。

日志只记录 job id、doc id、阶段与错误码，不记录正文、隐私信息、密钥或宿主机路径。
同一进程内绝不嵌套写事务：每个阶段使用独立的短事务。

当前文档路径：
``parsed → chunked → embedding（非独立检查点） → Chroma upsert → Chroma 对账 → vector_indexed
→ FTS 写入与对账 → keyword_indexed → 三方对账 → completed``
SQLite 与 Chroma 无法组成事务，因此用「确定性 chunk_id + upsert + 指纹 + 集合对账」
保证至少一次执行产生恰好一次的数据效果。
"""

from __future__ import annotations

import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app import constants
from app.config import Settings
from app.core.errors import (
    DEMO_ACTIVATION_FAILED,
    DEMO_DATASET_CHANGED,
    DEMO_PIPELINE_CHANGED,
    DOCUMENT_CORRUPT,
    DOCUMENT_INDEX_FAILED,
    DOCUMENT_KEYWORD_INDEX_FAILED,
    DOCUMENT_PARSE_FAILED,
    ApiError,
)
from app.db import session_scope
from app.demo.activation import activate_dataset
from app.demo.manifest import (
    DEMO_FILE_CHECKSUM_MISMATCH,
    DemoManifest,
    ManifestDocument,
    load_manifest,
    sha256_file,
)
from app.demo.service import job_documents, mark_job_failed
from app.academic.service import reconcile_academic_projection
from app.documents.chunking import ChunkSourceBlock, chunk_blocks
from app.documents.fingerprint import (
    CHUNKER_VERSION,
    PARSER_VERSION,
    pipeline_fingerprint,
    stage_fingerprints,
)
from app.documents.parsing import parse_document
from app.documents.service import (
    build_chunk_records,
    chunk_vector_metadata,
    ensure_pipeline_state,
    next_status_for_stage,
)
from app.embedding.base import descriptor_for
from app.embedding.factory import build_embedding_provider
from app.models import (
    DemoSeedJob,
    DemoSeedJobDocument,
    Document,
    DocumentBlock,
    DocumentChunk,
    DocumentPipelineState,
    utcnow,
)
from app.search import fts as fts_index
from app.runtime.coordinator import LocalModelCoordinator, embedding_lease
from app.vector.store import ChromaVectorStore, VectorRecord
from app.worker.lock import acquire_process_lock, default_lock_path

logger = logging.getLogger("app.worker")


@dataclass
class DocumentOutcome:
    """单文档处理结果；``result`` 取值来自 DEMO_DATA_SPEC 7.3。"""

    result: str
    status: str
    last_completed_stage: str
    doc_id: str | None
    pipeline_state_id: str | None
    failed_stage: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    retryable: bool = False
    attempts: int = 0


def _stage_rank(stage: str | None) -> int:
    try:
        return constants.STAGES.index(stage or constants.STAGE_NONE)
    except ValueError:  # pragma: no cover - 非法检查点按 none 处理
        return 0


def _min_stage(left: str, right: str) -> str:
    """返回两阶段中更早的一个（用于指纹失效时回退检查点）。"""
    return left if _stage_rank(left) <= _stage_rank(right) else right


def _batched(items: list, size: int):
    step = max(1, int(size))
    for start in range(0, len(items), step):
        yield items[start : start + step]


def requeue_interrupted_uploads(session: Session) -> int:
    """把中断的上传文档重新排队，避免停留在中间阶段。"""
    interrupted = [status for status in constants.ACTIVE_PUBLIC_STATUSES if status != constants.STATUS_QUEUED]
    return session.execute(
        update(Document)
        .where(
            Document.source_type == constants.SOURCE_UPLOAD,
            Document.deleted_at.is_(None),
            Document.status.in_(interrupted),
        )
        .values(
            status=constants.STATUS_QUEUED,
            current_stage=None,
            lease_owner=None,
            lease_expires_at=None,
            updated_at=utcnow(),
        )
    ).rowcount


def requeue_expired_jobs(session_factory: sessionmaker[Session]) -> int:
    """把 lease 已过期的 running 任务恢复为 queued。"""
    with session_scope(session_factory) as session:
        return session.execute(
            update(DemoSeedJob)
            .where(
                DemoSeedJob.status == constants.JOB_RUNNING,
                DemoSeedJob.lease_expires_at.is_not(None),
                DemoSeedJob.lease_expires_at < utcnow(),
            )
            .values(
                status=constants.JOB_QUEUED,
                lease_owner=None,
                lease_expires_at=None,
                current_stage=None,
            )
        ).rowcount


class Worker:
    """FastAPI lifespan 启停的应用内 worker。"""

    def __init__(
        self,
        settings: Settings,
        session_factory: sessionmaker[Session],
        worker_id: str | None = None,
        coordinator: LocalModelCoordinator | None = None,
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
        self.descriptor = descriptor_for(settings)
        # 本地 Embedding 与 Local Reranker 共用同一进程级协调器：
        # worker Embedding 与查询侧 Embedding / Reranker 不会同时驻留。
        self.coordinator = coordinator or LocalModelCoordinator()
        self.embeddings = build_embedding_provider(settings, self.coordinator)
        self.vectors = ChromaVectorStore(settings, self.descriptor)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="document-parse")
        self._lock = None
        self.has_process_lock = False

    # -- 生命周期 ---------------------------------------------------------

    def prepare(self) -> bool:
        """获取独占锁并执行启动恢复，但不启动后台线程（供测试确定性驱动）。"""
        lock = acquire_process_lock(default_lock_path(self.settings))
        if lock is None:
            # 另一个进程持有写入锁：本进程不得进行任何外部写入
            logger.warning("worker_lock_unavailable worker_id=%s", self.worker_id)
            return False
        self._lock = lock
        self.has_process_lock = True
        self.recover()
        return True

    def start(self) -> None:
        if not self.prepare():
            return
        self._thread = threading.Thread(target=self._loop, name="campus-rag-worker", daemon=False)
        self._thread.start()
        logger.info("worker_started worker_id=%s", self.worker_id)

    def stop(self, timeout: float = 15.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None
        self._executor.shutdown(wait=False)
        try:
            self.embeddings.close()
            self.vectors.close()
        except Exception:  # noqa: BLE001 - 关闭阶段不得抛出
            logger.warning("worker_cleanup_failed worker_id=%s", self.worker_id)
        if self._lock is not None:
            self._lock.release()
            self._lock = None
            self.has_process_lock = False
        logger.info("worker_stopped worker_id=%s", self.worker_id)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                worked = self.run_once()
            except Exception:  # noqa: BLE001 - worker 循环必须存活
                logger.exception("worker_iteration_failed worker_id=%s", self.worker_id)
                worked = False
            if not worked:
                self._stop.wait(self.settings.worker_poll_seconds)

    def run_once(self) -> bool:
        """处理一个演示任务或一个待处理上传文档；无工作时返回 False。"""
        if not self.has_process_lock:
            return False
        requeue_expired_jobs(self.session_factory)
        if self._run_demo_job():
            return True
        return self._run_upload_document()

    def recover(self) -> None:
        """启动恢复：本进程持有写入锁，因此遗留 running 任务可安全回到 queued。"""
        with session_scope(self.session_factory) as session:
            reset_jobs = session.execute(
                update(DemoSeedJob)
                .where(DemoSeedJob.status == constants.JOB_RUNNING)
                .values(
                    status=constants.JOB_QUEUED,
                    lease_owner=None,
                    lease_expires_at=None,
                    current_stage=None,
                )
            ).rowcount
            reset_documents = requeue_interrupted_uploads(session)
        if reset_jobs or reset_documents:
            logger.info(
                "worker_recovered worker_id=%s jobs=%s documents=%s",
                self.worker_id,
                reset_jobs,
                reset_documents,
            )

    # -- 演示任务 ---------------------------------------------------------

    def _run_demo_job(self) -> bool:
        job_id = self._claim_job()
        if job_id is None:
            return False
        self._process_job(job_id)
        return True

    def _claim_job(self) -> str | None:
        with session_scope(self.session_factory) as session:
            job = session.scalar(
                select(DemoSeedJob)
                .where(DemoSeedJob.status == constants.JOB_QUEUED)
                .order_by(DemoSeedJob.created_at)
                .limit(1)
            )
            if job is None:
                return None
            now = utcnow()
            claimed = session.execute(
                update(DemoSeedJob)
                .where(DemoSeedJob.id == job.id, DemoSeedJob.status == constants.JOB_QUEUED)
                .values(
                    status=constants.JOB_RUNNING,
                    lease_owner=self.worker_id,
                    lease_generation=DemoSeedJob.lease_generation + 1,
                    lease_expires_at=now + timedelta(seconds=self.settings.demo_job_lease_seconds),
                    started_at=func.coalesce(DemoSeedJob.started_at, now),
                    current_stage=constants.JOB_STAGE_VALIDATING,
                )
            ).rowcount
            if claimed != 1:
                return None
            return job.id

    def _process_job(self, job_id: str) -> None:
        with session_scope(self.session_factory) as session:
            job = session.get(DemoSeedJob, job_id)
            if job is None:  # pragma: no cover - 任务已被删除
                return
            try:
                manifest = load_manifest(
                    self.settings.demo_dataset_path, self.settings.demo_dataset_version
                )
            except ApiError as error:
                mark_job_failed(session, job, error.code, error.message)
                logger.warning("job_failed job_id=%s code=%s", job_id, error.code)
                return

            if manifest.manifest_sha256 != job.manifest_sha256:
                mark_job_failed(session, job, DEMO_DATASET_CHANGED, "manifest 在任务期间发生变化")
                logger.warning("job_failed job_id=%s code=%s", job_id, DEMO_DATASET_CHANGED)
                return
            if pipeline_fingerprint(self.settings) != job.pipeline_fingerprint:
                mark_job_failed(session, job, DEMO_PIPELINE_CHANGED, "流水线指纹在任务期间发生变化")
                logger.warning("job_failed job_id=%s code=%s", job_id, DEMO_PIPELINE_CHANGED)
                return

            documents_by_path = {document.path: document for document in manifest.documents}
            document_refs = [(row.id, row.manifest_path) for row in job_documents(session, job.id)]

        interrupted = False
        for job_document_id, manifest_path in document_refs:
            if self._stop.is_set():
                interrupted = True
                break
            self._renew_job_lease(job_id)
            manifest_document = documents_by_path.get(manifest_path)
            if manifest_document is None:
                self._save_job_document(
                    job_document_id,
                    DocumentOutcome(
                        result=constants.RESULT_FAILED,
                        status=constants.JOB_DOC_FAILED,
                        last_completed_stage=constants.STAGE_NONE,
                        doc_id=None,
                        pipeline_state_id=None,
                        error_code=DEMO_DATASET_CHANGED,
                        error_message="文件已不在当前 manifest 中",
                        retryable=False,
                    ),
                )
                continue
            outcome = self._process_job_document(job_document_id, manifest_document, job_id)
            self._save_job_document(job_document_id, outcome)
            logger.info(
                "job_document_finished job_id=%s doc_id=%s result=%s stage=%s code=%s",
                job_id,
                outcome.doc_id,
                outcome.result,
                outcome.last_completed_stage,
                outcome.error_code or "-",
            )

        if interrupted:
            # 关闭中：保留 running 状态与租约，由下次启动或租约过期恢复
            logger.info("job_interrupted job_id=%s", job_id)
            return
        self._finalize_job(job_id, manifest)

    def _finalize_job(self, job_id: str, manifest: DemoManifest) -> None:
        with session_scope(self.session_factory) as session:
            job = session.get(DemoSeedJob, job_id)
            if job is None:  # pragma: no cover
                return
            counts = dict(
                session.execute(
                    select(DemoSeedJobDocument.result, func.count(DemoSeedJobDocument.id))
                    .where(DemoSeedJobDocument.job_id == job_id)
                    .group_by(DemoSeedJobDocument.result)
                ).all()
            )
            imported = int(counts.get(constants.RESULT_IMPORTED, 0))
            resumed = int(counts.get(constants.RESULT_RESUMED, 0))
            skipped = int(counts.get(constants.RESULT_SKIPPED, 0))
            failed = int(counts.get(constants.RESULT_FAILED, 0))
            outstanding = (
                session.scalar(
                    select(func.count(DemoSeedJobDocument.id)).where(
                        DemoSeedJobDocument.job_id == job_id,
                        DemoSeedJobDocument.result.is_(None),
                    )
                )
                or 0
            )

            if outstanding:
                status = constants.JOB_FAILED
            elif failed:
                status = constants.JOB_COMPLETED_WITH_ERRORS
            else:
                status = constants.JOB_COMPLETED

        # 全部文档成功时才尝试原子激活；失败/未完成一律不动旧 active 指针
        activation_error: str | None = None
        if status == constants.JOB_COMPLETED:
            with session_scope(self.session_factory) as session:
                outcome = activate_dataset(session, self.settings, manifest, self.vectors)
            if outcome.activated:
                logger.info(
                    "dataset_activated job_id=%s dataset_version=%s retired=%s",
                    job_id,
                    outcome.dataset_version,
                    outcome.retired_documents,
                )
            else:
                activation_error = DEMO_ACTIVATION_FAILED
                logger.warning(
                    "dataset_activation_skipped job_id=%s reason=%s",
                    job_id,
                    outcome.reason or "-",
                )

        # 阶段 7B-1：demo 任务完成后做一次幂等学业投影对账。
        # 即使本次 seed 全部 skipped（例如升级前数据集已 active），也要补建缺失投影。
        if status == constants.JOB_COMPLETED and activation_error is None:
            self._reconcile_academic_projection(job_id)

        with session_scope(self.session_factory) as session:
            job = session.get(DemoSeedJob, job_id)
            if job is None:  # pragma: no cover
                return
            job.imported_count = imported
            job.resumed_count = resumed
            job.skipped_count = skipped
            job.failed_count = failed
            job.current_stage = None
            job.lease_owner = None
            job.lease_expires_at = None
            job.finished_at = utcnow()
            job.active_marker = None
            if activation_error is not None:
                job.status = constants.JOB_COMPLETED_WITH_ERRORS
                job.error_code = activation_error
                job.error_message = "演示数据集激活前置条件未满足"
            else:
                job.status = status
            logger.info(
                "job_finished job_id=%s status=%s imported=%s resumed=%s skipped=%s failed=%s",
                job_id,
                job.status,
                imported,
                resumed,
                skipped,
                failed,
            )

    def _reconcile_academic_projection(self, job_id: str) -> None:
        """幂等投影对账；失败只记录日志，绝不谎报成功，也不影响 RAG 检索结果。"""
        try:
            with session_scope(self.session_factory) as session:
                report = reconcile_academic_projection(session, self.settings)
        except Exception:  # noqa: BLE001 - 学业投影失败不得中断 demo 任务
            logger.exception("academic_projection_failed job_id=%s", job_id)
            return
        logger.info(
            "academic_projection_done job_id=%s dataset_version=%s record_sets=%s "
            "rule_sets=%s skipped=%s",
            job_id,
            report.dataset_version,
            report.record_sets,
            report.rule_sets,
            report.skipped,
        )

    def _renew_job_lease(self, job_id: str) -> None:
        """续租：只有 owner 匹配时才更新。"""
        with session_scope(self.session_factory) as session:
            session.execute(
                update(DemoSeedJob)
                .where(
                    DemoSeedJob.id == job_id,
                    DemoSeedJob.lease_owner == self.worker_id,
                    DemoSeedJob.status == constants.JOB_RUNNING,
                )
                .values(
                    lease_expires_at=utcnow() + timedelta(seconds=self.settings.demo_job_lease_seconds)
                )
            )

    # -- 单文档流水线 -----------------------------------------------------

    def _process_job_document(
        self, job_document_id: int, manifest_document: ManifestDocument, job_id: str
    ) -> DocumentOutcome:
        with session_scope(self.session_factory) as session:
            document = self._ensure_demo_document(session, manifest_document)
            state = ensure_pipeline_state(session, document, self.settings)
            previous_stage = state.last_completed_stage
            effective_stage = self._effective_stage(state, previous_stage)
            state_id = state.id
            document.lease_owner = self.worker_id
            document.lease_generation = int(document.lease_generation or 0) + 1
            document.lease_expires_at = utcnow() + timedelta(
                seconds=self.settings.demo_job_lease_seconds
            )
            document_id = document.id
            owner = document.lease_owner
            generation = document.lease_generation

        effective_stage = self._verified_stage(
            effective_stage, document_id, manifest_document.sha256
        )
        if _stage_rank(effective_stage) >= _stage_rank(constants.TARGET_STAGE):
            return DocumentOutcome(
                result=constants.RESULT_SKIPPED,
                status=constants.JOB_DOC_SKIPPED,
                last_completed_stage=constants.TARGET_STAGE,
                doc_id=document_id,
                pipeline_state_id=state_id,
            )

        return self._execute_with_retries(
            document_id=document_id,
            state_id=state_id,
            expected_sha256=manifest_document.sha256,
            file_path=str(Path(self.settings.demo_dataset_path) / manifest_document.path),
            file_type=manifest_document.file_type,
            owner=owner,
            generation=generation,
            checksum_error_code=DEMO_FILE_CHECKSUM_MISMATCH,
            previous_stage=previous_stage,
            start_stage=effective_stage,
            title=manifest_document.title,
            log_id=job_id,
        )

    def _effective_stage(self, state: DocumentPipelineState, stored_stage: str) -> str:
        """按分阶段指纹决定最早需要重新执行的检查点。

        - 解析器 / 规范化变化：``parsed`` 阶段本身失效 → 回退到 ``stored``（重新解析）
        - 切片配置或 chunker 版本变化：``chunked`` 失效 → 回退到 ``parsed``（保留块，重新切片）
        - Embedding 身份或向量 schema 变化：保留有效 chunk → 回退到 ``chunked``（只重建向量）
        """
        current = stage_fingerprints(self.settings)
        stage = stored_stage or constants.STAGE_NONE
        if (
            state.parser_fingerprint != current["parser_fingerprint"]
            or state.normalization_fingerprint != current["normalization_fingerprint"]
        ):
            return _min_stage(stage, constants.STAGE_STORED)
        if state.chunker_fingerprint != current["chunker_fingerprint"]:
            return _min_stage(stage, constants.STAGE_PARSED)
        if (
            state.embedding_fingerprint != current["embedding_fingerprint"]
            or state.vector_schema_fingerprint != current["vector_schema_fingerprint"]
        ):
            return _min_stage(stage, constants.STAGE_CHUNKED)
        if state.fts_schema_fingerprint != current["fts_schema_fingerprint"]:
            # FTS 结构 / tokenizer / 中文规范化变化：保留 chunk 与正确向量，只重建 FTS
            return _min_stage(stage, constants.STAGE_VECTOR_INDEXED)
        return stage

    def _verified_stage(self, stage: str, document_id: str, expected_sha256: str) -> str:
        """在分阶段指纹之上，再核对实际产物集合，得到可信的最早重建阶段。

        只有「指纹一致」并且「切片 / 向量 / FTS 三者集合与计数都对账一致」时，
        才能沿用检查点；否则把阶段下调到对应的重建起点，让本次执行真正收敛。
        **不允许仅凭计数相等判定成功，也不允许 ID 集合不一致时停留在 completed。**
        """
        with session_scope(self.session_factory) as session:
            state = session.scalar(
                select(DocumentPipelineState).where(DocumentPipelineState.doc_id == document_id)
            )
            if state is None or state.file_sha256 != expected_sha256:
                return constants.STAGE_NONE
            expected_chunk_count = int(state.expected_chunk_count or 0)
            vector_record_count = int(state.vector_record_count or 0)
            fts_record_count = int(state.fts_record_count or 0)
            expected_ids = set(
                session.scalars(
                    select(DocumentChunk.id).where(DocumentChunk.doc_id == document_id)
                )
            )
            fts_ids = fts_index.existing_ids(session, document_id)

        if not expected_ids or len(expected_ids) != expected_chunk_count:
            return _min_stage(stage, constants.STAGE_PARSED)
        if _stage_rank(stage) < _stage_rank(constants.STAGE_CHUNKED):
            return stage

        current = stage_fingerprints(self.settings)
        existing = self.vectors.vectors_for_document(document_id)
        if set(existing) != expected_ids or vector_record_count != len(expected_ids):
            return _min_stage(stage, constants.STAGE_CHUNKED)
        for metadata in existing.values():
            if metadata.get("embedding_fingerprint") != current["embedding_fingerprint"]:
                return _min_stage(stage, constants.STAGE_CHUNKED)
            if metadata.get("vector_schema_fingerprint") != current["vector_schema_fingerprint"]:
                return _min_stage(stage, constants.STAGE_CHUNKED)
            if metadata.get("chunker_version") != CHUNKER_VERSION:
                return _min_stage(stage, constants.STAGE_CHUNKED)
        if _stage_rank(stage) < _stage_rank(constants.STAGE_VECTOR_INDEXED):
            return stage

        with session_scope(self.session_factory) as session:
            state = session.scalar(
                select(DocumentPipelineState).where(DocumentPipelineState.doc_id == document_id)
            )
            fts_fingerprint = state.fts_schema_fingerprint if state else None
        if fts_fingerprint != current["fts_schema_fingerprint"]:
            return _min_stage(stage, constants.STAGE_VECTOR_INDEXED)
        if fts_ids != expected_ids or fts_record_count != len(expected_ids):
            return _min_stage(stage, constants.STAGE_VECTOR_INDEXED)
        return stage

    def _ensure_demo_document(self, session: Session, manifest_document: ManifestDocument) -> Document:
        """按 (source_type=demo, source_key) 取得或创建文档行。"""
        source_key = f"{self.settings.demo_dataset_version}:{manifest_document.path}"
        document = session.scalar(
            select(Document)
            .where(
                Document.source_type == constants.SOURCE_DEMO,
                Document.source_key == source_key,
                Document.deleted_at.is_(None),
            )
            .order_by(Document.created_at.desc())
        )
        if document is None:
            document = Document(
                source_type=constants.SOURCE_DEMO,
                source_key=source_key,
                file_name=manifest_document.file_name,
                safe_storage_name=None,
                file_type=manifest_document.file_type,
                mime_type=constants.MIME_TYPES.get(manifest_document.file_type, "application/octet-stream"),
                size_bytes=manifest_document.size_bytes,
                sha256=manifest_document.sha256,
                storage_path=None,
                doc_category=manifest_document.doc_category,
                dataset_version=self.settings.demo_dataset_version,
                document_version=manifest_document.file_version,
                effective_from=manifest_document.effective_from,
                status=constants.STATUS_QUEUED,
                retrievable=False,
                activation_state=constants.ACTIVATION_CANDIDATE,
            )
            session.add(document)
            session.flush()
        else:
            document.size_bytes = manifest_document.size_bytes
            document.sha256 = manifest_document.sha256
            document.activation_state = constants.ACTIVATION_CANDIDATE
            document.retrievable = False
        return document

    def _execute_with_retries(
        self,
        *,
        document_id: str,
        state_id: str,
        expected_sha256: str,
        file_path: str,
        file_type: str,
        owner: str,
        generation: int,
        checksum_error_code: str,
        previous_stage: str,
        start_stage: str,
        title: str | None,
        log_id: str,
    ) -> DocumentOutcome:
        attempts = 0
        max_attempts = max(1, int(self.settings.demo_document_max_attempts))
        last_error: ApiError | None = None
        failed_stage: str | None = None

        while attempts < max_attempts:
            attempts += 1
            try:
                self._run_stages(
                    document_id=document_id,
                    expected_sha256=expected_sha256,
                    file_path=file_path,
                    file_type=file_type,
                    owner=owner,
                    generation=generation,
                    checksum_error_code=checksum_error_code,
                    start_stage=start_stage,
                    title=title,
                    log_id=log_id,
                )
            except ApiError as error:
                last_error = error
            except Exception:  # noqa: BLE001 - 非预期错误按可重试处理
                logger.exception("document_attempt_crashed log_id=%s doc_id=%s", log_id, document_id)
                last_error = ApiError(DOCUMENT_PARSE_FAILED)

            if last_error is not None:
                failed_stage = self._record_document_failure(
                    document_id, owner, generation, last_error, attempts
                )
                logger.warning(
                    "document_attempt_failed log_id=%s doc_id=%s attempt=%s code=%s",
                    log_id,
                    document_id,
                    attempts,
                    last_error.code,
                )
                if not last_error.retryable or attempts >= max_attempts:
                    break
                last_error = None
                continue

            return DocumentOutcome(
                result=(
                    constants.RESULT_RESUMED
                    if _stage_rank(previous_stage) > _stage_rank(constants.STAGE_NONE)
                    else constants.RESULT_IMPORTED
                ),
                status=constants.JOB_DOC_COMPLETED,
                last_completed_stage=constants.TARGET_STAGE,
                doc_id=document_id,
                pipeline_state_id=state_id,
                attempts=attempts,
            )

        error = last_error or ApiError(DOCUMENT_PARSE_FAILED)
        return DocumentOutcome(
            result=constants.RESULT_FAILED,
            status=constants.JOB_DOC_FAILED,
            last_completed_stage=self._last_completed_stage(document_id),
            doc_id=document_id,
            pipeline_state_id=state_id,
            failed_stage=failed_stage,
            error_code=error.code,
            error_message=error.message,
            retryable=bool(error.retryable),
            attempts=attempts,
        )

    def _run_stages(
        self,
        *,
        document_id: str,
        expected_sha256: str,
        file_path: str,
        file_type: str,
        owner: str,
        generation: int,
        checksum_error_code: str,
        start_stage: str,
        title: str | None,
        log_id: str,
    ) -> None:
        path = Path(file_path)
        rank = _stage_rank(start_stage)

        self._set_document_status(
            document_id, owner, generation, constants.STATUS_VALIDATING, constants.JOB_STAGE_VALIDATING
        )
        if not path.is_file():
            raise ApiError(DOCUMENT_CORRUPT, retryable=True)
        if sha256_file(path) != expected_sha256:
            raise ApiError(checksum_error_code, retryable=False)

        if rank < _stage_rank(constants.STAGE_PARSED):
            # 需要（重新）解析：完整走 validated → stored → parsed，绝不提前下调检查点
            self._checkpoint(
                document_id,
                owner,
                generation,
                constants.STAGE_VALIDATED,
                next_status=constants.STATUS_STORING,
                next_current=constants.JOB_STAGE_STORING,
            )
            self._set_document_status(
                document_id, owner, generation, constants.STATUS_STORING, constants.JOB_STAGE_STORING
            )
            self._guarded_document_update(document_id, owner, generation, {"storage_path": str(path)})
            self._checkpoint(
                document_id,
                owner,
                generation,
                constants.STAGE_STORED,
                next_status=constants.STATUS_PARSING,
                next_current=constants.JOB_STAGE_PARSING,
            )
            self._set_document_status(
                document_id, owner, generation, constants.STATUS_PARSING, constants.JOB_STAGE_PARSING
            )
            blocks = self._executor.submit(parse_document, path, file_type).result()
            self._persist_blocks(document_id, owner, generation, blocks)
        else:
            # 复用既有块：只确保 storage_path 指向当前文件，不回调检查点
            self._guarded_document_update(document_id, owner, generation, {"storage_path": str(path)})

        if rank < _stage_rank(constants.STAGE_CHUNKED):
            self._set_document_status(
                document_id, owner, generation, constants.STATUS_CHUNKING, constants.JOB_STAGE_CHUNKING
            )
            self._persist_chunks(document_id, owner, generation, title)

        if rank < _stage_rank(constants.STAGE_VECTOR_INDEXED):
            self._set_document_status(
                document_id, owner, generation, constants.STATUS_EMBEDDING, constants.JOB_STAGE_EMBEDDING
            )
            self._index_vectors(document_id, owner, generation, log_id)

        if rank < _stage_rank(constants.STAGE_KEYWORD_INDEXED):
            self._set_document_status(
                document_id,
                owner,
                generation,
                constants.STATUS_KEYWORD_INDEXING,
                constants.JOB_STAGE_KEYWORD_INDEXING,
            )
            self._index_keyword(document_id, owner, generation, log_id)

        if rank < _stage_rank(constants.STAGE_COMPLETED):
            self._finalize_document(document_id, owner, generation, log_id)

    def _set_document_status(
        self, document_id: str, owner: str, generation: int, status: str, current_stage: str
    ) -> None:
        self._guarded_document_update(
            document_id,
            owner,
            generation,
            {
                "status": status,
                "current_stage": current_stage,
                "error_code": None,
                "error_message": None,
                "error_retryable": None,
            },
        )

    def _checkpoint(
        self,
        document_id: str,
        owner: str,
        generation: int,
        stage: str,
        *,
        next_status: str,
        next_current: str | None,
    ) -> None:
        with session_scope(self.session_factory) as session:
            session.execute(
                update(DocumentPipelineState)
                .where(DocumentPipelineState.doc_id == document_id)
                .values(
                    last_completed_stage=stage,
                    failed_stage=None,
                    error_code=None,
                    error_message=None,
                    updated_at=utcnow(),
                )
            )
        self._guarded_document_update(
            document_id, owner, generation, {"status": next_status, "current_stage": next_current}
        )

    def _persist_blocks(self, document_id: str, owner: str, generation: int, blocks) -> None:
        """单个事务内先持久化块，再提交 parsed 检查点；重试不产生重复块。

        块变化意味着切片、向量与 FTS 都失效，因此同一事务内一并精确清理。
        """
        current = stage_fingerprints(self.settings)
        with session_scope(self.session_factory) as session:
            fts_index.delete_document_rows(session, document_id)
            session.execute(delete(DocumentChunk).where(DocumentChunk.doc_id == document_id))
            session.execute(delete(DocumentBlock).where(DocumentBlock.doc_id == document_id))
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
            session.execute(
                update(DocumentPipelineState)
                .where(DocumentPipelineState.doc_id == document_id)
                .values(
                    last_completed_stage=constants.STAGE_PARSED,
                    # 指纹只描述“本次实际产出该产物所用的组件版本”
                    parser_fingerprint=current["parser_fingerprint"],
                    normalization_fingerprint=current["normalization_fingerprint"],
                    expected_chunk_count=0,
                    vector_record_count=0,
                    failed_stage=None,
                    error_code=None,
                    error_message=None,
                    updated_at=utcnow(),
                )
            )
        self._guarded_document_update(
            document_id, owner, generation, {"status": constants.STATUS_CHUNKING, "current_stage": constants.JOB_STAGE_CHUNKING}
        )

    def _persist_chunks(
        self, document_id: str, owner: str, generation: int, title: str | None
    ) -> None:
        """单个事务内先持久化完整 chunk 集合与预期数量，再提交 chunked 检查点。"""
        chunker_fingerprint = stage_fingerprints(self.settings)["chunker_fingerprint"]

        with session_scope(self.session_factory) as session:
            document = session.get(Document, document_id)
            if document is None:  # pragma: no cover - 文档已被删除
                raise ApiError(DOCUMENT_PARSE_FAILED, retryable=False)
            blocks = list(
                session.scalars(
                    select(DocumentBlock)
                    .where(DocumentBlock.doc_id == document_id)
                    .order_by(DocumentBlock.block_index)
                )
            )
            source = [
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
            ]
            drafts = self._executor.submit(
                chunk_blocks,
                source,
                target_chars=self.settings.chunk_target_chars,
                overlap_chars=self.settings.chunk_overlap_chars,
            ).result()
            records = build_chunk_records(
                document,
                drafts,
                parser_version=PARSER_VERSION,
                chunker_version=CHUNKER_VERSION,
                chunker_fingerprint=chunker_fingerprint,
                title=title,
            )

            # 切片集合被替换：FTS 行号记录在旧 chunk 行上，必须先精确删除旧 FTS 记录
            fts_index.delete_document_rows(session, document_id)
            session.execute(delete(DocumentChunk).where(DocumentChunk.doc_id == document_id))
            session.add_all([DocumentChunk(**record) for record in records])
            session.execute(
                update(DocumentPipelineState)
                .where(DocumentPipelineState.doc_id == document_id)
                .values(
                    last_completed_stage=constants.STAGE_CHUNKED,
                    chunker_fingerprint=chunker_fingerprint,
                    expected_chunk_count=len(records),
                    vector_record_count=0,
                    fts_record_count=0,
                    failed_stage=None,
                    error_code=None,
                    error_message=None,
                    updated_at=utcnow(),
                )
            )
        self._guarded_document_update(
            document_id,
            owner,
            generation,
            {"status": constants.STATUS_EMBEDDING, "current_stage": constants.JOB_STAGE_EMBEDDING},
        )

    def _index_vectors(self, document_id: str, owner: str, generation: int, log_id: str) -> None:
        """生成缺失向量、清理多余向量并对账，然后提交 vector_indexed 检查点。

        若上次在「Chroma upsert 之后、SQLite 检查点之前」崩溃，
        本方法会复用 ``embedding_fingerprint`` 正确的既有向量，不再调用 Embedding。
        """
        embedding_descriptor = self.descriptor
        pipeline_fp = pipeline_fingerprint(self.settings)
        current = stage_fingerprints(self.settings)
        current_embedding = current["embedding_fingerprint"]

        with session_scope(self.session_factory) as session:
            document = session.get(Document, document_id)
            if document is None:  # pragma: no cover
                raise ApiError(DOCUMENT_PARSE_FAILED, retryable=False)
            chunks = list(
                session.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.doc_id == document_id)
                    .order_by(DocumentChunk.chunk_index)
                )
            )
            if not chunks:
                raise ApiError(DOCUMENT_INDEX_FAILED, retryable=False)
            metadata_by_id = {
                chunk.id: chunk_vector_metadata(
                    document,
                    chunk,
                    embedding_descriptor,
                    pipeline_fingerprint_value=pipeline_fp,
                    vector_schema_fingerprint=current["vector_schema_fingerprint"],
                )
                for chunk in chunks
            }
            text_by_id = {chunk.id: chunk.text for chunk in chunks}
            expected_ids = [chunk.id for chunk in chunks]

        expected = set(expected_ids)
        existing = self.vectors.vectors_for_document(document_id)

        stale = [chunk_id for chunk_id in existing if chunk_id not in expected]
        if stale:
            self.vectors.delete_ids(stale)

        reusable = {
            chunk_id
            for chunk_id, metadata in existing.items()
            if chunk_id in expected
            and metadata.get("embedding_fingerprint") == current_embedding
            and metadata.get("vector_schema_fingerprint") == current["vector_schema_fingerprint"]
            and metadata.get("chunker_version") == CHUNKER_VERSION
        }
        pending = [chunk_id for chunk_id in expected_ids if chunk_id not in reusable]

        reused = len(expected_ids) - len(pending)
        if reused:
            logger.info(
                "vector_reused log_id=%s doc_id=%s reused=%s",
                log_id,
                document_id,
                reused,
            )

        # 整个文档的批量 Embedding 共用一个有界窗口：窗口内模型只加载一次，
        # 窗口结束（含异常）即释放，保证与 Local Reranker 不会同时驻留。
        with embedding_lease(self.coordinator):
            for batch in _batched(pending, self.settings.embedding_batch_size):
                texts = [text_by_id[chunk_id] for chunk_id in batch]
                vectors = self._executor.submit(self.embeddings.embed_documents, texts).result()
                records = [
                    VectorRecord(
                        chunk_id=chunk_id,
                        text=text_by_id[chunk_id],
                        metadata=metadata_by_id[chunk_id],
                    )
                    for chunk_id in batch
                ]
                self.vectors.upsert(records, vectors)

        reconciled = self.vectors.vectors_for_document(document_id)
        if set(reconciled) != expected:
            raise ApiError(DOCUMENT_INDEX_FAILED, retryable=True)
        for metadata in reconciled.values():
            if metadata.get("embedding_fingerprint") != current_embedding:
                raise ApiError(DOCUMENT_INDEX_FAILED, retryable=True)
            if metadata.get("vector_schema_fingerprint") != current["vector_schema_fingerprint"]:
                raise ApiError(DOCUMENT_INDEX_FAILED, retryable=True)

        with session_scope(self.session_factory) as session:
            session.execute(
                update(DocumentPipelineState)
                .where(DocumentPipelineState.doc_id == document_id)
                .values(
                    last_completed_stage=constants.STAGE_VECTOR_INDEXED,
                    embedding_fingerprint=current_embedding,
                    vector_schema_fingerprint=current["vector_schema_fingerprint"],
                    vector_record_count=len(reconciled),
                    failed_stage=None,
                    error_code=None,
                    error_message=None,
                    updated_at=utcnow(),
                )
            )
        self._guarded_document_update(
            document_id,
            owner,
            generation,
            {
                "status": next_status_for_stage(constants.STAGE_VECTOR_INDEXED),
                "current_stage": None,
                "retrievable": False,
                "attempt_count": 0,
                "error_code": None,
                "error_message": None,
                "error_retryable": None,
            },
        )
        logger.info(
            "vector_indexed log_id=%s doc_id=%s chunks=%s reused=%s embedded=%s",
            log_id,
            document_id,
            len(reconciled),
            reused,
            len(pending),
        )

    def _index_keyword(self, document_id: str, owner: str, generation: int, log_id: str) -> None:
        """单个事务内完成 FTS 写入、精确增删、ID 对账与 keyword_indexed 检查点。

        FTS5 与业务表同库，因此「索引记录 + 计数 + 检查点」要么一起提交、
        要么一起回滚，不可能出现「检查点成功但 FTS 记录不完整」。
        """
        fingerprint = stage_fingerprints(self.settings)["fts_schema_fingerprint"]

        with session_scope(self.session_factory) as session:
            document = session.get(Document, document_id)
            if document is None:  # pragma: no cover - 文档已被删除
                raise ApiError(DOCUMENT_KEYWORD_INDEX_FAILED, retryable=False)
            chunks = list(
                session.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.doc_id == document_id)
                    .order_by(DocumentChunk.chunk_index)
                )
            )
            if not chunks:
                raise ApiError(DOCUMENT_KEYWORD_INDEX_FAILED, retryable=False)

            values_by_chunk = {
                chunk.id: fts_index.build_row_values(document, chunk, fingerprint)
                for chunk in chunks
            }
            result = fts_index.reconcile_document(
                session, document_id, values_by_chunk, fingerprint
            )
            expected_ids = {chunk.id for chunk in chunks}
            actual_ids = fts_index.existing_ids(session, document_id)
            if result.total != len(expected_ids) or actual_ids != expected_ids:
                raise ApiError(
                    DOCUMENT_KEYWORD_INDEX_FAILED,
                    retryable=True,
                    details={"reason": "fts_reconcile_mismatch"},
                )

            session.execute(
                update(DocumentPipelineState)
                .where(DocumentPipelineState.doc_id == document_id)
                .values(
                    last_completed_stage=constants.STAGE_KEYWORD_INDEXED,
                    fts_schema_fingerprint=fingerprint,
                    fts_record_count=len(actual_ids),
                    failed_stage=None,
                    error_code=None,
                    error_message=None,
                    updated_at=utcnow(),
                )
            )

        self._guarded_document_update(
            document_id,
            owner,
            generation,
            {
                "status": next_status_for_stage(constants.STAGE_KEYWORD_INDEXED),
                "current_stage": None,
            },
        )
        logger.info(
            "keyword_indexed log_id=%s doc_id=%s chunks=%s inserted=%s deleted=%s",
            log_id,
            document_id,
            len(expected_ids),
            result.inserted,
            result.deleted,
        )

    def _finalize_document(self, document_id: str, owner: str, generation: int, log_id: str) -> None:
        """三方 ID 集合与全部计数一致后才写 completed 检查点。"""
        with session_scope(self.session_factory) as session:
            document = session.get(Document, document_id)
            state = session.scalar(
                select(DocumentPipelineState).where(DocumentPipelineState.doc_id == document_id)
            )
            if document is None or state is None:  # pragma: no cover
                raise ApiError(DOCUMENT_INDEX_FAILED, retryable=False)
            expected_ids = set(
                session.scalars(
                    select(DocumentChunk.id).where(DocumentChunk.doc_id == document_id)
                )
            )
            counts = (
                int(state.expected_chunk_count or 0),
                int(state.vector_record_count or 0),
                int(state.fts_record_count or 0),
            )
            fts_ids = fts_index.existing_ids(session, document_id)
            source_type = document.source_type

        if not expected_ids or any(count != len(expected_ids) for count in counts):
            raise ApiError(
                DOCUMENT_INDEX_FAILED, retryable=True, details={"reason": "count_mismatch"}
            )
        if fts_ids != expected_ids:
            raise ApiError(
                DOCUMENT_INDEX_FAILED, retryable=True, details={"reason": "fts_set_mismatch"}
            )
        if set(self.vectors.vectors_for_document(document_id)) != expected_ids:
            raise ApiError(
                DOCUMENT_INDEX_FAILED, retryable=True, details={"reason": "vector_set_mismatch"}
            )

        with session_scope(self.session_factory) as session:
            session.execute(
                update(DocumentPipelineState)
                .where(DocumentPipelineState.doc_id == document_id)
                .values(
                    last_completed_stage=constants.STAGE_COMPLETED,
                    failed_stage=None,
                    error_code=None,
                    error_message=None,
                    updated_at=utcnow(),
                )
            )

        values: dict[str, Any] = {
            "current_stage": None,
            "attempt_count": 0,
            "error_code": None,
            "error_message": None,
            "error_retryable": None,
        }
        if source_type == constants.SOURCE_UPLOAD:
            # 上传文档不依赖 demo 整体激活：对账通过即可检索
            values.update(
                {
                    "status": constants.STATUS_READY,
                    "retrievable": True,
                    "activation_state": None,
                }
            )
        else:
            # demo 文档在整体原子激活前保持候选态，不可检索
            values.update(
                {
                    "status": constants.STATUS_QUEUED,
                    "retrievable": False,
                    "activation_state": constants.ACTIVATION_CANDIDATE,
                }
            )
        self._guarded_document_update(document_id, owner, generation, values)
        logger.info(
            "document_completed log_id=%s doc_id=%s chunks=%s source=%s",
            log_id,
            document_id,
            len(expected_ids),
            source_type,
        )

    def _record_document_failure(
        self,
        document_id: str,
        owner: str,
        generation: int,
        error: ApiError,
        attempts: int,
    ) -> str | None:
        """在单个事务内记录失败检查点与安全错误，返回失败所在阶段。"""
        with session_scope(self.session_factory) as session:
            state = session.scalar(
                select(DocumentPipelineState).where(DocumentPipelineState.doc_id == document_id)
            )
            failed_stage = state.last_completed_stage if state else constants.STAGE_NONE
            if state is not None:
                state.failed_stage = failed_stage
                state.error_code = error.code
                state.error_message = error.message
                state.updated_at = utcnow()
            session.execute(
                update(Document)
                .where(
                    Document.id == document_id,
                    Document.lease_owner == owner,
                    Document.lease_generation == generation,
                )
                .values(
                    status=constants.STATUS_FAILED,
                    current_stage=None,
                    attempt_count=attempts,
                    error_code=error.code,
                    error_message=error.message,
                    error_retryable=bool(error.retryable),
                    retrievable=False,
                    updated_at=utcnow(),
                )
            )
        return failed_stage

    def _guarded_document_update(
        self, document_id: str, owner: str, generation: int, values: dict[str, Any]
    ) -> int:
        """文档状态更新必须匹配 lease owner + generation。"""
        with session_scope(self.session_factory) as session:
            return session.execute(
                update(Document)
                .where(
                    Document.id == document_id,
                    Document.lease_owner == owner,
                    Document.lease_generation == generation,
                )
                .values(**values)
            ).rowcount

    def _last_completed_stage(self, document_id: str) -> str:
        with session_scope(self.session_factory) as session:
            state = session.scalar(
                select(DocumentPipelineState).where(DocumentPipelineState.doc_id == document_id)
            )
            return state.last_completed_stage if state else constants.STAGE_NONE

    def _save_job_document(self, job_document_id: int, outcome: DocumentOutcome) -> None:
        with session_scope(self.session_factory) as session:
            job_document = session.get(DemoSeedJobDocument, job_document_id)
            if job_document is None:  # pragma: no cover
                return
            job_document.status = outcome.status
            job_document.result = outcome.result
            job_document.last_completed_stage = outcome.last_completed_stage
            job_document.failed_stage = outcome.failed_stage
            job_document.retryable = outcome.retryable
            job_document.attempt_count = outcome.attempts
            job_document.error_code = outcome.error_code
            job_document.error_message = outcome.error_message
            job_document.doc_id = outcome.doc_id
            job_document.pipeline_state_id = outcome.pipeline_state_id
            job_document.updated_at = utcnow()

    # -- 上传文档 ---------------------------------------------------------

    def _run_upload_document(self) -> bool:
        with session_scope(self.session_factory) as session:
            document = session.scalar(
                select(Document)
                .where(
                    Document.source_type == constants.SOURCE_UPLOAD,
                    Document.status == constants.STATUS_QUEUED,
                    Document.deleted_at.is_(None),
                )
                .order_by(Document.created_at)
                .limit(1)
            )
            if document is None:
                return False
            claimed = session.execute(
                update(Document)
                .where(Document.id == document.id, Document.status == constants.STATUS_QUEUED)
                .values(
                    status=constants.STATUS_VALIDATING,
                    current_stage=constants.JOB_STAGE_VALIDATING,
                    lease_owner=self.worker_id,
                    lease_generation=Document.lease_generation + 1,
                    lease_expires_at=utcnow() + timedelta(seconds=self.settings.demo_job_lease_seconds),
                    attempt_count=0,
                    error_code=None,
                    error_message=None,
                    error_retryable=None,
                    updated_at=utcnow(),
                )
            ).rowcount
            if claimed != 1:
                return False

            refreshed = session.get(Document, document.id)
            state = ensure_pipeline_state(session, refreshed, self.settings)
            document_id = refreshed.id
            owner = refreshed.lease_owner
            generation = refreshed.lease_generation
            expected_sha256 = refreshed.sha256
            file_path = refreshed.storage_path or ""
            file_type = refreshed.file_type
            state_id = state.id
            previous_stage = state.last_completed_stage
            effective_stage = self._effective_stage(state, previous_stage)

        effective_stage = self._verified_stage(effective_stage, document_id, expected_sha256)
        outcome = self._execute_with_retries(
            document_id=document_id,
            state_id=state_id,
            expected_sha256=expected_sha256,
            file_path=file_path,
            file_type=file_type,
            owner=owner,
            generation=generation,
            checksum_error_code=DOCUMENT_CORRUPT,
            previous_stage=previous_stage,
            start_stage=effective_stage,
            title=None,
            log_id="-",
        )
        logger.info(
            "upload_document_finished doc_id=%s result=%s stage=%s code=%s",
            document_id,
            outcome.result,
            outcome.last_completed_stage,
            outcome.error_code or "-",
        )
        return True


def _build_provider(settings: Settings):
    """延迟到运行期导入工厂，避免模块级循环依赖。"""
    from app.embedding.factory import build_embedding_provider

    return build_embedding_provider(settings)


__all__ = ["DocumentOutcome", "Worker", "requeue_expired_jobs", "requeue_interrupted_uploads"]
