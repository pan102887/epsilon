"""Backward-compatible server config import.

The HTTP adapter implementation lives in ``application.api.server_config``.
"""

from application.api.server_config import ServerConfig, service_config

__all__ = ["ServerConfig", "service_config"]
