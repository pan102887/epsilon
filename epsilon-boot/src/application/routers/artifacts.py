"""Backward-compatible artifact router import."""

from application.api.routers.artifacts import list_artifacts, router

__all__ = ["list_artifacts", "router"]
