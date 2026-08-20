"""健康检查 API presenter。"""

from __future__ import annotations

from domain.health.value_objects import HealthCheckResult, ReadinessResult


def _health_check_result_to_response_body(value: HealthCheckResult) -> dict[str, object]:
    """把单个健康检查结果映射为 readiness 响应中的嵌套字段。"""
    result: dict[str, object] = {"status": value.status.value}
    if value.reason is not None:
        result["reason"] = value.reason
    return result


def readiness_result_to_response_body(value: ReadinessResult) -> dict[str, object]:
    """把 readiness 领域结果映射为 HTTP 响应体。

    Args:
        value: 就绪探针聚合结果。

    Returns:
        包含整体 ``status`` 和按依赖名分组 ``checks`` 的 JSON-safe 字典。
    """
    return {
        "status": value.status.value,
        "checks": {
            check.name: _health_check_result_to_response_body(check) for check in value.checks
        },
    }
