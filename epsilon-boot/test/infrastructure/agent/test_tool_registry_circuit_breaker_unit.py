"""ToolRegistry + circuit breaker 集成单元测试。

验证：
- registry 注入 breaker → execute 调用通过 guard 保护
- registry 未注入 breaker → 走原路径无 guard
"""

from typing import Any

import pytest

from domain.agent.exceptions import ToolCircuitOpenError, ToolExecutionError
from domain.agent.tools import Tool, ToolRegistry
from domain.model_access.value_objects import ToolCallRequest
from infrastructure.agent.circuit_breaker import ToolCircuitBreaker


class _FakeTool(Tool):
    """可控的 fake tool 用于测试。"""

    def __init__(self, name: str, fail: bool = False):
        self._name = name
        self._fail = fail

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "fake"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> str:
        if self._fail:
            raise RuntimeError("tool broken")
        return "ok"


def _make_request(tool_name: str) -> ToolCallRequest:
    return ToolCallRequest(id="call_1", name=tool_name, arguments="{}")


class TestRegistryWithBreaker:
    """注入 breaker 的 registry 行为。"""

    @pytest.mark.asyncio
    async def test_execute_goes_through_guard(self):
        """execute 经过 breaker guard，连续失败后被拒绝。"""
        breaker = ToolCircuitBreaker(failure_threshold=2, recovery_timeout=60.0)
        registry = ToolRegistry(circuit_breaker=breaker)
        registry.register(_FakeTool("broken", fail=True))

        req = _make_request("broken")
        # 失败 2 次
        for _ in range(2):
            with pytest.raises(ToolExecutionError):
                await registry.execute(req)

        # 第 3 次应被熔断
        with pytest.raises(ToolCircuitOpenError):
            await registry.execute(req)

    @pytest.mark.asyncio
    async def test_success_resets_counter(self):
        """成功调用重置失败计数。"""
        breaker = ToolCircuitBreaker(failure_threshold=3, recovery_timeout=60.0)
        registry = ToolRegistry(circuit_breaker=breaker)
        registry.register(_FakeTool("ok", fail=False))

        req = _make_request("ok")
        # 多次调用不触发熔断
        for _ in range(10):
            result = await registry.execute(req)
            assert result == "ok"


class TestRegistryWithoutBreaker:
    """未注入 breaker 的 registry 走快路径。"""

    @pytest.mark.asyncio
    async def test_no_breaker_direct_call(self):
        """无 breaker 时直接执行 tool.run。"""
        registry = ToolRegistry()  # 默认 circuit_breaker=None
        registry.register(_FakeTool("simple", fail=False))

        req = _make_request("simple")
        result = await registry.execute(req)
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_no_breaker_failure_passes_through(self):
        """无 breaker 时工具失败直接抛出，不会被熔断拦截。"""
        registry = ToolRegistry()
        registry.register(_FakeTool("fail", fail=True))

        req = _make_request("fail")
        # 即使多次失败也不会触发 ToolCircuitOpenError
        for _ in range(10):
            with pytest.raises(ToolExecutionError):
                await registry.execute(req)
