"""ReActAgentAdapter 核心场景单元测试模块。

对 ReActAgentAdapter 进行核心场景的单元测试，验证：
- 单轮无工具调用：LLM 直接返回文本，AgentResult 正确
- 多轮工具调用：模拟 2 轮工具调用 + 1 轮文本回复，上下文消息序列正确
- 达到 max_rounds：验证返回最后一轮响应
- 工具异常：模拟工具抛出异常，ToolMessage content 为异常信息
- AgentPort Protocol 结构：验证 ReActAgentAdapter 具备 AgentPort 所需的方法签名

与属性测试互补，覆盖具体示例和边界情况。

**Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.5, 4.6**
"""

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import AgentConfig, AgentResult
from domain.chat.context import (
    AssistantMessage,
    ConversationContext,
    ToolMessage,
    UserMessage,
)
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.value_objects import LLMResponse, ToolCallRequest
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from test.infrastructure.agent._v3_stream_helpers import install_stream_mock


def _make_config(
    max_rounds: int = 10,
    tool_schemas: list[dict] | None = None,
) -> AgentConfig:
    """构造测试用 AgentConfig。

    Args:
        max_rounds: 最大迭代轮次
        tool_schemas: 工具 schema 列表，默认包含 test_tool
    """
    if tool_schemas is None:
        tool_schemas = [{"type": "function", "function": {"name": "test_tool"}}]
    return AgentConfig(
        system_prompt="你是一个助手",
        tool_schemas=tool_schemas,
        model="gpt-4",
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
    )


def _make_adapter(
    tool_registry: MagicMock | None = None,
    context_builder: MagicMock | None = None,
) -> ReActAgentAdapter:
    """构造测试用 ReActAgentAdapter，使用 mock 依赖。"""
    if tool_registry is None:
        tool_registry = MagicMock()
        tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="tool result"))
    if context_builder is None:
        context_builder = MagicMock()
        context_builder.build = AsyncMock(
            side_effect=lambda msgs, **kwargs: ContextBuilderResult(
                messages=msgs,
                usage={},
            )
        )
    return ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=context_builder,
    )


class TestSingleRoundNoToolCalls:
    """单轮无工具调用场景测试。

    LLM 直接返回纯文本回复（无 tool_calls），验证 AgentResult 字段正确。
    """

    @pytest.mark.asyncio
    async def test_returns_correct_agent_result(self) -> None:
        """LLM 返回纯文本时，AgentResult 的 content、model、usage、latency_ms 均正确。

        **Validates: Requirements 4.3, 4.6**
        """
        model_access = MagicMock()
        install_stream_mock(
            model_access,
            [
                LLMResponse(
                    content="你好，有什么可以帮你的？",
                    model="gpt-4",
                    usage={"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18},
                    latency_ms=150.0,
                    tool_calls=[],
                )
            ],
        )

        adapter = _make_adapter()
        context = ConversationContext()
        context.add_user_message("你好")
        config = _make_config()

        result = await adapter.run(context, config, model_access)

        assert isinstance(result, AgentResult)
        assert result.content == "你好，有什么可以帮你的？"
        assert result.model == "gpt-4"
        assert result.usage == {"prompt_tokens": 10, "completion_tokens": 8, "total_tokens": 18}
        # v3：``latency_ms`` 由累积器测量 stream 全量耗时；具体数值取决于运行环境，
        # 此处仅断言为非负 float（v2 的 150.0 来自 LLMResponse 字段透传，v3 不复用）。
        assert isinstance(result.latency_ms, float)
        assert result.latency_ms >= 0.0

    @pytest.mark.asyncio
    async def test_model_access_called_once(self) -> None:
        """单轮无工具调用时，model_access.chat 仅被调用一次。

        **Validates: Requirements 4.3**
        """
        model_access = MagicMock()
        counter = install_stream_mock(
            model_access,
            [
                LLMResponse(
                    content="回复",
                    model="gpt-4",
                    usage={},
                    latency_ms=100.0,
                    tool_calls=[],
                )
            ],
        )

        adapter = _make_adapter()
        context = ConversationContext()
        context.add_user_message("测试")

        await adapter.run(context, _make_config(), model_access)

        # v3：ReAct 内部仅通过 stream 推进，单轮 → 1 次 stream 调用。
        assert counter.call_count == 1


class TestMultiRoundToolCalls:
    """多轮工具调用场景测试。

    模拟 2 轮工具调用 + 1 轮纯文本回复，验证上下文消息序列和 token 累计。
    """

    @pytest.mark.asyncio
    async def test_context_message_sequence(self) -> None:
        """2 轮工具调用 + 1 轮文本回复后，上下文包含正确的消息序列。

        预期消息序列：
        UserMessage → AssistantMessage(tool_calls_1) → ToolMessage_1
        → AssistantMessage(tool_calls_2) → ToolMessage_2
        → （最终纯文本回复不追加到上下文，由 AgentResult 返回）

        **Validates: Requirements 4.3, 4.5, 4.6**
        """
        tool_call_1 = ToolCallRequest(id="tc1", name="read_file", arguments='{"path": "a.txt"}')
        tool_call_2 = ToolCallRequest(id="tc2", name="read_file", arguments='{"path": "b.txt"}')

        responses = [
            # 第 1 轮：返回工具调用
            LLMResponse(
                content="",
                model="gpt-4",
                usage={"prompt_tokens": 10, "completion_tokens": 5},
                latency_ms=100.0,
                tool_calls=[tool_call_1],
            ),
            # 第 2 轮：返回工具调用
            LLMResponse(
                content="",
                model="gpt-4",
                usage={"prompt_tokens": 20, "completion_tokens": 10},
                latency_ms=200.0,
                tool_calls=[tool_call_2],
            ),
            # 第 3 轮：返回纯文本
            LLMResponse(
                content="文件内容已读取完毕",
                model="gpt-4",
                usage={"prompt_tokens": 30, "completion_tokens": 15},
                latency_ms=150.0,
                tool_calls=[],
            ),
        ]

        model_access = MagicMock()
        install_stream_mock(model_access, responses)

        tool_registry = MagicMock()
        tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="file content"))

        adapter = _make_adapter(tool_registry=tool_registry)
        context = ConversationContext()
        context.add_user_message("请读取文件")

        result = await adapter.run(
            context,
            _make_config(
                tool_schemas=[
                    {"type": "function", "function": {"name": "read_file"}},
                ]
            ),
            model_access,
        )

        # 验证最终结果
        assert result.content == "文件内容已读取完毕"
        assert result.model == "gpt-4"

        # 验证 token 累计（3 轮累加）
        assert result.usage == {
            "prompt_tokens": 60,
            "completion_tokens": 30,
        }

        # 验证上下文消息序列
        messages = context.get_messages()
        # UserMessage + SystemMessage（幂等注入） + (AssistantMessage + ToolMessage) * 2 = 6 条
        assert len(messages) == 6

        # 第 1 条：原始 UserMessage
        assert messages[0].role == "user"
        assert messages[0].content == "请读取文件"

        # 第 2 条：幂等注入的 SystemMessage
        assert messages[1].role == "system"
        assert messages[1].content == "你是一个助手"

        # 第 3 条：第 1 轮 AssistantMessage（含 tool_calls）
        assert isinstance(messages[2], AssistantMessage)
        assert len(messages[2].tool_calls) == 1
        assert messages[2].tool_calls[0].id == "tc1"

        # 第 4 条：第 1 轮 ToolMessage
        assert isinstance(messages[3], ToolMessage)
        assert messages[3].content == "file content"
        assert messages[3].tool_call_id == "tc1"

        # 第 5 条：第 2 轮 AssistantMessage（含 tool_calls）
        assert isinstance(messages[4], AssistantMessage)
        assert len(messages[4].tool_calls) == 1
        assert messages[4].tool_calls[0].id == "tc2"

        # 第 6 条：第 2 轮 ToolMessage
        assert isinstance(messages[5], ToolMessage)
        assert messages[5].content == "file content"
        assert messages[5].tool_call_id == "tc2"

    @pytest.mark.asyncio
    async def test_model_access_called_three_times(self) -> None:
        """2 轮工具调用 + 1 轮文本回复，model_access.chat 被调用 3 次。

        **Validates: Requirements 4.3**
        """
        tool_call = ToolCallRequest(id="tc1", name="tool", arguments='{"x": 1}')

        responses = [
            LLMResponse(content="", model="m", usage={}, tool_calls=[tool_call]),
            LLMResponse(content="", model="m", usage={}, tool_calls=[tool_call]),
            LLMResponse(content="done", model="m", usage={}, tool_calls=[]),
        ]

        model_access = MagicMock()
        counter = install_stream_mock(model_access, responses)

        tool_registry = MagicMock()
        tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="ok"))

        adapter = _make_adapter(tool_registry=tool_registry)
        context = ConversationContext()
        context.add_user_message("go")

        await adapter.run(
            context,
            _make_config(
                tool_schemas=[
                    {"type": "function", "function": {"name": "tool"}},
                ]
            ),
            model_access,
        )

        # v3：ReAct 内部全程 stream。3 轮 → 3 次 stream 调用。
        assert counter.call_count == 3


class TestMaxRounds:
    """达到 max_rounds 上限场景测试。

    设置 max_rounds=2，两轮均返回 tool_calls，验证返回最后一轮的响应内容。
    """

    @pytest.mark.asyncio
    async def test_returns_last_round_response_at_max_rounds(self) -> None:
        """达到 max_rounds 上限时，返回最后一轮 LLM 响应的 content。

        **Validates: Requirements 4.3, 4.6**
        """
        tool_call_1 = ToolCallRequest(id="tc1", name="tool_a", arguments='{"k": "v"}')
        tool_call_2 = ToolCallRequest(id="tc2", name="tool_b", arguments='{"k": "v"}')

        responses = [
            # 第 1 轮：工具调用
            LLMResponse(
                content="thinking round 1",
                model="gpt-4",
                usage={"prompt_tokens": 10, "completion_tokens": 5},
                latency_ms=100.0,
                tool_calls=[tool_call_1],
            ),
            # 第 2 轮（最后一轮）：仍然是工具调用
            LLMResponse(
                content="thinking round 2",
                model="gpt-4",
                usage={"prompt_tokens": 20, "completion_tokens": 10},
                latency_ms=200.0,
                tool_calls=[tool_call_2],
            ),
        ]

        model_access = MagicMock()
        counter = install_stream_mock(model_access, responses)

        tool_registry = MagicMock()
        tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="result"))

        adapter = _make_adapter(tool_registry=tool_registry)
        context = ConversationContext()
        context.add_user_message("执行任务")
        config = _make_config(
            max_rounds=2,
            tool_schemas=[
                {"type": "function", "function": {"name": "tool_a"}},
                {"type": "function", "function": {"name": "tool_b"}},
            ],
        )

        result = await adapter.run(context, config, model_access)

        # 达到 max_rounds：v3 业内共识方案下 ``terminated_reason="max_rounds"``，
        # 最后一轮 tool_calls 响应的 ``content`` 通常为空（v2 PR-4 已对齐）。
        assert result.terminated_reason == "max_rounds"
        assert result.content == "thinking round 2"
        assert result.model == "gpt-4"
        assert result.usage == {"prompt_tokens": 30, "completion_tokens": 15}
        # v3：2 轮 → 2 次 stream（均为中间轮，max_rounds 命中跳过最后一轮 stream）
        assert counter.call_count == 2


class TestToolException:
    """工具执行异常场景测试。

    模拟工具抛出异常，验证 ToolMessage content 为异常信息字符串，且循环继续。
    """

    @pytest.mark.asyncio
    async def test_tool_exception_captured_as_tool_message(self) -> None:
        """工具抛出异常时，异常信息作为 ToolMessage content 追加到上下文，循环继续。

        **Validates: Requirements 4.5**
        """
        tool_call = ToolCallRequest(id="tc1", name="failing_tool", arguments='{"x": 1}')

        responses = [
            # 第 1 轮：工具调用（工具会抛异常）
            LLMResponse(
                content="",
                model="gpt-4",
                usage={"prompt_tokens": 10, "completion_tokens": 5},
                latency_ms=100.0,
                tool_calls=[tool_call],
            ),
            # 第 2 轮：LLM 收到异常信息后返回纯文本
            LLMResponse(
                content="工具执行失败，请检查参数",
                model="gpt-4",
                usage={"prompt_tokens": 20, "completion_tokens": 10},
                latency_ms=150.0,
                tool_calls=[],
            ),
        ]

        model_access = MagicMock()
        install_stream_mock(model_access, responses)

        tool_registry = MagicMock()
        tool_registry.execute = AsyncMock(side_effect=RuntimeError("文件不存在: /tmp/missing.txt"))

        adapter = _make_adapter(tool_registry=tool_registry)
        context = ConversationContext()
        context.add_user_message("读取文件")

        result = await adapter.run(
            context,
            _make_config(
                tool_schemas=[
                    {"type": "function", "function": {"name": "failing_tool"}},
                ]
            ),
            model_access,
        )

        # 循环继续，最终返回第 2 轮的纯文本回复
        assert result.content == "工具执行失败，请检查参数"

        # 验证上下文中 ToolMessage 的 content 为异常信息
        messages = context.get_messages()
        tool_messages = [m for m in messages if isinstance(m, ToolMessage)]
        assert len(tool_messages) == 1
        assert tool_messages[0].content == "文件不存在: /tmp/missing.txt"
        assert tool_messages[0].tool_call_id == "tc1"


class TestAgentPortProtocolStructure:
    """AgentPort Protocol 结构验证测试。

    AgentPort 使用 Protocol 定义但未标记 @runtime_checkable，
    因此通过检查 ReActAgentAdapter 是否具备所需方法及其签名来验证协议满足性。
    """

    def test_has_run_method(self) -> None:
        """验证 ReActAgentAdapter 具有 run 方法。

        **Validates: Requirements 4.1**
        """
        assert hasattr(ReActAgentAdapter, "run")
        assert callable(ReActAgentAdapter.run)

    def test_has_run_streaming_method(self) -> None:
        """验证 ReActAgentAdapter 具有 run_streaming 方法。

        **Validates: Requirements 4.1**
        """
        assert hasattr(ReActAgentAdapter, "run_streaming")
        assert callable(ReActAgentAdapter.run_streaming)

    def test_run_is_async(self) -> None:
        """验证 run 方法是异步协程函数。

        **Validates: Requirements 4.1, 4.3**
        """
        assert inspect.iscoroutinefunction(ReActAgentAdapter.run)

    def test_run_signature_has_correct_parameters(self) -> None:
        """验证 run 方法签名包含 context、config、model_access 三个参数。

        **Validates: Requirements 4.1, 4.2**
        """
        sig = inspect.signature(ReActAgentAdapter.run)
        param_names = list(sig.parameters.keys())
        # 排除 self
        assert "self" in param_names
        assert "context" in param_names
        assert "config" in param_names
        assert "model_access" in param_names

    def test_run_streaming_signature_has_correct_parameters(self) -> None:
        """验证 run_streaming 方法签名包含 context、config、model_access 三个参数。

        **Validates: Requirements 4.1, 4.4**
        """
        sig = inspect.signature(ReActAgentAdapter.run_streaming)
        param_names = list(sig.parameters.keys())
        assert "self" in param_names
        assert "context" in param_names
        assert "config" in param_names
        assert "model_access" in param_names


class TestCompactionUsageAccumulation:
    """摘要压缩 usage 累计测试。"""

    @pytest.mark.asyncio
    async def test_run_accumulates_summary_usage(self) -> None:
        """run 累加摘要 usage 与主模型 usage。"""
        context_builder = MagicMock()
        context_builder.build = AsyncMock(
            return_value=ContextBuilderResult(
                messages=[UserMessage(content="hello")],
                usage={"summary_tokens": 3},
            )
        )
        model_access = MagicMock()
        install_stream_mock(
            model_access,
            [
                LLMResponse(
                    content="final",
                    model="gpt-4",
                    usage={"prompt_tokens": 2},
                )
            ],
        )

        result = await _make_adapter(context_builder=context_builder).run(
            ConversationContext(),
            _make_config(),
            model_access,
        )

        assert result.usage == {"summary_tokens": 3, "prompt_tokens": 2}

    @pytest.mark.asyncio
    async def test_run_streaming_merges_summary_usage_on_final_chunk(self) -> None:
        """run_streaming 在最终 chunk 合并摘要 usage。"""
        from domain.model_access.value_objects import StreamingChunk

        context_builder = MagicMock()
        context_builder.build = AsyncMock(
            return_value=ContextBuilderResult(
                messages=[UserMessage(content="hello")],
                usage={"summary_tokens": 3},
            )
        )
        model_access = MagicMock()

        async def stream(_request):
            yield StreamingChunk(
                delta_content="final",
                finished=True,
                usage={"prompt_tokens": 2},
            )

        model_access.stream = stream

        chunks = [
            chunk
            async for chunk in _make_adapter(context_builder=context_builder).run_streaming(
                ConversationContext(),
                _make_config(max_rounds=1),
                model_access,
            )
        ]

        assert chunks[-1].usage == {"summary_tokens": 3, "prompt_tokens": 2}

    @pytest.mark.asyncio
    async def test_run_events_merges_summary_usage_on_done(self) -> None:
        """run_events 在 assistant_done 合并摘要 usage。"""
        from domain.model_access.value_objects import StreamingChunk

        context_builder = MagicMock()
        context_builder.build = AsyncMock(
            return_value=ContextBuilderResult(
                messages=[UserMessage(content="hello")],
                usage={"summary_tokens": 3},
            )
        )
        model_access = MagicMock()

        async def stream(_request):
            yield StreamingChunk(delta_content="final", finished=False)
            yield StreamingChunk(
                delta_content="",
                finished=True,
                usage={"prompt_tokens": 2},
            )

        model_access.stream = stream

        events = [
            event
            async for event in _make_adapter(context_builder=context_builder).run_events(
                ConversationContext(),
                _make_config(max_rounds=1),
                model_access,
            )
        ]

        done = next(event for event in events if event.kind == "assistant_done")
        assert done.usage == {"summary_tokens": 3, "prompt_tokens": 2}
