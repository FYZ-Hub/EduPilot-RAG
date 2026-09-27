"""阶段 7B-2：``GET /api/academic/options`` 的可选上下文。

字段严格对齐 ``docs/PRODUCT_SPEC.md`` 6.4：顶层只有 ``record_sets`` 与 ``rule_sets``，
子项字段不得增删；不返回 ``source_key`` / ``activation_state`` / ``dataset_version`` /
路径 / 哈希 / 真实身份，也不返回默认选中项（后端绝不静默选择规则版本）。

可见性口径：

- **demo**：``source_type=demo`` + ``dataset_version`` 等于唯一 active 指针 +
  ``activation_state=active`` + ``status=ready`` + 来源文档仍有效；
- **upload**：``source_type=upload`` + ``status=ready`` + 来源文档仍有效，
  不依赖 demo active 指针，因此 demo 切换不会影响 upload 选项。
"""

from __future__ import annotations

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app import constants
from app.documents.service import isoformat_utc
from app.models import AcademicRecordSet, AcademicRuleSet, DemoActiveDataset, Document

RECORD_SET_FIELDS = ("id", "name", "source_doc_id", "status", "updated_at")
RULE_SET_FIELDS = (
    "id",
    "name",
    "major",
    "admission_year",
    "rule_version",
    "effective_from",
    "source_doc_id",
    "status",
)


def active_demo_version(session: Session) -> str | None:
    """唯一 active demo dataset 版本；不存在或不唯一时按「无 active demo」处理。"""
    versions = [
        str(value)
        for value in session.scalars(
            select(DemoActiveDataset.dataset_version).where(
                DemoActiveDataset.active_marker.is_not(None)
            )
        ).all()
    ]
    if len(versions) != 1:
        return None
    return versions[0]


def visibility_clause(model, active_version: str | None):
    """demo / upload 的可选可见性口径；``options`` 与 ``plan`` **必须共用**这一处定义。"""
    upload_branch = model.source_type == constants.SOURCE_UPLOAD
    if active_version is None:
        return upload_branch
    demo_branch = and_(
        model.source_type == constants.SOURCE_DEMO,
        model.activation_state == constants.ACTIVATION_ACTIVE,
        model.dataset_version == active_version,
    )
    return or_(upload_branch, demo_branch)


def _decorate(items: list[dict], key) -> list[dict]:
    """稳定去重与排序；相同 key 只保留一次，顺序与数据库返回顺序无关。"""
    unique: dict[tuple, dict] = {}
    for item in items:
        unique.setdefault(key(item), item)
    return [unique[item_key] for item_key in sorted(unique)]


def record_set_options(session: Session, active_version: str | None) -> list[dict]:
    rows = session.scalars(
        select(AcademicRecordSet)
        .join(Document, Document.id == AcademicRecordSet.source_doc_id)
        .where(
            AcademicRecordSet.status == constants.STATUS_READY,
            Document.deleted_at.is_(None),
            visibility_clause(AcademicRecordSet, active_version),
        )
    ).all()
    items = [
        {
            "id": row.id,
            "name": row.display_name,
            "source_doc_id": row.source_doc_id,
            "status": row.status,
            "updated_at": isoformat_utc(row.updated_at),
        }
        for row in rows
    ]
    return _decorate(
        items,
        lambda item: (item["name"], item["updated_at"] or "", item["id"]),
    )


def rule_set_options(session: Session, active_version: str | None) -> list[dict]:
    rows = session.scalars(
        select(AcademicRuleSet)
        .join(Document, Document.id == AcademicRuleSet.source_doc_id)
        .where(
            AcademicRuleSet.status == constants.STATUS_READY,
            Document.deleted_at.is_(None),
            visibility_clause(AcademicRuleSet, active_version),
        )
    ).all()
    items = [
        {
            "id": row.id,
            "name": row.display_name,
            "major": row.major,
            "admission_year": row.admission_year,
            "rule_version": row.rule_version,
            "effective_from": row.effective_from,
            "source_doc_id": row.source_doc_id,
            "status": row.status,
        }
        for row in rows
    ]
    return _decorate(
        items,
        lambda item: (
            item["major"],
            item["admission_year"],
            item["rule_version"],
            item["id"],
        ),
    )


def academic_options(session: Session) -> dict:
    """恢复可选上下文；无数据时返回两个空数组，不返回任何默认选中项。"""
    active_version = active_demo_version(session)
    return {
        "record_sets": record_set_options(session, active_version),
        "rule_sets": rule_set_options(session, active_version),
    }


__all__ = [
    "RECORD_SET_FIELDS",
    "RULE_SET_FIELDS",
    "academic_options",
    "active_demo_version",
    "record_set_options",
    "rule_set_options",
]
