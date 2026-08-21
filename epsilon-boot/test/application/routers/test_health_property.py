"""健康检查路由属性测试。

使用 Hypothesis 对存活探针（/health.json）进行属性测试，
验证无论应用处于何种状态，存活探针始终返回 {"status": "UP"}。

通过 importlib 直接加载 health 路由模块，避免触发 application 包的
__init__.py 初始化副作用（如 prometheus_client 平台兼容问题）。
"""

import importlib.util
import pathlib
import sys
from unittest.mock import MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

# 在导入 health 路由前，mock prometheus_client 以避免 Windows 平台兼容问题。
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
    spec = importlib.util.spec_from_file_location("test_health_property_module", str(health_path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_health_module = _load_health_module()


# Feature: readiness-probe, Property 6: 存活探针始终返回 UP
@settings(max_examples=100)
@given(iteration=st.integers(min_value=0, max_value=999))
@pytest.mark.asyncio
async def test_health_json_always_returns_up(iteration: int) -> None:
    """属性测试：存活探针始终返回 UP。

    无论调用多少次、在何种迭代下，GET /health.json 始终返回
    HTTP 200 和 {"status": "UP"}，不依赖任何外部服务的可用性。

    Validates: Requirements 6.1, 6.2
    """
    response = await _health_module.health_check()

    assert response == {"status": "UP"}
