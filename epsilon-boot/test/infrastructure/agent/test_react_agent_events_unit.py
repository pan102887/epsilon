"""Structured event tests for ReActAgentAdapter."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import pytest

from domain.agent.ports import ApprovalPolicyPort, ApprovalStateStorePort
from domain.agent.tools import ToolExecutionResult, ToolRegistry
from domain.agent.value_objects import (
    AgentConfig,
    AgentStreamEvent,
    ApprovalInterrupt,
    ApprovalInterruptSummary,
    ApprovalPolicy,
)
from domain.chat.context import BaseMessage, ConversationContext, UserMessage
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
    ToolCallRequest,
)
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from infrastructure.chat.environment_context_provider import EnvironmentContextBuildError


class FakeContextBuilder:
    """按顺序返回上下文构建结果的测试 fake。"""

    def __init__(
        self,
        results: list[ContextBuilderResult] | None = None,
        error: Exception | None = None,
    ) -> None:
        self.results = results or [
            ContextBuilderResult(
                messages=[UserMessage(content="builder round 1")],
                environment_injected=True,
            )
        ]
        self.error = error
        self.calls = 0

    async def build(
        self,
        messages: list[BaseMessage],
        *,
        model_access: ModelAccessPort | None = None,
        model: str | None = None,
    ) -> ContextBuilderResult:
        del messages, model_access, model
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.results.pop(0)


class StaticPolicy(ApprovalPolicyPort):
    """测试用静态审批策略。"""

    def __init__(self, policies: dict[str, ApprovalPolicy]) -> None:
        self._policies = policies

    def policy_for(self, tool_name: str) -> ApprovalPolicy:
        return self._policies.get(tool_name, ApprovalPolicy(tool_name, False, frozenset()))


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
        del session_id
        return []


class FakeToolRegistry:
    async def execute(self, request: ToolCallRequest) -> ToolExecutionResult:
        self.last_request = request
        return ToolExecutionResult(content="tool ok")


class FakeModelAccess:
    def __init__(self) -> None:
        self.chat_calls = 0
        self.stream_calls = 0
        self._responses = [
            LLMResponse(
                content="",
                model="test-model",
                usage={"prompt_tokens": 11, "completion_tokens": 4},
                tool_calls=[
                    ToolCallRequest(
                        id="call-1",
                        name="lookup",
                        arguments='{"query": "hello"}',
                    )
                ],
            ),
            LLMResponse(
                content="final answer",
                model="test-model",
                usage={"total_tokens": 7},
            ),
        ]

    async def chat(self, request: ChatRequest) -> LLMResponse:
        # v3 ReAct 不再调用 chat()，保留方法以兼容 ChatServiceAdapter 等非 ReAct 场景。
        self.chat_calls += 1
        self.last_request = request
        return self._responses.pop(0)

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        # v3：ReAct 内部全程 stream + 内部累积。每次调用消费一个 LLMResponse
        # 并按等价分片产出（NFR-3）。
        from test.infrastructure.agent._v3_stream_helpers import response_to_chunks

        self.stream_calls += 1
        self.last_request = request
        if not self._responses:
            yield StreamingChunk(delta_content="", finished=True, usage={})
            return
        response = self._responses.pop(0)
        for chunk in response_to_chunks(response):
            yield chunk


def _tool_registry(fake: FakeToolRegistry) -> ToolRegistry:
    return cast(ToolRegistry, fake)


def _model_access(fake: FakeModelAccess) -> ModelAccessPort:
    return cast(ModelAccessPort, fake)


async def test_react_agent_run_events_emits_tool_and_assistant_events() -> None:
    builder = FakeContextBuilder(
        [
            ContextBuilderResult(
                messages=[UserMessage(content="builder round 1")],
                usage={"prompt_tokens": 3},
                environment_injected=True,
            ),
            ContextBuilderResult(
                messages=[UserMessage(content="builder round 2")],
                usage={"prompt_tokens": 5, "total_tokens": 2},
                environment_injected=True,
            ),
        ]
    )
    adapter = ReActAgentAdapter(
        tool_registry=_tool_registry(FakeToolRegistry()),
        context_builder=builder,
    )
    context = ConversationContext()
    context.add_user_message("hello")
    config = AgentConfig(
        system_prompt="system",
        tool_schemas=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Lookup",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        model="test-model",
        max_rounds=3,
        prompt_id="chat-default@v1",
    )

    events = [
        event
        async for event in adapter.run_events(
            context,
            config,
            _model_access(FakeModelAccess()),
        )
    ]

    assert [event.kind for event in events] == [
        "status",
        "tool_start",
        "tool_result",
        "status",
        "assistant_delta",
        "assistant_done",
    ]
    assert events[1].tool_name == "lookup"
    assert events[1].arguments == '{"query": "hello"}'
    assert events[2].content == "tool ok"
    assert events[4].content == "final answer"
    assert events[5].usage == {
        "prompt_tokens": 19,
        "completion_tokens": 4,
        "total_tokens": 9,
    }
    assert builder.calls == 2


async def test_react_agent_run_events_emits_approval_required_shape() -> None:
    """验证审批事件结构保持 approval_required 事件载荷。"""
    store = MemoryApprovalStore()
    adapter = ReActAgentAdapter(
        tool_registry=FakeToolRegistry(),  # type: ignore[arg-type]
        context_builder=FakeContextBuilder(
            [
                ContextBuilderResult(
                    messages=[UserMessage(content="builder")],
                    usage={"total_tokens": 2},
                    environment_injected=True,
                )
            ]
        ),  # type: ignore[arg-type]
        approval_policy=StaticPolicy(
            {
                "lookup": ApprovalPolicy(
                    "lookup",
                    True,
                    frozenset({"approve", "reject"}),
                    "lookup requires approval",
                )
            }
        ),
        approval_store=store,
    )
    context = ConversationContext()
    context.add_user_message("hello")
    config = AgentConfig(
        system_prompt="system",
        tool_schemas=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Lookup",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        model="test-model",
        max_rounds=3,
        prompt_id="chat-default@v1",
    )
    model_access = FakeModelAccess()

    events = [
        event
        async for event in adapter.run_events(
            context,
            config,
            model_access,  # type: ignore[arg-type]
        )
    ]

    assert [event.kind for event in events] == ["status", "approval_required"]
    approval_event = events[-1]
    assert store.saved is not None
    assert approval_event.content == "当前请求等待人工审批，请通过审批恢复接口提交决策。"
    assert approval_event.metadata["round"] == 1
    assert approval_event.metadata["session_id"] == ""
    assert approval_event.metadata["approval_id"] == store.saved.approval_id
    assert approval_event.metadata["action_count"] == 1
    assert approval_event.metadata["action_summaries"] == [
        {
            "tool_call_id": "call-1",
            "tool_name": "lookup",
            "allowed_decisions": ["approve", "reject"],
            "reason": "lookup requires approval",
        }
    ]
    assert "actions" not in approval_event.metadata
    assert approval_event.usage == {
        "prompt_tokens": 11,
        "completion_tokens": 4,
        "total_tokens": 2,
    }
    # v3：ReAct 内部全程 stream，approval 触发于第 1 轮 → 1 次 stream，0 次 chat。
    assert model_access.chat_calls == 0
    assert model_access.stream_calls == 1


async def test_react_agent_run_events_builder_failure_skips_model_calls() -> None:
    """验证 builder 失败时 run_events 不执行主模型 chat/stream。"""
    adapter = ReActAgentAdapter(
        tool_registry=FakeToolRegistry(),  # type: ignore[arg-type]
        context_builder=FakeContextBuilder(
            error=EnvironmentContextBuildError("环境上下文生成失败")
        ),  # type: ignore[arg-type]
    )
    context = ConversationContext()
    context.add_user_message("hello")
    config = AgentConfig(
        system_prompt="system",
        tool_schemas=[],
        model="test-model",
        max_rounds=3,
        prompt_id="chat-default@v1",
    )
    model_access = FakeModelAccess()
    events: list[AgentStreamEvent] = []

    with pytest.raises(EnvironmentContextBuildError):
        async for event in adapter.run_events(
            context,
            config,
            model_access,  # type: ignore[arg-type]
        ):
            events.append(event)

    assert [event.kind for event in events] == ["status"]
    assert model_access.chat_calls == 0
    assert model_access.stream_calls == 0


# ── 事件 kind 集合断言 ──


_ALLOWED_EVENT_KINDS = frozenset(
    {
        "status",
        "assistant_delta",
        "assistant_done",
        "tool_start",
        "tool_result",
        "tool_error",
        "approval_required",
        "error",
    }
)
"""run_events 允许产出的 AgentStreamEvent.kind 完整集合。

重构后不应引入新的事件类型（需求 1.11 / NFR.6）。
"""


async def test_run_events_all_kinds_within_allowed_set() -> None:
    """验证 run_events 产出的所有事件 kind 属于允许集合。

    构造包含工具调用的多轮场景，收集所有产出事件的 kind，
    断言其为 _ALLOWED_EVENT_KINDS 的子集。确保重构后不会
    引入新的事件类型（如 heartbeat / tool_progress 等仅属于
    StreamingChunk 形态的分片不应出现在 run_events 中）。

    **Validates: Requirement 1.11, NFR.6**
    """
    builder = FakeContextBuilder(
        [
            ContextBuilderResult(
                messages=[UserMessage(content="builder round 1")],
                usage={"prompt_tokens": 3},
                environment_injected=True,
            ),
            ContextBuilderResult(
                messages=[UserMessage(content="builder round 2")],
                usage={"prompt_tokens": 5},
                environment_injected=True,
            ),
        ]
    )
    adapter = ReActAgentAdapter(
        tool_registry=FakeToolRegistry(),  # type: ignore[arg-type]
        context_builder=builder,  # type: ignore[arg-type]
    )
    context = ConversationContext()
    context.add_user_message("hello")
    config = AgentConfig(
        system_prompt="system",
        tool_schemas=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Lookup",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        model="test-model",
        max_rounds=3,
        prompt_id="chat-default@v1",
    )

    events = [
        event
        async for event in adapter.run_events(
            context,
            config,
            FakeModelAccess(),  # type: ignore[arg-type]
        )
    ]

    actual_kinds = {event.kind for event in events}
    # 所有产出的 kind 必须在允许集合内
    unexpected = actual_kinds - _ALLOWED_EVENT_KINDS
    assert not unexpected, (
        f"run_events 产出了不在允许集合中的事件 kind: {unexpected}\n"
        f"允许: {sorted(_ALLOWED_EVENT_KINDS)}\n"
        f"实际: {sorted(actual_kinds)}"
    )
    # 至少有事件产出（非空）
    assert len(events) > 0, "run_events 应至少产出 1 个事件"


async def test_run_events_approval_kinds_within_allowed_set() -> None:
    """验证审批场景下 run_events 产出的事件 kind 也属于允许集合。

    **Validates: Requirement 1.11, NFR.6**
    """
    store = MemoryApprovalStore()
    adapter = ReActAgentAdapter(
        tool_registry=FakeToolRegistry(),  # type: ignore[arg-type]
        context_builder=FakeContextBuilder(
            [
                ContextBuilderResult(
                    messages=[UserMessage(content="builder")],
                    usage={"total_tokens": 2},
                    environment_injected=True,
                )
            ]
        ),  # type: ignore[arg-type]
        approval_policy=StaticPolicy(
            {
                "lookup": ApprovalPolicy(
                    "lookup",
                    True,
                    frozenset({"approve", "reject"}),
                    "lookup requires approval",
                )
            }
        ),
        approval_store=store,
    )
    context = ConversationContext()
    context.add_user_message("hello")
    config = AgentConfig(
        system_prompt="system",
        tool_schemas=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Lookup",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        model="test-model",
        max_rounds=3,
        prompt_id="chat-default@v1",
    )

    events = [
        event
        async for event in adapter.run_events(
            context,
            config,
            FakeModelAccess(),  # type: ignore[arg-type]
        )
    ]

    actual_kinds = {event.kind for event in events}
    unexpected = actual_kinds - _ALLOWED_EVENT_KINDS
    assert not unexpected, (
        f"审批场景 run_events 产出了不在允许集合中的事件 kind: {unexpected}\n"
        f"允许: {sorted(_ALLOWED_EVENT_KINDS)}\n"
        f"实际: {sorted(actual_kinds)}"
    )
