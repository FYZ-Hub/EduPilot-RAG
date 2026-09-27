"""学业规划接口（阶段 7B-2）：课程记录 / 培养方案导入与可选上下文。

本阶段只实现 ``POST /api/academic/records/import``、``POST /api/academic/rules/import``
与 ``GET /api/academic/options``；**不实现** ``POST /api/academic/plan``，
``GET /api/health`` 的 ``planning`` 仍为 ``unavailable``。

两个导入接口固定为 ``multipart/form-data``（``file`` 必填、``name`` 可选），
成功响应严格为 ``{id, status, warnings}``，不含来源键、路径、哈希或身份字段。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.academic import imports, options
from app.api.deps import AppContext, get_context, get_session

router = APIRouter(prefix="/academic", tags=["academic"])


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
