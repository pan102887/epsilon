"""ToolRegistry 属性测试模块。

使用 Hypothesis 对 ToolRegistry 的注册、查找、移除、schema 生成和执行方法
进行属性测试，验证注册表行为的正确性和一致性。

测试通过 FakeTool 具体子类驱动，配合 Hypothesis @st.composite 策略
生成随机的工具集合和调用请求。
"""

import json
from typing import Any

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from domain.agent.exceptions import ToolNotFoundError
from domain.agent.tools import Tool, ToolRegistry
from domain.model_access.value_objects import ToolCallRequest

# ── 参数名称和工具名称策略 ──

tool_name_st = st.from_regex(r"[a-z][a-z0-9_]{1,20}", fullmatch=True)


# ── FakeTool：用于驱动属性测试的具体 Tool 子类 ──


class FakeTool(Tool):
    """用于 ToolRegistry 属性测试的具体 Tool 实现。

    允许在构造时注入 name、description、parameters 和 execute 行为，
    以便 Hypothesis 生成随机配置的工具实例。
    """

    def __init__(
        self,
        tool_name: str = "fake_tool",
        tool_description: str = "A fake tool for testing",
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


# ── Hypothesis 策略 ──


@st.composite
def unique_fake_tools_st(draw: st.DrawFn) -> list[FakeTool]:
    """生成一组名称唯一的 FakeTool 实例。

    每个工具拥有随机生成的唯一名称和描述，用于测试 ToolRegistry 的批量操作。

    Returns:
        名称互不相同的 FakeTool 列表。
    """
    num_tools = draw(st.integers(min_value=1, max_value=6))
    names = draw(st.lists(tool_name_st, min_size=num_tools, max_size=num_tools, unique=True))
    tools: list[FakeTool] = []
    for n in names:
        desc = draw(st.text(min_size=1, max_size=30))
        tools.append(FakeTool(tool_name=n, tool_description=desc))
    return tools


# ── Property 8: ToolRegistry 注册与查找一致性 ──
# Feature: tool-abstraction, Property 8: ToolRegistry 注册与查找一致性


@settings(max_examples=100)
@given(tools=unique_fake_tools_st())
def test_registry_get_and_has_consistent_after_register(
    tools: list[FakeTool],
) -> None:
    """验证注册工具后 get/has 行为与注册状态一致。

    对于任意一组名称唯一的工具，注册后：
    - has(name) 对所有已注册名称返回 True
    - get(name) 对所有已注册名称返回对应的 Tool 实例
    - has(name) 对未注册名称返回 False
    - get(name) 对未注册名称返回 None
    """
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)

    # 已注册的工具应可查找
    for tool in tools:
        assert registry.has(tool.name), f"已注册工具 {tool.name} 应 has() 返回 True"
        assert registry.get(tool.name) is tool, f"get({tool.name}) 应返回注册的实例"

    # 未注册的名称应查找不到
    unregistered = "__not_registered__"
    assert not registry.has(unregistered), "未注册名称应 has() 返回 False"
    assert registry.get(unregistered) is None, "未注册名称应 get() 返回 None"


# ── Property 9: ToolRegistry unregister 移除工具 ──
# Feature: tool-abstraction, Property 9: ToolRegistry unregister 移除工具


@settings(max_examples=100)
@given(tools=unique_fake_tools_st())
def test_registry_unregister_removes_tool(
    tools: list[FakeTool],
) -> None:
    """验证 unregister 后工具不再可查找。

    对于任意已注册的工具，调用 unregister(name) 后：
    - has(name) 返回 False
    - get(name) 返回 None
    其余工具不受影响。
    """
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)

    # 移除第一个工具
    removed = tools[0]
    registry.unregister(removed.name)

    assert not registry.has(removed.name), f"unregister 后 has({removed.name}) 应返回 False"
    assert registry.get(removed.name) is None, f"unregister 后 get({removed.name}) 应返回 None"

    # 其余工具不受影响
    for tool in tools[1:]:
        assert registry.has(tool.name), f"未移除的工具 {tool.name} 应仍可查找"
        assert registry.get(tool.name) is tool


# ── Property 10: ToolRegistry get_schemas 与注册工具一致 ──
# Feature: tool-abstraction, Property 10: ToolRegistry get_schemas 与注册工具一致


@settings(max_examples=100)
@given(tools=unique_fake_tools_st())
def test_registry_get_schemas_matches_registered_tools(
    tools: list[FakeTool],
) -> None:
    """验证 get_schemas 返回的列表与注册工具的 to_schema 输出一致。

    对于任意已注册的工具集合：
    - get_schemas() 列表长度等于已注册工具数量
    - 列表中每个 schema 都能在某个已注册工具的 to_schema() 输出中找到匹配
    """
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)

    schemas = registry.get_schemas()

    assert len(schemas) == len(tools), (
        f"get_schemas 长度 {len(schemas)} 应等于注册工具数 {len(tools)}"
    )

    expected = {tool.name: tool.to_schema() for tool in tools}
    for schema in schemas:
        fn_name = schema["function"]["name"]
        assert fn_name in expected, f"schema 中的工具名 {fn_name} 应在已注册工具中"
        assert schema == expected[fn_name], f"工具 {fn_name} 的 schema 应与 to_schema() 输出一致"


# ── Property 11: ToolRegistry execute 对未注册工具抛出 ToolNotFoundError ──
# Feature: tool-abstraction, Property 11: ToolRegistry execute 对未注册工具抛出 ToolNotFoundError


@settings(max_examples=100)
@given(
    registered_names=st.lists(tool_name_st, min_size=0, max_size=4, unique=True),
    unregistered_name=tool_name_st,
)
@pytest.mark.asyncio
async def test_registry_execute_raises_tool_not_found_for_unregistered(
    registered_names: list[str],
    unregistered_name: str,
) -> None:
    """验证 execute 对未注册工具名称抛出 ToolNotFoundError。

    构造一个包含若干已注册工具的 ToolRegistry，用一个不在注册表中的名称
    构造 ToolCallRequest，验证 execute 抛出 ToolNotFoundError 且
    tool_name 属性等于请求的工具名称。
    """
    # 确保 unregistered_name 不在已注册名称中
    if unregistered_name in registered_names:
        return  # 跳过名称冲突的情况

    registry = ToolRegistry()
    for name in registered_names:
        registry.register(FakeTool(tool_name=name))

    request = ToolCallRequest(
        id="test-registry",
        name=unregistered_name,
        arguments=json.dumps({}),
    )

    with pytest.raises(ToolNotFoundError) as exc_info:
        await registry.execute(request)

    assert exc_info.value.tool_name == unregistered_name
