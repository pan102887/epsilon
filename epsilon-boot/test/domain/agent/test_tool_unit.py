"""Tool 抽象体系单元测试模块。

包含异常层次结构的继承关系与属性验证，以及 ToolRegistry.execute
委托调用的集成测试。
"""

import json
from typing import Any

import pytest

from common.exceptions import BizException
from domain.agent.exceptions import (
    ToolExecutionError,
    ToolNotFoundError,
    ToolParameterValidationError,
)
from domain.agent.tools import Tool, ToolRegistry
from domain.model_access.value_objects import ToolCallRequest

# ── 测试用 FakeTool ──


class FakeTool(Tool):
    """用于单元测试的具体 Tool 实现。"""

    def __init__(
        self,
        tool_name: str = "fake_tool",
        tool_description: str = "A fake tool",
        tool_parameters: dict[str, Any] | None = None,
        execute_fn: Any = None,
    ):
        self._name = tool_name
        self._description = tool_description
        self._parameters = tool_parameters or {
            "type": "object",
            "properties": {},
        }
        self._execute_fn = execute_fn

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, **kwargs: Any) -> str:
        """执行工具逻辑，委托给注入的 execute_fn 或返回默认值。"""
        if self._execute_fn is not None:
            return await self._execute_fn(**kwargs)
        return "ok"


# ═══════════════════════════════════════════════════════════════
# 5.1 异常层次结构单元测试
# Requirements: 7.1, 7.2, 7.3, 7.4, 7.5
# ═══════════════════════════════════════════════════════════════


class TestToolExecutionError:
    """ToolExecutionError 异常测试。"""

    def test_inherits_from_biz_exception(self) -> None:
        """验证 ToolExecutionError 继承自 BizException。"""
        err = ToolExecutionError(message="boom", tool_name="my_tool")
        assert isinstance(err, BizException)
        assert isinstance(err, Exception)

    def test_default_code(self) -> None:
        """验证默认错误码为 60001。"""
        err = ToolExecutionError(message="boom", tool_name="my_tool")
        assert err.code == 60001

    def test_custom_code(self) -> None:
        """验证可自定义错误码。"""
        err = ToolExecutionError(message="boom", tool_name="my_tool", code=69999)
        assert err.code == 69999

    def test_message_and_tool_name(self) -> None:
        """验证 message 和 tool_name 属性正确存储。"""
        err = ToolExecutionError(message="执行失败", tool_name="search")
        assert err.message == "执行失败"
        assert err.tool_name == "search"
        assert str(err) == "执行失败"


class TestToolNotFoundError:
    """ToolNotFoundError 异常测试。"""

    def test_inherits_from_tool_execution_error(self) -> None:
        """验证 ToolNotFoundError → ToolExecutionError → BizException 继承链。"""
        err = ToolNotFoundError(tool_name="missing_tool")
        assert isinstance(err, ToolNotFoundError)
        assert isinstance(err, ToolExecutionError)
        assert isinstance(err, BizException)

    def test_code_is_60002(self) -> None:
        """验证错误码为 60002。"""
        err = ToolNotFoundError(tool_name="missing_tool")
        assert err.code == 60002

    def test_tool_name_stored(self) -> None:
        """验证 tool_name 属性正确存储。"""
        err = ToolNotFoundError(tool_name="missing_tool")
        assert err.tool_name == "missing_tool"

    def test_message_contains_tool_name(self) -> None:
        """验证 message 中包含工具名称。"""
        err = ToolNotFoundError(tool_name="weather")
        assert "weather" in err.message


class TestToolParameterValidationError:
    """ToolParameterValidationError 异常测试。"""

    def test_inherits_from_tool_execution_error(self) -> None:
        """验证 ToolParameterValidationError → ToolExecutionError → BizException 继承链。"""
        err = ToolParameterValidationError(tool_name="calc", errors=["缺少必填参数: x"])
        assert isinstance(err, ToolParameterValidationError)
        assert isinstance(err, ToolExecutionError)
        assert isinstance(err, BizException)

    def test_code_is_60003(self) -> None:
        """验证错误码为 60003。"""
        err = ToolParameterValidationError(tool_name="calc", errors=["err"])
        assert err.code == 60003

    def test_tool_name_and_errors_stored(self) -> None:
        """验证 tool_name 和 errors 属性正确存储。"""
        errors = ["缺少必填参数: x", "参数 y 类型不匹配"]
        err = ToolParameterValidationError(tool_name="calc", errors=errors)
        assert err.tool_name == "calc"
        assert err.errors == errors

    def test_message_contains_all_errors(self) -> None:
        """验证 message 中包含所有校验错误信息。"""
        errors = ["error_a", "error_b"]
        err = ToolParameterValidationError(tool_name="calc", errors=errors)
        for e in errors:
            assert e in err.message

    def test_empty_errors_list(self) -> None:
        """验证空 errors 列表也能正常构造。"""
        err = ToolParameterValidationError(tool_name="calc", errors=[])
        assert err.errors == []
        assert err.code == 60003


# ═══════════════════════════════════════════════════════════════
# 5.2 ToolRegistry.execute 委托调用集成测试
# Requirements: 6.6
# ═══════════════════════════════════════════════════════════════


class TestToolRegistryExecuteDelegation:
    """验证 ToolRegistry.execute 正确委托给 Tool.run 并传播异常。"""

    @pytest.mark.asyncio
    async def test_execute_delegates_to_tool_run_and_returns_result(self) -> None:
        """验证 execute 正确委托给 Tool.run 并返回结果。"""

        async def echo_execute(**kwargs: Any) -> str:
            return f"echo: {kwargs.get('msg', '')}"

        tool = FakeTool(
            tool_name="echo",
            tool_parameters={
                "type": "object",
                "properties": {"msg": {"type": "string"}},
                "required": ["msg"],
            },
            execute_fn=echo_execute,
        )
        registry = ToolRegistry()
        registry.register(tool)

        request = ToolCallRequest(
            id="call_1",
            name="echo",
            arguments=json.dumps({"msg": "hello"}),
        )
        result = await registry.execute(request)
        assert result == "echo: hello"

    @pytest.mark.asyncio
    async def test_execute_propagates_tool_execution_error(self) -> None:
        """验证 ToolExecutionError 从 Tool.run 正确传播到 Registry.execute。"""

        async def failing_execute(**kwargs: Any) -> str:
            raise ToolExecutionError(message="内部错误", tool_name="bad_tool")

        tool = FakeTool(tool_name="bad_tool", execute_fn=failing_execute)
        registry = ToolRegistry()
        registry.register(tool)

        request = ToolCallRequest(
            id="call_2",
            name="bad_tool",
            arguments="{}",
        )
        with pytest.raises(ToolExecutionError) as exc_info:
            await registry.execute(request)
        assert exc_info.value.tool_name == "bad_tool"
        assert exc_info.value.message == "内部错误"

    @pytest.mark.asyncio
    async def test_execute_propagates_parameter_validation_error(self) -> None:
        """验证参数校验失败时 ToolParameterValidationError 正确传播。"""
        tool = FakeTool(
            tool_name="strict",
            tool_parameters={
                "type": "object",
                "properties": {"x": {"type": "integer"}},
                "required": ["x"],
            },
        )
        registry = ToolRegistry()
        registry.register(tool)

        # 缺少必填参数 x
        request = ToolCallRequest(
            id="call_3",
            name="strict",
            arguments="{}",
        )
        with pytest.raises(ToolParameterValidationError) as exc_info:
            await registry.execute(request)
        assert exc_info.value.tool_name == "strict"
        assert len(exc_info.value.errors) > 0

    @pytest.mark.asyncio
    async def test_execute_wraps_unexpected_exception_from_tool(self) -> None:
        """验证 Tool.execute 抛出的非 ToolExecutionError 被包装后传播。"""

        async def raise_value_error(**kwargs: Any) -> str:
            raise ValueError("unexpected")

        tool = FakeTool(tool_name="crasher", execute_fn=raise_value_error)
        registry = ToolRegistry()
        registry.register(tool)

        request = ToolCallRequest(
            id="call_4",
            name="crasher",
            arguments="{}",
        )
        with pytest.raises(ToolExecutionError) as exc_info:
            await registry.execute(request)
        assert exc_info.value.tool_name == "crasher"
        # 应该是包装后的 ToolExecutionError，不是原始 ValueError
        assert not isinstance(exc_info.value, ToolParameterValidationError)
        assert not isinstance(exc_info.value, ToolNotFoundError)
