"""共享测试夹具。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client() -> TestClient:
    """返回未启动真实服务器的测试客户端。"""
    return TestClient(create_app())
