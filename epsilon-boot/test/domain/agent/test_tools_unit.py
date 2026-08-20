"""``ToolExecutionResult`` 值对象与 Tool 契约集成单元测试模块。

覆盖 structured-tool-result spec 需求 1 / 需求 2：

1. ``ToolExecutionResult`` 为 ``frozen=True`` 值对象，属性赋值后不可变更；
2. ``metadata`` 默认为独立的空 dict（``default_factory``，实例间不共享）；
3. ``content`` 与 ``metadata`` 可正常赋值并按值判等；
4. ``Tool.run()`` / ``ToolRegistry.execute()`` / ``ScopedToolRegistry.execute()``
   正常路径透传 ``ToolExecutionResult``，异常路径语义不变。

设计依据：design.md §2.1 / §2.2 / §2.3；需求 1.1–1.3、2.1–2.5。
"""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from domain.agent.exceptions import ToolPermissionDeniedError
from domain.agent.tools import (
    ScopedToolRegistry,
    Tool,
    ToolExecutionResult,
    ToolRegistry,
)
from domain.model_access.value_objects import ToolCallRequest


class _StructuredTool(Tool):
    """返回 ``ToolExecutionResult`` 的测试用 Tool 实现。"""

    def __init__(self, tool_name: str = "structured") -> None:
        self._name = tool_name

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "structured tool"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """回显参数并携带结构化 metadata。"""
        return ToolExecutionResult(
            content=f"echo:{kwargs.get('msg', '')}",
            metadata={"arg_count": len(kwargs)},
        )


# ═══════════════════════════════════════════════════════════════
# ToolExecutionResult 值对象
# 需求 1.1 / 1.2 / 1.3
# ═══════════════════════════════════════════════════════════════


class TestToolExecutionResultValueObject:
    """``ToolExecutionResult`` 值对象契约测试。"""

    def test_content_and_metadata_assignment(self) -> None:
        """content 与 metadata 正确存储。"""
        result = ToolExecutionResult(content="hello", metadata={"exit_code": 0})
        assert result.content == "hello"
        assert result.metadata == {"exit_code": 0}

    def test_metadata_defaults_to_empty_dict(self) -> None:
        """不传 metadata 时默认空 dict。"""
        result = ToolExecutionResult(content="only content")
        assert result.metadata == {}

    def test_metadata_default_not_shared_between_instances(self) -> None:
        """default_factory 保证不同实例的 metadata 互不共享。"""
        first = ToolExecutionResult(content="a")
        second = ToolExecutionResult(content="b")
        assert first.metadata is not second.metadata

    def test_is_frozen_content_immutable(self) -> None:
        """frozen dataclass：赋值 content 抛 FrozenInstanceError。"""
        result = ToolExecutionResult(content="x")
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.content = "y"  # type: ignore[misc]

    def test_is_frozen_metadata_field_immutable(self) -> None:
        """frozen dataclass：重新绑定 metadata 字段抛 FrozenInstanceError。"""
        result = ToolExecutionResult(content="x")
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.metadata = {"a": 1}  # type: ignore[misc]

    def test_value_equality(self) -> None:
        """相同 content 与 metadata 的实例按值相等。"""
        a = ToolExecutionResult(content="c", metadata={"k": 1})
        b = ToolExecutionResult(content="c", metadata={"k": 1})
        assert a == b

    def test_metadata_supports_heterogeneous_value_types(self) -> None:
        """metadata 允许异构值类型（int / str / bool / list）。"""
        result = ToolExecutionResult(
            content="c",
            metadata={
                "exit_code": 0,
                "path": "/src",
                "truncated": True,
                "targets": ["a", "b"],
            },
        )
        assert result.metadata["exit_code"] == 0
        assert result.metadata["path"] == "/src"
        assert result.metadata["truncated"] is True
        assert result.metadata["targets"] == ["a", "b"]


# ═══════════════════════════════════════════════════════════════
# Tool.run() / ToolRegistry / ScopedToolRegistry 透传契约
# 需求 2.2 / 2.4 / 2.5
# ═══════════════════════════════════════════════════════════════


class TestToolRunReturnsExecutionResult:
    """``Tool.run()`` 正常路径透传 ``ToolExecutionResult``。"""

    @pytest.mark.asyncio
    async def test_run_returns_execution_result(self) -> None:
        """run() 返回值为 ToolExecutionResult 且透传 execute() 的 content/metadata。"""
        tool = _StructuredTool()
        request = ToolCallRequest(
            id="c1", name="structured", arguments=json.dumps({"msg": "hi"})
        )
        result = await tool.run(request)
        assert isinstance(result, ToolExecutionResult)
        assert result.content == "echo:hi"
        assert result.metadata == {"arg_count": 1}


class TestRegistryExecuteReturnsExecutionResult:
    """``ToolRegistry.execute()`` 与 ``ScopedToolRegistry.execute()`` 透传契约。"""

    @pytest.mark.asyncio
    async def test_registry_execute_passes_through_execution_result(self) -> None:
        """ToolRegistry.execute() 透传底层 run() 的 ToolExecutionResult。"""
        registry = ToolRegistry()
        registry.register(_StructuredTool(tool_name="structured"))
        request = ToolCallRequest(
            id="c2", name="structured", arguments=json.dumps({"msg": "x"})
        )
        result = await registry.execute(request)
        assert isinstance(result, ToolExecutionResult)
        assert result.content == "echo:x"
        assert result.metadata == {"arg_count": 1}

    @pytest.mark.asyncio
    async def test_scoped_registry_execute_passes_through_when_allowed(self) -> None:
        """作用域内的工具，ScopedToolRegistry.execute() 透传 ToolExecutionResult。"""
        registry = ToolRegistry()
        registry.register(_StructuredTool(tool_name="structured"))
        scoped = registry.create_scoped_view(frozenset({"structured"}))
        request = ToolCallRequest(
            id="c3", name="structured", arguments=json.dumps({"msg": "y"})
        )
        result = await scoped.execute(request)
        assert isinstance(result, ToolExecutionResult)
        assert result.content == "echo:y"

    @pytest.mark.asyncio
    async def test_scoped_registry_execute_denies_out_of_scope(self) -> None:
        """作用域外的工具仍抛 ToolPermissionDeniedError（透传语义不改变异常行为）。"""
        registry = ToolRegistry()
        registry.register(_StructuredTool(tool_name="structured"))
        scoped: ScopedToolRegistry = registry.create_scoped_view(frozenset({"other"}))
        request = ToolCallRequest(
            id="c4", name="structured", arguments=json.dumps({"msg": "z"})
        )
        with pytest.raises(ToolPermissionDeniedError) as exc_info:
            await scoped.execute(request)
        assert exc_info.value.tool_name == "structured"
