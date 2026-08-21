"""就绪状态聚合器属性测试。

使用 Hypothesis 对 ReadinessAggregator 的聚合行为进行属性测试，
验证聚合状态逻辑和结果完整性始终满足正确性属性。
"""

from typing import cast
from unittest.mock import AsyncMock

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from domain.health.aggregator import ReadinessAggregator
from domain.health.ports import HealthCheckPort
from domain.health.value_objects import (
    HealthCheckResult,
    HealthStatus,
    ReadinessResult,
)

# ── Hypothesis 生成策略 ──

health_status_st = st.sampled_from(HealthStatus)

name_st = st.text(min_size=1, max_size=50).filter(lambda s: s.strip() != "")

reason_st = st.one_of(st.none(), st.text(min_size=1, max_size=200))

health_check_result_st = st.builds(
    HealthCheckResult,
    name=name_st,
    status=health_status_st,
    reason=reason_st,
)

health_check_result_list_st = st.lists(health_check_result_st, min_size=0, max_size=10)


def _make_mock_check_port(result: HealthCheckResult) -> AsyncMock:
    """根据给定的 HealthCheckResult 创建 mock HealthCheckPort。

    Args:
        result: 该 mock 端口调用 check() 时应返回的检查结果

    Returns:
        配置好返回值的 AsyncMock 对象
    """
    mock = AsyncMock()
    mock.check.return_value = result
    return mock


# ── Property 1: 聚合状态等价于全部 UP ──
# Feature: readiness-probe, Property 1: 聚合状态等价于全部 UP


@settings(max_examples=100)
@given(results=health_check_result_list_st)
@pytest.mark.asyncio
async def test_aggregated_status_is_up_iff_all_checks_are_up(
    results: list[HealthCheckResult],
) -> None:
    """验证聚合状态为 UP 当且仅当所有检查结果均为 UP。

    对于任意随机生成的 HealthCheckResult 列表，ReadinessAggregator
    返回的整体状态应满足：全部 UP → 整体 UP，存在任一 DOWN → 整体 DOWN。
    空列表视为全部 UP（无 DOWN 项）。
    """
    mock_ports = [_make_mock_check_port(r) for r in results]
    aggregator = ReadinessAggregator(checks=cast(list[HealthCheckPort], mock_ports))

    readiness: ReadinessResult = await aggregator.check_readiness()

    all_up = all(r.status == HealthStatus.UP for r in results)
    if all_up:
        assert readiness.status == HealthStatus.UP
    else:
        assert readiness.status == HealthStatus.DOWN


# ── Property 2: 聚合结果包含所有检查项 ──
# Feature: readiness-probe, Property 2: 聚合结果包含所有检查项


@settings(max_examples=100)
@given(results=health_check_result_list_st)
@pytest.mark.asyncio
async def test_aggregated_result_contains_all_check_items(
    results: list[HealthCheckResult],
) -> None:
    """验证聚合结果包含所有检查项，数量和名称均完整。

    对于任意随机生成的 HealthCheckPort 实例列表，
    ReadinessAggregator.check_readiness() 返回的 checks 元组长度
    应等于输入的检查实例数量，且每个检查的 name 都出现在结果中。
    """
    mock_ports = [_make_mock_check_port(r) for r in results]
    aggregator = ReadinessAggregator(checks=cast(list[HealthCheckPort], mock_ports))

    readiness: ReadinessResult = await aggregator.check_readiness()

    assert len(readiness.checks) == len(results)

    expected_names = [r.name for r in results]
    actual_names = [c.name for c in readiness.checks]
    assert actual_names == expected_names
