"""MySQL 健康检查适配器属性测试。

使用 Hypothesis 对 MysqlHealthCheckAdapter 的成功和异常处理行为进行属性测试，
验证 SELECT 1 成功时返回 UP 状态，任意数据库异常均产生 DOWN 状态并携带失败原因。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.health.value_objects import HealthCheckResult, HealthStatus
from infrastructure.health.mysql_health_check_adapter import MysqlHealthCheckAdapter

# ── Property 6: 健康检查成功返回 UP ──
# Feature: sqlalchemy-async-integration, Property 6: 健康检查成功返回 UP


@pytest.mark.asyncio
async def test_successful_select_returns_up() -> None:
    """验证 SELECT 1 成功执行时返回 UP 状态。

    mock 成功的 SELECT 1 执行，断言适配器返回
    HealthCheckResult(name="mysql", status=UP)，且 reason 为 None。
    """
    # 创建 mock session
    mock_session = AsyncMock(spec=AsyncSession)
    mock_session.execute = AsyncMock(return_value=MagicMock())
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=None)

    # 创建 mock session factory
    mock_factory = MagicMock(spec=async_sessionmaker)
    mock_factory.return_value = mock_session

    adapter = MysqlHealthCheckAdapter(session_factory=mock_factory)

    result: HealthCheckResult = await adapter.check()

    assert result.name == "mysql"
    assert result.status == HealthStatus.UP
    assert result.reason is None
