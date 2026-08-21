"""Agent Loop 同步对话单元测试。

验证 ReActAgentAdapter.run 方法的核心行为，
包括单轮工具调用、最大轮次限制、工具异常处理和 token 用量累计。
同时验证 ChatServiceAdapter 在 tool_calling_enabled=False 时的直接 LLM 调用路径。

Agent Loop 逻辑已从 ChatServiceAdapter 迁移到 ReActAgentAdapter，
因此 Agent Loop 相关测试直接测试 ReActAgentAdapter.run()。
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.ports import ApprovalPolicyPort, ApprovalStateStorePort
from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import (
    AgentConfig,
    ApprovalDecision,
    ApprovalInterrupt,
    ApprovalInterruptSummary,
    ApprovalPolicy,
    PendingActionRequest,
)
from domain.chat.context import (
    AssistantMessage,
    ConversationContext,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from domain.chat.value_objects import (
    ChatRequestVO,
    ContextBuilderResult,
)
from domain.model_access.value_objects import LLMResponse, ToolCallRequest
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from infrastructure.chat.environment_context_provider import EnvironmentContextBuildError
from test.infrastructure.agent._v3_stream_helpers import install_stream_mock
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies


class _StaticApprovalPolicy(ApprovalPolicyPort):
    """测试用静态审批策略。"""

    def policy_for(self, tool_name: str) -> ApprovalPolicy:
        """所有 test_tool 调用都需要审批。"""
        return ApprovalPolicy(
            tool_name=tool_name,
            interrupt=tool_name == "test_tool",
            allowed_decisions=frozenset({"approve", "reject"}),
            risk_label="测试审批",
        )


class _MemoryApprovalStore(ApprovalStateStorePort):
    """测试用内存审批状态存储。"""

    def __init__(self) -> None:
        """初始化空审批状态。"""
        self.saved: ApprovalInterrupt | None = None

    async def save(self, interrupt: ApprovalInterrupt) -> None:
        """保存最近一次审批中断。"""
        self.saved = interrupt

    async def load(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        """返回最近一次审批中断。"""
        return self.saved

    async def consume(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        """消费并清空最近一次审批中断。"""
        interrupt = self.saved
        self.saved = None
        return interrupt

    async def delete(self, session_id: str, approval_id: str) -> None:
        """清空最近一次审批中断。"""
        self.saved = None

    async def delete_session(self, session_id: str) -> None:
        """清空最近一次审批中断。"""
        self.saved = None

    async def list_pending_by_session(
        self, session_id: str
    ) -> list[ApprovalInterruptSummary]:
        """返回空审批摘要。"""
        return []


def _make_react_adapter(
    tool_registry: MagicMock | None = None,
    context_builder: MagicMock | None = None,
    approval_policy: ApprovalPolicyPort | None = None,
    approval_store: ApprovalStateStorePort | None = None,
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
        approval_policy=approval_policy,
        approval_store=approval_store,
    )


def _make_config(
    tool_schemas: list[dict[str, Any]] | None = None,
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


@pytest.mark.asyncio
async def test_agent_loop_single_tool_call_then_text_reply() -> None:
    """验证单轮工具调用后返回文本回复的完整流程。

    模拟 LLM 第一次返回 tool_calls，第二次返回纯文本回复。
    验证：
    - run() 返回最终的文本回复
    - ToolRegistry.execute 被正确调用
    - 上下文包含完整的消息序列：
      system → user → assistant(tool_calls) → tool → (最终回复由编排层追加)
    """
    tool_call = ToolCallRequest(id="call_1", name="test_tool", arguments='{"key": "value"}')

    # 第一次调用：返回 tool_calls
    first_response = LLMResponse(
        content="正在调用工具...",
        model="gpt-4o",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        tool_calls=[tool_call],
    )
    # 第二次调用：返回纯文本
    second_response = LLMResponse(
        content="工具执行完毕，结果如下。",
        model="gpt-4o",
        usage={"prompt_tokens": 15, "completion_tokens": 8},
        tool_calls=[],
    )

    model_access = AsyncMock()
    counter = install_stream_mock(model_access, [first_response, second_response])

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

    result = await adapter.run(context, config, model_access)

    # 验证返回最终文本回复
    assert result.content == "工具执行完毕，结果如下。"

    # 验证 ToolRegistry.execute 被正确调用
    tool_registry.execute.assert_awaited_once_with(tool_call)

    # 验证上下文消息序列（Agent Loop 内部追加的中间消息）
    messages = context.get_messages()

    # system → user → assistant(tool_calls) → tool = 4 条
    # 最终的 assistant 回复由编排层（ChatServiceAdapter）追加，不在 Agent Loop 内
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

    # v3：ReAct 内部全程 stream，counter.calls 顺序记录每轮 ChatRequest。
    first_request = counter.calls[0]
    second_request = counter.calls[1]
    assert first_request.messages == [UserMessage(content="builder round 1")]
    assert second_request.messages == [UserMessage(content="builder round 2")]
    assert first_request.tools == config.tool_schemas
    assert second_request.tools == config.tool_schemas


@pytest.mark.asyncio
async def test_agent_loop_max_rounds_stops() -> None:
    """验证达到最大迭代轮次时 Agent Loop 停止。

    设置 max_rounds=2，模拟 LLM 始终返回 tool_calls。
    验证：
    - run() 在恰好 2 轮后返回
    - model_access.chat 被调用恰好 2 次
    - 返回最后一轮的 content
    """
    tool_call = ToolCallRequest(id="call_x", name="test_tool", arguments='{"a": "b"}')

    always_tool_response = LLMResponse(
        content="继续调用工具",
        model="gpt-4o",
        usage={"prompt_tokens": 5, "completion_tokens": 3},
        tool_calls=[tool_call],
    )

    model_access = AsyncMock()
    counter = install_stream_mock(model_access, [always_tool_response, always_tool_response])

    adapter = _make_react_adapter()
    config = _make_config(max_rounds=2)

    context = ConversationContext()
    context.add_system_message("你是一个有用的 AI 助手。")
    context.add_user_message("测试最大轮次")

    result = await adapter.run(context, config, model_access)

    # v3：ReAct 内部全程 stream。max_rounds=2 → 2 次 stream（中间两轮均 tool_calls，
    # 命中 max_rounds 跳过最后一轮 _stream_final_round）。
    assert counter.call_count == 2

    # 验证返回最后一轮的 content
    assert result.content == "继续调用工具"
    assert result.terminated_reason == "max_rounds"


@pytest.mark.asyncio
async def test_agent_loop_tool_exception_passed_to_llm() -> None:
    """验证工具执行异常时异常信息被回传给 LLM。

    模拟 ToolRegistry.execute 抛出异常，LLM 第一次返回 tool_calls，
    第二次返回纯文本回复。
    验证：
    - 异常信息作为 ToolMessage 的 content 传递
    - 循环继续并返回最终文本回复
    """
    tool_call = ToolCallRequest(id="call_err", name="test_tool", arguments='{"x": "1"}')

    first_response = LLMResponse(
        content="调用工具",
        model="gpt-4o",
        usage={"prompt_tokens": 5, "completion_tokens": 2},
        tool_calls=[tool_call],
    )
    second_response = LLMResponse(
        content="工具出错了，我来处理。",
        model="gpt-4o",
        usage={"prompt_tokens": 8, "completion_tokens": 4},
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
    tool_registry.execute = AsyncMock(side_effect=RuntimeError("连接超时"))

    adapter = _make_react_adapter(tool_registry=tool_registry)
    config = _make_config()

    context = ConversationContext()
    context.add_system_message("你是一个有用的 AI 助手。")
    context.add_user_message("测试异常处理")

    result = await adapter.run(context, config, model_access)

    # 验证循环继续并返回最终文本回复
    assert result.content == "工具出错了，我来处理。"

    # 验证异常信息作为 ToolMessage content 保存到上下文
    messages = context.get_messages()
    tool_msgs = [m for m in messages if isinstance(m, ToolMessage)]
    assert len(tool_msgs) == 1
    assert tool_msgs[0].content == "连接超时"


@pytest.mark.asyncio
async def test_agent_loop_disabled_no_tools_passed() -> None:
    """验证 tool_calling_enabled=False 时不传递 tools 参数。

    设置 tool_calling_enabled=False，验证：
    - chat() 直接调用 model_access.chat（不经过 Agent Loop）
    - 发送给 LLM 的 ChatRequest 不包含 tools 参数
    """
    plain_response = LLMResponse(
        content="普通回复",
        model="gpt-4o",
        usage={"prompt_tokens": 5, "completion_tokens": 3},
        tool_calls=[],
    )

    model_access = AsyncMock()
    model_access.chat = AsyncMock(return_value=plain_response)

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

    model_registry = MagicMock()
    model_registry.get_adapter_for_model = MagicMock(return_value=model_access)
    model_registry.get_default_model = MagicMock(return_value="gpt-4o")

    agent = MagicMock()

    loaded_prompt = LoadedPrompt(
        prompt_id="chat-default@v1",
        name="chat-default",
        version="v1",
        content="你是一个有用的 AI 助手。",
    )

    adapter = ChatServiceAdapter(
        session_store=session_store,
        model_registry=model_registry,
        prompt_registry=MagicMock(get=MagicMock(return_value=loaded_prompt)),
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

    request = ChatRequestVO(session_id="s4", message="普通对话")

    result = await adapter.chat(request)

    assert result.reply == "普通回复"

    # 验证 model_access.chat 只被调用一次
    model_access.chat.assert_awaited_once()

    # 验证 ChatRequest 中没有 tools 参数
    chat_request = model_access.chat.call_args[0][0]
    assert chat_request.tools is None


@pytest.mark.asyncio
async def test_agent_loop_usage_accumulated() -> None:
    """验证多轮 Agent Loop 的 token 用量正确累计。

    模拟两轮调用：第一轮 prompt_tokens=10, completion_tokens=5，
    第二轮 prompt_tokens=8, completion_tokens=3。
    验证返回的 AgentResult.usage 累计主模型 usage 与每轮 builder usage。
    """
    tool_call = ToolCallRequest(id="call_u", name="test_tool", arguments='{"k": "v"}')

    first_response = LLMResponse(
        content="调用工具",
        model="gpt-4o",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        tool_calls=[tool_call],
    )
    second_response = LLMResponse(
        content="最终回复",
        model="gpt-4o",
        usage={"prompt_tokens": 8, "completion_tokens": 3},
        tool_calls=[],
    )

    model_access = AsyncMock()
    counter = install_stream_mock(model_access, [first_response, second_response])

    context_builder = MagicMock()
    context_builder.build = AsyncMock(
        side_effect=[
            ContextBuilderResult(
                messages=[UserMessage(content="builder usage 1")],
                usage={"prompt_tokens": 2, "builder_tokens": 7},
                environment_injected=True,
            ),
            ContextBuilderResult(
                messages=[UserMessage(content="builder usage 2")],
                usage={"prompt_tokens": 3, "builder_tokens": 11},
                environment_injected=True,
            ),
        ]
    )
    adapter = _make_react_adapter(context_builder=context_builder)
    config = _make_config()

    context = ConversationContext()
    context.add_system_message("你是一个有用的 AI 助手。")
    context.add_user_message("测试用量累计")

    result = await adapter.run(context, config, model_access)

    assert result.usage["prompt_tokens"] == 23
    assert result.usage["completion_tokens"] == 8
    assert result.usage["builder_tokens"] == 18

    assert counter.calls[0].messages == [
        UserMessage(content="builder usage 1"),
    ]
    assert counter.calls[1].messages == [
        UserMessage(content="builder usage 2"),
    ]


@pytest.mark.asyncio
async def test_agent_loop_builder_failure_skips_model_and_history_append() -> None:
    """builder 失败时不调用主模型，也不追加 assistant/tool 消息。"""
    model_access = AsyncMock()
    model_access.chat = AsyncMock()

    context_builder = MagicMock()
    context_builder.build = AsyncMock(
        side_effect=EnvironmentContextBuildError("environment build failed")
    )
    adapter = _make_react_adapter(context_builder=context_builder)
    config = _make_config()

    context = ConversationContext()
    context.add_system_message("你是一个有用的 AI 助手。")
    context.add_user_message("测试 builder 失败")
    before_messages = list(context.get_messages())

    with pytest.raises(EnvironmentContextBuildError):
        await adapter.run(context, config, model_access)

    model_access.chat.assert_not_awaited()
    assert context.get_messages() == before_messages


@pytest.mark.asyncio
async def test_agent_loop_approval_interrupt_keeps_tool_execution_pending() -> None:
    """验证审批中断保存状态且暂不执行工具。"""
    tool_call = ToolCallRequest(id="call_a", name="test_tool", arguments='{"k": "v"}')
    response = LLMResponse(
        content="需要审批",
        model="gpt-4o",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        tool_calls=[tool_call],
    )
    model_access = AsyncMock()
    install_stream_mock(model_access, [response])

    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="ok"))
    approval_store = _MemoryApprovalStore()
    adapter = _make_react_adapter(
        tool_registry=tool_registry,
        approval_policy=_StaticApprovalPolicy(),
        approval_store=approval_store,
    )
    config = _make_config()
    context = ConversationContext()
    context.session_id = "s-approval"
    context.add_user_message("需要调用工具")

    result = await adapter.run(context, config, model_access)

    assert result.status == "approval_required"
    assert result.approval is not None
    assert result.approval.actions[0].tool_call_id == "call_a"
    assert approval_store.saved is not None
    tool_registry.execute.assert_not_awaited()
    messages = context.get_messages()
    # UserMessage + SystemMessage（幂等注入） + AssistantMessage = 3
    assert len(messages) == 3
    assert isinstance(messages[-1], AssistantMessage)
    assert messages[-1].tool_calls[0].id == "call_a"
    assert result.usage["prompt_tokens"] == 11


@pytest.mark.asyncio
async def test_agent_loop_resume_approve_uses_builder_and_continues() -> None:
    """验证审批恢复 approve 后执行工具，并继续使用 builder 调用模型。"""
    context = ConversationContext()
    context.session_id = "s-resume"
    context.add_system_message("你是一个有用的 AI 助手。")
    context.add_user_message("继续")
    context.add_assistant_message_with_tool_calls(
        "需要审批",
        [ToolCallRequest(id="call_r", name="test_tool", arguments='{"k": "v"}')],
    )
    interrupt = ApprovalInterrupt(
        session_id="s-resume",
        approval_id="approval-1",
        actions=(
            PendingActionRequest(
                tool_call_id="call_r",
                tool_name="test_tool",
                arguments='{"k": "v"}',
                allowed_decisions=frozenset({"approve", "reject"}),
            ),
        ),
        context_snapshot=context.to_dict(),
        round_num=1,
        model="gpt-4o",
        usage_so_far={"prompt_tokens": 5},
    )

    model_access = AsyncMock()
    counter = install_stream_mock(
        model_access,
        [
            LLMResponse(
                content="恢复完成",
                model="gpt-4o",
                usage={"prompt_tokens": 7, "completion_tokens": 3},
                tool_calls=[],
            )
        ],
    )
    context_builder = MagicMock()
    context_builder.build = AsyncMock(
        return_value=ContextBuilderResult(
            messages=[UserMessage(content="builder resume")],
            usage={"prompt_tokens": 2, "builder_tokens": 4},
            environment_injected=True,
        )
    )
    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="approved result"))
    adapter = _make_react_adapter(
        tool_registry=tool_registry,
        context_builder=context_builder,
    )

    result = await adapter.resume(
        context,
        _make_config(),
        model_access,
        interrupt,
        (ApprovalDecision("approve", "call_r"),),
    )

    assert result.content == "恢复完成"
    tool_registry.execute.assert_awaited_once()
    assert context.get_messages()[-1].content == "approved result"
    assert counter.calls[-1].messages == [
        UserMessage(content="builder resume"),
    ]
    assert result.usage["prompt_tokens"] == 14
    assert result.usage["completion_tokens"] == 3
    assert result.usage["builder_tokens"] == 4
