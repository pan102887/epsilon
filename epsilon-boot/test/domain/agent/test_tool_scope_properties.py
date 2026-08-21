"""工具作用域与权限校验属性测试模块。

使用 Hypothesis 对工具作用域与权限校验相关组件进行属性测试，验证在任意有效输入下，
核心不变量始终成立。包括 ToolPermissionDeniedError 构造完整性、get_schemas 过滤、
ScopedToolRegistry 作用域隔离、AgentConfig allowed_tool_names 自动提取等属性。

测试文件对应设计文档中定义的正确性属性（Correctness Properties），
每个属性测试通过注释标注对应的设计属性编号和验证的需求编号。
"""

import string
from typing import Any

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from domain.agent.exceptions import ToolPermissionDeniedError
from domain.agent.tools import ScopedToolRegistry, Tool, ToolExecutionResult, ToolRegistry
from domain.agent.value_objects import AgentConfig
from domain.model_access.value_objects import ToolCallRequest

# ── Hypothesis 生成策略 ──

# 工具名称策略：生成 1-10 个小写字母组成的字符串
tool_name_st = st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=10)

# 允许工具集合策略：生成 0-5 个工具名称的 frozenset
allowed_tools_st = st.frozensets(
    st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=10),
    max_size=5,
)


class TestToolPermissionDeniedErrorProperties:
    """ToolPermissionDeniedError 构造完整性属性测试类。

    验证在任意有效的 tool_name 和 allowed_tools 输入下，
    ToolPermissionDeniedError 的构造结果满足所有不变量。
    """

    @given(tool_name=tool_name_st, allowed_tools=allowed_tools_st)
    @settings(max_examples=100, deadline=5000)
    def test_permission_denied_error_construction(
        self, tool_name: str, allowed_tools: frozenset[str]
    ) -> None:
        """属性测试：ToolPermissionDeniedError 构造完整性。

        **Validates: Requirements 3.2, 3.3, 3.4**

        Property 4: 对任意 tool_name 和 allowed_tools frozenset，
        构造 ToolPermissionDeniedError 后，验证：
        1. error.code == 60004
        2. error.tool_name == tool_name
        3. error.allowed_tools == allowed_tools
        4. allowed_tools 中每个工具名称都出现在 error.message 中
        5. tool_name 出现在 error.message 中
        """
        error = ToolPermissionDeniedError(tool_name=tool_name, allowed_tools=allowed_tools)

        # 验证错误码
        assert error.code == 60004

        # 验证 tool_name 属性
        assert error.tool_name == tool_name

        # 验证 allowed_tools 属性
        assert error.allowed_tools == allowed_tools

        # 验证 message 包含 tool_name
        assert tool_name in error.message

        # 验证 message 包含 allowed_tools 中的每个工具名称
        for name in allowed_tools:
            assert name in error.message


# ── 属性测试用 FakeTool ──


class FakeTool(Tool):
    """用于属性测试的具体 Tool 实现。

    仅实现 Tool 抽象基类的必要接口，用于在属性测试中快速构造
    可注册到 ToolRegistry 的工具实例。
    """

    def __init__(self, tool_name: str, tool_description: str = "fake") -> None:
        self._name = tool_name
        self._description = tool_description

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        return ToolExecutionResult(content="ok")


class TestGetSchemasFilteringProperties:
    """get_schemas 按名称子集过滤属性测试类。

    验证在任意有效的 ToolRegistry 和 tool_names 参数下，
    get_schemas 的过滤行为满足所有不变量。
    """

    @given(
        tool_names=st.lists(
            st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=8),
            min_size=1,
            max_size=5,
            unique=True,
        ),
        extra_names=st.lists(
            st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=8),
            min_size=0,
            max_size=3,
            unique=True,
        ),
        use_none=st.booleans(),
        use_empty=st.booleans(),
    )
    @settings(max_examples=100, deadline=5000)
    def test_get_schemas_filtering(
        self,
        tool_names: list[str],
        extra_names: list[str],
        use_none: bool,
        use_empty: bool,
    ) -> None:
        """属性测试：get_schemas 按名称子集过滤。

        **Validates: Requirements 1.1, 1.2, 1.3, 1.4**

        Property 1: 对任意 ToolRegistry（包含任意数量的已注册工具）和任意
        tool_names 参数（None 或 set[str]），get_schemas(tool_names) 返回的
        schema 列表应满足：
        1. None 返回所有已注册工具的 schema
        2. set 返回过滤结果：每个返回 schema 的 function.name 在 tool_names 中，
           且所有在 tool_names 中且已注册的工具都出现在返回列表中
        3. 空 set 返回空列表
        """
        # 构造 ToolRegistry 并注册工具
        registry = ToolRegistry()
        registered_names: set[str] = set()
        for name in tool_names:
            registry.register(FakeTool(tool_name=name))
            registered_names.add(name)

        if use_empty:
            # 验证空 set 返回空列表
            result = registry.get_schemas(tool_names=set())
            assert result == []
            return

        if use_none:
            # 验证 None 返回所有已注册工具的 schema
            result = registry.get_schemas(tool_names=None)
            result_names = {s["function"]["name"] for s in result}
            assert result_names == registered_names
            assert len(result) == len(registered_names)
            return

        # 构造 tool_names 子集：从已注册名称中随机选取 + 可能包含未注册名称
        # extra_names 中去除已注册的名称，确保它们是"未注册"的
        unregistered = {n for n in extra_names if n not in registered_names}
        # 取已注册名称的一个子集（至少包含部分）
        subset = set(tool_names[: len(tool_names) // 2 + 1])
        query_names = subset | unregistered

        result = registry.get_schemas(tool_names=query_names)
        result_names = {s["function"]["name"] for s in result}

        # 每个返回的 schema 的 function.name 都在 query_names 中
        for schema in result:
            assert schema["function"]["name"] in query_names

        # 所有在 query_names 中且已注册的工具都出现在返回列表中
        expected = registered_names & query_names
        assert result_names == expected

        # 未注册的名称不应出现在结果中（静默忽略）
        for name in unregistered:
            assert name not in result_names


class TestScopedRegistryGetSchemasProperties:
    """ScopedToolRegistry get_schemas 作用域隔离与快照语义属性测试类。

    验证在任意有效的 ToolRegistry 和工具名称子集下，通过 create_scoped_view
    创建的 ScopedToolRegistry 的 get_schemas() 满足：
    1. 仅返回作用域内已注册工具的 schema
    2. 创建后向底层 ToolRegistry 注册新工具不影响已创建视图（快照语义）
    """

    @given(
        tool_names=st.lists(
            st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=8),
            min_size=2,
            max_size=5,
            unique=True,
        ),
    )
    @settings(max_examples=100, deadline=5000)
    def test_scoped_registry_get_schemas_snapshot(self, tool_names: list[str]) -> None:
        """属性测试：ScopedToolRegistry get_schemas 作用域隔离与快照语义。

        **Validates: Requirements 2.3, 2.7**

        Property 2: 对任意 ToolRegistry 和任意工具名称子集，通过
        create_scoped_view 创建的 ScopedToolRegistry 的 get_schemas() 应满足：
        1. 仅返回 scope 中已注册工具的 schema
        2. 创建 ScopedToolRegistry 后向底层 ToolRegistry 注册新工具，
           新工具不出现在 scoped get_schemas() 结果中（快照语义）
        """
        # 1. 构造 ToolRegistry 并注册工具
        registry = ToolRegistry()
        for name in tool_names:
            registry.register(FakeTool(tool_name=name))

        # 2. 选取前半部分作为 scope 子集
        scope_names = frozenset(tool_names[: len(tool_names) // 2 + 1])

        # 3. 创建 ScopedToolRegistry
        scoped = registry.create_scoped_view(scope_names)
        assert isinstance(scoped, ScopedToolRegistry)

        # 4. 验证 get_schemas() 仅返回 scope 内工具的 schema
        schemas = scoped.get_schemas()
        result_names = {s["function"]["name"] for s in schemas}

        # scope 中已注册的工具应全部出现
        expected = scope_names & set(tool_names)
        assert result_names == expected

        # 每个返回的 schema 的 function.name 都在 scope 中
        for schema in schemas:
            assert schema["function"]["name"] in scope_names

        # 5. 快照语义：注册新工具后，scoped view 不受影响
        new_tool_name = "zzznewtoolthatdoesnotexist"
        registry.register(FakeTool(tool_name=new_tool_name))

        schemas_after = scoped.get_schemas()
        result_names_after = {s["function"]["name"] for s in schemas_after}

        # 新工具不应出现在 scoped view 中
        assert new_tool_name not in result_names_after

        # scoped view 结果应与之前一致
        assert result_names_after == expected


class TestScopedRegistryExecutePermissionProperties:
    """ScopedToolRegistry execute 权限控制属性测试类。

    验证在任意有效的 ScopedToolRegistry 和 ToolCallRequest 下，
    作用域内工具正常执行（委托底层 ToolRegistry），
    作用域外工具抛出 ToolPermissionDeniedError 且异常属性正确。
    """

    @pytest.mark.asyncio
    @given(
        tool_names=st.lists(
            st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=8),
            min_size=2,
            max_size=5,
            unique=True,
        ),
    )
    @settings(max_examples=100, deadline=5000)
    async def test_scoped_registry_execute_permission(self, tool_names: list[str]) -> None:
        """属性测试：ScopedToolRegistry execute 权限控制。

        **Validates: Requirements 2.4, 2.5**

        Property 3: 对任意 ScopedToolRegistry 和任意 ToolCallRequest，
        当 request.name 在作用域内时，execute(request) 应委托底层 ToolRegistry
        执行并返回结果；当 request.name 不在作用域内时，execute(request) 应抛出
        ToolPermissionDeniedError，且异常的 tool_name 等于 request.name，
        allowed_tools 等于作用域的工具名称集合。
        """
        # 1. 构造 ToolRegistry 并注册所有工具
        registry = ToolRegistry()
        for name in tool_names:
            registry.register(FakeTool(tool_name=name))

        # 2. 选取前半部分作为 scope 子集，确保至少 1 个在 scope 内、1 个在 scope 外
        scope_names = frozenset(tool_names[: len(tool_names) // 2 + 1])
        out_of_scope = [n for n in tool_names if n not in scope_names]

        # 3. 创建 ScopedToolRegistry
        scoped = registry.create_scoped_view(scope_names)

        # 4. 作用域内工具：execute 应正常返回 "ok"
        in_scope_name = next(iter(scope_names))
        in_scope_request = ToolCallRequest(id="call-in", name=in_scope_name, arguments="{}")
        result = await scoped.execute(in_scope_request)
        assert result.content == "ok"

        # 5. 作用域外工具：execute 应抛出 ToolPermissionDeniedError
        if out_of_scope:
            out_name = out_of_scope[0]
            out_request = ToolCallRequest(id="call-out", name=out_name, arguments="{}")
            with pytest.raises(ToolPermissionDeniedError) as exc_info:
                await scoped.execute(out_request)

            # 验证异常属性
            assert exc_info.value.tool_name == out_name
            assert exc_info.value.allowed_tools == scope_names


# ── AgentConfig 属性测试用 Hypothesis 策略 ──

# 生成 OpenAI function calling 格式的 tool_schemas 列表
def _tool_schema(name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "test",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def _tool_schema_name(schema: dict[str, Any]) -> str:
    return str(schema["function"]["name"])


tool_schema_st: st.SearchStrategy[list[dict[str, Any]]] = st.lists(
    st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=8).map(
        _tool_schema
    ),
    min_size=1,
    max_size=5,
    unique_by=_tool_schema_name,
)


class TestAgentConfigAllowedToolNamesProperties:
    """AgentConfig allowed_tool_names 自动提取与显式覆盖属性测试类。

    验证在任意有效的 tool_schemas 输入下，AgentConfig 的 allowed_tool_names
    字段在未显式传入时自动从 tool_schemas 提取，在显式传入时使用传入值。
    """

    @given(schemas=tool_schema_st)
    @settings(max_examples=100, deadline=5000)
    def test_agent_config_auto_extraction(self, schemas: list[dict[str, Any]]) -> None:
        """属性测试：AgentConfig allowed_tool_names 自动提取。

        **Validates: Requirements 4.2, 4.3**

        Property 5: 对任意非空 tool_schemas 列表（每个 schema 包含 function.name），
        当构造 AgentConfig 时不显式传入 allowed_tool_names，
        AgentConfig.allowed_tool_names 应等于从 tool_schemas 中提取的所有
        function.name 组成的 frozenset。
        """
        config = AgentConfig(
            system_prompt="test",
            tool_schemas=schemas,
            model=None,
            max_rounds=1,
            prompt_id="chat-default@v1",
        )

        expected_names = frozenset(schema["function"]["name"] for schema in schemas)

        # allowed_tool_names 应等于从 tool_schemas 自动提取的名称集合
        assert config.allowed_tool_names == expected_names

        # 类型应为 frozenset，确保不可变性
        assert isinstance(config.allowed_tool_names, frozenset)

    @given(schemas=tool_schema_st)
    @settings(max_examples=100, deadline=5000)
    def test_agent_config_explicit_override(self, schemas: list[dict[str, Any]]) -> None:
        """属性测试：AgentConfig allowed_tool_names 显式覆盖。

        **Validates: Requirements 4.4, 4.5**

        Property 6: 对任意 tool_schemas 列表和任意显式传入的 allowed_tool_names，
        当构造 AgentConfig 时显式传入 allowed_tool_names，
        AgentConfig.allowed_tool_names 应等于显式传入的值，不执行自动提取。
        """
        explicit_names = frozenset({"custom_a", "custom_b"})

        config = AgentConfig(
            system_prompt="test",
            tool_schemas=schemas,
            model=None,
            max_rounds=1,
            prompt_id="chat-default@v1",
            allowed_tool_names=explicit_names,
        )

        # allowed_tool_names 应等于显式传入的值，不执行自动提取
        assert config.allowed_tool_names == explicit_names

        # 类型应为 frozenset，确保不可变性
        assert isinstance(config.allowed_tool_names, frozenset)

        # 不应等于从 tool_schemas 自动提取的名称
        # （除非恰好相同，但 custom_a/custom_b 不会出现在随机生成的 schema 中）
        auto_names = frozenset(schema["function"]["name"] for schema in schemas)
        assert config.allowed_tool_names != auto_names or explicit_names == auto_names
