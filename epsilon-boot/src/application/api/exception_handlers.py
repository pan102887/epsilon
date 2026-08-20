"""统一异常处理模块，类似 Spring 的 @ControllerAdvice。

将所有异常处理器集中注册到 FastAPI app 上，
确保任何未捕获的异常都返回统一的 JSON 响应格式。
"""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from common.exceptions import BizException
from domain.run.exceptions import (
    RunCheckpointStoreUnavailableError,
    RunRecoveryUnavailableError,
    RunToolReplayBlockedError,
)

logger = logging.getLogger(__name__)


def register_exception_handlers(app: FastAPI) -> None:
    """将所有异常处理器注册到 FastAPI 应用实例上。"""

    @app.exception_handler(BizException)
    async def biz_exception_handler(_request: Request, exc: BizException) -> JSONResponse:
        """业务异常处理：返回业务错误码和描述。"""
        logger.warning("BizException: code=%s, message=%s", exc.code, exc.message)
        return JSONResponse(
            status_code=_biz_exception_status_code(exc),
            content={"code": exc.code, "message": exc.message},
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        """请求参数校验异常处理。"""
        logger.warning("RequestValidationError: %s", exc.errors())
        return JSONResponse(
            status_code=400,
            content={"code": 400, "message": "参数校验失败", "detail": exc.errors()},
        )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(
        _request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        """HTTP 异常处理（404、405 等）。"""
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": exc.status_code, "message": exc.detail or "请求错误"},
        )

    @app.exception_handler(Exception)
    async def global_exception_handler(_request: Request, exc: Exception) -> JSONResponse:
        """全局兜底异常处理，捕获所有未处理的异常。"""
        logger.exception("Unhandled exception: %s", exc)
        return JSONResponse(
            status_code=500,
            content={"code": 500, "message": "服务器内部错误"},
        )


def _biz_exception_status_code(exc: BizException) -> int:
    """Return HTTP status for BizException classes that need non-200 transport."""

    if isinstance(exc, (RunRecoveryUnavailableError, RunToolReplayBlockedError)):
        return 409
    if isinstance(exc, RunCheckpointStoreUnavailableError):
        return 503
    return 200
