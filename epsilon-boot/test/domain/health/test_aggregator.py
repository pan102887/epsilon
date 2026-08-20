"""就绪状态聚合器单元测试。

针对 ReadinessAggregator 的具体示例和边界情况编写单元测试，
覆盖空检查列表、全部 UP、存在 DOWN 三种典型场景。
"""

from unittest.mock import AsyncMock

import pytest

from domain.health.aggregator import ReadinessAggregator
from domain.health.value_objects import HealthCheckResult, HealthStatus


def _make_mock_port(result: HealthCheckResult) -> AsyncMock:
    """根据给定的 HealthCheckResult 创建 mock HealthCheckPort。

    Args:
        result: 该 mock 端口调用 check() 时应返回的检查结果

    Returns:
        配置好返回值的 AsyncMock 对象
    """
    mock = AsyncMock()
    mock.check.return_value = result
    return mock


@pytest.mark.asyncio
async def test_empty_checks_returns_up() -> None:
    """空检查列表聚合应返回整体状态 UP，checks 为空元组。

    当没有任何已注册的健康检查端口时，不存在 DOWN 项，
    聚合器应视为全部通过，返回 UP 状态。
    """
    aggregator = ReadinessAggregator(checks=[])

    result = await aggregator.check_readiness()

    assert result.status == HealthStatus.UP
    assert result.checks == ()


@pytest.mark.asyncio
async def test_all_up_returns_up() -> None:
    """所有检查均为 UP 时，聚合结果整体状态应为 UP。

    模拟两个依赖（redis、database）均返回 UP，
    验证聚合器正确返回 UP 并包含所有逐项结果。
    """
    redis_result = HealthCheckResult(name="redis", status=HealthStatus.UP)
    db_result = HealthCheckResult(name="database", status=HealthStatus.UP)
    ports = [_make_mock_port(redis_result), _make_mock_port(db_result)]
    aggregator = ReadinessAggregator(checks=ports)

    result = await aggregator.check_readiness()

    assert result.status == HealthStatus.UP
    assert len(result.checks) == 2
    assert result.checks[0].name == "redis"
    assert result.checks[0].status == HealthStatus.UP
    assert result.checks[1].name == "database"
    assert result.checks[1].status == HealthStatus.UP


@pytest.mark.asyncio
async def test_any_down_returns_down() -> None:
    """存在任一 DOWN 检查时，聚合结果整体状态应为 DOWN。

    模拟 redis 返回 UP、database 返回 DOWN（携带失败原因），
    验证聚合器返回 DOWN 并保留所有逐项结果（包括 UP 和 DOWN 项）。
    """
    redis_result = HealthCheckResult(name="redis", status=HealthStatus.UP)
    db_result = HealthCheckResult(
        name="database", status=HealthStatus.DOWN, reason="Connection refused"
    )
    ports = [_make_mock_port(redis_result), _make_mock_port(db_result)]
    aggregator = ReadinessAggregator(checks=ports)

    result = await aggregator.check_readiness()

    assert result.status == HealthStatus.DOWN
    assert len(result.checks) == 2
    assert result.checks[0].name == "redis"
    assert result.checks[0].status == HealthStatus.UP
    assert result.checks[1].name == "database"
    assert result.checks[1].status == HealthStatus.DOWN
    assert result.checks[1].reason == "Connection refused"
