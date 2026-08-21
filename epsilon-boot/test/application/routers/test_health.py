"""健康检查路由单元测试。

针对 health.py 路由模块中的存活探针（/health.json）和就绪探针（/readiness）
编写单元测试。通过 FastAPI dependency_overrides 机制替换 ReadinessAggregator
的 DI 注入，使用 mock 对象控制聚合器返回值，验证路由的 HTTP 状态码和响应体。

由于 ``src/application/__init__.py`` 会触发 server_app 的完整初始化链
（包含 prometheus_client 等平台相关依赖），测试中通过预先 mock
prometheus_client 模块并使用 importlib 直接加载 health 路由模块，
避免触发 application 包的 __init__.py 初始化副作用。
"""

import importlib.util
import json
import pathlib
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.health.aggregator import ReadinessAggregator
from domain.health.value_objects import (
    HealthCheckResult,
    HealthStatus,
    ReadinessResult,
)

# 在导入 health 路由前，mock prometheus_client 以避免 Windows 平台兼容问题。
# prometheus_client 在 Windows 上导入时可能因 resource.getpagesize() 不可用而失败，
# 但路由测试不需要 Prometheus 指标功能。
if "prometheus_client" not in sys.modules:
    _mock_prom = MagicMock()
    _mock_prom.CONTENT_TYPE_LATEST = "text/plain"
    _mock_prom.generate_latest = MagicMock(return_value=b"")
    sys.modules["prometheus_client"] = _mock_prom


def _load_health_module():
    """直接加载 health 路由模块，绕过 application 包的 __init__.py。

    使用 importlib 从文件路径加载 ``src/application/routers/health.py``，
    避免触发 ``application/__init__.py`` 中 server_app 的完整初始化链。

    Returns:
        health 路由模块对象
    """
    health_path = (
        pathlib.Path(__file__).resolve().parents[3]
        / "src"
        / "application"
        / "routers"
        / "health.py"
    )
    spec = importlib.util.spec_from_file_location("test_health_module", str(health_path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_health_module = _load_health_module()
router = _health_module.router


# ── GET /readiness 返回 200（全部 UP） ──


@pytest.mark.asyncio
async def test_readiness_all_up_returns_200() -> None:
    """验证所有依赖检查均为 UP 时，/readiness 返回 HTTP 200 和正确的响应体。"""
    mock_agg = AsyncMock(spec=ReadinessAggregator)
    mock_agg.check_readiness.return_value = ReadinessResult(
        status=HealthStatus.UP,
        checks=(HealthCheckResult(name="redis", status=HealthStatus.UP),),
    )

    response = await _health_module.readiness_check(mock_agg)

    assert response.status_code == 200
    assert json.loads(response.body) == {
        "status": "UP",
        "checks": {
            "redis": {"status": "UP"},
        },
    }
    mock_agg.check_readiness.assert_awaited_once()


# ── GET /readiness 返回 503（存在 DOWN） ──


@pytest.mark.asyncio
async def test_readiness_any_down_returns_503() -> None:
    """验证任意依赖检查为 DOWN 时，/readiness 返回 HTTP 503 和失败原因。"""
    mock_agg = AsyncMock(spec=ReadinessAggregator)
    mock_agg.check_readiness.return_value = ReadinessResult(
        status=HealthStatus.DOWN,
        checks=(
            HealthCheckResult(
                name="redis",
                status=HealthStatus.DOWN,
                reason="Connection refused",
            ),
        ),
    )

    response = await _health_module.readiness_check(mock_agg)

    assert response.status_code == 503
    assert json.loads(response.body) == {
        "status": "DOWN",
        "checks": {
            "redis": {
                "status": "DOWN",
                "reason": "Connection refused",
            },
        },
    }
    mock_agg.check_readiness.assert_awaited_once()


# ── GET /readiness 多依赖混合状态 ──


@pytest.mark.asyncio
async def test_readiness_mixed_checks_returns_503() -> None:
    """验证多个依赖中存在 DOWN 时，整体返回 503 且包含所有检查项。"""
    mock_agg = AsyncMock(spec=ReadinessAggregator)
    mock_agg.check_readiness.return_value = ReadinessResult(
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

    response = await _health_module.readiness_check(mock_agg)

    assert response.status_code == 503
    assert json.loads(response.body) == {
        "status": "DOWN",
        "checks": {
            "redis": {"status": "UP"},
            "database": {
                "status": "DOWN",
                "reason": "Connection timeout",
            },
        },
    }


# ── GET /health.json 保持不变 ──


@pytest.mark.asyncio
async def test_health_json_always_returns_up() -> None:
    """验证 /health.json 存活探针始终返回 HTTP 200 和 {"status": "UP"}。

    存活探针不依赖任何外部服务，无论就绪状态如何都应返回 UP。
    """
    response = await _health_module.health_check()

    assert response == {"status": "UP"}


@pytest.mark.asyncio
async def test_health_json_independent_of_readiness() -> None:
    """验证即使就绪探针为 DOWN，/health.json 仍返回 UP。

    确认存活探针与就绪探针职责分离，互不影响。
    """
    mock_agg = AsyncMock(spec=ReadinessAggregator)
    mock_agg.check_readiness.return_value = ReadinessResult(
        status=HealthStatus.DOWN,
        checks=(
            HealthCheckResult(
                name="redis",
                status=HealthStatus.DOWN,
                reason="Redis is down",
            ),
        ),
    )

    response = await _health_module.health_check()

    assert response == {"status": "UP"}
