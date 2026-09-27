"""演示数据集服务：status、seed 与 job 查询。"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import constants
from app.config import Settings
from app.core.errors import DEMO_DATASET_DISABLED, DEMO_JOB_NOT_FOUND, ApiError
from app.demo.manifest import DemoManifest, load_manifest
from app.documents.fingerprint import pipeline_fingerprint
from app.documents.service import isoformat_utc
from app.models import DemoActiveDataset, DemoSeedJob, DemoSeedJobDocument, Document, utcnow


def load_demo_manifest(settings: Settings) -> DemoManifest:
    return load_manifest(settings.demo_dataset_path, settings.demo_dataset_version)


def active_job(session: Session) -> DemoSeedJob | None:
    return session.scalar(
        select(DemoSeedJob)
        .where(DemoSeedJob.active_marker.is_not(None))
        .order_by(DemoSeedJob.created_at.desc())
    )


def latest_job(session: Session) -> DemoSeedJob | None:
    return session.scalar(select(DemoSeedJob).order_by(DemoSeedJob.created_at.desc()))


def get_job(session: Session, job_id: str) -> DemoSeedJob:
    job = session.scalar(select(DemoSeedJob).where(DemoSeedJob.id == job_id))
    if job is None:
        raise ApiError(DEMO_JOB_NOT_FOUND)
    return job


def _reason(error: ApiError) -> dict:
    return {"code": error.code, "message": error.message, "retryable": bool(error.retryable)}


def _manifest_source_keys(manifest: DemoManifest) -> list[str]:
    return [manifest.source_key(document) for document in manifest.documents]


def build_status(session: Session, settings: Settings) -> dict:
    """构造 ``GET /api/demo/status`` 响应；``loaded`` 在本阶段恒为 false。"""
    payload: dict = {
        "enabled": bool(settings.demo_dataset_enabled),
        "state": constants.DEMO_STATE_EMPTY,
        "dataset_version": settings.demo_dataset_version,
        "manifest_sha256": None,
        "pipeline_fingerprint": pipeline_fingerprint(settings),
        "available_documents": 0,
        "ready_documents": 0,
        "failed_documents": 0,
        "loaded": False,
        "poll_after_seconds": settings.demo_job_poll_seconds,
        "active_dataset_version": None,
        "serving_previous_version": False,
        "active_job_id": None,
        "last_job_id": None,
        "reason": None,
    }

    running = active_job(session)
    previous = latest_job(session)
    if running is not None:
        payload["active_job_id"] = running.id
    if previous is not None:
        payload["last_job_id"] = previous.id

    active_pointer = session.scalar(
        select(DemoActiveDataset).order_by(DemoActiveDataset.activated_at.desc())
    )
    if active_pointer is not None:
        # 返回真实的活动版本，即使它属于旧版本（serving_previous_version）
        payload["active_dataset_version"] = active_pointer.dataset_version
        payload["serving_previous_version"] = (
            active_pointer.dataset_version != settings.demo_dataset_version
        )

    if not settings.demo_dataset_enabled:
        payload["state"] = constants.DEMO_STATE_DISABLED
        payload["reason"] = _reason(ApiError(DEMO_DATASET_DISABLED))
        return payload

    try:
        manifest = load_demo_manifest(settings)
    except ApiError as error:
        payload["state"] = constants.DEMO_STATE_UNAVAILABLE
        payload["reason"] = _reason(error)
        return payload

    payload["manifest_sha256"] = manifest.manifest_sha256
    payload["available_documents"] = len(manifest.documents)

    keys = _manifest_source_keys(manifest)
    rows = session.execute(
        select(Document.status, func.count(Document.id))
        .where(
            Document.source_type == constants.SOURCE_DEMO,
            Document.source_key.in_(keys),
            Document.deleted_at.is_(None),
        )
        .group_by(Document.status)
    ).all()
    status_counts = {status: count for status, count in rows}
    ready_documents = status_counts.get(constants.STATUS_READY, 0)
    failed_documents = status_counts.get(constants.STATUS_FAILED, 0)
    payload["ready_documents"] = ready_documents
    payload["failed_documents"] = failed_documents

    # loaded 必须严格匹配：唯一 active 指针 + 版本 + manifest + 当前流水线指纹 + 全部 ready
    current_pipeline = pipeline_fingerprint(settings)
    loaded = (
        active_pointer is not None
        and active_pointer.dataset_version == settings.demo_dataset_version
        and active_pointer.manifest_sha256 == manifest.manifest_sha256
        and active_pointer.pipeline_fingerprint == current_pipeline
        and len(manifest.documents) > 0
        and ready_documents == len(manifest.documents)
        and failed_documents == 0
    )
    payload["loaded"] = loaded

    if running is not None:
        payload["state"] = (
            constants.DEMO_STATE_QUEUED if running.status == constants.JOB_QUEUED else constants.DEMO_STATE_RUNNING
        )
    elif loaded:
        payload["state"] = constants.DEMO_STATE_LOADED
    elif ready_documents > 0:
        payload["state"] = constants.DEMO_STATE_PARTIAL
    elif previous is not None and previous.status == constants.JOB_FAILED:
        payload["state"] = constants.DEMO_STATE_FAILED
        payload["reason"] = {
            "code": previous.error_code or "DEMO_PIPELINE_UNAVAILABLE",
            "message": previous.error_message or "演示任务失败",
            "retryable": True,
        }
    else:
        payload["state"] = constants.DEMO_STATE_EMPTY

    return payload


def seed_job(session: Session, settings: Settings) -> tuple[DemoSeedJob, bool]:
    """创建新任务或复用活动任务，返回 (job, reused_active_job)。

    只校验配置与 manifest、写入持久任务；不在请求线程内解析任何文件。
    """
    if not settings.demo_dataset_enabled:
        raise ApiError(DEMO_DATASET_DISABLED)

    manifest = load_demo_manifest(settings)
    existing = active_job(session)
    if existing is not None:
        return existing, True

    job = DemoSeedJob(
        dataset_version=manifest.dataset_version,
        manifest_sha256=manifest.manifest_sha256,
        pipeline_fingerprint=pipeline_fingerprint(settings),
        target_stage=constants.TARGET_STAGE,
        status=constants.JOB_QUEUED,
        total_count=len(manifest.documents),
        active_marker=1,
    )
    session.add(job)
    session.flush()
    for document in manifest.documents:
        session.add(
            DemoSeedJobDocument(
                job_id=job.id,
                manifest_path=document.path,
                expected_sha256=document.sha256,
                status=constants.JOB_DOC_PENDING,
                last_completed_stage=constants.STAGE_NONE,
            )
        )
    session.flush()
    return job, False


def job_documents(session: Session, job_id: str) -> list[DemoSeedJobDocument]:
    return list(
        session.scalars(
            select(DemoSeedJobDocument)
            .where(DemoSeedJobDocument.job_id == job_id)
            .order_by(DemoSeedJobDocument.manifest_path)
        )
    )


def serialize_job(session: Session, job: DemoSeedJob, settings: Settings) -> dict:
    documents = job_documents(session, job.id)
    processed = job.imported_count + job.resumed_count + job.skipped_count + job.failed_count
    progress = round(processed * 100 / job.total_count) if job.total_count else 0

    items = [
        {
            "manifest_path": document.manifest_path,
            "file_name": document.manifest_path.rsplit("/", 1)[-1],
            "doc_id": document.doc_id,
            "status": document.status,
            "last_completed_stage": document.last_completed_stage,
            "result": document.result,
            "error_code": document.error_code,
        }
        for document in documents
    ]
    errors = [
        {
            "manifest_path": document.manifest_path,
            "file_name": document.manifest_path.rsplit("/", 1)[-1],
            "code": document.error_code or "DOCUMENT_PARSE_FAILED",
            "message": document.error_message or "文档处理失败",
            "retryable": bool(document.retryable),
        }
        for document in documents
        if document.status == constants.JOB_DOC_FAILED
    ]

    return {
        "job_id": job.id,
        "dataset_version": job.dataset_version,
        "target_stage": job.target_stage,
        "status": job.status,
        "current_stage": job.current_stage,
        "total": job.total_count,
        "imported": job.imported_count,
        "resumed": job.resumed_count,
        "skipped": job.skipped_count,
        "failed": job.failed_count,
        "processed": processed,
        "progress_percent": progress,
        "poll_after_seconds": settings.demo_job_poll_seconds,
        "created_at": isoformat_utc(job.created_at),
        "started_at": isoformat_utc(job.started_at),
        "finished_at": isoformat_utc(job.finished_at),
        "documents": items,
        "errors": errors,
    }


def mark_job_failed(session: Session, job: DemoSeedJob, code: str, message: str) -> None:
    """任务级失败：终止活动态并释放活动标记。"""
    job.status = constants.JOB_FAILED
    job.error_code = code
    job.error_message = message
    job.current_stage = None
    job.finished_at = utcnow()
    job.active_marker = None
    job.lease_owner = None
    job.lease_expires_at = None
