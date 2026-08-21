"""TaskAgentAdapter 审批恢复与风险门禁测试。"""

from __future__ import annotations

from collections.abc import Set as AbstractSet
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.value_objects import (
    AgentConfig,
    AgentResult,
    ApprovalDecision,
    ApprovalInterrupt,
    ApprovalRequiredPayload,
    PendingActionRequest,
)
from domain.chat.context import ConversationContext, SystemMessage, ToolMessage, UserMessage
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import ToolCallRequest
from domain.prompt.value_objects import LoadedPrompt
from domain.task.value_objects import TaskApprovalResumeRequest, TaskContinueRequest, TaskStatus
from infrastructure.agent.approval_serialization import approval_payload_to_metadata
from infrastructure.agent.approval_state_store import approval_interrupt_to_dict
from infrastructure.task.task_agent_adapter import TaskAgentAdapter


def _schema(name: str) -> dict[str, Any]:
    """构造测试工具 schema。"""
    return {
        "type": "function",
        "function": {"name": name, "description": name, "parameters": {}},
    }


def _valid_context(boundary: list[str] | None = None) -> ConversationContext:
    """构造满足任务恢复前置条件的上下文。"""
    context = ConversationContext()
    context.append_message(
        SystemMessage(
            content="system",
            metadata={"task_allowed_tool_names": boundary},
        )
    )
    context.add_user_message("goal")
    context.add_assistant_message_with_tool_calls(
        "",
        [ToolCallRequest(id="call-1", name="search", arguments="{}")],
    )
    context.add_tool_result("search", "result", "call-1")
    return context


def _adapter(
    *,
    agent: MagicMock,
    context: ConversationContext,
    approval_store: MagicMock | None,
) -> tuple[TaskAgentAdapter, MagicMock]:
    """构造测试用任务适配器。"""
    tool_registry = MagicMock()

    def get_schemas(
        tool_names: AbstractSet[str] | None = None,
    ) -> list[dict[str, Any]]:
        return [
            schema
            for schema in [_schema("search"), _schema("write")]
            if tool_names is None or schema["function"]["name"] in set(tool_names)
        ]

    tool_registry.get_schemas.side_effect = get_schemas
    model_registry = MagicMock()
    model_registry.get_default_model.return_value = "test-model"
    model_registry.get_adapter_for_model.return_value = MagicMock()
    session_store = MagicMock()
    session_store.load = AsyncMock(return_value=context)
    session_store.save = AsyncMock()
    adapter = TaskAgentAdapter(
        agent=agent,
        tool_registry=tool_registry,
        model_registry=model_registry,
        compaction=MagicMock(),
        session_store=session_store,
        prompt_registry=MagicMock(
            get=MagicMock(
                return_value=LoadedPrompt(
                    prompt_id="task-template@v1",
                    name="task-template",
                    version="v1",
                    content="template",
                )
            )
        ),
        approval_store=approval_store,
    )
    return adapter, session_store


@pytest.mark.asyncio
async def test_resume_approval_preserves_tool_boundary_and_no_duplicate_goal() -> None:
    """审批恢复应复用中断上下文与工具边界，不重复追加原始 goal。"""
    context = _valid_context(boundary=["search"])
    interrupt = ApprovalInterrupt(
        session_id="s1",
        approval_id="approval-1",
        actions=(
            PendingActionRequest(
                tool_call_id="call-1",
                tool_name="search",
                arguments='{"query":"safe"}',
                allowed_decisions=frozenset({"approve"}),
            ),
        ),
        context_snapshot=context.to_dict(),
        round_num=1,
        model="stored-model",
    )
    approval_store = MagicMock()
    approval_store.load = AsyncMock(return_value=interrupt)
    approval_store.consume = AsyncMock(return_value=interrupt)
    captured_configs: list[AgentConfig] = []

    async def resume(
        ctx: ConversationContext,
        config: AgentConfig,
        _model_access: ModelAccessPort,
        consumed: ApprovalInterrupt,
        decisions: tuple[ApprovalDecision, ...],
    ) -> AgentResult:
        captured_configs.append(config)
        assert consumed.approval_id == "approval-1"
        assert decisions == (ApprovalDecision(type="approve", tool_call_id="call-1"),)
        assert sum(isinstance(message, UserMessage) for message in ctx.get_messages()) == 1
        return AgentResult(content="done", model="resumed-model")

    agent = MagicMock()
    agent.resume = AsyncMock(side_effect=resume)
    adapter, session_store = _adapter(
        agent=agent,
        context=context,
        approval_store=approval_store,
    )

    result = await adapter.resume_approval(
        TaskApprovalResumeRequest(
            session_id="s1",
            approval_id="approval-1",
            decisions=(ApprovalDecision(type="approve", tool_call_id="call-1"),),
            model="override-model",
        )
    )

    assert result.status == TaskStatus.SUCCESS
    assert result.content == "done"
    assert captured_configs[0].model == "override-model"
    assert captured_configs[0].allowed_tool_names == frozenset({"search"})
    session_store.save.assert_awaited_once()


@pytest.mark.asyncio
async def test_resume_approval_marks_guardrail_approval_as_risk_gate_required() -> None:
    """guardrail 来源审批恢复后再次等待审批时应透传风险门禁。"""
    context = _valid_context(boundary=["search"])
    interrupt = ApprovalInterrupt(
        session_id="s1",
        approval_id="approval-1",
        actions=(
            PendingActionRequest(
                tool_call_id="call-1",
                tool_name="search",
                arguments='{"query":"danger"}',
                allowed_decisions=frozenset({"approve", "reject"}),
            ),
        ),
        context_snapshot=context.to_dict(),
        round_num=1,
        model="stored-model",
        metadata={"source": "guardrail"},
    )
    approval_store = MagicMock()
    approval_store.load = AsyncMock(return_value=interrupt)
    approval_store.consume = AsyncMock(return_value=interrupt)

    async def resume(
        ctx: ConversationContext,
        _config: AgentConfig,
        _model_access: ModelAccessPort,
        _consumed: ApprovalInterrupt,
        _decisions: tuple[ApprovalDecision, ...],
    ) -> AgentResult:
        ctx.add_assistant_message_with_tool_calls(
            "",
            [ToolCallRequest(id="call-2", name="search", arguments='{"query":"danger"}')],
        )
        ctx.add_tool_result("search", "blocked", "call-2")
        tool_message = ctx.get_messages()[-1]
        assert isinstance(tool_message, ToolMessage)
        tool_message.metadata.update(
            {
                "guardrail_action": "stop",
                "guardrail_reason": "tool_risk_gate_required",
                "risk_gate_required": True,
            }
        )
        return AgentResult(
            content="",
            model="resumed-model",
            status="approval_required",
            approval=ApprovalRequiredPayload(
                session_id="s1",
                approval_id="approval-2",
                actions=(
                    PendingActionRequest(
                        tool_call_id="call-2",
                        tool_name="search",
                        arguments='{"query":"danger"}',
                        allowed_decisions=frozenset({"approve", "reject"}),
                        reason="need approval again",
                    ),
                ),
                prompt_id="task-template@v1",
                metadata={
                    "source": "guardrail",
                    "guardrail_reason": "tool_risk_gate_required",
                    "risk_gate_required": True,
                },
            ),
        )

    agent = MagicMock()
    agent.resume = AsyncMock(side_effect=resume)
    adapter, session_store = _adapter(
        agent=agent,
        context=context,
        approval_store=approval_store,
    )

    result = await adapter.resume_approval(
        TaskApprovalResumeRequest(
            session_id="s1",
            approval_id="approval-1",
            decisions=(ApprovalDecision(type="approve", tool_call_id="call-1"),),
        )
    )

    assert result.status == TaskStatus.HUMAN_INTERVENTION_REQUIRED
    assert result.approval_id == "approval-2"
    assert result.segment_metadata.risk_gate_required is True
    assert result.segment_metadata.guardrail_reason == "tool_risk_gate_required"
    session_store.save.assert_not_called()


@pytest.mark.asyncio
async def test_resume_approval_observe_metadata_does_not_force_risk_gate() -> None:
    """observe 模式只透传 guardrail_reason，不应误置风险门禁。"""
    context = _valid_context(boundary=["search"])
    interrupt = ApprovalInterrupt(
        session_id="s1",
        approval_id="approval-1",
        actions=(
            PendingActionRequest(
                tool_call_id="call-1",
                tool_name="search",
                arguments='{"query":"observe"}',
                allowed_decisions=frozenset({"approve"}),
            ),
        ),
        context_snapshot=context.to_dict(),
        round_num=1,
        model="stored-model",
    )
    approval_store = MagicMock()
    approval_store.load = AsyncMock(return_value=interrupt)
    approval_store.consume = AsyncMock(return_value=interrupt)

    async def resume(
        ctx: ConversationContext,
        _config: AgentConfig,
        _model_access: ModelAccessPort,
        _consumed: ApprovalInterrupt,
        _decisions: tuple[ApprovalDecision, ...],
    ) -> AgentResult:
        ctx.add_assistant_message_with_tool_calls(
            "",
            [ToolCallRequest(id="call-2", name="search", arguments='{"query":"observe"}')],
        )
        ctx.add_tool_result("search", "observed", "call-2")
        tool_message = ctx.get_messages()[-1]
        assert isinstance(tool_message, ToolMessage)
        tool_message.metadata.update(
            {
                "guardrail_action": "observe",
                "guardrail_reason": "tool_risk_gate_required",
                "risk_gate_required": False,
            }
        )
        return AgentResult(
            content="",
            model="resumed-model",
            terminated_reason="max_rounds",
        )

    agent = MagicMock()
    agent.resume = AsyncMock(side_effect=resume)
    adapter, _ = _adapter(
        agent=agent,
        context=context,
        approval_store=approval_store,
    )

    result = await adapter.resume_approval(
        TaskApprovalResumeRequest(
            session_id="s1",
            approval_id="approval-1",
            decisions=(ApprovalDecision(type="approve", tool_call_id="call-1"),),
        )
    )

    assert result.status == TaskStatus.PAUSED
    assert result.segment_metadata.risk_gate_required is False
    assert result.segment_metadata.guardrail_reason == "tool_risk_gate_required"


@pytest.mark.asyncio
async def test_continue_task_reads_risk_gate_required_from_tool_metadata() -> None:
    """任务 continue 应从稳定 ToolMessage.metadata 推导风险门禁。"""
    context = _valid_context(boundary=["search"])

    async def run(
        ctx: ConversationContext,
        _config: AgentConfig,
        _model_access: ModelAccessPort,
    ) -> AgentResult:
        ctx.add_assistant_message_with_tool_calls(
            "",
            [ToolCallRequest(id="call-2", name="search", arguments='{"query":"danger"}')],
        )
        ctx.add_tool_result("search", "blocked", "call-2")
        tool_message = ctx.get_messages()[-1]
        assert isinstance(tool_message, ToolMessage)
        tool_message.metadata.update(
            {
                "guardrail_action": "stop",
                "guardrail_reason": "tool_risk_gate_required",
                "risk_gate_required": True,
            }
        )
        return AgentResult(
            content="",
            model="test-model",
            terminated_reason="max_rounds",
            usage={"total_tokens": 1},
        )

    agent = MagicMock()
    agent.run = AsyncMock(side_effect=run)
    adapter, session_store = _adapter(
        agent=agent,
        context=context,
        approval_store=None,
    )

    result = await adapter.continue_task(TaskContinueRequest(session_id="s1"))

    assert result.status == TaskStatus.PAUSED
    assert result.segment_metadata.segment_stop_reason == "risk_gate_required"
    assert result.segment_metadata.risk_gate_required is True
    assert result.segment_metadata.guardrail_reason == "tool_risk_gate_required"
    session_store.save.assert_awaited_once()


def test_approval_display_keeps_full_arguments_but_metadata_redacts_them() -> None:
    """审批展示保留完整参数，而通用元数据/日志安全序列化不复制完整参数。"""
    actions = (
        PendingActionRequest(
            tool_call_id="call-2",
            tool_name="search",
            arguments='{"query":"danger","token":"secret"}',
            allowed_decisions=frozenset({"approve", "reject"}),
            reason="need approval again",
        ),
    )
    payload = ApprovalRequiredPayload(
        session_id="s1",
        approval_id="approval-2",
        actions=actions,
        prompt_id="task-template@v1",
        metadata={
            "source": "guardrail",
            "guardrail_reason": "tool_risk_gate_required",
            "risk_gate_required": True,
            "raw_arguments": '{"query":"danger","token":"secret"}',
        },
    )
    interrupt = ApprovalInterrupt(
        session_id="s1",
        approval_id="approval-2",
        actions=actions,
        context_snapshot={},
        round_num=2,
        model="test-model",
        metadata={
            "source": "guardrail",
            "guardrail_reason": "tool_risk_gate_required",
            "risk_gate_required": True,
        },
    )

    metadata = approval_payload_to_metadata(payload)
    stored = approval_interrupt_to_dict(interrupt)

    assert stored["actions"][0]["arguments"] == '{"query":"danger","token":"secret"}'
    assert payload.actions[0].arguments == '{"query":"danger","token":"secret"}'
    assert metadata["action_summaries"][0]["tool_name"] == "search"
    assert "arguments" not in metadata["action_summaries"][0]
    assert "raw_arguments" not in metadata
    assert '{"query":"danger","token":"secret"}' not in str(metadata)
