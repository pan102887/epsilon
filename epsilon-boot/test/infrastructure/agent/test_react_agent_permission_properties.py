"""ReActAgentAdapter 权限校验属性测试模块。

使用 Hypothesis 属性测试验证 ReActAgentAdapter 在执行工具调用时的权限校验行为：
- 允许的工具被正常执行（ToolRegistry.execute 被调用）
- 未授权的工具不被执行（ToolRegistry.execute 不被调用），且上下文中追加包含
  ToolPermissionDeniedError 错误信息的 ToolMessage

**Validates: Requirements 5.1, 5.2, 5.3, 5.4**
"""

import string
from unittest.mock import AsyncMock, MagicMock

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import AgentConfig
from domain.chat.context import ConversationContext, ToolMessage, UserMessage
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.value_objects import LLMResponse, ToolCallRequest
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from test.infrastructure.agent._v3_stream_helpers import install_stream_mock

# ── Hypothesis 策略 ──────────────────────────────────────────────

_tool_name_st = st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=8)
"""工具名称策略：1-8 位小写字母字符串。"""


@st.composite
def _tool_partition_st(draw: st.DrawFn):
    """生成工具名称分区策略。

    生成 2-4 个唯一工具名称作为"全部工具"，然后从中选取一个非空真子集作为
    "允许的工具"，确保至少存在一个允许的工具和一个未授权的工具。

    Returns:
        (all_tools, allowed_tools) 元组，all_tools 为全部工具名称列表，
        allowed_tools 为允许的工具名称 frozenset，且 allowed_tools 是 all_tools 的真子集。
    """
    all_tools = draw(st.lists(_tool_name_st, min_size=2, max_size=4, unique=True))
    # 选取一个非空真子集作为允许的工具
    subset_size = draw(st.integers(min_value=1, max_value=len(all_tools) - 1))
    allowed_tools = frozenset(
        draw(st.sampled_from([frozenset(combo) for combo in _combinations(all_tools, subset_size)]))
    )
    return all_tools, allowed_tools


def _combinations(items: list[str], r: int) -> list[tuple[str, ...]]:
    """生成组合列表，避免导入 itertools 以保持简洁。"""
    from itertools import combinations

    return list(combinations(items, r))


# ── 属性测试 ─────────────────────────────────────────────────────


@settings(max_examples=100, deadline=5000)
@given(data=_tool_partition_st())
@pytest.mark.asyncio
async def test_react_agent_permission_check(data: tuple[list[str], frozenset[str]]) -> None:
    """Property 7: ReActAgentAdapter 权限校验。

    对于任意工具名称集合和允许的工具子集，当 LLM 返回包含所有工具的 tool_calls 时：
    - 允许的工具：ToolRegistry.execute 被调用
    - 未授权的工具：ToolRegistry.execute 不被调用，上下文中追加的 ToolMessage
      包含 ToolPermissionDeniedError 的错误信息

    **Validates: Requirements 5.1, 5.2, 5.3, 5.4**
    """
    all_tools, allowed_names = data
    unauthorized_names = set(all_tools) - allowed_names

    # 构造 tool_calls：为每个工具生成一个 ToolCallRequest
    tool_calls = [
        ToolCallRequest(id=f"tc_{name}", name=name, arguments='{"k": "v"}') for name in all_tools
    ]

    # Mock LLM：第 1 轮返回 tool_calls，第 2 轮返回纯文本
    round1_response = LLMResponse(
        content="",
        model="test-model",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        latency_ms=100.0,
        tool_calls=tool_calls,
    )
    round2_response = LLMResponse(
        content="done",
        model="test-model",
        usage={"prompt_tokens": 20, "completion_tokens": 10},
        latency_ms=50.0,
        tool_calls=[],
    )
    model_access = MagicMock()
    install_stream_mock(model_access, [round1_response, round2_response])

    # Mock ToolRegistry.execute：对任何工具返回 "ok"
    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="ok"))

    # Mock builder：Agent 每轮模型调用都应使用 builder 输出的消息
    context_builder = MagicMock()
    context_builder.build = AsyncMock(
        return_value=ContextBuilderResult(
            messages=[UserMessage(content="builder message")],
            usage={"builder_tokens": 1},
            environment_injected=True,
        )
    )

    # 构造 AgentConfig，仅允许 allowed_names 中的工具
    allowed_schemas = [{"type": "function", "function": {"name": name}} for name in allowed_names]
    config = AgentConfig(
        system_prompt="test",
        tool_schemas=allowed_schemas,
        model=None,
        max_rounds=3,
        prompt_id="chat-default@v1",
        allowed_tool_names=allowed_names,
    )

    adapter = ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=context_builder,
    )
    context = ConversationContext()
    context.add_user_message("go")

    # ── 执行 run() ──
    result = await adapter.run(context, config, model_access)
    assert result.content == "done"
    assert result.usage["builder_tokens"] == 2
    for call in model_access.chat.call_args_list:
        assert call.args[0].messages == [
            {"role": "user", "content": "builder message"},
        ]

    # ── 验证 ToolRegistry.execute 仅被允许的工具调用 ──
    executed_names = {call.args[0].name for call in tool_registry.execute.call_args_list}
    assert executed_names == allowed_names, (
        f"execute 应仅被允许的工具调用: expected={allowed_names}, actual={executed_names}"
    )

    # ── 验证上下文中未授权工具的 ToolMessage 包含错误信息 ──
    messages = context.get_messages()
    tool_messages = [m for m in messages if isinstance(m, ToolMessage)]

    for name in unauthorized_names:
        # 找到对应的 ToolMessage
        matching = [tm for tm in tool_messages if tm.tool_call_id == f"tc_{name}"]
        assert len(matching) == 1, f"未授权工具 {name} 应有且仅有一条 ToolMessage"
        tm = matching[0]
        # 验证 content 包含 ToolPermissionDeniedError 的错误信息
        assert "未授权" in tm.content, (
            f"未授权工具 {name} 的 ToolMessage content 应包含 '未授权': {tm.content}"
        )
        assert name in tm.content, (
            f"未授权工具 {name} 的 ToolMessage content 应包含工具名称: {tm.content}"
        )

    # ── 验证允许的工具的 ToolMessage content 为 "ok" ──
    for name in allowed_names:
        matching = [tm for tm in tool_messages if tm.tool_call_id == f"tc_{name}"]
        assert len(matching) == 1, f"允许的工具 {name} 应有且仅有一条 ToolMessage"
        assert matching[0].content == "ok", (
            f"允许的工具 {name} 的 ToolMessage content 应为 'ok': {matching[0].content}"
        )
