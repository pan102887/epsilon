"""健康检查 API presenter 单元测试。"""

from application.api.presenters.health_presenter import readiness_result_to_response_body
from domain.health.value_objects import HealthCheckResult, HealthStatus, ReadinessResult


def test_readiness_result_to_response_body_preserves_up_body() -> None:
    """验证 UP readiness 响应体保持既有线格式。"""
    result = ReadinessResult(
        status=HealthStatus.UP,
        checks=(HealthCheckResult(name="redis", status=HealthStatus.UP),),
    )

    assert readiness_result_to_response_body(result) == {
        "status": "UP",
        "checks": {
            "redis": {"status": "UP"},
        },
    }


def test_readiness_result_to_response_body_preserves_down_reason_body() -> None:
    """验证 DOWN readiness 响应体保留 checks 嵌套 reason。"""
    result = ReadinessResult(
        status=HealthStatus.DOWN,
        checks=(
            HealthCheckResult(name="redis", status=HealthStatus.UP),
            HealthCheckResult(
                name="database",
                status=HealthStatus.DOWN,
                reason="Connection timeout",
            ),
        ),
    )

    assert readiness_result_to_response_body(result) == {
        "status": "DOWN",
        "checks": {
            "redis": {"status": "UP"},
            "database": {
                "status": "DOWN",
                "reason": "Connection timeout",
            },
        },
    }
