"""ChatServiceAdapter 委托路由属性测试模块。

使用 Hypothesis 对重构后的 ChatServiceAdapter 的委托路由行为进行属性测试，验证：
- tool_calling_enabled=True 且有工具时，chat() 委托 AgentPort.run()
- tool_calling_enabled=False 时，chat() 直接调用 LLM
- tool_calling_enabled=True 但无工具时，chat() 直接调用 LLM
"""

from unittest.mock import AsyncMock, MagicMock

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from domain.agent.value_objects import AgentResult
from domain.chat.context import ConversationContext, UserMessage
from domain.chat.value_objects import ChatRequestVO, ContextBuilderResult
from domain.model_access.value_objects import LLMResponse
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies

# ── Hypothesis 策略 ──

# session_id 策略：非空字符串
session_id_st = st.text(min_size=1, max_size=50).filter(lambda s: len(s.strip()) > 0)

# message 策略：非空且非纯空白字符串
message_st = st.text(min_size=1, max_size=200).filter(lambda s: len(s.strip()) > 0)

# model 策略：None 或非空字符串
model_st = st.one_of(st.none(), st.text(min_size=1, max_size=30))

# ChatRequestVO 策略
chat_request_vo_st = st.builds(
    ChatRequestVO,
    session_id=session_id_st,
    message=message_st,
    stream=st.just(False),
    model=model_st,
)

# 工具 schema 策略：非空列表
tool_schema_st = st.just(
    [
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

# 系统提示词策略
system_prompt_st = st.text(min_size=1, max_size=100).filter(lambda s: len(s.strip()) > 0)

# max_tool_rounds 策略
max_tool_rounds_st = st.integers(min_value=1, max_value=20)


def _build_mocks(
    tool_calling_enabled: bool,
    tool_schemas: list[dict],
    system_prompt: str,
    max_tool_rounds: int,
):
    """构建 ChatServiceAdapter 所需的全部 mock 对象和适配器实例。

    Args:
        tool_calling_enabled: 是否启用工具调用
        tool_schemas: 工具 schema 列表
        system_prompt: 系统提示词
        max_tool_rounds: 最大工具调用轮次

    Returns:
        (adapter, session_store, model_access, agent_mock, model_registry) 元组
    """
    # Mock SessionContextStorePort
    session_store = AsyncMock()
    session_store.load = AsyncMock(return_value=ConversationContext())
    session_store.save = AsyncMock()

    # Mock ModelAccessPort
    model_access = AsyncMock()
    model_access.chat = AsyncMock(
        return_value=LLMResponse(
            content="llm direct response",
            model="test-model",
            usage={"prompt_tokens": 10, "completion_tokens": 5},
            latency_ms=50.0,
        )
    )

    # Mock ModelRegistryPort
    model_registry = MagicMock()
    model_registry.get_adapter_for_model = MagicMock(return_value=model_access)
    model_registry.get_default_model = MagicMock(return_value="test-model")

    # Mock ContextBuilderPort
    context_builder = MagicMock()
    context_builder.build = AsyncMock(
        return_value=ContextBuilderResult(
            messages=[UserMessage(content="builder message")],
            usage={"summary_tokens": 1},
            environment_injected=True,
        )
    )

    # Mock AgentPort
    agent_mock = AsyncMock()
    agent_mock.run = AsyncMock(
        return_value=AgentResult(
            content="agent response",
            model="test-model",
            usage={"prompt_tokens": 20, "completion_tokens": 10},
            latency_ms=100.0,
        )
    )
    loaded_prompt = LoadedPrompt(
        prompt_id="chat-default@v1",
        name="chat-default",
        version="v1",
        content=system_prompt,
    )

    adapter = ChatServiceAdapter(
        session_store=session_store,
        model_registry=model_registry,
        prompt_registry=MagicMock(
            get=MagicMock(
                return_value=loaded_prompt
            )
        ),
        context_builder=context_builder,
        agent=agent_mock,
        tool_calling_enabled=tool_calling_enabled,
        max_tool_rounds=max_tool_rounds,
        tool_schemas=tool_schemas,
        **make_chat_adapter_dependencies(
            session_store=session_store,
            model_registry=model_registry,
            loaded_prompt=loaded_prompt,
            agent=agent_mock,
            tool_schemas=tool_schemas,
            max_tool_rounds=max_tool_rounds,
        ),
    )

    return adapter, session_store, model_access, agent_mock, model_registry


# ── Property 8: ChatServiceAdapter delegation routing ──
# Feature: agent-abstraction-layer, Property 8: ChatServiceAdapter delegation routing


@settings(max_examples=100, deadline=5000)
@given(
    request=chat_request_vo_st,
    system_prompt=system_prompt_st,
    max_tool_rounds=max_tool_rounds_st,
)
@pytest.mark.asyncio
async def test_delegation_routing_enabled_with_tools(
    request: ChatRequestVO,
    system_prompt: str,
    max_tool_rounds: int,
) -> None:
    """验证 tool_calling_enabled=True 且有工具时，chat() 委托 AgentPort.run() 而非直接调用 LLM。

    对于任意合法的 ChatRequestVO，当 tool_calling_enabled 为 True 且 tool_schemas 非空时，
    ChatServiceAdapter.chat() 应调用 agent.run()，不应直接调用 model_access.chat()。
    返回的 ChatResponseVO 应包含 AgentResult 中的内容。

    **Validates: Requirements 5.2, 5.3, 5.4**
    """
    tool_schemas = [
        {
            "type": "function",
            "function": {
                "name": "test_tool",
                "description": "A test tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    adapter, _session_store, model_access, agent_mock, _ = _build_mocks(
        tool_calling_enabled=True,
        tool_schemas=tool_schemas,
        system_prompt=system_prompt,
        max_tool_rounds=max_tool_rounds,
    )

    response = await adapter.chat(request)

    # agent.run() 应被调用
    agent_mock.run.assert_called_once()

    # model_access.chat() 不应被直接调用
    model_access.chat.assert_not_called()

    # 返回内容应来自 AgentResult
    assert response.reply == "agent response"
    assert response.session_id == request.session_id


@settings(max_examples=100, deadline=5000)
@given(
    request=chat_request_vo_st,
    system_prompt=system_prompt_st,
    max_tool_rounds=max_tool_rounds_st,
)
@pytest.mark.asyncio
async def test_delegation_routing_disabled(
    request: ChatRequestVO,
    system_prompt: str,
    max_tool_rounds: int,
) -> None:
    """验证 tool_calling_enabled=False 时，chat() 直接调用 LLM 而非委托 AgentPort.run()。

    对于任意合法的 ChatRequestVO，当 tool_calling_enabled 为 False 时，
    ChatServiceAdapter.chat() 应直接调用 model_access.chat()，不应调用 agent.run()。
    返回的 ChatResponseVO 应包含 LLMResponse 中的内容。

    **Validates: Requirements 5.2, 5.3, 5.4**
    """
    tool_schemas = [
        {
            "type": "function",
            "function": {
                "name": "test_tool",
                "description": "A test tool",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    adapter, _session_store, model_access, agent_mock, _ = _build_mocks(
        tool_calling_enabled=False,
        tool_schemas=tool_schemas,
        system_prompt=system_prompt,
        max_tool_rounds=max_tool_rounds,
    )

    response = await adapter.chat(request)

    # agent.run() 不应被调用
    agent_mock.run.assert_not_called()

    # model_access.chat() 应被直接调用
    model_access.chat.assert_called_once()

    # 返回内容应来自 LLMResponse
    assert response.reply == "llm direct response"
    assert response.session_id == request.session_id


@settings(max_examples=100, deadline=5000)
@given(
    request=chat_request_vo_st,
    system_prompt=system_prompt_st,
    max_tool_rounds=max_tool_rounds_st,
)
@pytest.mark.asyncio
async def test_delegation_routing_enabled_but_no_tools(
    request: ChatRequestVO,
    system_prompt: str,
    max_tool_rounds: int,
) -> None:
    """验证 tool_calling_enabled=True 但 tool_schemas 为空时，
    chat() 直接调用 LLM 而非委托 AgentPort.run()。

    对于任意合法的 ChatRequestVO，当 tool_calling_enabled 为 True 但 tool_schemas 为空列表时，
    ChatServiceAdapter.chat() 应直接调用 model_access.chat()，不应调用 agent.run()。
    这确保了"启用工具调用但无已注册工具"的场景退化为直接 LLM 调用。

    **Validates: Requirements 5.2, 5.3, 5.4**
    """
    adapter, _session_store, model_access, agent_mock, _ = _build_mocks(
        tool_calling_enabled=True,
        tool_schemas=[],  # 空工具列表
        system_prompt=system_prompt,
        max_tool_rounds=max_tool_rounds,
    )

    response = await adapter.chat(request)

    # agent.run() 不应被调用
    agent_mock.run.assert_not_called()

    # model_access.chat() 应被直接调用
    model_access.chat.assert_called_once()

    # 返回内容应来自 LLMResponse
    assert response.reply == "llm direct response"
    assert response.session_id == request.session_id
