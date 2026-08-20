"""ReActAgentAdapter guardrail 运行时接线测试。"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.guardrails import (
    GuardrailMode,
    GuardrailModelPricing,
    GuardrailPolicy,
    ToolRiskLevel,
    merge_guardrail_summary,
)
from domain.agent.ports import RunGuardrailRecorderPort
from domain.agent.tools import Tool, ToolExecutionResult
from domain.agent.value_objects import (
    AgentConfig,
    ApprovalDecision,
    ApprovalInterrupt,
    ApprovalRequiredPayload,
    EditedAction,
    PendingActionRequest,
)
from domain.chat.context import AssistantMessage, ConversationContext, ToolMessage
from domain.model_access.value_objects import LLMResponse, ToolCallRequest
from domain.run.checkpoint_context import (
    RunCheckpointExecutionContext,
    reset_run_checkpoint_context,
    set_run_checkpoint_context,
)
from domain.run.runtime_context import (
    RunExecutionContext,
    reset_run_execution_context,
    set_run_execution_context,
)
from domain.run.value_objects import (
    ToolLedgerStatus,
    ToolReplayPolicy,
    ToolResultLedgerEntry,
    ToolSideEffectLevel,
)
from domain.run.workflow import AgentRoleCapability, CollaborationLimit, WorkflowPhase
from domain.run.workflow_context import (
    WorkflowCollaborationContext,
    reset_workflow_collaboration_context,
    set_workflow_collaboration_context,
)
from infrastructure.agent.approval_serialization import approval_payload_to_metadata
from infrastructure.agent.guardrail_serialization import (
    guardrail_observation_to_event_payload,
    guardrail_summary_to_dict,
)
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from infrastructure.agent.static_guardrail_policy import StaticAgentGuardrailPolicy
from test.infrastructure.agent.test_react_agent_hitl_unit import FakeContextBuilder, FakeModel


class _HighRiskTool(Tool):
    @property
    def name(self) -> str:
        return "high_risk_tool"

    @property
    def description(self) -> str:
        return "high risk"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def risk_level(self) -> ToolRiskLevel:
        return ToolRiskLevel.HIGH

    async def execute(self, **kwargs) -> ToolExecutionResult:
        return ToolExecutionResult(content="should not run")


class _CriticalTool(Tool):
    @property
    def name(self) -> str:
        return "critical_tool"

    @property
    def description(self) -> str:
        return "critical"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    @property
    def risk_level(self) -> ToolRiskLevel:
        return ToolRiskLevel.CRITICAL

    async def execute(self, **kwargs) -> ToolExecutionResult:
        return ToolExecutionResult(content="should not run")


class _FailingEchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo_tool"

    @property
    def description(self) -> str:
        return "failing echo"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}}

    @property
    def risk_level(self) -> ToolRiskLevel:
        return ToolRiskLevel.LOW

    async def execute(self, **kwargs: Any) -> str:
        raise RuntimeError("boom")


class _EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo_tool"

    @property
    def description(self) -> str:
        return "echo"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }

    @property
    def risk_level(self) -> ToolRiskLevel:
        return ToolRiskLevel.HIGH

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        return ToolExecutionResult(content=str(kwargs.get("path", "")))


class _EventStore:
    """记录 Run 事件的测试 event store。"""

    def __init__(self) -> None:
        self.events = []

    async def append_event(self, run_id, event_type, payload):
        """追加事件并保留原始 payload。"""

        event = MagicMock(run_id=run_id, event_type=event_type, payload=payload)
        self.events.append(event)
        return event


class _Recorder(RunGuardrailRecorderPort):
    """记录 guardrail observation 的测试 recorder。"""

    def __init__(self) -> None:
        self.calls = []

    async def record_observation(self, *, observation):
        self.calls.append(observation)
        return None


class _ReplaySink:
    """返回已完成工具账本的测试 checkpoint sink。"""

    def __init__(self, entry: ToolResultLedgerEntry) -> None:
        self.entry = entry
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def before_tool_call(self, **kwargs: Any) -> ToolResultLedgerEntry:
        """记录 before 调用并返回可 replay 的已完成账本。"""

        self.calls.append(("before_tool_call", kwargs))
        return self.entry

    async def after_tool_call(self, **kwargs: Any) -> None:
        """记录 after 调用；replay 场景不应触达。"""

        self.calls.append(("after_tool_call", kwargs))
        return None


def _config(tool_name: str) -> AgentConfig:
    return AgentConfig(
        system_prompt="sys",
        tool_schemas=[{"type": "function", "function": {"name": tool_name, "parameters": {}}}],
        model="test-model",
        max_rounds=3,
        prompt_id="chat-default@v1",
    )


def _interrupt_for_tool(tool_name: str, arguments: str = "{}") -> ApprovalInterrupt:
    return ApprovalInterrupt(
        session_id="session-1",
        approval_id="approval-original",
        actions=(
            PendingActionRequest(
                tool_call_id="call-1",
                tool_name=tool_name,
                arguments=arguments,
                allowed_decisions=frozenset({"approve", "edit", "reject"}),
                reason="高风险工具需要人工确认",
            ),
        ),
        context_snapshot={},
        round_num=1,
        model="test-model",
        usage_so_far={"total_tokens": 9},
        metadata={"source": "guardrail", "tool_call_ids": ["call-1"]},
    )


@pytest.mark.asyncio
async def test_require_approval_reuses_interrupt_and_records_guardrail_observation() -> None:
    registry = MagicMock()
    registry.get.return_value = _HighRiskTool()
    registry.execute = AsyncMock(return_value=ToolExecutionResult(content="tool ok"))
    recorder = _Recorder()
    approval_store = AsyncMock()
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=MagicMock(),
        approval_store=approval_store,
        guardrail_policy=StaticAgentGuardrailPolicy(
            GuardrailPolicy(mode=GuardrailMode.ENFORCE, enforce_high_risk_tools=True)
        ),
        run_guardrail_recorder=recorder,
    )
    context = ConversationContext()
    context.session_id = "session-1"
    token = set_run_execution_context(
        RunExecutionContext(run_id="run-1", owner_id="worker-1", segment_index=2)
    )
    try:
        result = await adapter._prepare_tool_calls_for_execution(
            context=context,
            config=_config("high_risk_tool"),
            tool_calls=(ToolCallRequest(id="call-1", name="high_risk_tool", arguments="{}"),),
            round_num=1,
            model="test-model",
            usage_so_far={"total_tokens": 9},
        )
    finally:
        reset_run_execution_context(token)

    executable, approval = result
    assert executable == ()
    assert isinstance(approval, ApprovalRequiredPayload)
    approval_store.save.assert_awaited_once()
    interrupt = approval_store.save.await_args.args[0]
    assert interrupt.metadata["source"] == "guardrail"
    assert interrupt.metadata["guardrail_action"] == "require_approval"
    assert interrupt.metadata["guardrail_reason"] == "tool_risk_gate_required"
    assert interrupt.metadata["risk_gate_required"] is True
    assert interrupt.metadata["tool_call_ids"] == ["call-1"]
    registry.execute.assert_not_awaited()
    assert len(recorder.calls) == 1
    observation = recorder.calls[0]
    assert observation.stage.value == "tool_before_execution"
    assert observation.approval_id == approval.approval_id
    assert observation.tool_name == "high_risk_tool"


@pytest.mark.asyncio
async def test_workflow_role_capability_denies_real_react_tool_before_execution() -> None:
    """role capability 开启时真实 ReAct 工具调用应在 ToolRegistry.execute 前拒绝。"""

    registry = MagicMock()
    registry.get.return_value = _EchoTool()
    registry.execute = AsyncMock(return_value=ToolExecutionResult(content="tool ok"))
    approval_store = AsyncMock()
    events = _EventStore()
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=MagicMock(),
        approval_store=approval_store,
        run_event_store=events,
    )
    context = ConversationContext()
    context.session_id = "session-1"
    run_token = set_run_execution_context(
        RunExecutionContext(run_id="run-1", owner_id="worker-1", segment_index=1)
    )
    workflow_token = set_workflow_collaboration_context(
        WorkflowCollaborationContext(
            run_id="run-1",
            workflow_name="code_change",
            phase=WorkflowPhase.EXECUTE,
            source_role="executor",
            limit=CollaborationLimit(),
            depth=0,
            handoff_count=0,
            delegation_count=0,
            role_capability_enabled=True,
            roles=(AgentRoleCapability("executor"),),
        )
    )
    try:
        executable, approval = await adapter._prepare_tool_calls_for_execution(
            context=context,
            config=_config("echo_tool"),
            tool_calls=(ToolCallRequest(id="call-denied", name="echo_tool", arguments="{}"),),
            round_num=1,
            model="test-model",
            usage_so_far={},
        )
    finally:
        reset_workflow_collaboration_context(workflow_token)
        reset_run_execution_context(run_token)

    assert executable == ()
    assert approval is not None
    assert approval.metadata["source"] == "workflow_role_capability"
    registry.execute.assert_not_awaited()
    approval_store.save.assert_awaited_once()
    assert events.events[0].event_type.value == "role_capability_rejected"
    assert events.events[0].payload["action"] == "tool"
    assert events.events[0].payload["target"] == "echo_tool"


@pytest.mark.asyncio
async def test_stop_marks_tool_message_with_stable_risk_gate_metadata() -> None:
    registry = MagicMock()
    registry.get.return_value = _CriticalTool()
    registry.execute = AsyncMock(return_value=ToolExecutionResult(content="tool ok"))
    recorder = _Recorder()
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=MagicMock(),
        guardrail_policy=StaticAgentGuardrailPolicy(GuardrailPolicy(mode=GuardrailMode.ENFORCE)),
        run_guardrail_recorder=recorder,
    )
    context = ConversationContext()
    token = set_run_execution_context(
        RunExecutionContext(run_id="run-1", owner_id="worker-1", segment_index=3)
    )
    try:
        executable, approval = await adapter._prepare_tool_calls_for_execution(
            context=context,
            config=_config("critical_tool"),
            tool_calls=(ToolCallRequest(id="call-2", name="critical_tool", arguments="{}"),),
            round_num=2,
            model="test-model",
            usage_so_far={"total_tokens": 11},
        )
    finally:
        reset_run_execution_context(token)

    assert executable == ()
    assert approval is None
    registry.execute.assert_not_awaited()
    last = context.get_messages()[-1]
    assert isinstance(last, ToolMessage)
    assert last.metadata["error"] is True
    assert last.metadata["guardrail_blocked"] is True
    assert last.metadata["guardrail_action"] == "stop"
    assert last.metadata["guardrail_reason"] == "tool_risk_gate_required"
    assert last.metadata["risk_gate_required"] is True
    assert len(recorder.calls) == 1
    assert recorder.calls[0].decision.action.value == "stop"


@pytest.mark.asyncio
async def test_observe_mode_keeps_tool_execution_and_records_before_after_observations() -> None:
    registry = MagicMock()
    registry.get.return_value = _CriticalTool()
    registry.execute = AsyncMock(return_value=ToolExecutionResult(content="tool ok"))
    recorder = _Recorder()
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=MagicMock(),
        guardrail_policy=StaticAgentGuardrailPolicy(GuardrailPolicy(mode=GuardrailMode.OBSERVE)),
        run_guardrail_recorder=recorder,
    )
    context = ConversationContext()
    token = set_run_execution_context(
        RunExecutionContext(run_id="run-1", owner_id="worker-1", segment_index=1)
    )
    try:
        executable, approval = await adapter._prepare_tool_calls_for_execution(
            context=context,
            config=_config("critical_tool"),
            tool_calls=(ToolCallRequest(id="call-3", name="critical_tool", arguments="{}"),),
            round_num=4,
            model="test-model",
            usage_so_far={"total_tokens": 3},
        )
        assert approval is None
        assert executable[0].id == "call-3"
        result, is_error = await adapter._execute_tool_call(
            context,
            executable[0],
            _config("critical_tool"),
            round_num=4,
            skip_guardrail_before=True,
        )
    finally:
        reset_run_execution_context(token)

    assert result.content == "tool ok"
    assert is_error is False
    registry.execute.assert_awaited_once()
    assert [item.stage.value for item in recorder.calls] == [
        "tool_before_execution",
        "tool_after_execution",
    ]
    assert recorder.calls[0].decision.action.value == "observe"
    assert recorder.calls[1].decision.action.value == "allow"


@pytest.mark.asyncio
async def test_resume_approve_returns_new_guardrail_approval_instead_of_raising() -> None:
    registry = MagicMock()
    registry.get.return_value = _HighRiskTool()
    registry.execute = AsyncMock(return_value=ToolExecutionResult(content="tool ok"))
    approval_store = AsyncMock()
    recorder = _Recorder()
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=MagicMock(),
        approval_store=approval_store,
        guardrail_policy=StaticAgentGuardrailPolicy(
            GuardrailPolicy(mode=GuardrailMode.ENFORCE, enforce_high_risk_tools=True)
        ),
        run_guardrail_recorder=recorder,
    )
    context = ConversationContext()
    context.session_id = "session-1"
    context.add_system_message("sys")
    context.add_user_message("run risky tool once")
    context._messages.append(
        AssistantMessage(
            content="",
            tool_calls=[ToolCallRequest("call-1", "high_risk_tool", "{}")],
        )
    )
    token = set_run_execution_context(
        RunExecutionContext(run_id="run-1", owner_id="worker-1", segment_index=4)
    )
    try:
        result = await adapter.resume(
            context,
            _config("high_risk_tool"),
            FakeModel(["should not be used"]),  # type: ignore[arg-type]
            _interrupt_for_tool("high_risk_tool"),
            (ApprovalDecision("approve", "call-1"),),
        )
    finally:
        reset_run_execution_context(token)

    assert result.status == "approval_required"
    assert result.approval is not None
    assert result.approval.approval_id != "approval-original"
    assert result.approval.actions[0].tool_call_id == "call-1"
    approval_store.save.assert_awaited_once()
    saved_interrupt = approval_store.save.await_args.args[0]
    assert saved_interrupt.approval_id == result.approval.approval_id
    assert saved_interrupt.metadata["source"] == "guardrail"
    assert saved_interrupt.metadata["tool_call_ids"] == ["call-1"]
    registry.execute.assert_not_awaited()
    assert len(recorder.calls) == 1
    assert recorder.calls[0].approval_id == result.approval.approval_id
    assert recorder.calls[0].decision.action.value == "require_approval"
    messages = context.get_messages()
    assert [message.role for message in messages].count("user") == 1


@pytest.mark.asyncio
async def test_resume_edit_returns_new_guardrail_approval_without_duplicate_user_input() -> None:
    registry = MagicMock()
    registry.get.return_value = _EchoTool()
    registry.execute = AsyncMock(return_value=ToolExecutionResult(content="tool ok"))
    approval_store = AsyncMock()
    recorder = _Recorder()
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=MagicMock(),
        approval_store=approval_store,
        guardrail_policy=StaticAgentGuardrailPolicy(
            GuardrailPolicy(mode=GuardrailMode.ENFORCE, enforce_high_risk_tools=True)
        ),
        run_guardrail_recorder=recorder,
    )
    context = ConversationContext()
    context.session_id = "session-1"
    context.add_system_message("sys")
    context.add_user_message("run risky tool once")
    context._messages.append(
        AssistantMessage(
            content="",
            tool_calls=[ToolCallRequest("call-1", "echo_tool", '{"path":"a.txt"}')],
        )
    )
    token = set_run_execution_context(
        RunExecutionContext(run_id="run-1", owner_id="worker-1", segment_index=5)
    )
    try:
        result = await adapter.resume(
            context,
            _config("echo_tool"),
            FakeModel(["should not be used"]),  # type: ignore[arg-type]
            _interrupt_for_tool("echo_tool", '{"path":"a.txt"}'),
            (
                ApprovalDecision(
                    "edit",
                    "call-1",
                    edited_action=EditedAction("echo_tool", '{"path":"b.txt"}'),
                ),
            ),
        )
    finally:
        reset_run_execution_context(token)

    assert result.status == "approval_required"
    assert result.approval is not None
    assert result.approval.approval_id != "approval-original"
    assert result.approval.actions[0].tool_name == "echo_tool"
    assert result.approval.actions[0].arguments == '{"path":"b.txt"}'
    approval_store.save.assert_awaited_once()
    saved_interrupt = approval_store.save.await_args.args[0]
    assert saved_interrupt.actions[0].arguments == '{"path":"b.txt"}'
    metadata = approval_payload_to_metadata(result.approval)
    assert metadata["source"] == "guardrail"
    assert metadata["risk_gate_required"] is True
    assert metadata["action_summaries"][0]["tool_name"] == "echo_tool"
    assert "arguments" not in metadata["action_summaries"][0]
    assert '{"path":"b.txt"}' not in str(metadata)
    assert '{"path":"b.txt"}' not in str(saved_interrupt.metadata)
    registry.execute.assert_not_awaited()
    assert len(recorder.calls) == 1
    assert recorder.calls[0].tool_name == "echo_tool"
    assert recorder.calls[0].approval_id == result.approval.approval_id
    messages = context.get_messages()
    assert [message.role for message in messages].count("user") == 1


@pytest.mark.asyncio
async def test_model_completed_observation_uses_response_usage_real_clock_and_pricing() -> None:
    registry = MagicMock()
    registry.get.return_value = None
    recorder = _Recorder()
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=FakeContextBuilder(),
        guardrail_policy=StaticAgentGuardrailPolicy(
            GuardrailPolicy(
                mode=GuardrailMode.OBSERVE,
                model_pricing={
                    "test-model": GuardrailModelPricing(
                        prompt_per_1m=1.0,
                        completion_per_1m=3.0,
                    )
                },
            )
        ),
        run_guardrail_recorder=recorder,
    )
    context = ConversationContext()
    context.add_user_message("hello")
    token = set_run_execution_context(
        RunExecutionContext(run_id="run-1", owner_id="worker-1", segment_index=1)
    )
    try:
        result = await adapter.run(
            context,
            _config("echo_tool"),
            FakeModel(
                [
                    LLMResponse(
                        content="done",
                        model="test-model",
                        usage={
                            "prompt_tokens": 1000,
                            "completion_tokens": 500,
                            "total_tokens": 1500,
                        },
                    )
                ]
            ),
        )
    finally:
        reset_run_execution_context(token)

    assert result.content == "done"
    assert [call.stage.value for call in recorder.calls] == ["model_completed"]
    stats = recorder.calls[0].stats
    assert stats.total_tokens == 1500
    assert stats.prompt_tokens == 1000
    assert stats.completion_tokens == 500
    assert stats.total_model_calls == 1
    assert stats.context_growth_messages == 1
    assert stats.elapsed_ms >= 0
    assert stats.estimated_cost == 0.0025
    assert stats.cost_available is True


@pytest.mark.asyncio
async def test_model_completed_missing_price_keeps_cost_unavailable_without_blocking() -> None:
    registry = MagicMock()
    registry.get.return_value = None
    recorder = _Recorder()
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=FakeContextBuilder(),
        guardrail_policy=StaticAgentGuardrailPolicy(
            GuardrailPolicy(mode=GuardrailMode.OBSERVE, model_pricing={})
        ),
        run_guardrail_recorder=recorder,
    )
    context = ConversationContext()
    context.add_user_message("hello")
    token = set_run_execution_context(
        RunExecutionContext(run_id="run-1", owner_id="worker-1", segment_index=1)
    )
    try:
        result = await adapter.run(
            context,
            _config("echo_tool"),
            FakeModel(
                [
                    LLMResponse(
                        content="done",
                        model="unpriced-model",
                        usage={"prompt_tokens": 10, "completion_tokens": 5},
                    )
                ]
            ),
        )
    finally:
        reset_run_execution_context(token)

    assert result.status == "completed"
    assert recorder.calls[0].decision.action.value == "allow"
    assert recorder.calls[0].stats.total_tokens == 15
    assert recorder.calls[0].stats.estimated_cost is None
    assert recorder.calls[0].stats.cost_available is False


@pytest.mark.asyncio
async def test_tool_runtime_stats_accumulate_in_model_order_with_failures() -> None:
    registry = MagicMock()
    registry.get.return_value = _FailingEchoTool()
    registry.execute = AsyncMock(side_effect=RuntimeError("boom"))
    recorder = _Recorder()
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=MagicMock(),
        guardrail_policy=StaticAgentGuardrailPolicy(
            GuardrailPolicy(
                mode=GuardrailMode.OBSERVE,
                max_repeated_tool_calls=99,
                max_consecutive_failures=2,
            )
        ),
        run_guardrail_recorder=recorder,
    )
    context = ConversationContext()
    tool_calls = (
        ToolCallRequest("call-1", "echo_tool", '{"path":"a"}'),
        ToolCallRequest("call-2", "echo_tool", '{"path":"a"}'),
    )
    token = set_run_execution_context(
        RunExecutionContext(run_id="run-1", owner_id="worker-1", segment_index=1)
    )
    try:
        executable, approval = await adapter._prepare_tool_calls_for_execution(
            context=context,
            config=_config("echo_tool"),
            tool_calls=tool_calls,
            round_num=1,
            model="test-model",
            usage_so_far={"total_tokens": 5},
        )
        assert approval is None
        assert executable == tool_calls
        await adapter._dispatch_concurrent_tool_calls(
            context,
            executable,
            _config("echo_tool"),
            session_id="session-1",
            round_num=1,
        )
    finally:
        reset_run_execution_context(token)

    before_calls = [call for call in recorder.calls if call.stage.value == "tool_before_execution"]
    after_calls = [call for call in recorder.calls if call.stage.value == "tool_after_execution"]
    assert [call.tool_call_id for call in before_calls] == ["call-1", "call-2"]
    assert [call.tool_call_id for call in after_calls] == ["call-1", "call-2"]
    assert before_calls[0].stats.repeated_tool_call_count == 0
    assert before_calls[1].stats.repeated_tool_call_count == 1
    assert after_calls[0].stats.repeated_tool_call_count == 0
    assert after_calls[0].stats.total_tool_calls == 1
    assert after_calls[0].stats.consecutive_failure_count == 1
    assert after_calls[0].stats.last_tool_error is True
    assert after_calls[1].stats.repeated_tool_call_count == 1
    assert after_calls[1].stats.total_tool_calls == 2
    assert after_calls[1].stats.consecutive_failure_count == 2
    assert after_calls[1].decision.reason.value == "repeated_failure"
    assert all(isinstance(message, ToolMessage) for message in context.get_messages())


@pytest.mark.asyncio
async def test_checkpoint_completed_replay_skips_guardrail_stats_and_observations() -> None:
    registry = MagicMock()
    registry.get.return_value = _EchoTool()
    registry.execute = AsyncMock(return_value=ToolExecutionResult(content="should not run"))
    recorder = _Recorder()
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=MagicMock(),
        guardrail_policy=StaticAgentGuardrailPolicy(GuardrailPolicy(mode=GuardrailMode.OBSERVE)),
        run_guardrail_recorder=recorder,
    )
    context = ConversationContext()
    persisted_summary = {
        "runtime_stats": {
            "total_tool_calls": 4,
            "repeated_tool_call_count": 1,
            "consecutive_failure_count": 2,
        }
    }
    sink = _ReplaySink(_completed_ledger(result="cached", is_error=False))
    run_token = set_run_execution_context(
        RunExecutionContext(
            run_id="run-1",
            owner_id="worker-1",
            segment_index=1,
            recovery_mode=True,
            guardrail_summary=persisted_summary,
        )
    )
    checkpoint_token = set_run_checkpoint_context(
        RunCheckpointExecutionContext(
            run_id="run-1",
            owner_id="worker-1",
            segment_index=1,
            recovery_mode=True,
            sink=sink,
        )
    )
    try:
        executable, approval = await adapter._prepare_tool_calls_for_execution(
            context=context,
            config=_config("echo_tool"),
            tool_calls=(ToolCallRequest("call-1", "echo_tool", '{"path":"old"}'),),
            round_num=1,
            model="test-model",
            usage_so_far={"total_tokens": 9},
        )
        stats = adapter._guardrail_runtime_accumulator().snapshot()
    finally:
        reset_run_checkpoint_context(checkpoint_token)
        reset_run_execution_context(run_token)

    assert executable == ()
    assert approval is None
    registry.execute.assert_not_awaited()
    assert [name for name, _ in sink.calls] == ["before_tool_call"]
    assert recorder.calls == []
    assert isinstance(context.get_messages()[-1], ToolMessage)
    assert context.get_messages()[-1].content == "cached"
    assert stats.total_tool_calls == 4
    assert stats.repeated_tool_call_count == 1
    assert stats.consecutive_failure_count == 2


@pytest.mark.asyncio
async def test_guardrail_runtime_restores_persisted_stats_without_recounting_history() -> None:
    registry = MagicMock()
    registry.get.return_value = _EchoTool()
    registry.execute = AsyncMock(return_value=ToolExecutionResult(content="ok"))
    recorder = _Recorder()
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=MagicMock(),
        guardrail_policy=StaticAgentGuardrailPolicy(GuardrailPolicy(mode=GuardrailMode.OBSERVE)),
        run_guardrail_recorder=recorder,
    )
    context = ConversationContext()
    persisted_summary = {
        "runtime_stats": {
            "total_tokens": 100,
            "prompt_tokens": 70,
            "completion_tokens": 30,
            "elapsed_ms": 10.0,
            "context_growth_messages": 3,
            "repeated_tool_call_count": 1,
            "consecutive_failure_count": 0,
            "total_model_calls": 2,
            "total_tool_calls": 4,
            "estimated_cost": 0.01,
            "cost_available": True,
        }
    }
    token = set_run_execution_context(
        RunExecutionContext(
            run_id="run-1",
            owner_id="worker-1",
            segment_index=2,
            recovery_mode=True,
            guardrail_summary=persisted_summary,
        )
    )
    try:
        executable, approval = await adapter._prepare_tool_calls_for_execution(
            context=context,
            config=_config("echo_tool"),
            tool_calls=(ToolCallRequest("call-1", "echo_tool", '{"path":"new"}'),),
            round_num=3,
            model="test-model",
            usage_so_far={"total_tokens": 999},
        )
        assert approval is None
        await adapter._dispatch_concurrent_tool_calls(
            context,
            executable,
            _config("echo_tool"),
            session_id="session-1",
            round_num=3,
        )
    finally:
        reset_run_execution_context(token)

    assert recorder.calls[0].stats.total_tokens == 100
    assert recorder.calls[0].stats.total_tool_calls == 4
    assert recorder.calls[-1].stats.total_tokens == 100
    assert recorder.calls[-1].stats.total_tool_calls == 5
    assert recorder.calls[-1].stats.total_model_calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("decision_type", ["approve", "edit"])
async def test_approval_resume_tool_stats_continue_from_persisted_summary_once(
    decision_type: str,
) -> None:
    registry = MagicMock()
    registry.get.return_value = _EchoTool()
    registry.execute = AsyncMock(return_value=ToolExecutionResult(content="ok"))
    recorder = _Recorder()
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=FakeContextBuilder(),
        guardrail_policy=StaticAgentGuardrailPolicy(GuardrailPolicy(mode=GuardrailMode.OBSERVE)),
        run_guardrail_recorder=recorder,
    )
    context = ConversationContext()
    context.session_id = "session-1"
    context.add_system_message("sys")
    context.add_user_message("run approved tool")
    context._messages.append(
        AssistantMessage(
            content="",
            tool_calls=[ToolCallRequest("call-1", "echo_tool", '{"path":"a.txt"}')],
        )
    )
    persisted_summary = {
        "runtime_stats": {
            "total_tokens": 100,
            "repeated_tool_call_count": 1,
            "consecutive_failure_count": 0,
            "total_model_calls": 2,
            "total_tool_calls": 4,
        }
    }
    if decision_type == "approve":
        decisions = (ApprovalDecision("approve", "call-1"),)
    else:
        decisions = (
            ApprovalDecision(
                "edit",
                "call-1",
                edited_action=EditedAction("echo_tool", '{"path":"b.txt"}'),
            ),
        )
    token = set_run_execution_context(
        RunExecutionContext(
            run_id="run-1",
            owner_id="worker-1",
            segment_index=2,
            recovery_mode=True,
            guardrail_summary=persisted_summary,
        )
    )
    try:
        result = await adapter.resume(
            context,
            _config("echo_tool"),
            FakeModel([LLMResponse(content="done", model="test-model", usage={})]),  # type: ignore[arg-type]
            _interrupt_for_tool("echo_tool", '{"path":"a.txt"}'),
            decisions,
        )
    finally:
        reset_run_execution_context(token)

    assert result.status == "completed"
    registry.execute.assert_awaited_once()
    before_calls = [call for call in recorder.calls if call.stage.value == "tool_before_execution"]
    after_calls = [call for call in recorder.calls if call.stage.value == "tool_after_execution"]
    assert len(before_calls) == 1
    assert len(after_calls) == 1
    assert before_calls[0].stats.total_tool_calls == 4
    assert before_calls[0].stats.repeated_tool_call_count == 1
    assert after_calls[0].stats.total_tool_calls == 5
    assert after_calls[0].stats.repeated_tool_call_count == 1
    assert after_calls[0].stats.consecutive_failure_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("decision_type", ["approve", "edit"])
async def test_approval_resume_preserves_recovered_tool_stats_for_next_model_call(
    decision_type: str,
) -> None:
    registry = MagicMock()
    registry.get.return_value = _FailingEchoTool()
    registry.execute = AsyncMock(side_effect=RuntimeError("boom"))
    recorder = _Recorder()
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=FakeContextBuilder(),
        guardrail_policy=StaticAgentGuardrailPolicy(
            GuardrailPolicy(
                mode=GuardrailMode.OBSERVE,
                max_repeated_tool_calls=99,
                max_consecutive_failures=99,
            )
        ),
        run_guardrail_recorder=recorder,
    )
    context = ConversationContext()
    context.session_id = "session-1"
    context.add_system_message("sys")
    context.add_user_message("run approved tool then continue")
    context._messages.append(
        AssistantMessage(
            content="",
            tool_calls=[ToolCallRequest("call-1", "echo_tool", '{"path":"a.txt"}')],
        )
    )
    persisted_summary = {
        "runtime_stats": {
            "total_tokens": 100,
            "prompt_tokens": 70,
            "completion_tokens": 30,
            "elapsed_ms": 10.0,
            "context_growth_messages": 3,
            "repeated_tool_call_count": 1,
            "consecutive_failure_count": 2,
            "total_model_calls": 2,
            "total_tool_calls": 4,
        }
    }
    if decision_type == "approve":
        decisions = (ApprovalDecision("approve", "call-1"),)
    else:
        decisions = (
            ApprovalDecision(
                "edit",
                "call-1",
                edited_action=EditedAction("echo_tool", '{"path":"b.txt"}'),
            ),
        )
    token = set_run_execution_context(
        RunExecutionContext(
            run_id="run-1",
            owner_id="worker-1",
            segment_index=2,
            recovery_mode=True,
            guardrail_summary=persisted_summary,
        )
    )
    try:
        result = await adapter.resume(
            context,
            _config("echo_tool"),
            FakeModel(
                [
                    LLMResponse(
                        content="done",
                        model="test-model",
                        usage={
                            "prompt_tokens": 10,
                            "completion_tokens": 5,
                            "total_tokens": 15,
                        },
                    )
                ]
            ),  # type: ignore[arg-type]
            _interrupt_for_tool("echo_tool", '{"path":"a.txt"}'),
            decisions,
        )
    finally:
        reset_run_execution_context(token)

    assert result.status == "completed"
    before_calls = [call for call in recorder.calls if call.stage.value == "tool_before_execution"]
    after_calls = [call for call in recorder.calls if call.stage.value == "tool_after_execution"]
    model_calls = [call for call in recorder.calls if call.stage.value == "model_completed"]
    assert len(before_calls) == 1
    assert len(after_calls) == 1
    assert len(model_calls) == 1
    assert before_calls[0].stats.total_tool_calls == 4
    assert before_calls[0].stats.repeated_tool_call_count == 1
    assert before_calls[0].stats.consecutive_failure_count == 2
    assert after_calls[0].stats.total_tool_calls == 5
    assert after_calls[0].stats.repeated_tool_call_count == 1
    assert after_calls[0].stats.consecutive_failure_count == 3
    assert after_calls[0].stats.last_tool_error is True
    assert model_calls[0].stats.total_tool_calls == 5
    assert model_calls[0].stats.repeated_tool_call_count == 1
    assert model_calls[0].stats.consecutive_failure_count == 3
    assert model_calls[0].stats.total_model_calls == 3
    assert model_calls[0].stats.total_tokens == 115
    assert model_calls[0].stats.prompt_tokens == 80
    assert model_calls[0].stats.completion_tokens == 35


@pytest.mark.asyncio
async def test_runtime_stats_payload_and_merged_summary_share_same_cost_snapshot() -> None:
    registry = MagicMock()
    registry.get.return_value = _EchoTool()
    registry.execute = AsyncMock(return_value=ToolExecutionResult(content="ok"))
    recorder = _Recorder()
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=FakeContextBuilder(),
        guardrail_policy=StaticAgentGuardrailPolicy(
            GuardrailPolicy(
                mode=GuardrailMode.OBSERVE,
                model_pricing={"test-model": GuardrailModelPricing(total_per_1m=2.0)},
                max_repeated_tool_calls=2,
            )
        ),
        run_guardrail_recorder=recorder,
    )
    context = ConversationContext()
    context.add_user_message("repeat tool")
    token = set_run_execution_context(
        RunExecutionContext(run_id="run-1", owner_id="worker-1", segment_index=1)
    )
    try:
        result = await adapter.run(
            context,
            _config("echo_tool"),
            FakeModel(
                [
                    LLMResponse(
                        content="",
                        model="test-model",
                        usage={"prompt_tokens": 1000, "completion_tokens": 500},
                        tool_calls=[
                            ToolCallRequest("call-1", "echo_tool", '{"path":"a"}'),
                            ToolCallRequest("call-2", "echo_tool", '{"path":"a"}'),
                        ],
                    ),
                    LLMResponse(
                        content="done",
                        model="test-model",
                        usage={"prompt_tokens": 200, "completion_tokens": 100},
                    ),
                ]
            ),
        )
    finally:
        reset_run_execution_context(token)

    assert result.status == "completed"
    model_call = recorder.calls[0]
    assert model_call.stage.value == "model_completed"
    event_payload = guardrail_observation_to_event_payload(model_call)
    summary = guardrail_summary_to_dict(
        merge_guardrail_summary(None, model_call, event_cursor=1)
    )
    assert event_payload["stats"] == summary["runtime_stats"]
    assert event_payload["stats"]["total_tokens"] == 1500
    assert event_payload["stats"]["total_model_calls"] == 1
    assert event_payload["stats"]["estimated_cost"] == 0.003
    assert event_payload["stats"]["cost_available"] is True

    after_calls = [call for call in recorder.calls if call.stage.value == "tool_after_execution"]
    assert after_calls[-1].stats.total_tool_calls == 2
    assert after_calls[-1].stats.repeated_tool_call_count == 1
    final_model = [call for call in recorder.calls if call.stage.value == "model_completed"][-1]
    assert final_model.stats.total_tokens == 1800
    assert final_model.stats.total_model_calls == 2
    assert final_model.stats.total_tool_calls == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mode", "tool", "expected_action", "expected_status"),
    [
        (GuardrailMode.OBSERVE, _CriticalTool(), "observe", "completed"),
        (GuardrailMode.ENFORCE, _HighRiskTool(), "require_approval", "approval_required"),
        (GuardrailMode.ENFORCE, _CriticalTool(), "stop", "completed"),
    ],
)
async def test_missing_pricing_does_not_change_guardrail_action_semantics(
    mode: GuardrailMode,
    tool: Tool,
    expected_action: str,
    expected_status: str,
) -> None:
    registry = MagicMock()
    registry.get.return_value = tool
    registry.execute = AsyncMock(return_value=ToolExecutionResult(content="ok"))
    approval_store = AsyncMock()
    recorder = _Recorder()
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=FakeContextBuilder(),
        approval_store=approval_store,
        guardrail_policy=StaticAgentGuardrailPolicy(
            GuardrailPolicy(
                mode=mode,
                enforce_high_risk_tools=True,
                model_pricing={},
            )
        ),
        run_guardrail_recorder=recorder,
    )
    context = ConversationContext()
    context.session_id = "session-1"
    context.add_user_message("use risky tool")
    token = set_run_execution_context(
        RunExecutionContext(run_id="run-1", owner_id="worker-1", segment_index=1)
    )
    try:
        result = await adapter.run(
            context,
            _config(tool.name),
            FakeModel(
                [
                    LLMResponse(
                        content="",
                        model="unpriced-model",
                        usage={"prompt_tokens": 10, "completion_tokens": 5},
                        tool_calls=[ToolCallRequest("call-1", tool.name, "{}")],
                    )
                ]
            ),
        )
    finally:
        reset_run_execution_context(token)

    assert result.status == expected_status
    assert recorder.calls[0].stage.value == "model_completed"
    assert recorder.calls[0].decision.action.value == "allow"
    assert recorder.calls[0].stats.cost_available is False
    assert recorder.calls[0].stats.estimated_cost is None
    tool_before = next(
        call for call in recorder.calls if call.stage.value == "tool_before_execution"
    )
    assert tool_before.decision.action.value == expected_action
    if expected_action == "require_approval":
        approval_store.save.assert_awaited_once()
        registry.execute.assert_not_awaited()
    elif expected_action == "stop":
        registry.execute.assert_not_awaited()
    else:
        registry.execute.assert_awaited_once()


def _completed_ledger(*, result: str, is_error: bool) -> ToolResultLedgerEntry:
    """构造已完成的 checkpoint 工具账本记录。"""

    now = datetime.now(UTC)
    return ToolResultLedgerEntry(
        run_id="run-1",
        tool_execution_key="tool-key-1",
        status=ToolLedgerStatus.COMPLETED,
        tool_name="echo_tool",
        tool_call_id="call-1",
        arguments_digest="digest",
        replay_policy=ToolReplayPolicy.REPLAY_RESULT,
        side_effect_level=ToolSideEffectLevel.NONE,
        idempotency_key="idem",
        result=result,
        is_error=is_error,
        metadata={},
        created_at=now,
        updated_at=now,
    )
