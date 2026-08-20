"""Backward-compatible exception handler import.

The HTTP adapter implementation lives in ``application.api.exception_handlers``.
"""

from application.api.exception_handlers import register_exception_handlers

__all__ = ["register_exception_handlers"]
