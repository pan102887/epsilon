"""Backward-compatible task router import."""

from application.api.routers.task import (
    TaskContinueRequestBody,
    TaskExecuteRequestBody,
    TaskExecuteResponseBody,
    TraceEntryBody,
    continue_task,
    execute_task,
    router,
)

__all__ = [
    "TaskContinueRequestBody",
    "TaskExecuteRequestBody",
    "TaskExecuteResponseBody",
    "TraceEntryBody",
    "continue_task",
    "execute_task",
    "router",
]
