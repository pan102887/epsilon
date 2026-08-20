"""上下文压缩策略单元测试模块。

包含边界条件测试、配置测试和集成测试（使用 mock），验证 SlidingWindowCompactionAdapter
的边界行为、配置参数校验，以及 ChatServiceAdapter 与压缩策略的集成编排逻辑。

测试覆盖的需求：
- 需求 1.4: compact 空列表返回空列表
- 需求 3.6: 仅 system 消息原样返回
- 需求 4.2, 4.3, 4.4: ChatServiceAdapter 调用 compact 后传压缩结果给 ModelAccessPort
- 需求 4.5: 保存完整历史到 SessionContextStorePort
- 需求 5.3, 5.4: 默认和自定义 max_messages 配置
- 需求 7.3: 非 system 为 0 时仅返回 system
- 需求 7.4: tool 消息视为非 system 参与裁剪
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.chat.context import (
    AssistantMessage,
    BaseMessage,
    ConversationContext,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from domain.chat.value_objects import ChatRequestVO, ContextBuilderResult, ContextCompactionResult
from domain.model_access.value_objects import LLMResponse, StreamingChunk
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from infrastructure.chat.sliding_window_compaction_adapter import SlidingWindowCompactionAdapter
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies

# ============================================================
# 边界条件测试
# ============================================================


class TestCompactBoundaryConditions:
    """SlidingWindowCompactionAdapter 边界条件测试。"""

    def test_compact_empty_list_returns_empty(self) -> None:
        """compact 接收空列表时应返回空列表。

        验证需求 1.4：当 compact 方法接收空消息列表时，返回空列表。
        """
        adapter = SlidingWindowCompactionAdapter(max_messages=10)
        result = adapter.compact_messages([])
        assert result == []

    def test_compact_only_system_messages_returns_all(self) -> None:
        """仅包含 system 消息的列表应原样返回全部 system 消息。

        验证需求 3.6：对于仅包含 system 消息的输入列表，原样返回全部 system 消息。
        """
        messages = [
            SystemMessage(content="你是一个助手"),
            SystemMessage(content="请使用中文回答"),
        ]
        adapter = SlidingWindowCompactionAdapter(max_messages=5)
        result = adapter.compact_messages(messages)
        assert len(result) == 2
        assert all(m.role == "system" for m in result)
        assert result[0].content == "你是一个助手"
        assert result[1].content == "请使用中文回答"

    def test_compact_no_non_system_returns_only_system(self) -> None:
        """system 消息 + 0 条非 system 消息时，仅返回 system 消息。

        验证需求 7.3：当非 system 消息数量为 0 时，仅返回 system 消息列表。
        """
        messages = [
            SystemMessage(content="系统提示词"),
        ]
        adapter = SlidingWindowCompactionAdapter(max_messages=50)
        result = adapter.compact_messages(messages)
        assert len(result) == 1
        assert result[0].role == "system"
        assert result[0].content == "系统提示词"

    def test_compact_tool_messages_treated_as_non_system(self) -> None:
        """tool 消息应被视为非 system 消息参与滑动窗口裁剪。

        验证需求 7.4：tool 角色消息参与滑动窗口裁剪，不被当作 system 消息保留。
        配对保护改造后：孤儿 ToolMessage（无对应 assistant tool_calls）会被丢弃。
        因此构造有完整配对的消息序列来验证。
        """
        from domain.model_access.value_objects import ToolCallRequest as TCR

        messages = [
            SystemMessage(content="系统提示词"),
            UserMessage(content="旧消息1"),
            AssistantMessage(content="", tool_calls=[TCR(id="tc1", name="search", arguments="{}")]),
            ToolMessage(content="工具结果1", tool_name="search", tool_call_id="tc1"),
            UserMessage(content="新消息"),
            AssistantMessage(content="旧回复1"),
        ]
        # max_messages=3，配对保护路径；最近 3 条非 system 从尾部反向：
        # AssistantMessage("旧回复1"), UserMessage("新消息"), 再尝试 tool group(2条超配额)
        adapter = SlidingWindowCompactionAdapter(max_messages=3)
        result = adapter.compact_messages(messages)

        system_msgs = [m for m in result if m.role == "system"]
        non_system_msgs = [m for m in result if m.role != "system"]

        assert len(system_msgs) == 1
        # 配对保护下 tool group 需要 2 条（assistant + tool），只剩 1 配额不够，整组丢弃
        assert len(non_system_msgs) <= 3
        # 每条 ToolMessage 都有对应 assistant 配对
        for m in non_system_msgs:
            if isinstance(m, ToolMessage):
                assert any(
                    isinstance(am, AssistantMessage)
                    and any(tc.id == m.tool_call_id for tc in am.tool_calls)
                    for am in non_system_msgs
                )

    @pytest.mark.asyncio
    async def test_async_compact_returns_context_compaction_result(self) -> None:
        """异步 compact 返回 ContextCompactionResult 且不创建摘要。"""
        messages = [SystemMessage(content="系统"), UserMessage(content="用户")]
        adapter = SlidingWindowCompactionAdapter(max_messages=1)

        result = await adapter.compact(messages)

        assert isinstance(result, ContextCompactionResult)
        assert result.messages == [messages[0], messages[1]]
        assert result.usage == {}
        assert result.summary_created is False


# ============================================================
# 配置测试
# ============================================================


class TestCompactConfiguration:
    """SlidingWindowCompactionAdapter 配置参数测试。"""

    def test_default_max_messages_is_50(self) -> None:
        """默认 max_messages 应为 50。

        验证需求 5.3：SlidingWindowCompactionAdapter 默认 max_messages 为 50。
        """
        adapter = SlidingWindowCompactionAdapter()
        assert adapter._max_messages == 50

    def test_custom_max_messages(self) -> None:
        """自定义 max_messages 值应生效。

        验证需求 5.4：配置指定不同 max_messages 值时使用配置值。
        """
        adapter = SlidingWindowCompactionAdapter(max_messages=10)
        assert adapter._max_messages == 10

    def test_max_messages_zero_raises_value_error(self) -> None:
        """max_messages 为 0 时应抛出 ValueError。"""
        with pytest.raises(ValueError, match="max_messages 必须为正整数"):
            SlidingWindowCompactionAdapter(max_messages=0)

    def test_max_messages_negative_raises_value_error(self) -> None:
        """max_messages 为负数时应抛出 ValueError。"""
        with pytest.raises(ValueError, match="max_messages 必须为正整数"):
            SlidingWindowCompactionAdapter(max_messages=-1)


# ============================================================
# 集成测试（mock）
# ============================================================


def _build_mock_dependencies() -> tuple[AsyncMock, AsyncMock, MagicMock]:
    """构建 ChatServiceAdapter 所需的 mock 依赖。

    Returns:
        (session_store, model_access, context_builder) 三个 mock 对象的元组
    """
    session_store = AsyncMock()
    model_access = AsyncMock()
    context_builder = MagicMock()
    context_builder.build = AsyncMock()
    return session_store, model_access, context_builder


def _builder_result(messages: "list[BaseMessage] | None" = None) -> ContextBuilderResult:
    """构造 ChatServiceAdapter 直接模型路径使用的 builder 结果。"""

    return ContextBuilderResult(
        messages=messages
        or [
            SystemMessage(content="<environment_context>safe</environment_context>"),
            UserMessage(content="builder message"),
        ],
        usage={"summary_tokens": 3},
        environment_injected=True,
    )


def _build_fake_prompt_registry() -> MagicMock:
    """构建 fake PromptRegistryPort，返回 chat-default@v1 的 LoadedPrompt。"""
    registry = MagicMock()
    registry.get.return_value = LoadedPrompt(
        prompt_id="chat-default@v1",
        name="chat-default",
        version="v1",
        content="你是一个助手",
    )
    return registry


def _build_chat_adapter(
    *,
    session_store: AsyncMock,
    model_registry: MagicMock,
    prompt_registry: MagicMock,
    context_builder: MagicMock,
) -> ChatServiceAdapter:
    """构造显式注入 application workflow/service 的 ChatServiceAdapter。"""

    agent = MagicMock()
    loaded_prompt = prompt_registry.get.return_value
    return ChatServiceAdapter(
        session_store=session_store,
        model_registry=model_registry,
        prompt_registry=prompt_registry,
        context_builder=context_builder,
        agent=agent,
        tool_calling_enabled=False,
        max_tool_rounds=10,
        tool_schemas=[],
        **make_chat_adapter_dependencies(
            session_store=session_store,
            model_registry=model_registry,
            loaded_prompt=loaded_prompt,
            agent=agent,
            tool_schemas=[],
            max_tool_rounds=10,
        ),
    )


def _build_context_with_messages() -> ConversationContext:
    """构建包含预设消息的 ConversationContext，用于集成测试。

    Returns:
        包含 1 条 system 消息和 2 条对话消息的 ConversationContext
    """
    ctx = ConversationContext()
    ctx.add_system_message("你是一个助手")
    ctx.add_user_message("你好")
    ctx.add_assistant_message("你好！有什么可以帮你的？")
    return ctx


class TestChatServiceAdapterIntegration:
    """ChatServiceAdapter 与上下文构建端口的集成测试（使用 mock）。

    验证 ChatServiceAdapter 在 chat() 和 stream_chat() 流程中正确调用
    ContextBuilderPort 构建模型输入，并将 builder 结果传给 ModelAccessPort，
    同时保存完整的未压缩历史到 SessionContextStorePort。
    """

    @pytest.mark.asyncio
    async def test_chat_calls_builder_and_passes_result_to_model(self) -> None:
        """chat() 应调用 builder，并将 messages_payload 传给 ModelAccessPort。

        验证需求 1.6, 9.3：
        - ChatServiceAdapter.chat() 在构建请求前调用 builder
        - 将 builder 返回的 messages_payload 传递给 ModelAccessPort
        """
        session_store, model_access, context_builder = _build_mock_dependencies()

        # 模拟 session_store.load 返回包含历史消息的上下文
        existing_context = _build_context_with_messages()
        session_store.load.return_value = existing_context

        builder_messages = [
            SystemMessage(content="<environment_context>safe</environment_context>"),
            UserMessage(content="新问题"),
        ]
        context_builder.build.return_value = _builder_result(messages=builder_messages)

        # 模拟模型响应
        model_access.chat.return_value = LLMResponse(
            content="这是回复",
            model="gpt-4o",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

        # 构建 mock ModelRegistryPort，包装 model_access
        model_registry = MagicMock()
        model_registry.get_adapter_for_model = MagicMock(return_value=model_access)
        model_registry.get_default_model = MagicMock(return_value="gpt-4o")

        adapter = _build_chat_adapter(
            session_store=session_store,
            model_registry=model_registry,
            prompt_registry=_build_fake_prompt_registry(),
            context_builder=context_builder,
        )

        request = ChatRequestVO(session_id="test-session", message="新问题")
        result = await adapter.chat(request)

        # 验证 builder 被调用，且参数是完整消息列表（包含新追加的用户消息）
        context_builder.build.assert_awaited_once()
        build_arg = context_builder.build.call_args.args[0]
        assert isinstance(build_arg, list)
        # 原有 3 条 + 新追加的 1 条用户消息 = 4 条
        assert len(build_arg) == 4
        assert build_arg[-1].role == "user"
        assert build_arg[-1].content == "新问题"
        assert context_builder.build.call_args.kwargs["model_access"] is model_access
        assert context_builder.build.call_args.kwargs["model"] == "gpt-4o"

        # 验证 model_access.chat 接收的是 builder 序列化消息
        model_access.chat.assert_called_once()
        chat_request = model_access.chat.call_args[0][0]
        assert chat_request.messages == builder_messages

        # 验证返回结果
        assert result.reply == "这是回复"
        assert result.usage == {
            "summary_tokens": 3,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }

    @pytest.mark.asyncio
    async def test_stream_chat_calls_builder_and_passes_result_to_model(self) -> None:
        """stream_chat() 应调用 builder，并将 messages_payload 传给 ModelAccessPort。

        验证需求 1.6, 9.3：
        - ChatServiceAdapter.stream_chat() 在构建请求前调用 builder
        - 将 builder 返回的 messages_payload 传递给 ModelAccessPort
        """
        session_store, model_access, context_builder = _build_mock_dependencies()

        existing_context = _build_context_with_messages()
        session_store.load.return_value = existing_context

        builder_messages = [
            SystemMessage(content="<environment_context>safe</environment_context>"),
            UserMessage(content="流式问题"),
        ]
        context_builder.build.return_value = _builder_result(messages=builder_messages)

        # 模拟流式响应：返回异步迭代器
        captured_requests = []

        async def mock_stream(request: object) -> AsyncMock:
            """模拟流式响应的异步生成器。"""
            captured_requests.append(request)
            chunks = [
                StreamingChunk(delta_content="这是", finished=False),
                StreamingChunk(
                    delta_content="流式回复",
                    finished=True,
                    usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                ),
            ]
            for chunk in chunks:
                yield chunk

        model_access.stream = mock_stream

        # 构建 mock ModelRegistryPort，包装 model_access
        model_registry = MagicMock()
        model_registry.get_adapter_for_model = MagicMock(return_value=model_access)
        model_registry.get_default_model = MagicMock(return_value="gpt-4o")

        adapter = _build_chat_adapter(
            session_store=session_store,
            model_registry=model_registry,
            prompt_registry=_build_fake_prompt_registry(),
            context_builder=context_builder,
        )

        request = ChatRequestVO(session_id="test-session", message="流式问题")
        collected_chunks: list[StreamingChunk] = []
        async for chunk in adapter.stream_chat(request):
            collected_chunks.append(chunk)

        # 验证 builder 被调用
        context_builder.build.assert_awaited_once()
        build_arg = context_builder.build.call_args.args[0]
        assert len(build_arg) == 4
        assert build_arg[-1].content == "流式问题"
        assert context_builder.build.call_args.kwargs["model"] == "gpt-4o"
        assert captured_requests[0].messages == builder_messages

        # 验证收到了流式分片
        assert len(collected_chunks) == 2
        assert collected_chunks[-1].finished is True
        assert collected_chunks[-1].usage == {
            "summary_tokens": 3,
            "prompt_tokens": 10,
            "completion_tokens": 5,
            "total_tokens": 15,
        }

    @pytest.mark.asyncio
    async def test_chat_saves_full_history_not_compacted(self) -> None:
        """chat() 保存到 SessionContextStorePort 的应是完整历史，而非压缩后的版本。

        验证需求 4.5：ChatServiceAdapter 将完整的消息列表（包含用户消息和助手回复，
        未经压缩）保存到 SessionContextStorePort，确保对话历史的完整性。
        """
        session_store, model_access, context_builder = _build_mock_dependencies()

        existing_context = _build_context_with_messages()
        session_store.load.return_value = existing_context

        context_builder.build.return_value = _builder_result()

        model_access.chat.return_value = LLMResponse(
            content="助手回复",
            model="gpt-4o",
            usage={"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        )

        # 构建 mock ModelRegistryPort，包装 model_access
        model_registry = MagicMock()
        model_registry.get_adapter_for_model = MagicMock(return_value=model_access)
        model_registry.get_default_model = MagicMock(return_value="gpt-4o")

        adapter = _build_chat_adapter(
            session_store=session_store,
            model_registry=model_registry,
            prompt_registry=_build_fake_prompt_registry(),
            context_builder=context_builder,
        )

        request = ChatRequestVO(session_id="test-session", message="保存测试")
        await adapter.chat(request)

        # 验证 session_store.save 被调用
        session_store.save.assert_called_once()
        saved_session_id = session_store.save.call_args[0][0]
        saved_context = session_store.save.call_args[0][1]

        assert saved_session_id == "test-session"

        # 保存的上下文应包含完整历史：
        # 原有 3 条 + 新用户消息 + 助手回复 = 5 条
        saved_messages = saved_context.get_messages()
        assert len(saved_messages) == 5
        assert saved_messages[0].role == "system"
        assert saved_messages[1].role == "user"
        assert saved_messages[1].content == "你好"
        assert saved_messages[2].role == "assistant"
        assert saved_messages[2].content == "你好！有什么可以帮你的？"
        assert saved_messages[3].role == "user"
        assert saved_messages[3].content == "保存测试"
        assert saved_messages[4].role == "assistant"
        assert saved_messages[4].content == "助手回复"
        assert "<environment_context>" not in str(saved_context.to_dict())
