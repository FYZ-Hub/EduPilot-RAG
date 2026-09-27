"""数据库层：PRAGMA、外键、部分唯一索引、持久化与租约条件更新。"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app import constants
from app.models import DemoSeedJob, Document, DocumentBlock, utcnow


def _job(**overrides) -> DemoSeedJob:
    values = {
        "dataset_version": "2026.1",
        "manifest_sha256": "a" * 64,
        "pipeline_fingerprint": "b" * 64,
        "target_stage": constants.TARGET_STAGE,
        "status": constants.JOB_QUEUED,
        "active_marker": 1,
    }
    values.update(overrides)
    return DemoSeedJob(**values)


def test_sqlite_pragmas_are_applied(context) -> None:
    with context.engine.connect() as connection:
        assert connection.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert connection.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
        assert (
            connection.exec_driver_sql("PRAGMA busy_timeout").scalar()
            == context.settings.sqlite_busy_timeout_ms
        )


def test_foreign_key_constraint_is_enforced(context) -> None:
    with context.session_factory() as session:
        session.add(
            DocumentBlock(doc_id="missing-document", block_index=0, text="x", block_type="paragraph")
        )
        with pytest.raises(IntegrityError):
            session.flush()
        session.rollback()


def test_block_index_is_unique_per_document(context) -> None:
    with context.session_factory() as session:
        document = Document(
            source_type=constants.SOURCE_UPLOAD,
            source_key="upload:test",
            file_name="a.pdf",
            file_type=constants.FILE_TYPE_PDF,
            mime_type="application/pdf",
            sha256="c" * 64,
        )
        session.add(document)
        session.commit()
        session.add_all(
            [
                DocumentBlock(doc_id=document.id, block_index=0, text="a", block_type="paragraph"),
                DocumentBlock(doc_id=document.id, block_index=0, text="b", block_type="paragraph"),
            ]
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


def test_only_one_active_demo_job_is_allowed(context) -> None:
    with context.session_factory() as session:
        session.add(_job(status=constants.JOB_QUEUED))
        session.commit()

    with context.session_factory() as session:
        session.add(_job(status=constants.JOB_RUNNING))
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    with context.session_factory() as session:
        assert session.scalar(select(func.count(DemoSeedJob.id))) == 1
        assert session.scalar(
            select(func.count(DemoSeedJob.id)).where(DemoSeedJob.active_marker.is_not(None))
        ) == 1


def test_terminal_job_releases_active_marker(context) -> None:
    with context.session_factory() as session:
        session.add(_job(status=constants.JOB_QUEUED))
        session.commit()

    with context.session_factory() as session:
        session.execute(
            update(DemoSeedJob)
            .where(DemoSeedJob.active_marker.is_not(None))
            .values(status=constants.JOB_COMPLETED, active_marker=None)
        )
        session.commit()

    with context.session_factory() as session:
        session.add(_job(status=constants.JOB_QUEUED))
        session.commit()
        assert session.scalar(select(func.count(DemoSeedJob.id))) == 2


def test_lease_owner_and_generation_guard(context) -> None:
    with context.session_factory() as session:
        document = Document(
            source_type=constants.SOURCE_UPLOAD,
            source_key="upload:lease",
            file_name="a.pdf",
            file_type=constants.FILE_TYPE_PDF,
            mime_type="application/pdf",
            sha256="d" * 64,
            lease_owner="worker-a",
            lease_generation=3,
        )
        session.add(document)
        session.commit()
        document_id = document.id

    with context.session_factory() as session:
        wrong_owner = session.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.lease_owner == "worker-b",
                Document.lease_generation == 3,
            )
            .values(status=constants.STATUS_PARSING)
        ).rowcount
        wrong_generation = session.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.lease_owner == "worker-a",
                Document.lease_generation == 4,
            )
            .values(status=constants.STATUS_PARSING)
        ).rowcount
        session.commit()

    with context.session_factory() as session:
        matching = session.execute(
            update(Document)
            .where(
                Document.id == document_id,
                Document.lease_owner == "worker-a",
                Document.lease_generation == 3,
            )
            .values(status=constants.STATUS_PARSING)
        ).rowcount
        session.commit()
        refreshed = session.get(Document, document_id)
        assert refreshed.status == constants.STATUS_PARSING

    assert wrong_owner == 0
    assert wrong_generation == 0
    assert matching == 1


def test_data_persists_across_sessions(context) -> None:
    with context.session_factory() as session:
        session.add(
            Document(
                source_type=constants.SOURCE_UPLOAD,
                source_key="upload:persist",
                file_name="a.pdf",
                file_type=constants.FILE_TYPE_PDF,
                mime_type="application/pdf",
                sha256="e" * 64,
            )
        )
        session.commit()

    with context.session_factory() as session:
        stored = session.scalar(select(Document).where(Document.source_key == "upload:persist"))
        assert stored is not None
        assert stored.status == constants.STATUS_QUEUED


def test_active_job_index_survives_reinitialisation(context) -> None:
    """重复初始化必须是幂等的。"""
    from app.db import init_database

    init_database(context.engine)
    with context.engine.connect() as connection:
        indexes = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='demo_seed_jobs'"
            ).fetchall()
        }
    assert "uq_demo_seed_jobs_single_active" in indexes


def test_utcnow_is_naive_utc() -> None:
    value = utcnow()
    assert value.tzinfo is None
    assert value - timedelta(seconds=1) < value
