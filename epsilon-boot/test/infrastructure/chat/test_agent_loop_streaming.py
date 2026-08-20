"""Agent Loop 流式对话单元测试。

验证 ReActAgentAdapter.run_streaming 方法的核心行为，
包括中间轮次不产出流式分片、最终轮次正确流式产出、以及达到最大轮次时的流式产出。

Agent Loop 逻辑已从 ChatServiceAdapter 迁移到 ReActAgentAdapter，
因此流式 Agent Loop 相关测试直接测试 ReActAgentAdapter.run_streaming()。

对应需求：7.1, 7.2, 7.4
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import AgentConfig
from domain.chat.context import (
    AssistantMessage,
    ConversationContext,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.value_objects import LLMResponse, StreamingChunk, ToolCallRequest
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from infrastructure.chat.environment_context_provider import EnvironmentContextBuildError
from test.infrastructure.agent._v3_stream_helpers import install_stream_mock


def _make_react_adapter(
    tool_registry: MagicMock | None = None,
    context_builder: MagicMock | None = None,
) -> ReActAgentAdapter:
    """创建测试用 ReActAgentAdapter 实例。

    Args:
        tool_registry: 模拟的工具注册表，为 None 时创建包含一个测试工具的默认注册表。

    Returns:
        配置好的 ReActAgentAdapter 实例。
    """
    if context_builder is None:
        context_builder = MagicMock()
        context_builder.build = AsyncMock(
            side_effect=[
                ContextBuilderResult(
                    messages=[
                        UserMessage(content="builder round 1"),
                    ],
                    usage={"prompt_tokens": 1},
                    environment_injected=True,
                ),
                ContextBuilderResult(
                    messages=[
                        UserMessage(content="builder round 2"),
                    ],
                    usage={"prompt_tokens": 2},
                    environment_injected=True,
                ),
            ]
        )

    if tool_registry is None:
        tool_registry = MagicMock()
        tool_registry.get_schemas.return_value = [
            {
                "type": "function",
                "function": {
                    "name": "test_tool",
                    "description": "test",
                    "parameters": {},
                },
            }
        ]
        tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="tool result"))

    return ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=context_builder,
    )


def _make_config(
    tool_schemas: list | None = None,
    max_rounds: int = 10,
) -> AgentConfig:
    """创建测试用 AgentConfig。

    Args:
        tool_schemas: 工具 schema 列表，为 None 时使用默认测试工具 schema。
        max_rounds: Agent Loop 最大迭代轮次。

    Returns:
        AgentConfig 实例。
    """
    if tool_schemas is None:
        tool_schemas = [
            {
                "type": "function",
                "function": {
                    "name": "test_tool",
                    "description": "test",
                    "parameters": {},
                },
            }
        ]
    return AgentConfig(
        system_prompt="你是一个有用的 AI 助手。",
        tool_schemas=tool_schemas,
        model=None,
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
    )


async def _collect_chunks(
    adapter: ReActAgentAdapter,
    context: ConversationContext,
    config: AgentConfig,
    model_access: AsyncMock,
) -> list[StreamingChunk]:
    """收集 run_streaming 产出的所有 StreamingChunk。

    Args:
        adapter: ReActAgentAdapter 实例。
        context: 对话上下文。
        config: Agent 执行配置。
        model_access: 模拟的模型接入端口。

    Returns:
        所有产出的 StreamingChunk 列表。
    """
    chunks: list[StreamingChunk] = []
    async for chunk in adapter.run_streaming(context, config, model_access):
        chunks.append(chunk)
    return chunks


@pytest.mark.asyncio
async def test_streaming_agent_loop_intermediate_no_chunks() -> None:
    """验证中间轮次不产出流式分片，仅最终轮次产出单个 StreamingChunk。

    模拟 LLM 第一次返回 tool_calls（中间轮次，使用同步 chat 调用），
    第二次返回纯文本回复（同步 chat 调用，内容被包装为单个 StreamingChunk）。
    验证：
    - 仅产出一个 StreamingChunk（最终回复，finished=True）
    - 该分片包含最终文本回复内容
    - ToolRegistry.execute 被正确调用
    - 上下文包含完整消息序列
    """
    tool_call = ToolCallRequest(id="call_1", name="test_tool", arguments='{"key": "value"}')

    # 第一次调用：返回 tool_calls（中间轮次）
    first_response = LLMResponse(
        content="正在调用工具...",
        model="gpt-4o",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        tool_calls=[tool_call],
    )
    # 第二次调用：返回纯文本（最终轮次，通过同步 chat 获取）
    second_response = LLMResponse(
        content="工具执行完毕，结果如下。",
        model="gpt-4o",
        usage={"prompt_tokens": 15, "completion_tokens": 8},
        tool_calls=[],
    )

    model_access = AsyncMock()
    install_stream_mock(model_access, [first_response, second_response])

    tool_registry = MagicMock()
    tool_registry.get_schemas.return_value = [
        {
            "type": "function",
            "function": {"name": "test_tool", "description": "test", "parameters": {}},
        }
    ]
    tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="tool result"))

    adapter = _make_react_adapter(tool_registry=tool_registry)
    config = _make_config()

    context = ConversationContext()
    context.add_system_message("你是一个有用的 AI 助手。")
    context.add_user_message("请帮我查一下")

    chunks = await _collect_chunks(adapter, context, config, model_access)

    # 中间轮次产出心跳与工具进度分片（heartbeat + tool_progress_start + tool_progress_end），
    # 最终轮次产出 finished=True 的结果分片。
    # 过滤出最终分片验证核心语义不变：
    final_chunks = [c for c in chunks if c.finished is True]
    assert len(final_chunks) == 1
    assert final_chunks[0].delta_content == "工具执行完毕，结果如下。"
    assert final_chunks[0].usage == {
        "prompt_tokens": 28,
        "completion_tokens": 13,
    }

    # 心跳与工具进度分片不应 finished=True，delta_content 为空
    intermediate_chunks = [c for c in chunks if c.finished is False]
    for ic in intermediate_chunks:
        assert ic.delta_content == ""
        assert ic.metadata.get("type") in ("heartbeat", "tool_progress")

    # ToolRegistry.execute 被正确调用
    tool_registry.execute.assert_awaited_once_with(tool_call)

    # 验证上下文消息序列完整
    messages = context.get_messages()

    # system → user → assistant(tool_calls) → tool = 4 条
    # 最终的 assistant 回复由编排层追加，不在 Agent Loop 内
    assert len(messages) == 4
    assert isinstance(messages[0], SystemMessage)
    assert isinstance(messages[1], UserMessage)
    assert messages[1].content == "请帮我查一下"
    assert isinstance(messages[2], AssistantMessage)
    assert len(messages[2].tool_calls) == 1
    assert messages[2].tool_calls[0].id == "call_1"
    assert isinstance(messages[3], ToolMessage)
    assert messages[3].content == "tool result"
    assert messages[3].tool_call_id == "call_1"


@pytest.mark.asyncio
async def test_streaming_agent_loop_final_round_streams() -> None:
    """验证最大轮次场景下的 max_rounds 命中行为（PR-4 更新）。

    设置 max_rounds=2，模拟 LLM 始终返回 tool_calls。
    第 1 轮（中间轮次）使用同步 chat 调用，工具被执行后循环耗尽。
    PR-4 引入业内共识方案：当 ``terminated_reason="max_rounds"`` 时，
    流式入口跳过 ``_stream_final_round``，直接产出
    ``delta_content=""`` + ``metadata.terminated_reason="max_rounds"`` 终止分片。

    验证：
    - 最终 chunk 为 ``finished=True`` 且携带 ``terminated_reason="max_rounds"``;
    - ``model_access.chat`` 被调用恰好 1 次（中间轮次）;
    - ``model_access.stream`` 未被调用（max_rounds 命中跳过 stream）。
    """
    tool_call = ToolCallRequest(id="call_x", name="test_tool", arguments='{"a": "b"}')

    # 中间轮次：始终返回 tool_calls
    always_tool_response = LLMResponse(
        content="继续调用工具",
        model="gpt-4o",
        usage={"prompt_tokens": 5, "completion_tokens": 3},
        tool_calls=[tool_call],
    )

    model_access = AsyncMock()
    counter = install_stream_mock(model_access, [always_tool_response])

    adapter = _make_react_adapter()
    config = _make_config(max_rounds=2)

    context = ConversationContext()
    context.add_system_message("你是一个有用的 AI 助手。")
    context.add_user_message("测试流式最终轮")

    chunks = await _collect_chunks(adapter, context, config, model_access)

    # PR-4: max_rounds 命中后产出 terminated_reason 终止分片
    finished_chunks = [c for c in chunks if c.finished]
    assert len(finished_chunks) == 1
    assert finished_chunks[0].delta_content == ""
    assert finished_chunks[0].metadata.get("terminated_reason") == "max_rounds"

    # v3：max_rounds=2 → terminal_round=1 → 1 次 stream（中间轮）；
    # 命中 max_rounds 时跳过最后一轮 _stream_final_round。
    assert counter.call_count == 1


@pytest.mark.asyncio
async def test_streaming_agent_loop_max_rounds_streams() -> None:
    """验证 max_rounds=1 时直接使用流式调用产出，不经过中间轮次。

    设置 max_rounds=1，由于只有一轮且为最后一轮，直接使用 model_access.stream。
    验证：
    - model_access.chat 未被调用（无中间轮次）
    - 正确产出所有 StreamingChunk
    """
    model_access = AsyncMock()

    # 流式产出分片
    async def mock_stream(*args, **kwargs):
        """模拟流式响应的异步生成器。"""
        yield StreamingChunk(delta_content="直接", finished=False)
        yield StreamingChunk(delta_content="流式", finished=False)
        yield StreamingChunk(
            delta_content="输出",
            finished=True,
            usage={"prompt_tokens": 3, "completion_tokens": 3},
        )

    model_access.stream = mock_stream

    adapter = _make_react_adapter()
    config = _make_config(max_rounds=1)

    context = ConversationContext()
    context.add_system_message("你是一个有用的 AI 助手。")
    context.add_user_message("测试直接流式")

    chunks = await _collect_chunks(adapter, context, config, model_access)

    # 正确产出所有 StreamingChunk
    assert len(chunks) == 3
    assert chunks[0].delta_content == "直接"
    assert chunks[0].finished is False
    assert chunks[1].delta_content == "流式"
    assert chunks[1].finished is False
    assert chunks[2].delta_content == "输出"
    assert chunks[2].finished is True
    assert chunks[2].usage == {
        "prompt_tokens": 4,
        "completion_tokens": 3,
    }

    # model_access.chat 未被调用
    model_access.chat.assert_not_awaited()


@pytest.mark.asyncio
async def test_streaming_agent_loop_builder_failure_skips_model_calls() -> None:
    """验证 builder 失败时不执行同步或流式主模型调用。"""
    context_builder = MagicMock()
    context_builder.build = AsyncMock(
        side_effect=EnvironmentContextBuildError("环境上下文生成失败")
    )
    model_access = AsyncMock()

    async def fail_stream(*args, **kwargs):
        """若被调用则说明 fail-fast 行为失效。"""
        raise AssertionError("stream should not be called")
        yield  # pragma: no cover

    model_access.stream = fail_stream

    adapter = _make_react_adapter(context_builder=context_builder)
    config = _make_config(max_rounds=1)

    context = ConversationContext()
    context.add_user_message("测试 builder 失败")

    with pytest.raises(EnvironmentContextBuildError):
        await _collect_chunks(adapter, context, config, model_access)

    context_builder.build.assert_awaited_once()
    model_access.chat.assert_not_awaited()
