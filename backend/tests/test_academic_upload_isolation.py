"""阶段 7B-2 独立回归修复轮：学业导入隔离不得误伤普通知识库上传。

不变量（BUG-7B2-02）：

- 流水线**所有权**由 ``document_pipeline_state`` 是否存在决定，而不是 ``doc_category``；
- ``degree_plan`` / ``course_records`` 是 PRODUCT_SPEC 定义的**业务内容分类**
  （前端过滤与 ``/api/retrieval/options`` 都会使用），不得影响普通上传的去重、
  worker 认领、失败重试与索引；
- academic import 建立文档但**不建立** ``DocumentPipelineState``，因此既不会被
  RAG worker 认领，也不会进入检索。

全部离线：不访问网络、不调用 LLM、不下载模型。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select

from app import constants
from app.models import AcademicRecordSet, AcademicRuleSet, Document, DocumentPipelineState
from tests.conftest import demo_file

DOCUMENTS_ENDPOINT = "/api/documents"
RECORDS_ENDPOINT = "/api/academic/records/import"
RULES_ENDPOINT = "/api/academic/rules/import"


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------


def _upload_document(client, path: Path, *, filename: str | None = None):
    with open(path, "rb") as handle:
        files = {"file": (filename or path.name, handle, "application/octet-stream")}
        return client.post(DOCUMENTS_ENDPOINT, files=files)


def _import_rules(client, path: Path):
    with open(path, "rb") as handle:
        files = {"file": (path.name, handle, "application/octet-stream")}
        return client.post(RULES_ENDPOINT, files=files)


def _classify(context, doc_id: str, category: str) -> None:
    """模拟文档在解析后被赋予业务内容分类（培养方案 / 成绩记录）。"""
    with context.session_factory() as session:
        document = session.get(Document, doc_id)
        assert document is not None
        document.doc_category = category
        session.commit()


def _document(context, doc_id: str) -> Document:
    with context.session_factory() as session:
        document = session.get(Document, doc_id)
        assert document is not None
        return document


def _has_pipeline_state(context, doc_id: str) -> bool:
    with context.session_factory() as session:
        return (
            session.scalar(
                select(DocumentPipelineState).where(DocumentPipelineState.doc_id == doc_id)
            )
            is not None
        )


def _stored_files(settings) -> list[Path]:
    root = Path(settings.upload_path)
    if not root.exists():
        return []
    return [path for path in root.iterdir() if path.is_file()]


# ---------------------------------------------------------------------------
# 1/2：业务分类不得影响 worker 认领
# ---------------------------------------------------------------------------


def test_general_degree_plan_upload_remains_worker_eligible(client, context, worker) -> None:
    path = demo_file("01-培养方案")
    response = _upload_document(client, path)
    assert response.status_code == 202, response.text
    document_id = response.json()["document_id"]

    _classify(context, document_id, "degree_plan")
    assert _has_pipeline_state(context, document_id) is True

    document = _document(context, document_id)
    assert document.status == constants.STATUS_QUEUED

    assert worker.run_once() is True, "普通培养方案上传即使被分类为 degree_plan 也必须可被 worker 认领"
    assert _document(context, document_id).status != constants.STATUS_QUEUED


def test_general_course_records_upload_remains_worker_eligible(client, context, worker) -> None:
    path = demo_file("13-课程记录-匿名学生A")
    response = _upload_document(client, path)
    assert response.status_code == 202, response.text
    document_id = response.json()["document_id"]

    _classify(context, document_id, "course_records")
    assert _has_pipeline_state(context, document_id) is True

    assert worker.run_once() is True, "普通成绩记录上传即使被分类为 course_records 也必须可被 worker 认领"
    assert _document(context, document_id).status != constants.STATUS_QUEUED


# ---------------------------------------------------------------------------
# 3：业务分类不得影响普通上传去重
# ---------------------------------------------------------------------------


def test_general_academic_category_upload_is_deduplicated(client, context, settings) -> None:
    path = demo_file("01-培养方案")
    first = _upload_document(client, path)
    assert first.status_code == 202
    document_id = first.json()["document_id"]

    _classify(context, document_id, "degree_plan")

    second = _upload_document(client, path)
    assert second.status_code == 202, second.text
    payload = second.json()
    assert payload["document_id"] == document_id, "同一文件必须复用同一个普通 Document"
    assert payload["deduplicated"] is True
    assert payload["disposition"] != constants.DISPOSITION_CREATED
    assert len(_stored_files(settings)) == 1, "去重不得产生多余文件"


# ---------------------------------------------------------------------------
# 4：业务分类不得影响失败重试
# ---------------------------------------------------------------------------


def test_retryable_general_degree_plan_can_be_reclaimed(client, context, worker) -> None:
    path = demo_file("01-培养方案")
    document_id = _upload_document(client, path).json()["document_id"]
    _classify(context, document_id, "degree_plan")

    with context.session_factory() as session:
        document = session.get(Document, document_id)
        document.status = constants.STATUS_FAILED
        document.error_code = "DOCUMENT_PARSE_FAILED"
        document.error_message = None
        document.error_retryable = True
        session.commit()

    retry = _upload_document(client, path)
    assert retry.status_code == 202, retry.text
    assert retry.json()["document_id"] == document_id
    assert retry.json()["disposition"] == constants.DISPOSITION_RETRY_STARTED

    assert _document(context, document_id).status == constants.STATUS_QUEUED
    assert worker.run_once() is True, "可重试的普通 degree_plan 文档必须能被再次认领"


# ---------------------------------------------------------------------------
# 5：academic import 文档不得被 RAG worker 认领
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "endpoint, fragment",
    [
        (RECORDS_ENDPOINT, "13-课程记录-匿名学生A"),
        (RULES_ENDPOINT, "01-培养方案"),
    ],
)
def test_academic_import_without_pipeline_state_is_not_claimed(
    client, context, worker, endpoint: str, fragment: str
) -> None:
    path = demo_file(fragment)
    with open(path, "rb") as handle:
        files = {"file": (path.name, handle, "application/octet-stream")}
        response = client.post(endpoint, files=files)
    assert response.status_code == 200, response.text

    with context.session_factory() as session:
        if endpoint == RECORDS_ENDPOINT:
            set_id = response.json()["id"]
            document_id = session.get(AcademicRecordSet, set_id).source_doc_id
        else:
            set_id = response.json()["id"]
            document_id = session.get(AcademicRuleSet, set_id).source_doc_id

    assert _has_pipeline_state(context, document_id) is False

    document = _document(context, document_id)
    assert document.status == constants.STATUS_QUEUED
    assert document.retrievable is False

    assert worker.run_once() is False, "没有 DocumentPipelineState 的学业导入文档不得被认领"

    refreshed = _document(context, document_id)
    assert refreshed.status == constants.STATUS_QUEUED
    assert refreshed.retrievable is False


# ---------------------------------------------------------------------------
# 6：同一文件的普通上传与 academic import 必须保持独立所有权
# ---------------------------------------------------------------------------


def test_general_and_academic_import_same_content_keep_separate_ownership(
    client, context, worker, settings
) -> None:
    path = demo_file("01-培养方案")

    general = _upload_document(client, path)
    assert general.status_code == 202, general.text
    general_id = general.json()["document_id"]
    # 培养方案的业务内容分类就是 degree_plan
    _classify(context, general_id, "degree_plan")

    imported = _import_rules(client, path)
    assert imported.status_code == 200, imported.text
    rule_set_id = imported.json()["id"]
    with context.session_factory() as session:
        academic_doc_id = session.get(AcademicRuleSet, rule_set_id).source_doc_id

    assert general_id != academic_doc_id, "两个通道必须使用各自的 Document"
    assert _document(context, general_id).sha256 == _document(context, academic_doc_id).sha256
    assert _has_pipeline_state(context, general_id) is True
    assert _has_pipeline_state(context, academic_doc_id) is False
    assert _document(context, academic_doc_id).retrievable is False
    assert len(_stored_files(settings)) == 2

    # 各自通道内重复请求保持幂等
    assert _upload_document(client, path).json()["document_id"] == general_id
    assert _import_rules(client, path).json()["id"] == rule_set_id
    assert len(_stored_files(settings)) == 2, "重复请求不得产生多余文件或互相删除"

    # 普通上传可以进入 RAG 流水线；academic import 不能
    assert worker.run_once() is True
    with context.session_factory() as session:
        assert session.get(Document, general_id).status != constants.STATUS_QUEUED
        academic = session.get(Document, academic_doc_id)
        assert academic.status == constants.STATUS_QUEUED
        assert academic.retrievable is False
