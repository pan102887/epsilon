"""Backward-compatible health router import."""

from application.api.routers.health import (
    health_check,
    prometheus_metrics,
    readiness_check,
    router,
)

__all__ = ["health_check", "prometheus_metrics", "readiness_check", "router"]
