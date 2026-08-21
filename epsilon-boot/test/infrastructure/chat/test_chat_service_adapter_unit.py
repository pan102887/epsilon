"""ChatServiceAdapter 单元测试模块。

验证 ChatServiceAdapter 构造 AgentConfig 时不显式传递 allowed_tool_names，
依赖 AgentConfig.__post_init__ 自动从 tool_schemas 提取默认值，确保向后兼容。

同时覆盖 PromptRegistry 接入：
- 构造期恰好调用一次 ``prompt_registry.get('chat-default')``
- ``AgentConfig.system_prompt`` 等于 ``LoadedPrompt.content`` 后追加路径规范文案
- ``ChatResponseVO.prompt_id`` 等于 ``LoadedPrompt.prompt_id``
- 多次调用 ``chat`` 后 ``_loaded_prompt`` / ``_prompt_id`` 不变

**Validates: Requirements 4.3, 4.4, 4.5, 6.1, 6.2, 6.6, 7.1**
"""

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.value_objects import AgentConfig, AgentResult
from domain.chat.context import BaseMessage, ConversationContext, SystemMessage, UserMessage
from domain.chat.value_objects import ChatRequestVO, ContextBuilderResult
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import ChatRequest, LLMResponse, StreamingChunk
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from infrastructure.chat.environment_context_provider import EnvironmentContextBuildError
from infrastructure.prompt.workspace_guidance import WORKSPACE_PATH_GUIDANCE
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies


def _direct_adapter(
    *,
    session_store: MagicMock,
    model_access: MagicMock,
    context_builder: MagicMock,
) -> ChatServiceAdapter:
    """构造直接 LLM 路径 ChatServiceAdapter。"""
    model_registry = MagicMock()
    model_registry.get_default_model.return_value = "test-model"
    model_registry.get_adapter_for_model.return_value = model_access
    loaded_prompt = LoadedPrompt(
        prompt_id="chat-default@v1",
        name="chat-default",
        version="v1",
        content="你是助手",
    )
    agent = MagicMock()
    return ChatServiceAdapter(
        session_store=session_store,
        model_registry=model_registry,
        prompt_registry=MagicMock(
            get=MagicMock(
                return_value=loaded_prompt
            )
        ),
        context_builder=context_builder,
        agent=agent,
        tool_calling_enabled=False,
        max_tool_rounds=5,
        tool_schemas=[],
        **make_chat_adapter_dependencies(
            session_store=session_store,
            model_registry=model_registry,
            loaded_prompt=loaded_prompt,
            agent=agent,
            tool_schemas=[],
            max_tool_rounds=5,
        ),
    )


def _builder_result(
    *,
    messages: "list[BaseMessage] | None" = None,
    usage: dict[str, int] | None = None,
) -> ContextBuilderResult:
    """构造默认包含环境上下文标记的 ContextBuilderResult。"""
    return ContextBuilderResult(
        messages=messages
        or [
            SystemMessage(content="<environment_context>safe</environment_context>"),
            UserMessage(content="builder hello"),
        ],
        usage=usage or {"summary_tokens": 3},
        environment_injected=True,
    )


class TestChatServiceAdapterCompactionUsage:
    """ChatServiceAdapter 直接 LLM 路径 builder usage 合并测试。"""

    @pytest.mark.asyncio
    async def test_chat_merges_summary_usage_and_saves_full_history(self) -> None:
        """chat 使用 builder 消息并合并 usage，保存完整历史而非环境上下文。"""
        context = ConversationContext()
        session_store = MagicMock()
        session_store.load = AsyncMock(return_value=context)
        session_store.save = AsyncMock()
        model_access = MagicMock()
        model_access.chat = AsyncMock(
            return_value=LLMResponse(
                content="reply",
                model="test-model",
                usage={"prompt_tokens": 5},
            )
        )
        context_builder = MagicMock()
        context_builder.build = AsyncMock(return_value=_builder_result())
        adapter = _direct_adapter(
            session_store=session_store,
            model_access=model_access,
            context_builder=context_builder,
        )

        response = await adapter.chat(ChatRequestVO(session_id="s1", message="hello"))

        context_builder.build.assert_awaited_once()
        chat_request = model_access.chat.call_args.args[0]
        assert chat_request.messages == [
            SystemMessage(content="<environment_context>safe</environment_context>"),
            UserMessage(content="builder hello"),
        ]
        assert response.usage == {"summary_tokens": 3, "prompt_tokens": 5}
        saved_context = session_store.save.call_args.args[1]
        assert [message.content for message in saved_context.get_messages()] == [
            "你是助手" + WORKSPACE_PATH_GUIDANCE,
            "hello",
            "reply",
        ]
        assert "<environment_context>" not in str(saved_context.to_dict())

    @pytest.mark.asyncio
    async def test_stream_chat_merges_summary_usage_on_final_chunk(self) -> None:
        """stream_chat 使用 builder 消息并仅在最终 chunk 合并 usage。"""
        session_store = MagicMock()
        session_store.load = AsyncMock(return_value=ConversationContext())
        session_store.save = AsyncMock()
        model_access = MagicMock()

        async def stream(_request: ChatRequest) -> AsyncIterator[StreamingChunk]:
            yield StreamingChunk(delta_content="a", finished=False)
            yield StreamingChunk(
                delta_content="b",
                finished=True,
                usage={"completion_tokens": 2},
            )

        model_access.stream = stream
        captured_requests: list[ChatRequest] = []

        async def capturing_stream(request: ChatRequest) -> AsyncIterator[StreamingChunk]:
            captured_requests.append(request)
            async for chunk in stream(request):
                yield chunk

        model_access.stream = capturing_stream
        context_builder = MagicMock()
        context_builder.build = AsyncMock(return_value=_builder_result())
        adapter = _direct_adapter(
            session_store=session_store,
            model_access=model_access,
            context_builder=context_builder,
        )

        chunks = [
            chunk
            async for chunk in adapter.stream_chat(ChatRequestVO(session_id="s1", message="hello"))
        ]

        context_builder.build.assert_awaited_once()
        assert captured_requests[0].messages == [
            SystemMessage(content="<environment_context>safe</environment_context>"),
            UserMessage(content="builder hello"),
        ]
        assert chunks[0].usage is None
        assert chunks[-1].usage == {"summary_tokens": 3, "completion_tokens": 2}
        saved_context = session_store.save.call_args.args[1]
        assert "<environment_context>" not in str(saved_context.to_dict())

    @pytest.mark.asyncio
    async def test_stream_chat_events_merges_summary_usage_on_done(self) -> None:
        """stream_chat_events 使用 builder 消息并在 assistant_done 合并 usage。"""
        session_store = MagicMock()
        session_store.load = AsyncMock(return_value=ConversationContext())
        session_store.save = AsyncMock()
        model_access = MagicMock()

        async def stream(_request: ChatRequest) -> AsyncIterator[StreamingChunk]:
            yield StreamingChunk(delta_content="a", finished=False)
            yield StreamingChunk(
                delta_content="",
                finished=True,
                usage={"completion_tokens": 2},
            )

        model_access.stream = stream
        captured_requests: list[ChatRequest] = []

        async def capturing_stream(request: ChatRequest) -> AsyncIterator[StreamingChunk]:
            captured_requests.append(request)
            async for chunk in stream(request):
                yield chunk

        model_access.stream = capturing_stream
        context_builder = MagicMock()
        context_builder.build = AsyncMock(return_value=_builder_result())
        adapter = _direct_adapter(
            session_store=session_store,
            model_access=model_access,
            context_builder=context_builder,
        )

        events = [
            event
            async for event in adapter.stream_chat_events(
                ChatRequestVO(session_id="s1", message="hello")
            )
        ]

        context_builder.build.assert_awaited_once()
        assert captured_requests[0].messages == [
            SystemMessage(content="<environment_context>safe</environment_context>"),
            UserMessage(content="builder hello"),
        ]
        done = next(event for event in events if event.kind == "assistant_done")
        assert done.usage == {"summary_tokens": 3, "completion_tokens": 2}
        saved_context = session_store.save.call_args.args[1]
        assert "<environment_context>" not in str(saved_context.to_dict())

    @pytest.mark.asyncio
    async def test_chat_builder_failure_skips_model_call_and_assistant_save(self) -> None:
        """builder 构建失败时不调用主模型，也不保存新助手回复。"""
        context = ConversationContext()
        session_store = MagicMock()
        session_store.load = AsyncMock(return_value=context)
        session_store.save = AsyncMock()
        model_access = MagicMock()
        model_access.chat = AsyncMock()
        context_builder = MagicMock()
        context_builder.build = AsyncMock(
            side_effect=EnvironmentContextBuildError("environment build failed")
        )
        adapter = _direct_adapter(
            session_store=session_store,
            model_access=model_access,
            context_builder=context_builder,
        )

        with pytest.raises(EnvironmentContextBuildError):
            await adapter.chat(ChatRequestVO(session_id="s1", message="hello"))

        model_access.chat.assert_not_called()
        session_store.save.assert_not_called()
        assert [message.role for message in context.get_messages()] == ["system", "user"]


class TestChatServiceAdapterAllowedToolNames:
    """ChatServiceAdapter allowed_tool_names 向后兼容单元测试类。

    验证 ChatServiceAdapter 在启用 tool_calling 时构造的 AgentConfig
    的 allowed_tool_names 由 __post_init__ 自动从 tool_schemas 提取，
    无需编排层显式传递。
    """

    @pytest.mark.asyncio
    async def test_agent_config_auto_extracts_allowed_tool_names(self) -> None:
        """ChatServiceAdapter 构造 AgentConfig 不传 allowed_tool_names 时，
        依赖自动提取，allowed_tool_names 应等于 tool_schemas 中的工具名称集合。
        """
        tool_schemas: list[dict[str, Any]] = [
            {
                "type": "function",
                "function": {"name": "search", "description": "搜索", "parameters": {}},
            },
            {
                "type": "function",
                "function": {"name": "calc", "description": "计算", "parameters": {}},
            },
        ]

        captured_configs: list[AgentConfig] = []

        async def capture_run(
            context: ConversationContext,
            config: AgentConfig,
            model_access: ModelAccessPort,
        ) -> AgentResult:
            """捕获 AgentPort.run() 的 config 参数。"""
            captured_configs.append(config)
            result = MagicMock()
            result.content = "ok"
            result.model = "test-model"
            result.usage = {}
            result.latency_ms = 50.0
            return result

        mock_agent = MagicMock()
        mock_agent.run = AsyncMock(side_effect=capture_run)

        mock_session_store = MagicMock()
        mock_session_store.load = AsyncMock(return_value=ConversationContext())
        mock_session_store.save = AsyncMock()

        mock_model_registry = MagicMock()
        mock_model_registry.get_default_model.return_value = "test-model"
        mock_model_registry.get_adapter_for_model.return_value = MagicMock()

        context_builder = MagicMock()
        context_builder.build = AsyncMock(return_value=_builder_result())
        loaded_prompt = LoadedPrompt(
            prompt_id="chat-default@v1",
            name="chat-default",
            version="v1",
            content="你是助手",
        )

        adapter = ChatServiceAdapter(
            session_store=mock_session_store,
            model_registry=mock_model_registry,
            prompt_registry=MagicMock(
                get=MagicMock(
                    return_value=loaded_prompt
                )
            ),
            context_builder=context_builder,
            agent=mock_agent,
            tool_calling_enabled=True,
            max_tool_rounds=5,
            tool_schemas=tool_schemas,
            **make_chat_adapter_dependencies(
                session_store=mock_session_store,
                model_registry=mock_model_registry,
                loaded_prompt=loaded_prompt,
                agent=mock_agent,
                tool_schemas=tool_schemas,
                max_tool_rounds=5,
            ),
        )

        request = ChatRequestVO(session_id="s1", message="你好", stream=False)
        await adapter.chat(request)

        # 验证 AgentConfig 被构造且 allowed_tool_names 自动提取
        assert len(captured_configs) == 1
        config = captured_configs[0]

        expected_names = frozenset({"search", "calc"})
        assert config.allowed_tool_names == expected_names
        assert config.tool_schemas == tool_schemas


class TestChatServiceAdapterPromptRegistry:
    """ChatServiceAdapter 与 ``PromptRegistryPort`` 集成单元测试。

    覆盖任务 7.7 要求的全部断言：构造期一次性加载、运行期不再查注册表、
    AgentConfig.system_prompt 拼接结果、ChatResponseVO.prompt_id 透传、
    多次调用对内部缓存的不变性。
    """

    @staticmethod
    def _build_adapter(
        loaded_prompt: LoadedPrompt,
        agent_run_result: AgentResult,
    ) -> tuple[ChatServiceAdapter, MagicMock, MagicMock, list[AgentConfig]]:
        """构造 ChatServiceAdapter 测试 dummy。

        Args:
            loaded_prompt: ``prompt_registry.get`` 期望返回的 LoadedPrompt。
            agent_run_result: ``agent.run`` 异步返回的 AgentResult。

        Returns:
            ``(adapter, prompt_registry_mock, agent_mock, captured_configs)`` 元组。
        """
        prompt_registry = MagicMock()
        prompt_registry.get = MagicMock(return_value=loaded_prompt)

        captured_configs: list[AgentConfig] = []

        async def capture_run(
            context: ConversationContext,
            config: AgentConfig,
            model_access: ModelAccessPort,
        ) -> AgentResult:
            captured_configs.append(config)
            return agent_run_result

        agent_mock = MagicMock()
        agent_mock.run = AsyncMock(side_effect=capture_run)

        session_store = MagicMock()
        session_store.load = AsyncMock(return_value=ConversationContext())
        session_store.save = AsyncMock()

        model_registry = MagicMock()
        model_registry.get_default_model.return_value = "test-model"
        model_registry.get_adapter_for_model.return_value = MagicMock()

        context_builder = MagicMock()
        context_builder.build = AsyncMock(return_value=_builder_result())

        tool_schemas: list[dict[str, Any]] = [
            {
                "type": "function",
                "function": {
                    "name": "search",
                    "description": "搜索",
                    "parameters": {},
                },
            }
        ]

        adapter = ChatServiceAdapter(
            session_store=session_store,
            model_registry=model_registry,
            prompt_registry=prompt_registry,
            context_builder=context_builder,
            agent=agent_mock,
            tool_calling_enabled=True,
            max_tool_rounds=5,
            tool_schemas=tool_schemas,
            **make_chat_adapter_dependencies(
                session_store=session_store,
                model_registry=model_registry,
                loaded_prompt=loaded_prompt,
                agent=agent_mock,
                tool_schemas=tool_schemas,
                max_tool_rounds=5,
            ),
        )
        return adapter, prompt_registry, agent_mock, captured_configs

    def test_constructor_calls_prompt_registry_get_chat_default_exactly_once(self) -> None:
        """构造期 ``prompt_registry.get('chat-default')`` 必须恰好被调用一次。"""
        loaded = LoadedPrompt(
            prompt_id="chat-default@v3",
            name="chat-default",
            version="v3",
            content="你是一个测试助手。",
        )
        adapter, prompt_registry, _, _ = self._build_adapter(
            loaded,
            AgentResult(
                content="ok",
                model="test-model",
                usage={},
                latency_ms=1.0,
            ),
        )

        assert prompt_registry.get.call_count == 1
        prompt_registry.get.assert_called_once_with("chat-default")
        assert adapter.prompt_id == "chat-default@v3"

    @pytest.mark.asyncio
    async def test_chat_passes_loaded_content_with_workspace_guidance_to_agent_config(
        self,
    ) -> None:
        """传入 ``agent.run`` 的 AgentConfig.system_prompt 应等于 content + 路径规范文案。"""
        loaded = LoadedPrompt(
            prompt_id="chat-default@v3",
            name="chat-default",
            version="v3",
            content="你是一个测试助手。",
        )
        adapter, _, _, captured_configs = self._build_adapter(
            loaded,
            AgentResult(
                content="ok",
                model="test-model",
                usage={},
                latency_ms=1.0,
            ),
        )

        request = ChatRequestVO(session_id="s1", message="你好", stream=False)
        await adapter.chat(request)

        assert len(captured_configs) == 1
        assert captured_configs[0].system_prompt == "你是一个测试助手。" + WORKSPACE_PATH_GUIDANCE

    @pytest.mark.asyncio
    async def test_chat_response_carries_prompt_id_from_loaded_prompt(self) -> None:
        """``ChatResponseVO.prompt_id`` 必须等于 ``LoadedPrompt.prompt_id``。"""
        loaded = LoadedPrompt(
            prompt_id="chat-default@v3",
            name="chat-default",
            version="v3",
            content="你是一个测试助手。",
        )
        adapter, _, _, _ = self._build_adapter(
            loaded,
            AgentResult(
                content="ok",
                model="test-model",
                usage={},
                latency_ms=1.0,
            ),
        )

        request = ChatRequestVO(session_id="s1", message="你好", stream=False)
        response = await adapter.chat(request)

        assert response.prompt_id == "chat-default@v3"

    @pytest.mark.asyncio
    async def test_multiple_chat_calls_do_not_invalidate_cached_prompt(self) -> None:
        """多次 ``chat`` 调用后 ``_prompt_id`` 与 ``_system_prompt`` 不变。"""
        loaded = LoadedPrompt(
            prompt_id="chat-default@v3",
            name="chat-default",
            version="v3",
            content="你是一个测试助手。",
        )
        adapter, prompt_registry, _, _ = self._build_adapter(
            loaded,
            AgentResult(
                content="ok",
                model="test-model",
                usage={},
                latency_ms=1.0,
            ),
        )

        snapshot_prompt = adapter.system_prompt
        snapshot_prompt_id = adapter.prompt_id

        for i in range(3):
            await adapter.chat(ChatRequestVO(session_id=f"s{i}", message="msg", stream=False))

        assert prompt_registry.get.call_count == 1
        assert adapter.system_prompt == snapshot_prompt
        assert adapter.prompt_id == snapshot_prompt_id
