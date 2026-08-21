"""动态模型路由属性测试模块。

使用 Hypothesis 对 ChatServiceAdapter._resolve_model_access 方法进行属性测试，
验证在任意有效输入下，动态模型路由的核心不变量始终成立。

测试文件对应设计文档中定义的正确性属性（Correctness Properties），
每个属性测试通过注释标注对应的设计属性编号和验证的需求编号。
"""

from unittest.mock import AsyncMock, MagicMock

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from domain.agent.tools import ToolExecutionResult
from domain.chat.context import ConversationContext, UserMessage
from domain.chat.value_objects import ChatRequestVO, ContextBuilderResult
from domain.model_access.exceptions import ModelAccessError
from domain.model_access.ports import ModelAccessPort, ModelRegistryPort
from domain.model_access.value_objects import LLMResponse, ToolCallRequest
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from infrastructure.model_access.provider_registry import ProviderRegistry
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies

# ── Hypothesis 生成策略 ──

# 模型名称策略：生成非空可打印字符串，模拟真实模型名称
model_name_st = st.text(
    min_size=1,
    max_size=50,
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S")),
)


def _make_adapter_with_registry(model_registry: MagicMock) -> ChatServiceAdapter:
    """创建测试用 ChatServiceAdapter 实例，注入 mock ModelRegistryPort。

    构建一个最小化的 ChatServiceAdapter，仅用于测试 _resolve_model_access 方法。
    session_store、context_builder、agent 等依赖使用 mock 占位。

    Args:
        model_registry: 模拟的 ModelRegistryPort 实例。

    Returns:
        配置好的 ChatServiceAdapter 实例。
    """
    session_store = MagicMock()
    context_builder = MagicMock()
    context_builder.build = AsyncMock(
        return_value=ContextBuilderResult(
            messages=[UserMessage(content="builder message")],
            environment_injected=True,
        )
    )
    agent = MagicMock()
    loaded_prompt = LoadedPrompt(
        prompt_id="chat-default@v1",
        name="chat-default",
        version="v1",
        content="test",
    )

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
        max_tool_rounds=1,
        tool_schemas=[],
        **make_chat_adapter_dependencies(
            session_store=session_store,
            model_registry=model_registry,
            loaded_prompt=loaded_prompt,
            agent=agent,
            tool_schemas=[],
            max_tool_rounds=1,
        ),
    )


# ── Property 1: 指定模型的动态路由 ──
# Feature: dynamic-model-routing, Property 1: 指定模型的动态路由


@settings(max_examples=100)
@given(model_name=model_name_st)
def test_property1_specified_model_dynamic_routing(model_name: str) -> None:
    """验证指定模型时，_resolve_model_access 使用该模型名称获取适配器。

    **Validates: Requirements 1.2, 2.1, 3.1, 4.1**

    对于任意非空模型名称，当调用 _resolve_model_access(model_name) 时：
    1. 应调用 ModelRegistryPort.get_adapter_for_model(model_name)，传入完全相同的模型名称
    2. 返回的适配器实例应与 registry 返回的实例一致
    3. 返回的模型名称应与传入的模型名称一致
    4. 不应调用 get_default_model()
    """
    # 构建 mock
    mock_adapter = MagicMock(spec=ModelAccessPort)
    model_registry = MagicMock(spec=ModelRegistryPort)
    model_registry.get_adapter_for_model = MagicMock(return_value=mock_adapter)

    adapter = _make_adapter_with_registry(model_registry)

    # 执行
    result_adapter, result_model = adapter.resolve_model_access(model_name)

    # 验证
    # 1. get_adapter_for_model 被调用且传入完全相同的模型名称
    model_registry.get_adapter_for_model.assert_called_once_with(model_name)

    # 2. 返回的适配器实例与 registry 返回的一致
    assert result_adapter is mock_adapter, (
        "返回的适配器实例应与 ModelRegistryPort.get_adapter_for_model() 返回的实例一致"
    )

    # 3. 返回的模型名称与传入的一致
    assert result_model == model_name, (
        f"返回的模型名称应与传入的一致: 期望={model_name!r}, 实际={result_model!r}"
    )

    # 4. 不应调用 get_default_model()
    model_registry.get_default_model.assert_not_called()


# ── Property 2: 未指定模型时回退到默认模型 ──
# Feature: dynamic-model-routing, Property 2: 未指定模型时回退到默认模型


@settings(max_examples=100)
@given(default_model_name=model_name_st)
def test_property2_fallback_to_default_model_when_none(default_model_name: str) -> None:
    """验证 model=None 时，先获取默认模型名称再获取适配器。

    **Validates: Requirements 2.2, 3.2, 4.2**

    对于任意默认模型名称，当调用 _resolve_model_access(None) 时：
    1. 应先调用 ModelRegistryPort.get_default_model() 获取默认模型名称
    2. 再调用 ModelRegistryPort.get_adapter_for_model(default_model_name) 获取适配器
    3. 返回的适配器实例应与 registry 返回的实例一致
    4. 返回的模型名称应与默认模型名称一致
    """
    # 构建 mock
    mock_adapter = MagicMock(spec=ModelAccessPort)
    model_registry = MagicMock(spec=ModelRegistryPort)
    model_registry.get_default_model = MagicMock(return_value=default_model_name)
    model_registry.get_adapter_for_model = MagicMock(return_value=mock_adapter)

    adapter = _make_adapter_with_registry(model_registry)

    # 执行
    result_adapter, result_model = adapter.resolve_model_access(None)

    # 验证
    # 1. get_default_model() 被调用
    model_registry.get_default_model.assert_called_once()

    # 2. get_adapter_for_model 被调用且传入默认模型名称
    model_registry.get_adapter_for_model.assert_called_once_with(default_model_name)

    # 3. 返回的适配器实例与 registry 返回的一致
    assert result_adapter is mock_adapter, (
        "返回的适配器实例应与 ModelRegistryPort.get_adapter_for_model() 返回的实例一致"
    )

    # 4. 返回的模型名称与默认模型名称一致
    assert result_model == default_model_name, (
        f"返回的模型名称应与默认模型名称一致: 期望={default_model_name!r}, 实际={result_model!r}"
    )


# ── Property 3: 未注册模型的错误传播 ──
# Feature: dynamic-model-routing, Property 3: 未注册模型的错误传播


@settings(max_examples=100)
@given(model_name=model_name_st)
def test_property3_unregistered_model_error_propagation(model_name: str) -> None:
    """验证未注册模型时，ModelAccessError 被正确传播。

    **Validates: Requirements 2.3**

    对于任意模型名称，当 ModelRegistryPort.get_adapter_for_model() 抛出
    ModelAccessError 时，_resolve_model_access(model_name) 应将该异常直接
    向上传播，不做任何吞没或包装。
    """
    # 构建 mock，get_adapter_for_model 抛出 ModelAccessError
    error = ModelAccessError(
        message=f"模型 {model_name} 未在任何提供商中注册",
        details={"requested_model": model_name, "available_models": []},
    )
    model_registry = MagicMock(spec=ModelRegistryPort)
    model_registry.get_adapter_for_model = MagicMock(side_effect=error)

    adapter = _make_adapter_with_registry(model_registry)

    # 执行并验证异常传播
    raised = False
    try:
        adapter.resolve_model_access(model_name)
    except ModelAccessError as e:
        raised = True
        # 验证传播的是同一个异常实例（未被包装）
        assert e is error, "应传播原始的 ModelAccessError 实例，而非包装后的新异常"
        # 验证异常详情中包含请求的模型名称
        assert e.details["requested_model"] == model_name, (
            f"异常详情中的 requested_model 应为 {model_name!r}，"
            f"实际为 {e.details['requested_model']!r}"
        )

    assert raised, (
        f"_resolve_model_access({model_name!r}) 应抛出 ModelAccessError，但未抛出任何异常"
    )


# ── 以下属性测试需要 async 支持 ──

# ── Property 4: 响应中的模型名称准确性 ──
# Feature: dynamic-model-routing, Property 4: 响应中的模型名称准确性


@pytest.mark.asyncio
@settings(max_examples=100)
@given(model_name=model_name_st)
async def test_property4_response_model_name_accuracy(model_name: str) -> None:
    """验证同步对话返回的 ChatResponseVO.model 与 LLMResponse.model 一致。

    **Validates: Requirements 3.3**

    对于任意模型名称，当 LLM 返回的 LLMResponse.model 为该名称时，
    chat() 方法返回的 ChatResponseVO.model 应与之完全一致，
    准确反映实际使用的模型名称。
    """
    # 构建 mock LLMResponse，model 字段使用生成的模型名称
    llm_response = LLMResponse(
        content="测试回复",
        model=model_name,
        usage={"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
        latency_ms=10.0,
        tool_calls=[],
    )

    # 构建 mock ModelAccessPort
    mock_model_access = AsyncMock()
    mock_model_access.chat = AsyncMock(return_value=llm_response)

    # 构建 mock ModelRegistryPort
    model_registry = MagicMock(spec=ModelRegistryPort)
    model_registry.get_adapter_for_model = MagicMock(return_value=mock_model_access)
    model_registry.get_default_model = MagicMock(return_value=model_name)

    # 构建 mock 依赖
    session_store = AsyncMock()
    session_store.load = AsyncMock(return_value=ConversationContext())
    session_store.save = AsyncMock()

    context_builder = MagicMock()
    context_builder.build = AsyncMock(
        return_value=ContextBuilderResult(
            messages=[UserMessage(content="builder message")],
            environment_injected=True,
        )
    )
    agent = MagicMock()
    loaded_prompt = LoadedPrompt(
        prompt_id="chat-default@v1",
        name="chat-default",
        version="v1",
        content="test",
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
        agent=agent,
        tool_calling_enabled=False,
        max_tool_rounds=1,
        tool_schemas=[],
        **make_chat_adapter_dependencies(
            session_store=session_store,
            model_registry=model_registry,
            loaded_prompt=loaded_prompt,
            agent=agent,
            tool_schemas=[],
            max_tool_rounds=1,
        ),
    )

    request = ChatRequestVO(session_id="test-session", message="hello", model=model_name)
    result = await adapter.chat(request)

    # 验证 ChatResponseVO.model 与 LLMResponse.model 一致
    assert result.model == model_name, (
        f"ChatResponseVO.model 应与 LLMResponse.model 一致: "
        f"期望={model_name!r}, 实际={result.model!r}"
    )


# ── Property 5: Agent Loop 中适配器一致性 ──
# Feature: dynamic-model-routing, Property 5: Agent Loop 中适配器一致性


@pytest.mark.asyncio
@settings(max_examples=100)
@given(
    model_name=model_name_st,
    num_tool_rounds=st.integers(min_value=1, max_value=5),
)
async def test_property5_agent_loop_adapter_consistency(
    model_name: str, num_tool_rounds: int
) -> None:
    """验证 Agent Loop 所有轮次使用同一个 ModelAccessPort 实例。

    **Validates: Requirements 5.1, 5.2**

    对于任意模型名称和 1-5 轮工具调用场景，Agent Loop 中：
    1. model_registry.get_adapter_for_model 应仅被调用一次（在 chat() 入口处）
    2. 所有轮次的 LLM 调用均使用同一个 ModelAccessPort 实例
    """
    # 构建工具调用请求
    tool_call = ToolCallRequest(id="call_1", name="test_tool", arguments='{"key": "value"}')

    # 构建中间轮次响应（带 tool_calls）和最终轮次响应（纯文本）
    tool_response = LLMResponse(
        content="调用工具中",
        model=model_name,
        usage={"prompt_tokens": 5, "completion_tokens": 3},
        tool_calls=[tool_call],
    )
    final_response = LLMResponse(
        content="最终回复",
        model=model_name,
        usage={"prompt_tokens": 5, "completion_tokens": 3},
        tool_calls=[],
    )

    # 构建 side_effect：前 num_tool_rounds-1 轮返回 tool_calls，最后一轮返回纯文本
    side_effects = [tool_response] * (num_tool_rounds - 1) + [final_response]

    # 构建 mock ModelAccessPort（v3：ReAct 内部全程 stream，安装等价 stream mock）
    from test.infrastructure.agent._v3_stream_helpers import install_stream_mock

    mock_model_access = AsyncMock()
    counter = install_stream_mock(mock_model_access, side_effects)

    # 构建 mock ModelRegistryPort
    model_registry = MagicMock(spec=ModelRegistryPort)
    model_registry.get_adapter_for_model = MagicMock(return_value=mock_model_access)
    model_registry.get_default_model = MagicMock(return_value=model_name)

    # 构建 mock 依赖
    session_store = AsyncMock()
    session_store.load = AsyncMock(return_value=ConversationContext())
    session_store.save = AsyncMock()

    context_builder = MagicMock()
    context_builder.build = AsyncMock(
        return_value=ContextBuilderResult(
            messages=[
                UserMessage(content="agent builder message"),
            ],
            usage={"builder_tokens": 1},
            environment_injected=True,
        )
    )

    tool_registry_mock = MagicMock()
    tool_registry_mock.get_schemas.return_value = [
        {
            "type": "function",
            "function": {"name": "test_tool", "description": "test", "parameters": {}},
        }
    ]
    tool_registry_mock.execute = AsyncMock(return_value=ToolExecutionResult(content="tool result"))

    tool_schemas = tool_registry_mock.get_schemas()

    # 构建 mock AgentPort，委托给真实的 ReActAgentAdapter
    from infrastructure.agent.react_agent_adapter import ReActAgentAdapter

    real_agent = ReActAgentAdapter(
        tool_registry=tool_registry_mock,
        context_builder=context_builder,
    )
    loaded_prompt = LoadedPrompt(
        prompt_id="chat-default@v1",
        name="chat-default",
        version="v1",
        content="test",
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
        agent=real_agent,
        tool_calling_enabled=True,
        max_tool_rounds=num_tool_rounds + 1,  # 确保不会因 max_rounds 提前终止
        tool_schemas=tool_schemas,
        **make_chat_adapter_dependencies(
            session_store=session_store,
            model_registry=model_registry,
            loaded_prompt=loaded_prompt,
            agent=real_agent,
            tool_schemas=tool_schemas,
            max_tool_rounds=num_tool_rounds + 1,
        ),
    )

    request = ChatRequestVO(session_id="test-session", message="hello", model=model_name)
    await adapter.chat(request)

    # 验证 get_adapter_for_model 仅被调用一次
    model_registry.get_adapter_for_model.assert_called_once_with(model_name)

    # v3：ReAct 内部全程 stream，验证所有轮次的 LLM 调用都在同一个
    # mock_model_access 实例上发生（counter 由 install_stream_mock 关联）。
    assert counter.call_count == num_tool_rounds, (
        f"mock_model_access.stream 应被调用 {num_tool_rounds} 次，"
        f"实际被调用 {counter.call_count} 次"
    )
    for chat_request in counter.calls:
        assert chat_request.messages == [
            UserMessage(content="agent builder message"),
        ]


# ── Property 6: Round-Robin 负载均衡保持 ──
# Feature: dynamic-model-routing, Property 6: Round-Robin 负载均衡保持


@settings(max_examples=100)
@given(
    model_name=model_name_st,
    num_providers=st.integers(min_value=2, max_value=5),
    num_requests=st.integers(min_value=2, max_value=20),
)
def test_property6_round_robin_load_balancing(
    model_name: str, num_providers: int, num_requests: int
) -> None:
    """验证多提供商场景下 Round-Robin 负载均衡的正确性。

    **Validates: Requirements 7.1**

    对于任意模型名称、2-5 个提供商和 2-20 次连续请求，
    通过 ProviderRegistry 获取的适配器应按 Round-Robin 顺序轮询分布，
    即第 i 次请求应返回第 (i % num_providers) 个提供商的适配器（按名称排序）。
    """
    # 使用真实的 ProviderRegistry
    registry = ProviderRegistry(default_model=model_name)

    # 注册多个提供商，每个提供商使用不同的 mock 适配器
    adapters: dict[str, MagicMock] = {}
    provider_names: list[str] = []
    for i in range(num_providers):
        provider_name = f"provider_{i:03d}"
        provider_names.append(provider_name)
        mock_adapter = MagicMock(spec=ModelAccessPort)
        mock_adapter._provider_name = provider_name  # 标记用于验证
        adapters[provider_name] = mock_adapter
        registry.register_provider(provider_name, mock_adapter, [model_name])

    # Round-Robin 按提供商名称排序轮询
    sorted_providers = sorted(provider_names)

    # 连续请求，验证 Round-Robin 分布
    for i in range(num_requests):
        result_adapter = registry.get_adapter_for_model(model_name)
        expected_provider = sorted_providers[i % num_providers]
        expected_adapter = adapters[expected_provider]
        assert result_adapter is expected_adapter, (
            f"第 {i} 次请求应返回提供商 {expected_provider!r} 的适配器，但返回了不同的实例"
        )
