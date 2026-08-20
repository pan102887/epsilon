"""Backward-compatible router imports.

The HTTP adapter implementation lives in ``application.api.routers``.
"""

from application.api.routers import (
    artifacts_router,
    chat_router,
    health_router,
    models_router,
    runs_router,
    task_router,
    test_router,
    traces_router,
)

__all__ = [
    "chat_router",
    "artifacts_router",
    "health_router",
    "models_router",
    "runs_router",
    "task_router",
    "test_router",
    "traces_router",
]
