"""ReActAgentAdapter HITL 中断与恢复单元测试模块。"""

from collections.abc import AsyncIterator

import pytest

from domain.agent.ports import ApprovalPolicyPort, ApprovalStateStorePort
from domain.agent.tools import Tool, ToolExecutionResult, ToolRegistry
from domain.agent.value_objects import (
    AgentConfig,
    ApprovalDecision,
    ApprovalInterrupt,
    ApprovalInterruptSummary,
    ApprovalPolicy,
    PendingActionRequest,
)
from domain.chat.context import AssistantMessage, BaseMessage, ConversationContext, ToolMessage
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
    ToolCallRequest,
)
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter


class FakeContextBuilder:
    """测试用上下文构建器。"""

    async def build(
        self,
        messages: list[BaseMessage],
        **kwargs: object,
    ) -> ContextBuilderResult:
        """原样透传领域消息列表。"""
        return ContextBuilderResult(
            messages=messages,
            usage={},
        )


class StaticPolicy(ApprovalPolicyPort):
    """测试用静态审批策略。"""

    def __init__(self, policies: dict[str, ApprovalPolicy]) -> None:
        self._policies = policies

    def policy_for(self, tool_name: str) -> ApprovalPolicy:
        return self._policies.get(
            tool_name,
            ApprovalPolicy(tool_name, False, frozenset()),
        )


class MemoryApprovalStore(ApprovalStateStorePort):
    """测试用内存审批状态存储。"""

    def __init__(self) -> None:
        self.saved: ApprovalInterrupt | None = None

    async def save(self, interrupt: ApprovalInterrupt) -> None:
        self.saved = interrupt

    async def load(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        return self.saved

    async def consume(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        interrupt = self.saved
        self.saved = None
        return interrupt

    async def delete(self, session_id: str, approval_id: str) -> None:
        self.saved = None

    async def delete_session(self, session_id: str) -> None:
        self.saved = None

    async def list_pending_by_session(
        self, session_id: str
    ) -> list[ApprovalInterruptSummary]:
        return []


class RecordingTool(Tool):
    """记录执行请求的测试工具。"""

    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    @property
    def name(self) -> str:
        return "write_file"

    @property
    def description(self) -> str:
        return "write file"

    @property
    def parameters(self) -> dict[str, object]:
        return {
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        }

    async def execute(self, **kwargs: object) -> ToolExecutionResult:
        self.requests.append(kwargs)
        return ToolExecutionResult(content="written")


class FakeModel:
    """v3：``stream`` 顺序消费 ``responses`` 队列并按等价分片产出。"""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self.responses = responses

    async def chat(self, request: ChatRequest) -> LLMResponse:
        return self.responses.pop(0)

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        from test.infrastructure.agent._v3_stream_helpers import response_to_chunks

        if self.responses:
            response = self.responses.pop(0)
            for chunk in response_to_chunks(response):
                yield chunk
            return
        yield StreamingChunk(delta_content="done", finished=True)

    def count_tokens(self, messages: list[BaseMessage]) -> int:
        return sum(len(message.content) for message in messages)


def _config() -> AgentConfig:
    return AgentConfig(
        system_prompt="system",
        tool_schemas=[{"type": "function", "function": {"name": "write_file"}}],
        model="gpt-test",
        max_rounds=4,
        prompt_id="chat-default@v1",
    )


def _adapter(store: MemoryApprovalStore, tool: RecordingTool | None = None) -> ReActAgentAdapter:
    registry = ToolRegistry()
    registry.register(tool or RecordingTool())
    return ReActAgentAdapter(
        tool_registry=registry,
        context_builder=FakeContextBuilder(),  # type: ignore[arg-type]
        approval_policy=StaticPolicy(
            {
                "write_file": ApprovalPolicy(
                    "write_file",
                    True,
                    frozenset({"approve", "edit", "reject"}),
                    "高风险写入",
                )
            }
        ),
        approval_store=store,
    )


async def test_hitl_interrupt_saves_state_and_does_not_execute_tool() -> None:
    """验证敏感工具触发审批中断且工具未执行。"""
    store = MemoryApprovalStore()
    tool = RecordingTool()
    adapter = _adapter(store, tool)
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

    result = await adapter.run(context, _config(), model)  # type: ignore[arg-type]

    assert result.status == "approval_required"
    assert result.approval is not None
    assert result.approval.actions[0].tool_call_id == "call-1"
    assert store.saved is not None
    assert tool.requests == []
    messages = context.get_messages()
    # UserMessage + SystemMessage（幂等注入） + AssistantMessage = 3
    assert len(messages) == 3
    assert messages[-1].role == "assistant"


async def test_hitl_resume_approve_executes_tool_and_continues() -> None:
    """验证 approve 执行原工具并继续获得最终回复。"""
    store = MemoryApprovalStore()
    tool = RecordingTool()
    adapter = _adapter(store, tool)
    context = ConversationContext()
    context.add_system_message("system")
    context.add_user_message("write")
    context.append_message(
        AssistantMessage(
            content="",
            tool_calls=[ToolCallRequest("call-1", "write_file", '{"path":"a.txt"}')],
        )
    )
    interrupt = ApprovalInterrupt(
        session_id="s1",
        approval_id="a1",
        actions=store.saved.actions
        if store.saved
        else (
            PendingActionRequest(
                "call-1",
                "write_file",
                '{"path":"a.txt"}',
                frozenset({"approve", "edit", "reject"}),
            ),
        ),
        context_snapshot=context.to_dict(),
        round_num=1,
        model="gpt-test",
        usage_so_far={"total_tokens": 2},
    )
    model = FakeModel([LLMResponse(content="done", model="gpt-test", usage={"total_tokens": 3})])

    result = await adapter.resume(
        context,
        _config(),
        model,  # type: ignore[arg-type]
        interrupt,
        (ApprovalDecision("approve", "call-1"),),
    )

    assert result.status == "completed"
    assert result.content == "done"
    assert result.usage == {"total_tokens": 5}
    assert tool.requests == [{"path": "a.txt"}]
    assert isinstance(context.get_messages()[-1], ToolMessage)


async def test_hitl_resume_reject_adds_tool_message_without_execution() -> None:
    """验证 reject 不执行工具而写入 ToolMessage。"""
    store = MemoryApprovalStore()
    tool = RecordingTool()
    adapter = _adapter(store, tool)
    context = ConversationContext()
    context.add_system_message("system")
    context.add_user_message("write")
    context.append_message(
        AssistantMessage(
            content="",
            tool_calls=[ToolCallRequest("call-1", "write_file", '{"path":"a.txt"}')],
        )
    )
    interrupt = ApprovalInterrupt(
        "s1",
        "a1",
        (
            PendingActionRequest(
                "call-1",
                "write_file",
                '{"path":"a.txt"}',
                frozenset({"approve", "edit", "reject"}),
            ),
        ),
        context.to_dict(),
        1,
        "gpt-test",
    )
    model = FakeModel([LLMResponse(content="done", model="gpt-test")])

    await adapter.resume(
        context,
        _config(),
        model,  # type: ignore[arg-type]
        interrupt,
        (ApprovalDecision("reject", "call-1", message="不要写"),),
    )

    assert tool.requests == []
    assert context.get_messages()[-1].content == "不要写"


async def test_hitl_streaming_and_events_emit_approval_required() -> None:
    """验证 streaming/events 命中审批时输出 approval_required。"""
    store = MemoryApprovalStore()
    adapter = _adapter(store)
    context = ConversationContext()
    context.add_user_message("write")
    response = LLMResponse(
        content="",
        model="gpt-test",
        tool_calls=[ToolCallRequest("call-1", "write_file", '{"path":"a.txt"}')],
    )

    chunks = [
        chunk
        async for chunk in adapter.run_streaming(
            context,
            _config(),
            FakeModel([response]),  # type: ignore[arg-type]
        )
    ]
    assert chunks[-1].metadata["status"] == "approval_required"

    context2 = ConversationContext()
    context2.add_user_message("write")
    events = [
        event
        async for event in adapter.run_events(
            context2,
            _config(),
            FakeModel([response]),  # type: ignore[arg-type]
        )
    ]
    assert events[-1].kind == "approval_required"


async def test_hitl_respond_decision_is_rejected_after_branch_removal() -> None:
    """验证 respond 死分支删除后，respond 决策被领域校验拒绝。

    需求 9.5：在删除 ``_apply_approval_decisions`` 中 ``decision.type ==
    "respond"`` 死分支后，构造一个 ``decision.type == "respond"`` 的恢复
    请求，应在 ``allowed_decisions`` 校验阶段抛出
    ``ApprovalDecisionNotAllowedError``（错误码 60025），而**不是**走原死
    分支的 ``ApprovalRespondNotAllowedError``。

    使用 ``cast(Any, ...)`` 绕过 ``ApprovalDecisionType`` 收窄后的类型校验，
    模拟"曾经有 respond 决策"的非法历史输入。
    """
    from typing import Any, cast

    from domain.agent.exceptions import ApprovalDecisionNotAllowedError

    store = MemoryApprovalStore()
    tool = RecordingTool()
    adapter = _adapter(store, tool)
    context = ConversationContext()
    context.add_user_message("write")
    context.append_message(
        AssistantMessage(
            content="",
            tool_calls=[ToolCallRequest("call-1", "write_file", '{"path":"a.txt"}')],
        )
    )
    interrupt = ApprovalInterrupt(
        session_id="s1",
        approval_id="a1",
        actions=(
            PendingActionRequest(
                "call-1",
                "write_file",
                '{"path":"a.txt"}',
                # 关键：allowed_decisions 不含 "respond"
                frozenset({"approve", "edit", "reject"}),
            ),
        ),
        context_snapshot=context.to_dict(),
        round_num=1,
        model="gpt-test",
        usage_so_far={"total_tokens": 2},
    )
    model = FakeModel([LLMResponse(content="done", model="gpt-test")])

    # 通过 cast 绕过 Literal 收窄，构造历史可能存在的 respond 决策。
    respond_decision = ApprovalDecision(
        type=cast(Any, "respond"),
        tool_call_id="call-1",
        message="人工回复内容",
    )

    with pytest.raises(ApprovalDecisionNotAllowedError) as exc_info:
        await adapter.resume(
            context,
            _config(),
            model,  # type: ignore[arg-type]
            interrupt,
            (respond_decision,),
        )

    # 错误码 60025 即 ApprovalDecisionNotAllowedError
    assert exc_info.value.code == 60025
    assert exc_info.value.decision_type == "respond"
    assert tool.requests == []  # respond 决策被拒绝，工具不应执行


def test_approval_payload_to_metadata_is_json_serializable() -> None:
    """验证通用审批元数据可被标准 json.dumps 直接序列化且不含完整参数。"""
    import json

    from domain.agent.value_objects import ApprovalRequiredPayload
    from infrastructure.agent.approval_serialization import (
        approval_payload_to_metadata,
    )

    actions = (
        PendingActionRequest(
            tool_call_id="call-1",
            tool_name="write_file",
            arguments='{"path":"a.txt"}',
            allowed_decisions=frozenset({"approve", "reject"}),
            reason="高风险写入",
        ),
        PendingActionRequest(
            tool_call_id="call-2",
            tool_name="write_file",
            arguments='{"path":"b.txt"}',
            allowed_decisions=frozenset({"approve", "edit", "reject"}),
            reason="",
        ),
    )
    payload = ApprovalRequiredPayload(
        session_id="session-1",
        approval_id="approval-1",
        actions=actions,
        prompt_id="chat-default@v1",
        metadata={
            "source": "guardrail",
            "guardrail_reason": "tool_risk_gate_required",
            "risk_gate_required": True,
            "unexpected_secret": '{"path":"/tmp/a.txt"}',
        },
    )

    metadata = approval_payload_to_metadata(payload)

    # JSON 安全性：标准 json.dumps（不传入 default）应直接成功
    serialized = json.dumps(metadata)
    assert isinstance(serialized, str)

    # 顶层关键字段
    assert metadata["status"] == "approval_required"
    assert metadata["session_id"] == "session-1"
    assert metadata["approval_id"] == "approval-1"
    assert metadata["action_count"] == 2
    assert metadata["source"] == "guardrail"
    assert metadata["guardrail_reason"] == "tool_risk_gate_required"
    assert metadata["risk_gate_required"] is True

    # 通用元数据只允许安全摘要，不暴露完整参数
    assert "actions" not in metadata
    assert "unexpected_secret" not in metadata
    for action_summary in metadata["action_summaries"]:
        assert isinstance(action_summary["allowed_decisions"], list)
        assert action_summary["allowed_decisions"] == sorted(action_summary["allowed_decisions"])
        assert "arguments" not in action_summary


def test_approval_payload_metadata_redacts_arguments_but_storage_keeps_full_actions() -> None:
    """验证通用审批元数据脱敏，而受控存储仍保留完整动作参数。"""
    from domain.agent.value_objects import ApprovalRequiredPayload
    from infrastructure.agent.approval_serialization import (
        approval_payload_to_metadata,
    )
    from infrastructure.agent.approval_state_store import (
        approval_interrupt_to_dict,
    )

    actions = (
        PendingActionRequest(
            tool_call_id="call-x",
            tool_name="write_file",
            arguments='{"path":"x"}',
            allowed_decisions=frozenset({"approve", "reject"}),
            reason="reason-x",
        ),
    )
    payload = ApprovalRequiredPayload(
        session_id="s",
        approval_id="a",
        actions=actions,
        prompt_id="chat-default@v1",
    )
    interrupt = ApprovalInterrupt(
        session_id="s",
        approval_id="a",
        actions=actions,
        context_snapshot={},
        round_num=1,
        model="gpt-test",
    )

    metadata = approval_payload_to_metadata(payload)
    metadata_summaries = metadata["action_summaries"]
    store_actions = approval_interrupt_to_dict(interrupt)["actions"]

    assert metadata["action_count"] == 1
    assert "actions" not in metadata
    assert metadata_summaries == [
        {
            "tool_call_id": "call-x",
            "tool_name": "write_file",
            "allowed_decisions": ["approve", "reject"],
            "reason": "reason-x",
        }
    ]
    assert "arguments" not in metadata_summaries[0]

    # 受控审批存储/展示面仍保留完整参数
    assert store_actions == [
        {
            "tool_call_id": "call-x",
            "tool_name": "write_file",
            "arguments": '{"path":"x"}',
            "allowed_decisions": ["approve", "reject"],
            "reason": "reason-x",
        }
    ]


# Public test builders reused by workflow regression coverage.
hitl_adapter = _adapter
hitl_config = _config
