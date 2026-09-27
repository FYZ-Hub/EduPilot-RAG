"""学业规划接口（阶段 7B-2 / 7C）。

- ``POST /api/academic/records/import``、``POST /api/academic/rules/import``：
  固定 ``multipart/form-data``（``file`` 必填、``name`` 可选），成功响应严格为
  ``{id, status, warnings}``，不含来源键、路径、哈希或身份字段。
- ``GET /api/academic/options``：恢复可选上下文；无数据时返回两个空数组，不返回默认选中项。
- ``POST /api/academic/plan``：请求体**只允许**两个显式 ID，响应直接是 ``PlanningResult``
  （顶层严格 8 个字段），数字只能来自 7A 的确定性引擎，不经过任何 LLM。
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.academic import imports, options, planning
from app.academic.types import planning_result_payload
from app.api.deps import AppContext, get_context, get_session

router = APIRouter(prefix="/academic", tags=["academic"])


class AcademicPlanRequest(BaseModel):
    """``POST /api/academic/plan`` 的请求体：字段固定，禁止任何额外字段。"""

    model_config = ConfigDict(extra="forbid")

    record_set_id: UUID
    rule_set_id: UUID


@router.post("/records/import")
async def import_records(
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    context: AppContext = Depends(get_context),
    session: Session = Depends(get_session),
) -> dict:
    """导入课程记录集合（仅 XLSX）；同内容重复导入返回同一集合。"""
    result = await imports.import_record_set(
        file, name=name, settings=context.settings, session=session
    )
    return result.payload()


@router.post("/rules/import")
async def import_rules(
    file: UploadFile = File(...),
    name: str | None = Form(default=None),
    context: AppContext = Depends(get_context),
    session: Session = Depends(get_session),
) -> dict:
    """导入培养方案规则集合（PDF / DOCX / XLSX）；同内容重复导入返回同一集合。"""
    result = await imports.import_rule_set(
        file, name=name, settings=context.settings, session=session
    )
    return result.payload()


@router.get("/options")
def read_options(session: Session = Depends(get_session)) -> dict:
    """恢复可选上下文；无数据时返回两个空数组，不返回默认选中项。"""
    return options.academic_options(session)


@router.post("/plan")
def create_plan(
    payload: AcademicPlanRequest,
    context: AppContext = Depends(get_context),
    session: Session = Depends(get_session),
) -> dict:
    """按用户**显式选择**的两个 ID 计算规划；响应直接是 ``PlanningResult``。"""
    result = planning.build_plan(
        session, context.settings, str(payload.record_set_id), str(payload.rule_set_id)
    )
    return planning_result_payload(result)
