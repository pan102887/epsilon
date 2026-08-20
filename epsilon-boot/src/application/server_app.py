"""Backward-compatible FastAPI app import.

The HTTP adapter implementation lives in ``application.api.server_app``.
"""

from application.api.server_app import app

__all__ = ["app"]
