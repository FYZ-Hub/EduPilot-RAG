"""FastAPI 应用入口。

阶段 1 只装配配置、CORS 与 ``/api/health``，不注册任何业务路由。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.config import Settings, get_settings


def configure_logging(settings: Settings) -> None:
    """配置结构化程度最低的日志；不输出配置值或密钥。"""
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建并配置 FastAPI 应用（工厂函数便于测试）。"""
    resolved = settings or get_settings()
    configure_logging(resolved)

    application = FastAPI(title=resolved.app_name, version=resolved.app_version)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    application.include_router(health_router, prefix="/api")
    return application


app = create_app()
