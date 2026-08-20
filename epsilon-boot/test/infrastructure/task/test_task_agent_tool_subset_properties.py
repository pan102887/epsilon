"""TaskAgentAdapter 工具子集路由属性测试模块。

使用 Hypothesis 属性测试验证 TaskAgentAdapter 在执行任务时，根据 Task.tool_names
字段正确路由工具子集：
- task.tool_names 不为 None 时，AgentConfig.tool_schemas 仅包含子集工具的 schema
- task.tool_names 为 None 时，AgentConfig.tool_schemas 包含全量工具 schema

**Validates: Requirements 7.1, 7.2**
"""

import string
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from domain.agent.tools import Tool, ToolRegistry
from domain.agent.value_objects import AgentConfig
from domain.chat.context import ConversationContext
from domain.chat.value_objects import ContextCompactionResult
from domain.prompt.value_objects import LoadedPrompt
from domain.task.value_objects import Task, TaskStatus
from infrastructure.task.task_agent_adapter import TaskAgentAdapter

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

    async def execute(self, **kwargs: Any) -> str:
        return "ok"


# ── Hypothesis 策略 ──

_tool_name_st = st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=8)
"""工具名称策略：1-8 位小写字母字符串。"""


def _make_mock_agent_result() -> MagicMock:
    """构造 Mock AgentResult，模拟 AgentPort.run() 的返回值。"""
    agent_result = MagicMock()
    agent_result.content = "task done"
    agent_result.model = "test-model"
    agent_result.usage = {"prompt_tokens": 10, "completion_tokens": 5}
    agent_result.latency_ms = 100.0
    return agent_result


def _make_adapter(
    tool_registry: ToolRegistry,
    captured_configs: list[AgentConfig],
) -> TaskAgentAdapter:
    """构造 TaskAgentAdapter，注入 Mock 依赖并捕获传入 AgentPort.run() 的 AgentConfig。

    Args:
        tool_registry: 已注册工具的 ToolRegistry 实例
        captured_configs: 用于捕获 AgentConfig 的列表，run() 被调用时将 config 追加到此列表

    Returns:
        配置好的 TaskAgentAdapter 实例
    """
    agent_result = _make_mock_agent_result()

    async def capture_run(context: Any, config: AgentConfig, model_access: Any) -> Any:
        """捕获 AgentPort.run() 的 config 参数。"""
        captured_configs.append(config)
        return agent_result

    mock_agent = MagicMock()
    mock_agent.run = AsyncMock(side_effect=capture_run)

    mock_model_registry = MagicMock()
    mock_model_registry.get_default_model.return_value = "test-model"
    mock_model_registry.get_adapter_for_model.return_value = MagicMock()

    mock_compaction = MagicMock()
    mock_compaction.compact = AsyncMock(
        side_effect=lambda msgs, **kwargs: ContextCompactionResult(messages=msgs)
    )

    mock_session_store = MagicMock()
    mock_session_store.load = AsyncMock(return_value=ConversationContext())
    mock_session_store.save = AsyncMock()

    return TaskAgentAdapter(
        agent=mock_agent,
        tool_registry=tool_registry,
        model_registry=mock_model_registry,
        compaction=mock_compaction,
        session_store=mock_session_store,
        prompt_registry=MagicMock(
            get=MagicMock(
                return_value=LoadedPrompt(
                    prompt_id="task-template@v1",
                    name="task-template",
                    version="v1",
                    content="骨架",
                )
            )
        ),
        max_rounds=5,
    )


# ── 属性测试 ──


@settings(max_examples=100, deadline=5000)
@given(
    all_tool_names=st.lists(_tool_name_st, min_size=2, max_size=5, unique=True),
    use_subset=st.booleans(),
)
@pytest.mark.asyncio
async def test_task_agent_tool_subset_routing(all_tool_names: list[str], use_subset: bool) -> None:
    """Property 8: TaskAgentAdapter 工具子集路由。

    对任意 Task 值对象和 ToolRegistry，当 task.tool_names 不为 None 时，
    TaskAgentAdapter 构造的 AgentConfig 的 tool_schemas 应仅包含
    task.tool_names 中已注册工具的 schema；当 task.tool_names 为 None 时，
    AgentConfig 的 tool_schemas 应包含全量工具 schema。

    **Validates: Requirements 7.1, 7.2**
    """
    # 1. 构造 ToolRegistry 并注册所有工具
    registry = ToolRegistry()
    for name in all_tool_names:
        registry.register(FakeTool(tool_name=name))

    registered_names = set(all_tool_names)

    # 2. 根据 use_subset 决定 task.tool_names
    if use_subset:
        # 选取前半部分作为子集，确保非空
        subset = frozenset(all_tool_names[: len(all_tool_names) // 2 + 1])
        task = Task(goal="测试工具子集路由", tool_names=subset)
    else:
        subset = None
        task = Task(goal="测试全量工具路由", tool_names=None)

    # 3. 捕获 AgentConfig
    captured_configs: list[AgentConfig] = []
    adapter = _make_adapter(registry, captured_configs)

    # 4. 执行任务
    result = await adapter.execute(task)
    assert result.status == TaskStatus.SUCCESS

    # 5. 验证捕获的 AgentConfig
    assert len(captured_configs) == 1
    config = captured_configs[0]

    schema_names = {s["function"]["name"] for s in config.tool_schemas}

    if subset is not None:
        # task.tool_names 不为 None：tool_schemas 仅包含子集中已注册的工具
        expected = subset & registered_names
        assert schema_names == expected, (
            f"tool_schemas 应仅包含子集工具: expected={expected}, actual={schema_names}"
        )
        # allowed_tool_names 应由 __post_init__ 自动提取，等于 schema_names
        assert config.allowed_tool_names == expected
    else:
        # task.tool_names 为 None：tool_schemas 包含全量工具
        assert schema_names == registered_names, (
            f"tool_schemas 应包含全量工具: expected={registered_names}, actual={schema_names}"
        )
        # allowed_tool_names 应由 __post_init__ 自动提取，等于全量
        assert config.allowed_tool_names == registered_names
