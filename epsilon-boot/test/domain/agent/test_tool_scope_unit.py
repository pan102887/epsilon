"""工具作用域与权限校验单元测试模块。

包含 ToolPermissionDeniedError 异常的继承关系、属性验证和边界条件测试。
后续将扩展 ScopedToolRegistry 和 get_schemas 边界条件的单元测试。
"""

from typing import Any

from common.exceptions import BizException
from domain.agent.exceptions import (
    ToolExecutionError,
    ToolPermissionDeniedError,
)
from domain.agent.tools import ScopedToolRegistry, Tool, ToolRegistry

# ═══════════════════════════════════════════════════════════════
# 1.3 ToolPermissionDeniedError 继承关系和边界条件
# Requirements: 3.1, 3.2, 3.4
# ═══════════════════════════════════════════════════════════════


class TestToolPermissionDeniedError:
    """ToolPermissionDeniedError 异常继承关系和边界条件测试。"""

    def test_inherits_from_tool_execution_error(self) -> None:
        """验证 ToolPermissionDeniedError → ToolExecutionError → BizException 继承链。"""
        err = ToolPermissionDeniedError(
            tool_name="secret_tool",
            allowed_tools=frozenset({"tool_a", "tool_b"}),
        )
        assert isinstance(err, ToolPermissionDeniedError)
        assert isinstance(err, ToolExecutionError)
        assert isinstance(err, BizException)
        assert isinstance(err, Exception)

    def test_code_is_60004(self) -> None:
        """验证错误码固定为 60004。"""
        err = ToolPermissionDeniedError(
            tool_name="blocked",
            allowed_tools=frozenset({"allowed"}),
        )
        assert err.code == 60004

    def test_tool_name_stored(self) -> None:
        """验证 tool_name 属性正确存储被拒绝的工具名称。"""
        err = ToolPermissionDeniedError(
            tool_name="forbidden_tool",
            allowed_tools=frozenset({"a"}),
        )
        assert err.tool_name == "forbidden_tool"

    def test_allowed_tools_stored(self) -> None:
        """验证 allowed_tools 属性正确存储允许的工具集合。"""
        allowed = frozenset({"search", "calc", "weather"})
        err = ToolPermissionDeniedError(
            tool_name="hack",
            allowed_tools=allowed,
        )
        assert err.allowed_tools == allowed

    def test_message_contains_tool_name_and_allowed_tools(self) -> None:
        """验证 message 包含被拒绝的工具名称和所有允许的工具名称。"""
        allowed = frozenset({"alpha", "beta"})
        err = ToolPermissionDeniedError(
            tool_name="gamma",
            allowed_tools=allowed,
        )
        assert "gamma" in err.message
        assert "alpha" in err.message
        assert "beta" in err.message

    def test_empty_allowed_tools_message_contains_empty_marker(self) -> None:
        """验证 allowed_tools 为空 frozenset 时，message 包含 "(空)" 标记。"""
        err = ToolPermissionDeniedError(
            tool_name="any_tool",
            allowed_tools=frozenset(),
        )
        assert "(空)" in err.message
        assert "any_tool" in err.message
        assert err.allowed_tools == frozenset()

    def test_str_equals_message(self) -> None:
        """验证 str(error) 与 error.message 一致。"""
        err = ToolPermissionDeniedError(
            tool_name="test",
            allowed_tools=frozenset({"x"}),
        )
        assert str(err) == err.message


# ═══════════════════════════════════════════════════════════════
# 2.3 get_schemas 边界条件
# Requirements: 1.3, 1.4
# ═══════════════════════════════════════════════════════════════


class FakeTool(Tool):
    """用于测试的假工具实现。"""

    def __init__(
        self,
        tool_name: str = "fake_tool",
        tool_description: str = "A fake tool",
        tool_parameters: dict | None = None,
    ):
        self._name = tool_name
        self._description = tool_description
        self._parameters = tool_parameters or {"type": "object", "properties": {}}

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict:
        return self._parameters

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


class TestGetSchemasBoundaryConditions:
    """get_schemas 边界条件单元测试。

    验证 ToolRegistry.get_schemas() 在空集合和全部未注册名称等边界场景下的行为。
    """

    def test_empty_set_returns_empty_list(self) -> None:
        """验证传入空 set 时返回空列表，即使注册表中有工具。"""
        registry = ToolRegistry()
        registry.register(FakeTool(tool_name="alpha"))
        registry.register(FakeTool(tool_name="beta"))

        result = registry.get_schemas(tool_names=set())

        assert result == []

    def test_all_unregistered_names_returns_empty_list(self) -> None:
        """验证 tool_names 全部为未注册名称时返回空列表。"""
        registry = ToolRegistry()
        registry.register(FakeTool(tool_name="alpha"))
        registry.register(FakeTool(tool_name="beta"))

        result = registry.get_schemas(tool_names={"nonexistent_a", "nonexistent_b"})

        assert result == []


# ═══════════════════════════════════════════════════════════════
# 3.5 ScopedToolRegistry 创建返回正确类型
# Requirements: 2.1
# ═══════════════════════════════════════════════════════════════


class TestScopedToolRegistryCreation:
    """ScopedToolRegistry 创建单元测试。

    验证 ToolRegistry.create_scoped_view() 返回 ScopedToolRegistry 实例。
    """

    def test_create_scoped_view_returns_scoped_tool_registry_instance(self) -> None:
        """验证 create_scoped_view 返回 ScopedToolRegistry 实例。"""
        registry = ToolRegistry()
        registry.register(FakeTool(tool_name="tool_a"))
        registry.register(FakeTool(tool_name="tool_b"))

        scoped = registry.create_scoped_view(frozenset({"tool_a"}))

        assert isinstance(scoped, ScopedToolRegistry)
