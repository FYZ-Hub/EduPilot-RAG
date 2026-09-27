"""LocalEmbeddingProvider：本地 BGE-M3（延迟导入、延迟加载）。

- 模型名、revision、维度来自 ``descriptor_for(settings)``，revision 固定为快照提交，
  不跟随可变的 ``main``。
- 依赖 ``sentence-transformers``（可选，见 ``requirements-embedding-local.txt``）：
  应用启动、健康检查与全部自动化测试都不会导入或加载模型。
- 默认 ``local_files_only=True``（``EMBEDDING_LOCAL_FILES_ONLY``），
  权重必须预先放入 ``MODEL_CACHE_PATH``，不会隐式下载。
- ``cuda`` 只由 GPU Compose 覆盖启用；不可用时明确失败，绝不静默回退 CPU。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.config import Settings
from app.core.errors import EMBEDDING_PROVIDER_UNAVAILABLE, ApiError
from app.embedding.base import EmbeddingProvider, descriptor_for
from app.runtime.coordinator import EMBEDDING_KIND, LocalModelCoordinator, empty_cuda_cache


class LocalEmbeddingProvider(EmbeddingProvider):
    def __init__(self, settings: Settings, coordinator: LocalModelCoordinator | None = None):
        self.settings = settings
        self.descriptor = descriptor_for(settings)
        self.coordinator = coordinator
        self._model: Any | None = None
        if coordinator is not None:
            # 由协调器在 Embedding 使用窗口结束时释放；与 Local Reranker 串行
            coordinator.register(EMBEDDING_KIND, self.close)

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _ensure_model(self) -> Any:
        if self._model is not None:
            return self._model

        try:
            import sentence_transformers
        except ModuleNotFoundError as error:  # 可选依赖缺失：明确、可操作，不回退
            raise ApiError(
                EMBEDDING_PROVIDER_UNAVAILABLE,
                details={"reason": "local_embedding_dependency_missing"},
            ) from error

        device = self.settings.embedding_device
        if device == "cuda":
            import torch

            if not torch.cuda.is_available():
                raise ApiError(
                    EMBEDDING_PROVIDER_UNAVAILABLE,
                    details={"reason": "cuda_unavailable"},
                )

        self._model = sentence_transformers.SentenceTransformer(
            model_name_or_path=self.descriptor.model,
            revision=self.descriptor.revision,
            device=device,
            cache_folder=self.settings.model_cache_path,
            trust_remote_code=False,
            local_files_only=self.settings.embedding_local_files_only,
        )
        return self._model

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        model = self._ensure_model()
        vectors = model.encode(
            list(texts),
            batch_size=self.settings.embedding_batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [[float(value) for value in vector] for vector in vectors]

    def close(self) -> None:
        """释放模型引用；仅在 torch 已加载且使用 CUDA 时清理显存缓存。"""
        if self._model is None:
            return
        self._model = None
        empty_cuda_cache(self.settings.embedding_device)
