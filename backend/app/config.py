"""集中式应用配置。

所有运行配置只在本模块读取环境变量并校验；其他模块不得直接访问 ``os.environ``。
配置校验、应用构造与健康检查都不加载、不下载任何真实模型。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

Device = Literal["cpu", "cuda"]
ProviderKind = Literal["local", "api", "fake"]


class Settings(BaseSettings):
    """单一配置入口，字段与 ``.env.example`` 一一对应。"""

    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        protected_namespaces=("settings_",),
    )

    # 应用
    app_env: str = "development"
    app_name: str = "campus-rag-assistant"
    app_version: str = "0.1.0"
    log_level: str = "INFO"

    # 端口与跨域
    frontend_port: int = 5173
    backend_port: int = 8000
    cors_origins: str = "http://localhost:5173"

    # 数据目录（容器内路径）
    database_url: str = "sqlite:////app/data/sqlite/app.db"
    chroma_path: str = "/app/data/chroma"
    upload_path: str = "/app/data/uploads"
    model_cache_path: str = "/app/data/models"

    # Embedding（阶段 3 起使用；构造与健康检查都不加载模型）
    embedding_provider: ProviderKind = "local"
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = "BAAI/bge-m3"
    embedding_dimension: int = 1024
    embedding_device: Device = "cpu"
    embedding_batch_size: int = 4
    # 显式模型 revision；留空时使用 Provider 内置的固定快照，绝不跟随可变 main
    embedding_revision: str = ""
    embedding_timeout_seconds: int = 30
    # 本地 Provider 默认只读缓存，不隐式下载权重（权重需预先放入 MODEL_CACHE_PATH）
    embedding_local_files_only: bool = True

    # Reranker（阶段 5 起使用；查询时能力，不产生持久索引、不进入 pipeline_fingerprint）
    rerank_provider: ProviderKind = "local"
    rerank_base_url: str = ""
    rerank_api_key: str = ""
    rerank_model: str = "BAAI/bge-reranker-v2-m3"
    rerank_device: Device = "cpu"
    rerank_batch_size: int = 2
    # 显式模型 revision；留空时使用 Provider 内置的固定快照，绝不跟随可变 main
    rerank_revision: str = ""
    rerank_timeout_seconds: int = 30
    # 本地 Provider 默认只读缓存，不隐式下载权重（权重需预先放入 MODEL_CACHE_PATH）
    rerank_local_files_only: bool = True

    # LLM（缺省未配置，不阻止服务启动）
    llm_provider: str = "openai_compatible"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    llm_timeout_seconds: int = 60

    # 检索与切片参数（阶段 2 起使用）
    chunk_target_chars: int = 550
    chunk_overlap_chars: int = 100
    dense_top_k: int = 12
    keyword_top_k: int = 12
    rerank_top_k: int = 6
    retrieval_score_threshold: float | None = None

    # 上传
    max_upload_mb: int = 50

    # 演示资料（阶段 2 起使用）
    demo_dataset_enabled: bool = True
    demo_dataset_path: str = "/app/demo"
    demo_dataset_version: str = "2026.1"
    demo_job_poll_seconds: int = 2
    demo_job_lease_seconds: int = 60
    demo_document_max_attempts: int = 3

    # 内置 worker（FastAPI lifespan 启停；测试可显式关闭）
    worker_enabled: bool = True
    worker_poll_seconds: float = 0.5

    # SQLite
    sqlite_busy_timeout_ms: int = 5000

    @property
    def cors_origin_list(self) -> list[str]:
        """把逗号分隔的 CORS 配置解析为列表，忽略空项。"""
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def llm_configured(self) -> bool:
        """只返回 LLM 是否具备最小配置，不暴露任何配置值。"""
        return bool(self.llm_base_url and self.llm_model and self.llm_api_key)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def upload_tmp_path(self) -> str:
        """上传落盘前的受控临时目录，与最终存储同处数据卷以便原子移动。"""
        return f"{self.upload_path.rstrip('/')}/.tmp"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回进程级单例配置。"""
    return Settings()
