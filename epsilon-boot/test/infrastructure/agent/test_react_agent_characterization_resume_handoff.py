"""ReActAgentAdapter resume + handoff 特征化测试模块。

锁定当前行为：审批恢复后下一轮工具执行触发 HandoffPerformed 信号，
Agent Loop 终止并产出 handoff 结果（控制转移语义）。

本测试属 characterization（回归基线）——只照当前实际行为写断言，不改行为语义。
harness 使用 MagicMock tool_registry（与既有 test_react_agent_handoff_unit 同构），
因为生产代码的 ``_execute_tool_call`` 依赖 ``except HandoffPerformed`` 分支
只在 ``tool_registry.execute`` 直接抛出 ``HandoffPerformed`` 时触发
（``Tool.run`` 的 generic exception wrapping 在真实 ToolRegistry 中会把
``HandoffPerformed`` 包装为 ``ToolExecutionError``；生产环境通过
``MagicMock`` 等桩绕过 ``Tool.run`` 调用链，与本 harness 一致）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.exceptions import HandoffPerformed
from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import (
    AgentConfig,
    ApprovalDecision,
    ApprovalInterrupt,
    PendingActionRequest,
)
from domain.chat.context import AssistantMessage, BaseMessage, ConversationContext
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.value_objects import (
    LLMResponse,
    ToolCallRequest,
)
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from test.infrastructure.agent._v3_stream_helpers import install_stream_mock
from test.infrastructure.agent.test_react_agent_hitl_unit import (
    MemoryApprovalStore,
)


def _config() -> AgentConfig:
    return AgentConfig(
        system_prompt="system",
        tool_schemas=[{"type": "function", "function": {"name": "handoff_to_agent"}}],
        model="gpt-test",
        max_rounds=4,
        prompt_id="chat-default@v1",
    )


def _make_adapter(store: MemoryApprovalStore) -> ReActAgentAdapter:
    """构造 adapter：tool_registry.execute 抛 HandoffPerformed，无审批策略。

    用于 resume 后 handoff 短路路径验证——恢复后的 tool_calls 不被审批拦截，
    使循环能进入下一轮入口的 detect_handoff 检测点。
    """

    async def _execute_proxy(req: ToolCallRequest) -> ToolExecutionResult:
        raise HandoffPerformed(
            target_agent="agent_b",
            content="reply from agent_b",
            usage={"total_tokens": 5},
            model="gpt-test",
        )

    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(side_effect=_execute_proxy)
    tool_registry.get = MagicMock(return_value=None)

    async def build_context(
        messages: list[BaseMessage],
        **kwargs: object,
    ) -> ContextBuilderResult:
        return ContextBuilderResult(messages=messages, usage={})

    context_builder = MagicMock()
    context_builder.build = AsyncMock(side_effect=build_context)
    return ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=context_builder,
        approval_store=store,
    )


def _seed_context_with_handoff_tool_call() -> ConversationContext:
    """构造含"待审批 handoff_to_agent 工具调用"的最小上下文。"""
    context = ConversationContext()
    context.add_system_message("system")
    context.add_user_message("handoff to agent_b")
    context.replace_messages(
        [
            *context.get_messages(),
            AssistantMessage(
            content="",
            tool_calls=[
                ToolCallRequest("call-h1", "handoff_to_agent", '{"target":"agent_b"}')
            ],
            ),
        ]
    )
    return context


def _pending_action() -> PendingActionRequest:
    """构造 handoff 待审批动作。"""
    return PendingActionRequest(
        "call-h1",
        "handoff_to_agent",
        '{"target":"agent_b"}',
        frozenset({"approve", "reject"}),
        reason="handoff 需要审批",
    )


def _interrupt(context: ConversationContext) -> ApprovalInterrupt:
    """构造 handoff 审批中断。"""
    return ApprovalInterrupt(
        session_id="s1",
        approval_id="ah1",
        actions=(_pending_action(),),
        context_snapshot=context.to_dict(),
        round_num=1,
        model="gpt-test",
        usage_so_far={"total_tokens": 5},
    )


@pytest.mark.asyncio
async def test_resume_approve_handoff_terminates_with_handoff_content() -> None:
    """锁定 resume + approve → handoff 终止行为。

    构造 round_num=1 审批中断（handoff_to_agent 工具），携带 approve 决策
    调用 resume。据当前行为：
    1. _apply_approval_decisions 执行 handoff_to_agent → HandoffPerformed 被捕获
    2. ToolMessage 写入 context 并带 handoff_target metadata
    3. resume 调 _iter_rounds(start_round=2)，下一轮模型返回 tool_calls
    4. 第 3 轮入口 detect_handoff 命中（round_num=3 > start_round=2）
    5. result.status == "completed"，content 为目标 Agent 回复

    注意：handoff 检测条件 ``round_num > start_round`` 意味着恢复后的第一轮
    模型调用必须发生（工具执行完成后的回合），handoff 在下一轮入口短路。
    因此需要第一轮模型返回 tool_calls（触发又一轮），第二轮入口才能命中。
    """
    store = MemoryApprovalStore()
    adapter = _make_adapter(store)
    context = _seed_context_with_handoff_tool_call()
    interrupt = _interrupt(context)

    # resume 后 iter_rounds(start_round=2)：
    # round_num=2: 模型调用发生（需 tool_calls 让循环继续）
    # round_num=3: 入口检测到 handoff 标记 → 短路
    model_access = MagicMock()
    install_stream_mock(
        model_access,
        responses=[
            # round 2: 返回 tool_calls 让循环继续
            LLMResponse(
                content="",
                model="gpt-test",
                usage={"total_tokens": 3},
                tool_calls=[
                    ToolCallRequest("call-2", "handoff_to_agent", '{"target":"another"}')
                ],
            ),
            # round 3: 不会被消费（handoff 短路在入口）
        ],
    )

    result = await adapter.resume(
        context,
        _config(),
        model_access,
        interrupt,
        (ApprovalDecision("approve", "call-h1"),),
    )

    # handoff 终止：content 来自 round 1 的 HandoffPerformed.content
    assert result.status == "completed"
    assert result.content == "reply from agent_b"
    assert result.terminated_reason == "completed"
