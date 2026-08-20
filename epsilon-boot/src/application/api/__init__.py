"""FastAPI HTTP adapter package.

The package exposes ``app`` and ``service_config`` lazily so importing an
individual submodule such as ``application.api.server_config`` does not create
the FastAPI application as a side effect.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .server_app import app as app
    from .server_config import service_config as service_config

__all__ = ["app", "service_config"]


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    """Lazily expose package-level compatibility attributes."""
    if name == "app":
        from .server_app import app

        return app
    if name == "service_config":
        from .server_config import service_config

        return service_config
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
