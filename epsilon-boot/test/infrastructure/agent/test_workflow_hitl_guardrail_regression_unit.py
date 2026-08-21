"""Workflow context 下 HITL 与 Guardrail 回归测试。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.guardrails import GuardrailMode, GuardrailPolicy
from domain.chat.context import ConversationContext, ToolMessage
from domain.model_access.value_objects import LLMResponse, ToolCallRequest
from domain.run.workflow import CollaborationLimit, WorkflowPhase
from domain.run.workflow_context import (
    WorkflowCollaborationContext,
    get_workflow_collaboration_context,
    reset_workflow_collaboration_context,
    set_workflow_collaboration_context,
)
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from infrastructure.agent.static_guardrail_policy import StaticAgentGuardrailPolicy
from test.infrastructure.agent.test_react_agent_guardrail_unit import (
    CriticalTool,
    guardrail_config,
    tool_call,
)
from test.infrastructure.agent.test_react_agent_hitl_unit import (
    FakeModel,
    MemoryApprovalStore,
    RecordingTool,
    hitl_adapter,
    hitl_config,
)


def _workflow_token():
    return set_workflow_collaboration_context(
        WorkflowCollaborationContext(
            run_id="run-1",
            workflow_name="code_change",
            phase=WorkflowPhase.EXECUTE,
            source_role="executor",
            limit=CollaborationLimit(),
            depth=0,
            handoff_count=0,
            delegation_count=0,
        )
    )


@pytest.mark.asyncio
async def test_hitl_interrupt_unchanged_with_workflow_context() -> None:
    store = MemoryApprovalStore()
    tool = RecordingTool()
    adapter = hitl_adapter(store, tool)
    context = ConversationContext()
    context.add_user_message("write")
    model = FakeModel(
        [
            LLMResponse(
                content="",
                model="gpt-test",
                usage={"total_tokens": 2},
                tool_calls=[
                    ToolCallRequest("call-1", "write_file", '{"path":"a.txt"}'),
                ],
            )
        ]
    )
    token = _workflow_token()
    try:
        result = await adapter.run(context, hitl_config(), model)
        assert get_workflow_collaboration_context() is not None
    finally:
        reset_workflow_collaboration_context(token)

    assert result.status == "approval_required"
    assert result.approval is not None
    assert store.saved is not None
    assert tool.requests == []


@pytest.mark.asyncio
async def test_guardrail_enforce_metadata_unchanged_with_workflow_context() -> None:
    registry = MagicMock()
    registry.get.return_value = CriticalTool()
    registry.execute = AsyncMock(return_value="tool ok")
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=MagicMock(),
        guardrail_policy=StaticAgentGuardrailPolicy(GuardrailPolicy(mode=GuardrailMode.ENFORCE)),
    )
    context = ConversationContext()
    token = _workflow_token()
    try:
        executable, approval = await adapter.prepare_tool_calls_for_execution(
            context=context,
            config=guardrail_config(),
            tool_calls=(tool_call(),),
            round_num=1,
            model="test-model",
            usage_so_far={},
        )
    finally:
        reset_workflow_collaboration_context(token)

    assert executable == ()
    assert approval is None
    registry.execute.assert_not_awaited()
    last = context.get_messages()[-1]
    assert isinstance(last, ToolMessage)
    assert last.metadata["error"] is True
    assert last.metadata["guardrail_blocked"] is True
    assert last.metadata["guardrail_reason"] == "tool_risk_gate_required"
    assert last.metadata["guardrail_action"] == "stop"
    assert last.metadata["risk_gate_required"] is True
