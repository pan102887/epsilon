"""health 序列化映射器的字面快照等价性测试。

对 ``infrastructure.health.health_serialization`` 的两个映射函数写字面
快照断言，覆盖 ``reason=None`` / 有 ``reason``、多 check 嵌套等边界，
锁定线格式行为等价。
"""

from __future__ import annotations

from domain.health.value_objects import (
    HealthCheckResult,
    HealthStatus,
    ReadinessResult,
)
from infrastructure.health.health_serialization import (
    health_check_result_to_dict,
    readiness_result_to_dict,
)


def test_health_check_result_up_without_reason() -> None:
    """UP 且 reason=None 时仅输出 status 键。"""
    result = HealthCheckResult(name="redis", status=HealthStatus.UP)
    assert health_check_result_to_dict(result) == {"status": "UP"}


def test_health_check_result_down_with_reason() -> None:
    """DOWN 且有 reason 时追加 reason 键。"""
    result = HealthCheckResult(
        name="mysql", status=HealthStatus.DOWN, reason="connection refused"
    )
    assert health_check_result_to_dict(result) == {
        "status": "DOWN",
        "reason": "connection refused",
    }


def test_readiness_result_multiple_checks_nested() -> None:
    """多 check 嵌套时按依赖名聚合各逐项结果。"""
    redis = HealthCheckResult(name="redis", status=HealthStatus.UP)
    mysql = HealthCheckResult(
        name="mysql", status=HealthStatus.DOWN, reason="timeout"
    )
    result = ReadinessResult(
        status=HealthStatus.DOWN, checks=(redis, mysql)
    )
    assert readiness_result_to_dict(result) == {
        "status": "DOWN",
        "checks": {
            "redis": {"status": "UP"},
            "mysql": {"status": "DOWN", "reason": "timeout"},
        },
    }


def test_readiness_result_empty_checks() -> None:
    """无 check 时 checks 为空字典。"""
    result = ReadinessResult(status=HealthStatus.UP, checks=())
    assert readiness_result_to_dict(result) == {"status": "UP", "checks": {}}
