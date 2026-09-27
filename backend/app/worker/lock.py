"""数据卷上的独占进程锁。

只有在成功持有该锁时，worker 才允许进行外部写入；
数据库 lease 过期本身不足以说明没有其他写入者。
"""

from __future__ import annotations

from pathlib import Path

from filelock import FileLock, Timeout

from app.config import Settings
from app.db import sqlite_file_path

LOCK_FILE_NAME = "worker.lock"


def default_lock_path(settings: Settings) -> Path:
    database_path = sqlite_file_path(settings.database_url)
    if database_path is not None:
        return database_path.parent / LOCK_FILE_NAME
    return Path(settings.upload_path).parent / LOCK_FILE_NAME


def acquire_process_lock(lock_path: Path, timeout: float = 0.0) -> FileLock | None:
    """尝试获取独占锁；已被其它进程持有时返回 None。"""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(lock_path))
    try:
        lock.acquire(timeout=timeout)
    except Timeout:
        return None
    return lock
