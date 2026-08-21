"""ReActAgentAdapter 属性测试模块。

使用 Hypothesis 对消息协议转换的核心行为进行属性测试，验证：
- 消息序列化正确性：OpenAICompatibleAdapter._to_openai_messages 输出格式正确
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import AgentConfig
from domain.chat.context import (
    AssistantMessage,
    BaseMessage,
    ConversationContext,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from domain.chat.ports import ContextBuilderPort
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import LLMResponse, StreamingChunk, ToolCallRequest
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter
from test.infrastructure.agent._v3_stream_helpers import install_stream_mock

# ── Hypothesis 策略 ──

# ToolCallRequest 策略：生成合法的工具调用请求
tool_call_request_st = st.builds(
    ToolCallRequest,
    id=st.text(min_size=1, max_size=20),
    name=st.text(min_size=1, max_size=20),
    arguments=st.just('{"key": "value"}'),
)

# 单条消息策略：从各种消息类型中随机选择
message_st = st.one_of(
    # SystemMessage
    st.builds(SystemMessage, content=st.text()),
    # UserMessage
    st.builds(UserMessage, content=st.text()),
    # AssistantMessage 无 tool_calls
    st.builds(AssistantMessage, content=st.text(), tool_calls=st.just([])),
    # AssistantMessage 有 tool_calls
    st.builds(
        AssistantMessage,
        content=st.text(),
        tool_calls=st.lists(tool_call_request_st, min_size=1, max_size=5),
    ),
    # ToolMessage
    st.builds(
        ToolMessage,
        content=st.text(),
        tool_name=st.text(min_size=1),
        tool_call_id=st.text(min_size=1),
    ),
)

# BaseMessage 列表策略
message_list_st = st.lists(message_st, min_size=0, max_size=20)


# ── Property 3: Message serialization correctness ──
# Feature: agent-abstraction-layer, Property 3: Message serialization correctness


@settings(max_examples=100, deadline=5000)
@given(messages=message_list_st)
def test_message_serialization_correctness(messages: list[BaseMessage]) -> None:
    """验证 OpenAICompatibleAdapter._to_openai_messages 输出格式正确。

    对于任意 BaseMessage 列表（包含 SystemMessage、UserMessage、
    AssistantMessage（含/不含 tool_calls）、ToolMessage），序列化方法
    应产生符合 OpenAI API 格式的字典列表，确保消息序列化逻辑正确。

    验证要点：
    1. 输出列表长度与输入一致
    2. 每条消息的 role 和 content 正确保留
    3. AssistantMessage 携带 tool_calls 时输出 OpenAI 嵌套格式
    4. ToolMessage 输出 tool_call_id
    5. SystemMessage/UserMessage 仅输出 role 和 content

    **Validates: Requirements 4.7**
    """
    result = OpenAICompatibleAdapter.to_openai_messages(messages)

    assert len(result) == len(messages), (
        f"序列化结果长度不一致: 输入={len(messages)}, 输出={len(result)}"
    )

    for msg, serialized in zip(messages, result, strict=True):
        assert serialized["role"] == msg.role
        assert serialized["content"] == msg.content

        if isinstance(msg, AssistantMessage) and msg.tool_calls:
            assert "tool_calls" in serialized
            assert len(serialized["tool_calls"]) == len(msg.tool_calls)
            for j, tc_dict in enumerate(serialized["tool_calls"]):
                assert tc_dict["id"] == msg.tool_calls[j].id
                assert tc_dict["type"] == "function"
                assert tc_dict["function"]["name"] == msg.tool_calls[j].name
                assert tc_dict["function"]["arguments"] == msg.tool_calls[j].arguments
        elif isinstance(msg, ToolMessage):
            assert "tool_call_id" in serialized
            assert serialized["tool_call_id"] == msg.tool_call_id
        else:
            assert set(serialized.keys()) == {"role", "content"}


# ── Property 4: Token usage accumulation ──
# Feature: agent-abstraction-layer, Property 4: Token usage accumulation

class _PassthroughContextBuilder:
    """测试用原样消息构建器。"""

    def __init__(self, usage: dict[str, int] | None = None) -> None:
        self._usage = usage or {}

    async def build(
        self,
        messages: list[BaseMessage],
        *,
        model_access: ModelAccessPort | None = None,
        model: str | None = None,
    ) -> ContextBuilderResult:
        del model_access, model
        return ContextBuilderResult(messages=messages, usage=dict(self._usage))


def _make_context_builder(usage: dict[str, int] | None = None) -> ContextBuilderPort:
    """构造原样透传领域消息列表的 ContextBuilderPort mock。"""
    return _PassthroughContextBuilder(usage)


# 策略：生成随机 token 用量字典
usage_st = st.fixed_dictionaries(
    {
        "prompt_tokens": st.integers(min_value=0, max_value=10000),
        "completion_tokens": st.integers(min_value=0, max_value=10000),
    }
)


def _build_llm_response_sequence(
    usages: list[dict[str, int]],
) -> list[LLMResponse]:
    """根据用量列表构建 LLMResponse 序列。

    中间轮次（除最后一个）携带 tool_calls，最后一轮不携带 tool_calls。

    Args:
        usages: 每轮的 token 用量字典列表

    Returns:
        LLMResponse 对象列表
    """
    responses: list[LLMResponse] = []
    for i, usage in enumerate(usages):
        is_last = i == len(usages) - 1
        tool_calls = (
            []
            if is_last
            else [ToolCallRequest(id=f"call_{i}", name="test_tool", arguments='{"key": "value"}')]
        )
        responses.append(
            LLMResponse(
                content=f"response_{i}",
                model="test-model",
                usage=usage,
                latency_ms=100.0,
                tool_calls=tool_calls,
            )
        )
    return responses


@settings(max_examples=100, deadline=5000)
@given(
    usages=st.lists(usage_st, min_size=1, max_size=5),
)
@pytest.mark.asyncio
async def test_token_usage_accumulation(usages: list[dict[str, int]]) -> None:
    """验证 ReActAgentAdapter.run() 返回的 AgentResult.usage
    等于所有轮次 LLMResponse.usage 的逐键累加。

    生成 1-5 轮 LLMResponse 序列（中间轮次含 tool_calls，最后一轮不含），
    mock ModelAccessPort.chat 按顺序返回这些响应，验证最终 AgentResult.usage
    等于所有轮次 usage 的 element-wise 求和。

    **Validates: Requirements 4.6**
    """
    responses = _build_llm_response_sequence(usages)

    # 计算期望的累计用量
    expected_usage: dict[str, int] = {}
    for usage in usages:
        for key, value in usage.items():
            expected_usage[key] = expected_usage.get(key, 0) + value

    # Mock ModelAccessPort：按顺序返回预设的 LLMResponse
    model_access = AsyncMock()
    install_stream_mock(model_access, list(responses))

    # Mock ContextBuilderPort：原样序列化消息列表
    context_builder = _make_context_builder()

    # Mock ToolRegistry：get_schemas 返回有效 schema，execute 返回 "ok"
    tool_registry = MagicMock()
    tool_registry.get_schemas = MagicMock(
        return_value=[
            {
                "type": "function",
                "function": {
                    "name": "test_tool",
                    "description": "A test tool",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
    )
    tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="ok"))

    # 构造 ConversationContext
    context = ConversationContext()
    context.add_system_message("system prompt")
    context.add_user_message("user message")

    # 构造 AgentConfig，max_rounds 足够覆盖所有轮次
    config = AgentConfig(
        system_prompt="system prompt",
        tool_schemas=tool_registry.get_schemas(),
        model="test-model",
        max_rounds=len(responses) + 5,
        prompt_id="chat-default@v1",
    )

    adapter = ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=context_builder,
    )

    result = await adapter.run(context, config, model_access)

    assert result.usage == expected_usage, (
        f"Token 用量累计不一致\n"
        f"轮次数: {len(usages)}\n"
        f"各轮用量: {usages}\n"
        f"期望累计: {expected_usage}\n"
        f"实际累计: {result.usage}"
    )


# ── Property 5: Tool exception handling in Agent Loop ──
# Feature: agent-abstraction-layer, Property 5: Tool exception handling in Agent Loop


@settings(max_examples=100, deadline=5000)
@given(
    error_message=st.text(min_size=1, max_size=200),
)
@pytest.mark.asyncio
async def test_tool_exception_handling(error_message: str) -> None:
    """验证工具执行抛出异常时，
    ReActAgentAdapter 将异常信息作为 ToolMessage content 追加到上下文，且循环继续运行。

    生成随机非空异常消息字符串，构造两轮 LLMResponse 序列：
    第一轮携带 tool_calls（触发工具执行），第二轮不携带 tool_calls（最终回复）。
    Mock ToolRegistry.execute 抛出 Exception(error_message)，验证：
    1. 上下文中包含一条 ToolMessage，其 content 等于 error_message
    2. Agent Loop 继续运行并返回有效的 AgentResult（来自第二轮响应）

    **Validates: Requirements 4.5, 7.4**
    """
    # 构造两轮 LLMResponse：第一轮含 tool_calls，第二轮为纯文本回复
    first_response = LLMResponse(
        content="thinking...",
        model="test-model",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        latency_ms=50.0,
        tool_calls=[
            ToolCallRequest(id="call_0", name="failing_tool", arguments='{"key": "value"}')
        ],
    )
    final_response = LLMResponse(
        content="final answer",
        model="test-model",
        usage={"prompt_tokens": 20, "completion_tokens": 10},
        latency_ms=80.0,
        tool_calls=[],
    )

    # Mock ModelAccessPort：按顺序返回两轮响应
    model_access = AsyncMock()
    install_stream_mock(model_access, [first_response, final_response])

    # Mock ContextBuilderPort：原样序列化消息列表
    context_builder = _make_context_builder()

    # Mock ToolRegistry：execute 抛出异常
    tool_registry = MagicMock()
    tool_registry.get_schemas = MagicMock(
        return_value=[
            {
                "type": "function",
                "function": {
                    "name": "failing_tool",
                    "description": "A tool that fails",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]
    )
    tool_registry.execute = AsyncMock(side_effect=Exception(error_message))

    # 构造 ConversationContext
    context = ConversationContext()
    context.add_system_message("system prompt")
    context.add_user_message("user message")

    # 构造 AgentConfig
    config = AgentConfig(
        system_prompt="system prompt",
        tool_schemas=tool_registry.get_schemas(),
        model="test-model",
        max_rounds=10,
        prompt_id="chat-default@v1",
    )

    adapter = ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=context_builder,
    )

    result = await adapter.run(context, config, model_access)

    # 验证 1：上下文中包含 ToolMessage，其 content 等于异常消息
    tool_messages = [msg for msg in context.get_messages() if isinstance(msg, ToolMessage)]
    assert len(tool_messages) == 1, (
        f"期望恰好 1 条 ToolMessage，实际 {len(tool_messages)} 条\n"
        f"所有消息: {[type(m).__name__ for m in context.get_messages()]}"
    )
    assert tool_messages[0].content == error_message, (
        f"ToolMessage content 不等于异常消息\n"
        f"期望: {error_message!r}\n"
        f"实际: {tool_messages[0].content!r}"
    )

    # 验证 2：循环继续运行，返回有效的 AgentResult（来自第二轮响应）
    assert result.content == "final answer", (
        f"AgentResult content 不正确\n期望: 'final answer'\n实际: {result.content!r}"
    )
    assert result.model == "test-model"


# ── Property 6: Agent run behavioral equivalence ──
# Feature: agent-abstraction-layer, Property 6: Agent run behavioral equivalence


@settings(max_examples=100, deadline=5000)
@given(
    usages=st.lists(usage_st, min_size=1, max_size=4),
)
@pytest.mark.asyncio
async def test_agent_run_behavioral_equivalence(
    usages: list[dict[str, int]],
) -> None:
    """验证 ReActAgentAdapter.run() 正确执行 Agent Loop 并返回预期结果。

    生成 1-4 轮 LLMResponse 序列（中间轮次含 tool_calls，最终轮不含），
    验证：
    1. AgentResult.content 等于最终轮 LLMResponse.content
    2. AgentResult.model 等于最终轮 LLMResponse.model
    3. 上下文中的消息序列正确（包含 AssistantMessage 和 ToolMessage）

    **Validates: Requirements 4.3, 2.5, 7.1**
    """
    responses = _build_llm_response_sequence(usages)
    max_rounds = len(responses) + 5

    tool_schemas: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "test_tool",
                "description": "A test tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    # ── ReActAgentAdapter 侧 ──
    model_access_a = AsyncMock()
    install_stream_mock(model_access_a, list(responses))

    context_builder_a = _make_context_builder()

    tool_registry_a = MagicMock()
    tool_registry_a.get_schemas = MagicMock(return_value=tool_schemas)
    tool_registry_a.execute = AsyncMock(return_value=ToolExecutionResult(content="ok"))

    context_a = ConversationContext()
    context_a.add_system_message("system prompt")
    context_a.add_user_message("user message")

    config = AgentConfig(
        system_prompt="system prompt",
        tool_schemas=tool_schemas,
        model="test-model",
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
    )

    adapter = ReActAgentAdapter(
        tool_registry=tool_registry_a,
        context_builder=context_builder_a,
    )

    agent_result = await adapter.run(context_a, config, model_access_a)

    # ── 验证 1：AgentResult 内容与最终轮 LLMResponse 一致 ──
    last_response = responses[-1]
    assert agent_result.content == last_response.content, (
        f"最终回复内容不一致\n"
        f"AgentResult.content: {agent_result.content!r}\n"
        f"LLMResponse.content: {last_response.content!r}"
    )
    assert agent_result.model == last_response.model, (
        f"模型名称不一致\n"
        f"AgentResult.model: {agent_result.model!r}\n"
        f"LLMResponse.model: {last_response.model!r}"
    )

    # ── 验证 2：上下文消息序列正确 ──
    msgs = context_a.get_messages()
    # 初始消息：system + user = 2
    # 每个中间轮次：assistant(tool_calls) + tool = 2
    # 最终轮次不追加（由编排层追加）
    num_intermediate = len(responses) - 1
    expected_msg_count = 2 + num_intermediate * 2
    assert len(msgs) == expected_msg_count, (
        f"上下文消息数量不一致\n"
        f"期望: {expected_msg_count}\n"
        f"实际: {len(msgs)}\n"
        f"消息类型: {[type(m).__name__ for m in msgs]}"
    )


# ── Property 7: Agent run_streaming behavioral equivalence ──
# Feature: agent-abstraction-layer, Property 7: Agent run_streaming behavioral equivalence


@settings(max_examples=100, deadline=5000)
@given(
    usages=st.lists(usage_st, min_size=1, max_size=4),
)
@pytest.mark.asyncio
async def test_agent_run_streaming_equivalence(
    usages: list[dict[str, int]],
) -> None:
    """验证 ReActAgentAdapter.run_streaming() 正确执行流式 Agent Loop。

    生成 1-4 轮 LLMResponse 序列（中间轮次含 tool_calls，最终轮不含），
    验证：
    1. 产出的 StreamingChunk 序列正确（最终轮包装为单个 finished=True 的 chunk）
    2. 上下文中的消息序列正确（包含 AssistantMessage 和 ToolMessage）

    当只有一轮（最终轮无 tool_calls）时，实现应将回复包装为单个
    StreamingChunk(delta_content=content, finished=True, usage=usage) 产出。
    当有多轮时，中间轮次使用同步调用执行工具，最终轮次包装为单个 StreamingChunk 产出。

    **Validates: Requirements 4.4, 7.2**
    """
    responses = _build_llm_response_sequence(usages)
    max_rounds = len(responses) + 5

    tool_schemas: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "test_tool",
                "description": "A test tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    # ── ReActAgentAdapter 侧 ──
    model_access_a = AsyncMock()
    install_stream_mock(model_access_a, list(responses))

    context_builder_a = _make_context_builder()

    tool_registry_a = MagicMock()
    tool_registry_a.get_schemas = MagicMock(return_value=tool_schemas)
    tool_registry_a.execute = AsyncMock(return_value=ToolExecutionResult(content="ok"))

    context_a = ConversationContext()
    context_a.add_system_message("system prompt")
    context_a.add_user_message("user message")

    config = AgentConfig(
        system_prompt="system prompt",
        tool_schemas=tool_schemas,
        model="test-model",
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
    )

    adapter = ReActAgentAdapter(
        tool_registry=tool_registry_a,
        context_builder=context_builder_a,
    )

    # run_streaming 是 async generator，直接迭代
    chunks_a: list[StreamingChunk] = []
    async for chunk in adapter.run_streaming(context_a, config, model_access_a):
        chunks_a.append(chunk)

    # ── 验证 1：产出的 StreamingChunk 序列正确 ──
    # 最终轮应包装为单个 finished=True 的 chunk
    assert len(chunks_a) >= 1, "应至少产出一个 StreamingChunk"
    assert chunks_a[-1].finished is True, "最后一个 chunk 应标记为 finished"

    last_response = responses[-1]
    assert chunks_a[-1].delta_content == last_response.content, (
        f"最终 chunk 内容不一致\n"
        f"期望: {last_response.content!r}\n"
        f"实际: {chunks_a[-1].delta_content!r}"
    )

    # ── 验证 2：上下文消息序列正确 ──
    msgs = context_a.get_messages()
    num_intermediate = len(responses) - 1
    # 初始消息：system + user = 2
    # 每个中间轮次：assistant(tool_calls) + tool = 2
    expected_msg_count = 2 + num_intermediate * 2
    assert len(msgs) == expected_msg_count, (
        f"上下文消息数量不一致\n"
        f"期望: {expected_msg_count}\n"
        f"实际: {len(msgs)}\n"
        f"消息类型: {[type(m).__name__ for m in msgs]}"
    )


# ── Property 8: _iter_rounds 与 run 产出最终内容等价 ──
# Feature: agent-adapter-refactor, Property 1: 轮次推进单源化


@settings(max_examples=100, deadline=5000)
@given(
    usages=st.lists(usage_st, min_size=1, max_size=4),
)
@pytest.mark.asyncio
async def test_iter_rounds_and_run_produce_equivalent_content(
    usages: list[dict[str, int]],
) -> None:
    """验证任意构造的多轮交互下，``run`` 与直接消费 ``_iter_rounds`` 产出的最终内容等价。

    构造 1-4 轮 LLMResponse 序列（中间轮次含 tool_calls，最终轮不含），
    分别通过 ``run`` 与直接消费 ``_iter_rounds`` 两条路径执行，验证两者
    得到的最终 ``AgentResult.content`` 完全一致。

    这保证 ``_iter_rounds`` 作为统一的轮次推进来源，与 ``run`` 的消费者
    组合后产出的业务结果等价——从而验证重构后"三入口共享同一套轮次推进
    逻辑"的正确性（设计正确性属性 Property 1）。

    **Validates: Requirement 1.11**
    """
    responses = _build_llm_response_sequence(usages)
    max_rounds = len(responses) + 5

    tool_schemas: list[dict[str, Any]] = [
        {
            "type": "function",
            "function": {
                "name": "test_tool",
                "description": "A test tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    # ── 路径 A：通过 run 消费 ──
    model_access_a = AsyncMock()
    install_stream_mock(model_access_a, list(responses))
    context_builder_a = _make_context_builder()
    tool_registry_a = MagicMock()
    tool_registry_a.execute = AsyncMock(return_value=ToolExecutionResult(content="ok"))

    context_a = ConversationContext()
    context_a.add_system_message("system prompt")
    context_a.add_user_message("user message")

    config = AgentConfig(
        system_prompt="system prompt",
        tool_schemas=tool_schemas,
        model="test-model",
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
    )

    adapter_a = ReActAgentAdapter(
        tool_registry=tool_registry_a,
        context_builder=context_builder_a,
    )
    result_a = await adapter_a.run(context_a, config, model_access_a)

    # ── 路径 B：直接消费 _iter_rounds ──
    model_access_b = AsyncMock()
    install_stream_mock(model_access_b, list(responses))
    context_builder_b = _make_context_builder()
    tool_registry_b = MagicMock()
    tool_registry_b.execute = AsyncMock(return_value=ToolExecutionResult(content="ok"))

    context_b = ConversationContext()
    context_b.add_system_message("system prompt")
    context_b.add_user_message("user message")

    adapter_b = ReActAgentAdapter(
        tool_registry=tool_registry_b,
        context_builder=context_builder_b,
    )

    # 直接消费 _iter_rounds 并手动执行工具
    final_content: str = ""
    async for outcome in adapter_b.iter_rounds(context_b, config, model_access_b):
        if outcome.kind == "tool_calls":
            for tool_call in outcome.tool_calls:
                context_b.add_tool_result(
                    tool_name=tool_call.name,
                    result="ok",
                    tool_call_id=tool_call.id,
                )
            continue
        # text / final / approval → 取 response.content
        final_content = outcome.response.content
        break

    # ── 验证等价性 ──
    assert result_a.content == final_content, (
        f"run 与 _iter_rounds 产出内容不一致\n"
        f"run result: {result_a.content!r}\n"
        f"_iter_rounds result: {final_content!r}\n"
        f"轮次数: {len(usages)}"
    )
