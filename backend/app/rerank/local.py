"""LocalReranker：本地 BAAI/bge-reranker-v2-m3（延迟导入、延迟加载）。

- 模型名、revision、最大长度来自 ``descriptor_for(settings)``，revision 固定为快照提交，
  绝不跟随可变的 ``main``。
- 依赖 ``sentence-transformers``（可选，见 ``requirements-embedding-local.txt``）：
  应用构造、健康检查与全部自动化测试都不会导入或加载模型。
- 默认 ``local_files_only=True``（``RERANK_LOCAL_FILES_ONLY``），
  ``cache_dir`` 显式指向 ``MODEL_CACHE_PATH``（即容器内 ``/app/data/models``），
  不会使用宿主机默认 Hugging Face 缓存，也不会隐式下载权重。
- 默认 ``device=cpu``、``batch_size=2``；``cuda`` 与 ``batch_size=4`` 只由 GPU Compose 覆盖启用。
- CUDA 不可用或推理 OOM 时**明确失败**，绝不静默回退 CPU，也绝不切换到 API 或 Fake。
- 加载与推理都在进程级协调器的独占窗口内进行，窗口结束后在 ``finally`` 中释放。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.config import Settings
from app.core.errors import RERANK_PROVIDER_UNAVAILABLE, RETRIEVAL_UNAVAILABLE, ApiError
from app.rerank.base import RerankProvider, descriptor_for
from app.runtime.coordinator import (
    RERANKER_KIND,
    LocalModelCoordinator,
    empty_cuda_cache,
    reranker_lease,
)


class LocalReranker(RerankProvider):
    def __init__(self, settings: Settings, coordinator: LocalModelCoordinator | None = None):
        self.settings = settings
        self.descriptor = descriptor_for(settings)
        self.coordinator = coordinator
        self._model: Any | None = None
        if coordinator is not None:
            coordinator.register(RERANKER_KIND, self.close)

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def rerank(self, query: str, candidates: Sequence[str]) -> list[float]:
        if not candidates:
            return []
        try:
            with reranker_lease(self.coordinator):
                model = self._ensure_model()
                scores = self._predict(model, query, list(candidates))
        except ApiError as error:
            if error.code == RETRIEVAL_UNAVAILABLE:
                # 协调器忙：明确失败，交由编排层做有记录的降级
                raise ApiError(
                    RERANK_PROVIDER_UNAVAILABLE, details={"reason": "local_model_busy"}
                ) from error
            raise
        return [float(value) for value in scores]

    # -- 延迟加载与推理 ---------------------------------------------------

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model

        try:
            import torch  # noqa: F401 - 仅用于设备校验
            from sentence_transformers import CrossEncoder
        except ModuleNotFoundError as error:  # 可选依赖缺失：明确、可操作，不回退
            raise ApiError(
                RERANK_PROVIDER_UNAVAILABLE,
                details={"reason": "local_rerank_dependency_missing"},
            ) from error

        device = self.settings.rerank_device
        if device == "cuda":
            import torch

            if not torch.cuda.is_available():
                raise ApiError(
                    RERANK_PROVIDER_UNAVAILABLE, details={"reason": "cuda_unavailable"}
                )

        try:
            model = CrossEncoder(
                self.descriptor.model,
                revision=self.descriptor.revision,
                device=device,
                max_length=self.descriptor.max_length,
                trust_remote_code=False,
                local_files_only=self.settings.rerank_local_files_only,
                cache_folder=self.settings.model_cache_path,
            )
        except ApiError:
            raise
        except Exception as error:  # noqa: BLE001 - 缺权重或加载失败统一收敛为安全错误
            raise ApiError(
                RERANK_PROVIDER_UNAVAILABLE, details={"reason": "local_rerank_load_failed"}
            ) from error
        self._model = model
        return model

    def _predict(self, model: Any, query: str, candidates: list[str]) -> list[float]:
        import torch

        pairs = [(query, text or "") for text in candidates]
        batch_size = max(1, int(self.settings.rerank_batch_size))
        try:
            model.model.eval()
            with torch.no_grad():
                outputs = model.predict(
                    pairs,
                    batch_size=batch_size,
                    show_progress_bar=False,
                )
        except Exception as error:  # noqa: BLE001 - CUDA OOM 等一律明确失败
            reason = "cuda_out_of_memory" if "out of memory" in str(error).lower() else "local_rerank_inference_failed"
            raise ApiError(RERANK_PROVIDER_UNAVAILABLE, details={"reason": reason}) from error
        return [float(value) for value in outputs]

    def close(self) -> None:
        """释放模型引用；仅在 torch 已加载且使用 CUDA 时清理显存缓存。"""
        if self._model is None:
            return
        self._model = None
        empty_cuda_cache(self.settings.rerank_device)


__all__ = ["LocalReranker"]
