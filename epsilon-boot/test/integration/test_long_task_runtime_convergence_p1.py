"""长任务运行时 P1 统计与价格收敛集成测试。

本模块组合本地文件 Run store、RunGuardrailRecorder 与 ReActAgentAdapter，
验证 token、耗时、上下文增长、重复工具调用、连续失败和成本估算都写入
同一条 Run 事件与 ``RunSnapshot.guardrail_summary``，并验证恢复基线不会
重复累计历史统计。测试不启动 HTTP、Redis、数据库或后台常驻 worker。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from application.run.run_guardrail_recorder import RunGuardrailRecorder
from domain.agent.guardrails import (
    GuardrailMode,
    GuardrailModelPricing,
    GuardrailPolicy,
    ToolRiskLevel,
)
from domain.agent.tools import Tool, ToolRegistry
from domain.agent.value_objects import AgentConfig
from domain.chat.context import ConversationContext
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
    ToolCallRequest,
)
from domain.run.runtime_context import (
    RunExecutionContext,
    reset_run_execution_context,
    set_run_execution_context,
)
from domain.run.value_objects import RunCreateRequest, RunEventType, RunKind, RunPayload
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from infrastructure.agent.static_guardrail_policy import StaticAgentGuardrailPolicy
from infrastructure.persistence.local_file.atomic_writer import TempFileAtomicWriter
from infrastructure.persistence.local_file.file_lock import LockFactory
from infrastructure.persistence.local_file.path_policy import CrossPlatformPathPolicy
from infrastructure.run.local_file_run_store_adapter import LocalFileRunStoreAdapter
from infrastructure.run.run_serialization_adapters import GuardrailSerializerAdapter
from test.infrastructure.agent._v3_stream_helpers import response_to_chunks

pytestmark = pytest.mark.asyncio


class _FailingTool(Tool):
    """始终失败的低风险测试工具，用于累计连续失败统计。"""

    @property
    def name(self) -> str:
        """返回工具名称。"""

        return "echo_tool"

    @property
    def description(self) -> str:
        """返回工具描述。"""

        return "failing echo"

    @property
    def parameters(self) -> dict[str, Any]:
        """返回工具参数 schema。"""

        return {"type": "object", "properties": {"path": {"type": "string"}}}

    @property
    def risk_level(self) -> ToolRiskLevel:
        """返回低风险，避免风险门禁掩盖预算统计。"""

        return ToolRiskLevel.LOW

    async def execute(self, **kwargs: Any) -> str:
        """抛出固定异常以模拟真实工具失败。"""

        raise RuntimeError("boom")


class _ContextBuilder:
    """原样透传上下文消息的测试构建器。"""

    async def build(self, messages: list[Any], **kwargs: Any) -> ContextBuilderResult:
        """构造 ContextBuilderResult。"""

        return ContextBuilderResult(messages=list(messages), usage={})


class _QueueModel:
    """按队列返回流式响应的模型 fake。"""

    def __init__(self, responses: list[LLMResponse]) -> None:
        """保存响应队列。"""

        self.responses = list(responses)

    async def chat(self, request: ChatRequest) -> LLMResponse:
        """返回下一条非流式响应。"""

        return self.responses.pop(0)

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        """按下一条响应产出等价流式分片。"""

        response = self.responses.pop(0)
        for chunk in response_to_chunks(response):
            yield chunk


def _store(root: Path) -> LocalFileRunStoreAdapter:
    """构造本地文件 Run store。"""

    return LocalFileRunStoreAdapter(
        root=root,
        lock_factory=LockFactory(acquire_timeout_ms=1000),
        path_policy=CrossPlatformPathPolicy(),
        atomic_writer=TempFileAtomicWriter(fsync_on_write=False),
    )


def _registry(tool: Tool) -> ToolRegistry:
    """构造只包含一个工具的注册表。"""

    registry = ToolRegistry()
    registry.register(tool)
    return registry


def _config() -> AgentConfig:
    """构造允许 echo_tool 的 ReAct 配置。"""

    return AgentConfig(
        system_prompt="sys",
        tool_schemas=[{"type": "function", "function": {"name": "echo_tool", "parameters": {}}}],
        model="priced-model",
        max_rounds=2,
        prompt_id="chat-default@v1",
    )


def _run_request(
    *,
    client_request_id: str = "client-p1",
    guardrail_summary: dict[str, Any] | None = None,
) -> RunCreateRequest:
    """构造测试 Run 创建请求。"""

    payload = RunPayload(
        kind=RunKind.CHAT,
        session_id="session-p1",
        chat={"session_id": "session-p1", "message": "repeat failing tool"},
        model="priced-model",
    )
    return RunCreateRequest(
        payload=payload,
        client_request_id=client_request_id,
        guardrail_summary=guardrail_summary,
    )


async def test_p1_runtime_stats_and_cost_are_persisted_in_event_and_summary(
    tmp_path: Path,
) -> None:
    """真实 recorder 写入的事件 stats 与 summary runtime_stats 必须保持一致。"""

    store = _store(tmp_path)
    created = await store.create_run(_run_request())
    claimed = await store.claim_next(owner_id="worker-p1", lease_seconds=60)
    assert claimed is not None
    recorder = RunGuardrailRecorder(
        run_store=store,
        observation_store=store,
        guardrail_serializer=GuardrailSerializerAdapter(),
    )
    registry = _registry(_FailingTool())
    adapter = ReActAgentAdapter(
        tool_registry=registry,
        context_builder=_ContextBuilder(),
        guardrail_policy=StaticAgentGuardrailPolicy(
            GuardrailPolicy(
                mode=GuardrailMode.OBSERVE,
                max_repeated_tool_calls=2,
                max_consecutive_failures=2,
                model_pricing={
                    "priced-model": GuardrailModelPricing(
                        prompt_per_1m=1.0,
                        completion_per_1m=3.0,
                    )
                },
            )
        ),
        run_guardrail_recorder=recorder,
    )
    context = ConversationContext()
    context.session_id = "session-p1"
    context.add_user_message("repeat failing tool")
    token = set_run_execution_context(
        RunExecutionContext(run_id=created.run_id, owner_id="worker-p1", segment_index=1)
    )
    try:
        result = await adapter.run(
            context,
            _config(),
            _QueueModel(
                [
                    LLMResponse(
                        content="",
                        model="priced-model",
                        usage={"prompt_tokens": 1000, "completion_tokens": 500},
                        tool_calls=[
                            ToolCallRequest("call-1", "echo_tool", '{"path":"a"}'),
                            ToolCallRequest("call-2", "echo_tool", '{"path":"a"}'),
                        ],
                    ),
                    LLMResponse(
                        content="done",
                        model="priced-model",
                        usage={"prompt_tokens": 200, "completion_tokens": 100},
                    ),
                ]
            ),
        )
    finally:
        reset_run_execution_context(token)

    assert result.status == "completed"
    events = await store.list_events(created.run_id, after_cursor=None, limit=50)
    guardrail_events = [
        event
        for event in events
        if event.event_type in {RunEventType.GUARDRAIL_EVALUATED, RunEventType.GUARDRAIL_BLOCKED}
    ]
    assert guardrail_events
    final_event = guardrail_events[-1]
    final_stats = final_event.payload["stats"]
    snapshot = await store.get_run(created.run_id)
    assert snapshot is not None
    assert snapshot.guardrail_summary is not None
    summary_stats = snapshot.guardrail_summary["runtime_stats"]

    assert summary_stats == final_stats
    assert snapshot.guardrail_summary["last_event_cursor"] == final_event.cursor
    assert summary_stats["total_tokens"] == 1800
    assert summary_stats["prompt_tokens"] == 1200
    assert summary_stats["completion_tokens"] == 600
    assert summary_stats["total_model_calls"] == 2
    assert summary_stats["context_growth_messages"] == 2
    assert summary_stats["total_tool_calls"] == 2
    assert summary_stats["repeated_tool_call_count"] == 1
    assert summary_stats["consecutive_failure_count"] == 2
    assert summary_stats["last_tool_error"] is True
    assert summary_stats["cost_available"] is True
    assert summary_stats["estimated_cost"] == 0.003
    assert snapshot.guardrail_summary["evaluation_count"] == len(guardrail_events)


async def test_p1_recovery_uses_persisted_summary_without_double_counting(tmp_path: Path) -> None:
    """恢复上下文应从已有 summary 继续累计，只增加新模型调用。"""

    store = _store(tmp_path)
    persisted_summary = {
        "mode": "observe",
        "action": "allow",
        "evaluation_count": 1,
        "blocked_count": 0,
        "approval_request_count": 0,
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
            "estimated_cost": 0.01,
            "cost_available": True,
        },
    }
    created = await store.create_run(
        _run_request(
            client_request_id="client-p1-recovery",
            guardrail_summary=persisted_summary,
        )
    )
    claimed = await store.claim_next(owner_id="worker-p1", lease_seconds=60)
    assert claimed is not None
    recorder = RunGuardrailRecorder(
        run_store=store,
        observation_store=store,
        guardrail_serializer=GuardrailSerializerAdapter(),
    )
    adapter = ReActAgentAdapter(
        tool_registry=_registry(_FailingTool()),
        context_builder=_ContextBuilder(),
        guardrail_policy=StaticAgentGuardrailPolicy(
            GuardrailPolicy(
                mode=GuardrailMode.OBSERVE,
                model_pricing={"priced-model": GuardrailModelPricing(total_per_1m=2.0)},
            )
        ),
        run_guardrail_recorder=recorder,
    )
    context = ConversationContext()
    context.add_user_message("continue")
    token = set_run_execution_context(
        RunExecutionContext(
            run_id=created.run_id,
            owner_id="worker-p1",
            segment_index=2,
            recovery_mode=True,
            guardrail_summary=persisted_summary,
        )
    )
    try:
        result = await adapter.run(
            context,
            _config(),
            _QueueModel(
                [
                    LLMResponse(
                        content="done",
                        model="priced-model",
                        usage={"prompt_tokens": 10, "completion_tokens": 5},
                    )
                ]
            ),
        )
    finally:
        reset_run_execution_context(token)

    assert result.status == "completed"
    snapshot = await store.get_run(created.run_id)
    assert snapshot is not None
    assert snapshot.guardrail_summary is not None
    stats = snapshot.guardrail_summary["runtime_stats"]
    assert stats["total_tokens"] == 115
    assert stats["prompt_tokens"] == 80
    assert stats["completion_tokens"] == 35
    assert stats["total_model_calls"] == 3
    assert stats["total_tool_calls"] == 4
    assert stats["repeated_tool_call_count"] == 1
    assert stats["consecutive_failure_count"] == 2
