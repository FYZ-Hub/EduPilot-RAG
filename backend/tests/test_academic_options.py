"""阶段 7B-2：``GET /api/academic/options`` 可选上下文测试。

覆盖 demo active/inactive 过滤、upload 独立可见性、字段严格性、稳定排序、
来源文档删除后的悬空集合隐藏，以及重启后的一致性。
"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import select

from app.academic.options import RECORD_SET_FIELDS, RULE_SET_FIELDS, academic_options
from app.db import create_db_engine, create_session_factory
from app.models import (
    AcademicRecordSet,
    AcademicRuleSet,
    Document,
    DemoActiveDataset,
)

OPTIONS_ENDPOINT = "/api/academic/options"
XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _add_demo_rule_set(context, *, dataset_version: str, activation_state: str, tag: str) -> str:
    with context.session_factory() as session:
        document = Document(
            source_type="demo",
            source_key=f"demo:{tag}",
            file_name=f"{tag}.pdf",
            file_type="pdf",
            mime_type="application/pdf",
            sha256=_sha(tag),
            doc_category="degree_plan",
            dataset_version=dataset_version,
            status="ready",
            retrievable=True,
            activation_state=activation_state,
        )
        session.add(document)
        session.flush()
        rule_set = AcademicRuleSet(
            source_type="demo",
            source_key=f"demo:{tag}",
            dataset_version=dataset_version,
            activation_state=activation_state,
            status="ready",
            display_name=f"{tag} · 培养方案",
            major="计算机科学与技术",
            admission_year=2025,
            rule_version=tag,
            required_credits="150.0",
            content_hash=_sha(tag),
            source_doc_id=document.id,
        )
        session.add(rule_set)
        session.commit()
        return rule_set.id


def _import_records(client, path) -> str:
    with open(path, "rb") as handle:
        files = {"file": (path.name, handle, XLSX_MIME)}
        response = client.post("/api/academic/records/import", files=files)
    assert response.status_code == 200, response.text
    return response.json()["id"]


@pytest.fixture
def demo_ready(ingest_demo, context, worker):
    """已投影 active demo 学业资料的上下文。"""
    ingest_demo()
    return context


# ---------------------------------------------------------------------------
# 空库与 active demo
# ---------------------------------------------------------------------------


def test_empty_database_returns_two_empty_arrays(client) -> None:
    payload = client.get(OPTIONS_ENDPOINT).json()
    assert set(payload) == {"record_sets", "rule_sets"}
    assert payload == {"record_sets": [], "rule_sets": []}


def test_active_demo_options_are_returned(client, demo_ready) -> None:
    payload = client.get(OPTIONS_ENDPOINT).json()
    assert len(payload["record_sets"]) == 2
    assert len(payload["rule_sets"]) == 2
    names = sorted(item["name"] for item in payload["record_sets"])
    assert names == ["匿名学生A · 课程记录", "匿名学生B · 课程记录"]
    versions = sorted(item["rule_version"] for item in payload["rule_sets"])
    assert versions == ["2025.1", "2026.1"], "多个培养方案版本必须同时保留，不静默选最新"


def test_inactive_and_stale_demo_sets_are_hidden(client, demo_ready) -> None:
    active_version = "2026.1"
    inactive_id = _add_demo_rule_set(
        demo_ready, dataset_version=active_version, activation_state="inactive", tag="停用版"
    )
    candidate_id = _add_demo_rule_set(
        demo_ready, dataset_version=active_version, activation_state="candidate", tag="候选版"
    )
    stale_id = _add_demo_rule_set(
        demo_ready, dataset_version="2025.0", activation_state="active", tag="旧版本"
    )

    payload = client.get(OPTIONS_ENDPOINT).json()
    returned = {item["id"] for item in payload["rule_sets"]}
    assert inactive_id not in returned
    assert candidate_id not in returned
    assert stale_id not in returned


def test_demo_set_without_active_pointer_is_hidden(client, context) -> None:
    """没有唯一 active 指针时，demo 集合整体不可选。"""
    _add_demo_rule_set(context, dataset_version="2026.1", activation_state="active", tag="孤儿版")
    payload = client.get(OPTIONS_ENDPOINT).json()
    assert payload["rule_sets"] == []


# ---------------------------------------------------------------------------
# upload 独立可见性
# ---------------------------------------------------------------------------


def test_upload_options_do_not_depend_on_demo_pointer(client, context) -> None:
    from tests.conftest import demo_file

    set_id = _import_records(client, demo_file("13-课程记录-匿名学生A"))
    payload = client.get(OPTIONS_ENDPOINT).json()
    assert [item["id"] for item in payload["record_sets"]] == [set_id]
    assert payload["rule_sets"] == []


def test_demo_switch_does_not_affect_upload_options(client, demo_ready) -> None:
    from tests.conftest import demo_file

    upload_id = _import_records(client, demo_file("14-课程记录-匿名学生B"))

    # 切换 active 指针到另一个版本：demo 选项变化，但 upload 必须保持可见
    with demo_ready.session_factory() as session:
        pointer = session.scalar(select(DemoActiveDataset))
        pointer.dataset_version = "2027.1"
        pointer.active_marker = 1
        session.commit()

    payload = client.get(OPTIONS_ENDPOINT).json()
    returned = [item["id"] for item in payload["record_sets"]]
    assert returned == [upload_id]
    assert payload["rule_sets"] == [], "旧 demo 版本不得再出现在选项中"


# ---------------------------------------------------------------------------
# 来源删除与悬空集合
# ---------------------------------------------------------------------------


def test_deleted_source_document_hides_option(client, context) -> None:
    from tests.conftest import demo_file

    set_id = _import_records(client, demo_file("13-课程记录-匿名学生A"))
    with context.session_factory() as session:
        record = session.get(AcademicRecordSet, set_id)
        document_id = record.source_doc_id

    assert client.delete(f"/api/documents/{document_id}").status_code == 204

    payload = client.get(OPTIONS_ENDPOINT).json()
    assert payload["record_sets"] == []
    with context.session_factory() as session:
        assert session.get(AcademicRecordSet, set_id) is not None, "集合本身不应被删除"


# ---------------------------------------------------------------------------
# 字段严格性、排序与稳定性
# ---------------------------------------------------------------------------


def test_option_fields_are_strict(client, demo_ready) -> None:
    from tests.conftest import demo_file

    _import_records(client, demo_file("13-课程记录-匿名学生A"))
    payload = client.get(OPTIONS_ENDPOINT).json()

    assert set(payload) == {"record_sets", "rule_sets"}
    for item in payload["record_sets"]:
        assert set(item) == set(RECORD_SET_FIELDS)
        assert item["updated_at"].endswith("Z")
        assert "+00:00" not in item["updated_at"]
    for item in payload["rule_sets"]:
        assert set(item) == set(RULE_SET_FIELDS)
    rendered = str(payload)
    for forbidden in ("source_key", "activation_state", "dataset_version", "sha256", "/app"):
        assert forbidden not in rendered


def test_options_are_stably_sorted(client, demo_ready) -> None:
    from tests.conftest import demo_file

    _import_records(client, demo_file("13-课程记录-匿名学生A"))
    payload = client.get(OPTIONS_ENDPOINT).json()

    record_keys = [
        (item["name"], item["updated_at"], item["id"]) for item in payload["record_sets"]
    ]
    assert record_keys == sorted(record_keys)
    rule_keys = [
        (item["major"], item["admission_year"], item["rule_version"], item["id"])
        for item in payload["rule_sets"]
    ]
    assert rule_keys == sorted(rule_keys)


def test_options_are_stable_across_restart(client, context, settings) -> None:
    from tests.conftest import demo_file

    _import_records(client, demo_file("13-课程记录-匿名学生A"))
    first = client.get(OPTIONS_ENDPOINT).json()

    engine = create_db_engine(settings)
    with create_session_factory(engine)() as session:
        second = academic_options(session)
    engine.dispose()
    assert second == first
