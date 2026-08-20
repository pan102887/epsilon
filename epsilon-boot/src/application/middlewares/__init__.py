"""Backward-compatible middleware imports.

The HTTP adapter implementation lives in ``application.api.middlewares``.
"""

from application.api.middlewares import (
    RequestLoggingConfig,
    RequestLoggingMiddleware,
    request_logging_config,
)

__all__ = ["RequestLoggingConfig", "RequestLoggingMiddleware", "request_logging_config"]
