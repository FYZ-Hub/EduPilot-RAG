"""演示数据集异步接口：status、seed 与 job 查询。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import AppContext, get_context, get_session
from app.core.errors import DEMO_PIPELINE_UNAVAILABLE, ApiError
from app.demo import service as demo_service

router = APIRouter(prefix="/demo", tags=["demo"])


@router.get("/status")
def read_status(
    context: AppContext = Depends(get_context), session: Session = Depends(get_session)
) -> dict:
    return demo_service.build_status(session, context.settings)


@router.post("/seed", status_code=202)
def seed_dataset(
    context: AppContext = Depends(get_context), session: Session = Depends(get_session)
) -> JSONResponse:
    """只校验配置与 manifest、创建或复用持久任务；不在请求内解析任何文件。"""
    settings = context.settings
    try:
        job, reused = demo_service.seed_job(session, settings)
    except IntegrityError:
        # 并发 POST：部分唯一索引拒绝第二个活动任务，改为复用现有任务
        session.rollback()
        job = demo_service.active_job(session)
        if job is None:
            raise ApiError(DEMO_PIPELINE_UNAVAILABLE) from None
        reused = True

    status_url = f"/api/demo/jobs/{job.id}"
    return JSONResponse(
        status_code=202,
        content={
            "job_id": job.id,
            "dataset_version": job.dataset_version,
            "target_stage": job.target_stage,
            "status": job.status,
            "status_url": status_url,
            "poll_after_seconds": settings.demo_job_poll_seconds,
            "reused_active_job": reused,
        },
        headers={
            "Location": status_url,
            "Retry-After": str(settings.demo_job_poll_seconds),
        },
    )


@router.get("/jobs/{job_id}")
def read_job(
    job_id: str,
    context: AppContext = Depends(get_context),
    session: Session = Depends(get_session),
) -> dict:
    job = demo_service.get_job(session, job_id)
    return demo_service.serialize_job(session, job, context.settings)
