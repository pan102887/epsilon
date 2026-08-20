"""Backward-compatible request logging config import."""

from application.api.middlewares.logging_config import (
    RequestLoggingConfig,
    ResponseLoggingConfig,
    request_logging_config,
    response_logging_config,
)

__all__ = [
    "RequestLoggingConfig",
    "ResponseLoggingConfig",
    "request_logging_config",
    "response_logging_config",
]
