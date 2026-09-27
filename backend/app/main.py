"""FastAPI 应用入口。

装配配置、数据库、文档与演示路由、统一错误体，并在 lifespan 内启停内置 worker。
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.demo import router as demo_router
from app.api.deps import AppContext
from app.api.documents import router as documents_router
from app.api.health import router as health_router
from app.config import Settings, get_settings
from app.core.errors import (
    HTTP_ERROR,
    METHOD_NOT_ALLOWED,
    NOT_FOUND,
    REQUEST_VALIDATION_ERROR,
    ApiError,
)
from app.core.request_id import RequestContextMiddleware, error_response, get_request_id
from app.db import create_db_engine, create_session_factory, init_database
from app.worker.runner import Worker

_logger = logging.getLogger("app.main")


def configure_logging(settings: Settings) -> None:
    """配置结构化程度最低的日志；不输出配置值或密钥。"""
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or get_request_id()


async def _api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return error_response(exc, _request_id(request))


async def _validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    # 只回显字段位置与错误类型，不回显用户输入值
    details = {
        "fields": [
            {"loc": [str(part) for part in error.get("loc", ())], "type": str(error.get("type", ""))}
            for error in exc.errors()
        ]
    }
    return error_response(ApiError(REQUEST_VALIDATION_ERROR, details=details), _request_id(request))


async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    if exc.status_code == 404:
        error = ApiError(NOT_FOUND)
    elif exc.status_code == 405:
        error = ApiError(METHOD_NOT_ALLOWED)
    else:
        error = ApiError(HTTP_ERROR, status_code=exc.status_code)
    return error_response(error, _request_id(request))


@asynccontextmanager
async def _lifespan(application: FastAPI):
    context: AppContext = application.state.context
    Path(context.settings.upload_path).mkdir(parents=True, exist_ok=True)
    Path(context.settings.upload_tmp_path).mkdir(parents=True, exist_ok=True)
    if context.settings.worker_enabled:
        context.worker.start()
    else:
        _logger.info("worker_disabled_by_configuration")
    try:
        yield
    finally:
        context.worker.stop()


def create_app(settings: Settings | None = None) -> FastAPI:
    """创建并配置 FastAPI 应用（工厂函数便于测试）。"""
    resolved = settings or get_settings()
    configure_logging(resolved)

    engine = create_db_engine(resolved)
    init_database(engine)
    session_factory = create_session_factory(engine)

    application = FastAPI(
        title=resolved.app_name,
        version=resolved.app_version,
        lifespan=_lifespan,
    )
    worker = Worker(resolved, session_factory)
    # Embedding Provider 与向量库由 worker 持有，全进程复用同一实例：
    # 构造阶段不加载模型、不下载权重、不发起网络请求。
    application.state.context = AppContext(
        settings=resolved,
        engine=engine,
        session_factory=session_factory,
        worker=worker,
        embeddings=worker.embeddings,
        vectors=worker.vectors,
    )

    # 先加请求上下文，再加 CORS，使 CORS 位于最外层（错误响应也带跨域头）
    application.add_middleware(RequestContextMiddleware)
    application.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    application.add_exception_handler(ApiError, _api_error_handler)
    application.add_exception_handler(RequestValidationError, _validation_error_handler)
    application.add_exception_handler(StarletteHTTPException, _http_exception_handler)

    application.include_router(health_router, prefix="/api")
    application.include_router(documents_router, prefix="/api")
    application.include_router(demo_router, prefix="/api")
    return application


app = create_app()
