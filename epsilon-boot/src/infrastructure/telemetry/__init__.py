"""OpenTelemetry 可观测性模块。

提供 Traces、Metrics、Logs 的统一初始化入口，
以及 FastAPI、httpx、Redis、SQLAlchemy 等组件的自动埋点。
"""

from .otel_config import OtelConfig, otel_config
from .otel_setup import init_telemetry, shutdown_telemetry

__all__ = [
    "OtelConfig",
    "init_telemetry",
    "otel_config",
    "shutdown_telemetry",
]
