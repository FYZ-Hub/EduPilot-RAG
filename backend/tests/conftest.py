"""共享测试夹具。

所有测试都在容器内离线运行：不访问网络、不下载模型、不依赖 GPU。
"""

from __future__ import annotations

import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import create_db_engine, create_session_factory, init_database
from app.main import create_app
from app.worker.runner import Worker

DEMO_DATASET_PATH = Path("/app/demo")
DEMO_CORPUS_PATH = DEMO_DATASET_PATH / "corpus"
DEMO_MANIFEST_PATH = DEMO_DATASET_PATH / "manifest.json"


def build_settings(tmp_path: Path, **overrides) -> Settings:
    """构造隔离的测试配置：临时数据库、临时上传目录、显式关闭后台线程。"""
    values: dict = {
        "app_env": "test",
        "log_level": "WARNING",
        "database_url": f"sqlite:///{tmp_path / 'app.db'}",
        "upload_path": str(tmp_path / "uploads"),
        "demo_dataset_path": str(DEMO_DATASET_PATH),
        "demo_dataset_version": "2026.1",
        "worker_enabled": False,
        "worker_poll_seconds": 0.05,
        "demo_job_poll_seconds": 1,
        "demo_job_lease_seconds": 60,
    }
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def settings(tmp_path) -> Settings:
    return build_settings(tmp_path)


@pytest.fixture
def context(settings):
    engine = create_db_engine(settings)
    init_database(engine)
    factory = create_session_factory(engine)
    yield SimpleNamespace(settings=settings, engine=engine, session_factory=factory)
    engine.dispose()


@pytest.fixture
def client(settings):
    application = create_app(settings)
    with TestClient(application) as test_client:
        yield test_client
    application.state.context.engine.dispose()


@pytest.fixture
def worker(context) -> Worker:
    """已持有进程锁但不启动后台线程的 worker，便于确定性驱动。"""
    instance = Worker(context.settings, context.session_factory, worker_id="test-worker")
    assert instance.prepare()
    yield instance
    instance.stop()


@pytest.fixture
def writable_dataset(tmp_path) -> Path:
    """可写的演示数据集副本，用于 manifest 变化等场景。"""
    target = tmp_path / "demo"
    shutil.copytree(DEMO_DATASET_PATH, target)
    return target


def demo_file(name_fragment: str) -> Path:
    matches = sorted(DEMO_CORPUS_PATH.glob(f"*{name_fragment}*"))
    assert matches, f"未找到演示文件 {name_fragment}"
    return matches[0]


def upload_file(client: TestClient, path: Path, *, filename: str | None = None, content_type: str | None = None):
    """以 multipart/form-data 上传单个文件。"""
    with open(path, "rb") as handle:
        files = {"file": (filename or path.name, handle, content_type or "application/octet-stream")}
        return client.post("/api/documents", files=files)


def upload_bytes(client: TestClient, payload: bytes, *, filename: str, content_type: str | None = None):
    files = {"file": (filename, payload, content_type or "application/octet-stream")}
    return client.post("/api/documents", files=files)
