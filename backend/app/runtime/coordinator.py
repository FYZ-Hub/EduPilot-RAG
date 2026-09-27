"""进程级共享的本地大模型运行协调器。

worker Embedding、Dense 查询 Embedding 与 Local Reranker 属于同一份显存/内存预算，
必须共用一个有界互斥机制：

- 任何时刻最多只有一个本地大模型驻留（拿到锁才允许加载）；
- 每个使用窗口结束后（正常、异常、超时、取消）都在 ``finally`` 中释放；
- 不允许无限等待锁：超时即抛出安全的 ``RETRIEVAL_UNAVAILABLE`` 错误。

本模块**不导入** torch / sentence-transformers：``empty_cuda_cache`` 只在 torch
已经被实际加载时才触碰它，因此 close/release 不会为了清理而触发重量级导入。
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager, contextmanager, nullcontext
from typing import Iterator

from app.core.errors import RETRIEVAL_UNAVAILABLE, ApiError

logger = logging.getLogger("app.runtime")

EMBEDDING_KIND = "embedding"
RERANKER_KIND = "reranker"

# 有界等待上限：绝不允许无限等待锁
DEFAULT_WAIT_SECONDS = 30.0


class LocalModelCoordinator:
    """本地模型串行协调器；全进程共享同一实例（由 ``AppContext`` 持有）。"""

    def __init__(self, wait_seconds: float = DEFAULT_WAIT_SECONDS):
        self._mutex = threading.Lock()
        self.wait_seconds = max(0.0, float(wait_seconds))
        self._releasers: dict[str, Callable[[], None]] = {}
        self._active: str | None = None

    @property
    def active_kind(self) -> str | None:
        """当前持有锁并使用本地模型的能力名；空闲时为 ``None``。"""
        return self._active

    @property
    def registered_kinds(self) -> tuple[str, ...]:
        return tuple(self._releasers)

    def register(self, kind: str, releaser: Callable[[], None]) -> None:
        """登记某一能力的释放回调；只有本地 Provider 需要登记。"""
        self._releasers[kind] = releaser

    def unregister(self, kind: str) -> None:
        self._releasers.pop(kind, None)

    @contextmanager
    def lease(self, kind: str) -> Iterator[None]:
        """有界独占窗口；进入即独占，退出（含异常路径）必定释放。"""
        if not self._mutex.acquire(timeout=self.wait_seconds):
            raise ApiError(
                RETRIEVAL_UNAVAILABLE, details={"reason": "local_model_busy"}
            )
        try:
            self._active = kind
            yield
        finally:
            self._active = None
            self._release(kind)
            self._mutex.release()

    def _release(self, kind: str) -> None:
        releaser = self._releasers.get(kind)
        if releaser is None:
            return
        try:
            releaser()
        except Exception:  # noqa: BLE001 - 释放失败不得打断调用方
            logger.warning("local_model_release_failed kind=%s", kind)

    def close(self) -> None:
        """幂等释放全部本地模型；获取不到锁只记录警告，绝不无限等待。"""
        if not self._mutex.acquire(timeout=self.wait_seconds):
            logger.warning("local_model_close_timeout")
            return
        try:
            for kind in list(self._releasers):
                self._release(kind)
            self._active = None
        finally:
            self._mutex.release()


def embedding_lease(
    coordinator: LocalModelCoordinator | None,
) -> AbstractContextManager[None]:
    """Embedding 使用窗口；没有协调器时退化为无操作上下文。"""
    if coordinator is None:
        return nullcontext()
    return coordinator.lease(EMBEDDING_KIND)


def reranker_lease(
    coordinator: LocalModelCoordinator | None,
) -> AbstractContextManager[None]:
    """Reranker 使用窗口；没有协调器时退化为无操作上下文。"""
    if coordinator is None:
        return nullcontext()
    return coordinator.lease(RERANKER_KIND)


def empty_cuda_cache(device: str | None) -> None:
    """只有 torch 已加载且确实使用 CUDA 时才清理显存缓存。"""
    if device != "cuda":
        return
    torch = sys.modules.get("torch")
    if torch is None:
        return
    try:
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        logger.warning("cuda_cache_cleanup_failed")


__all__ = [
    "DEFAULT_WAIT_SECONDS",
    "EMBEDDING_KIND",
    "RERANKER_KIND",
    "LocalModelCoordinator",
    "embedding_lease",
    "empty_cuda_cache",
    "reranker_lease",
]
