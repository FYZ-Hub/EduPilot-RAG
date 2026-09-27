"""应用内单 worker：SQLite 持久队列、租约、检查点恢复与逐文档重试。

日志只记录 job id、doc id、阶段与错误码，不记录正文、隐私信息、密钥或宿主机路径。
同一进程内绝不嵌套写事务：每个阶段使用独立的短事务。
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
    DEMO_DATASET_CHANGED,
    DEMO_PIPELINE_CHANGED,
    DOCUMENT_CORRUPT,
    DOCUMENT_PARSE_FAILED,
    ApiError,
)
from app.db import session_scope
from app.demo.manifest import DEMO_FILE_CHECKSUM_MISMATCH, ManifestDocument, load_manifest, sha256_file
from app.demo.service import job_documents, mark_job_failed
from app.documents.fingerprint import pipeline_fingerprint, stage_fingerprints
from app.documents.parsing import parse_document
from app.documents.service import ensure_pipeline_state
from app.models import (
    DemoSeedJob,
    DemoSeedJobDocument,
    Document,
    DocumentBlock,
    DocumentPipelineState,
    utcnow,
)
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
    ):
        self.settings = settings
        self.session_factory = session_factory
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:12]}"
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
        self._finalize_job(job_id)

    def _finalize_job(self, job_id: str) -> None:
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

            job.imported_count = imported
            job.resumed_count = resumed
            job.skipped_count = skipped
            job.failed_count = failed
            job.current_stage = None
            job.lease_owner = None
            job.lease_expires_at = None
            job.finished_at = utcnow()
            job.active_marker = None
            if outstanding:
                job.status = constants.JOB_FAILED
                job.error_code = job.error_code or DOCUMENT_PARSE_FAILED
                job.error_message = job.error_message or "任务未完成全部文档"
            elif failed:
                job.status = constants.JOB_COMPLETED_WITH_ERRORS
            else:
                job.status = constants.JOB_COMPLETED
            logger.info(
                "job_finished job_id=%s status=%s imported=%s resumed=%s skipped=%s failed=%s",
                job_id,
                job.status,
                imported,
                resumed,
                skipped,
                failed,
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
            state_id = state.id
            document.lease_owner = self.worker_id
            document.lease_generation = int(document.lease_generation or 0) + 1
            document.lease_expires_at = utcnow() + timedelta(
                seconds=self.settings.demo_job_lease_seconds
            )
            document_id = document.id
            owner = document.lease_owner
            generation = document.lease_generation

        if self._should_skip(document_id, manifest_document, constants.TARGET_STAGE):
            return DocumentOutcome(
                result=constants.RESULT_SKIPPED,
                status=constants.JOB_DOC_SKIPPED,
                last_completed_stage=constants.STAGE_PARSED,
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
            log_id=job_id,
        )

    def _should_skip(
        self, document_id: str, manifest_document: ManifestDocument, target_stage: str
    ) -> bool:
        """相同来源键、校验值与指纹，且已达到 target_stage 时跳过。"""
        with session_scope(self.session_factory) as session:
            state = session.scalar(
                select(DocumentPipelineState).where(DocumentPipelineState.doc_id == document_id)
            )
            if state is None or state.file_sha256 != manifest_document.sha256:
                return False
            for key, value in stage_fingerprints(self.settings).items():
                if getattr(state, key) != value:
                    return False
            return _stage_rank(state.last_completed_stage) >= _stage_rank(target_stage)

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
                )
            except ApiError as error:
                last_error = error
            except Exception:  # noqa: BLE001 - 非预期错误按可重试解析失败处理
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
                last_completed_stage=constants.STAGE_PARSED,
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
    ) -> None:
        path = Path(file_path)

        self._set_document_status(
            document_id, owner, generation, constants.STATUS_VALIDATING, constants.JOB_STAGE_VALIDATING
        )
        if not path.is_file():
            raise ApiError(DOCUMENT_CORRUPT, retryable=True)
        if sha256_file(path) != expected_sha256:
            raise ApiError(checksum_error_code, retryable=False)
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
        self._guarded_document_update(
            document_id, owner, generation, {"storage_path": str(path)}
        )
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
        """单个事务内先持久化块，再提交 parsed 检查点；重试不产生重复块。"""
        with session_scope(self.session_factory) as session:
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
                    failed_stage=None,
                    error_code=None,
                    error_message=None,
                    updated_at=utcnow(),
                )
            )
            session.execute(
                update(Document)
                .where(
                    Document.id == document_id,
                    Document.lease_owner == owner,
                    Document.lease_generation == generation,
                )
                .values(
                    status=constants.STATUS_QUEUED,
                    current_stage=None,
                    retrievable=False,
                    attempt_count=0,
                    error_code=None,
                    error_message=None,
                    error_retryable=None,
                    updated_at=utcnow(),
                )
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


__all__ = ["DocumentOutcome", "Worker", "requeue_expired_jobs", "requeue_interrupted_uploads"]
