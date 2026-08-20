"""FastAPI 应用实例创建与初始化模块。

负责创建全局 ``app`` 实例，并按顺序完成：
1. 通过 DI 容器的 lifespan 管理异步资源的启动与关闭
2. 注册统一异常处理器
3. 挂载业务路由

其他模块通过 ``from application.api import app`` 获取应用实例。
"""

import logging

from fastapi import FastAPI

from application.container_config import configure_container
from common.container import container
from infrastructure.telemetry.otel_setup import instrument_fastapi_app

from .exception_handlers import register_exception_handlers
from .middlewares import RequestLoggingMiddleware
from .routers import (
    artifacts_router,
    chat_router,
    health_router,
    models_router,
    runs_router,
    task_router,
    test_router,
    traces_router,
)

logger = logging.getLogger(__name__)

# 注册所有依赖和异步资源到容器
configure_container()

app = FastAPI(lifespan=container.lifespan)

# OpenTelemetry FastAPI 自动埋点（需要 app 实例，在中间件注册前执行）
instrument_fastapi_app(app)

# 注册请求日志中间件（ASGI middleware 注册顺序：后注册的先执行，日志中间件应最外层）
app.add_middleware(RequestLoggingMiddleware)

# 注册统一异常处理
register_exception_handlers(app)

# 注册路由
app.include_router(health_router)
app.include_router(test_router)
app.include_router(chat_router)
app.include_router(task_router)
app.include_router(runs_router)
app.include_router(models_router)
app.include_router(traces_router)
app.include_router(artifacts_router)
