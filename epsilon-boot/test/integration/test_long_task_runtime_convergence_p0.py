"""长任务运行时 P0 收敛集成回归测试。

本模块使用本地文件 Run store、真实 Run worker/执行协调器、Run 应用服务、
guardrail recorder、审批恢复分派器、ChatServiceAdapter、TaskAgentAdapter 与
ReActAgentAdapter 组成轻量集成链路。模型、工具、Prompt 注册表和上下文构建器
使用本地 fake，测试不启动数据库、缓存、HTTP 服务或后台常驻 worker。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from application.api.routers.runs import _event_body, _snapshot_body
from application.run.run_application_service import RunApplicationService
from application.run.run_approval_resumer import RunApprovalResumer
from application.run.run_execution_coordinator import RunExecutionCoordinator
from application.run.run_guardrail_recorder import RunGuardrailRecorder
from domain.agent.guardrails import GuardrailMode, GuardrailPolicy, ToolRiskLevel
from domain.agent.segmented_execution import SegmentExecutionPolicy
from domain.agent.tools import Tool, ToolExecutionResult, ToolRegistry
from domain.agent.value_objects import ApprovalDecision
from domain.chat.context import ConversationContext, UserMessage
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
    ToolCallRequest,
)
from domain.prompt.value_objects import LoadedPrompt
from domain.run.value_objects import (
    EventRetentionPolicy,
    RunCapacityPolicy,
    RunCreateRequest,
    RunEvent,
    RunEventType,
    RunKind,
    RunPayload,
    RunSnapshot,
    RunStatus,
)
from domain.task.value_objects import TaskApprovalResumeRequest
from infrastructure.agent.approval_state_store import LocalFileApprovalStateStore
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from infrastructure.agent.static_guardrail_policy import StaticAgentGuardrailPolicy
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from infrastructure.persistence.local_file.atomic_writer import TempFileAtomicWriter
from infrastructure.persistence.local_file.file_lock import LockFactory
from infrastructure.persistence.local_file.path_policy import CrossPlatformPathPolicy
from infrastructure.run.local_file_run_store_adapter import LocalFileRunStoreAdapter
from infrastructure.run.run_config import RunRuntimeConfig
from infrastructure.run.run_serialization_adapters import (
    GuardrailSerializerAdapter,
    SegmentSerializerAdapter,
    WorkflowSerializerAdapter,
)
from infrastructure.run.run_worker import RunWorker
from infrastructure.session.local_file_session_context_adapter import LocalFileSessionContextAdapter
from infrastructure.task.task_agent_adapter import TaskAgentAdapter
from test.infrastructure.agent._v3_stream_helpers import response_to_chunks
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies

pytestmark = pytest.mark.asyncio

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _QueueModel:
    """按队列返回 LLMResponse 的本地模型 fake。"""

    def __init__(self, responses: list[LLMResponse] | None = None) -> None:
        """初始化响应队列与调用记录。"""

        self.responses = list(responses or [])
        self.stream_requests: list[ChatRequest] = []

    def append(self, response: LLMResponse) -> None:
        """向响应队列追加一个模型响应。"""

        self.responses.append(response)

    async def chat(self, request: ChatRequest) -> LLMResponse:
        """兼容 ModelAccessPort.chat；ReAct v3 路径不会调用该方法。"""

        self.stream_requests.append(request)
        if self.responses:
            return self.responses.pop(0)
        return LLMResponse(content="done", model=request.model or "test-model")

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        """按响应队列产出等价流式分片。"""

        self.stream_requests.append(request)
        response = (
            self.responses.pop(0)
            if self.responses
            else LLMResponse(content="done", model=request.model or "test-model")
        )
        for chunk in response_to_chunks(response):
            yield chunk

    def count_tokens(self, messages: list[Any]) -> int:
        """返回用于测试的近似 token 数。"""

        return len(messages)


class _ModelRegistry:
    """按模型名返回本地 fake 模型的注册中心。"""

    def __init__(self, mapping: dict[str, _QueueModel], default_model: str) -> None:
        """初始化模型映射。"""

        self._mapping = mapping
        self._default_model = default_model

    def get_default_model(self) -> str:
        """返回默认模型名。"""

        return self._default_model

    def get_adapter_for_model(self, model: str):
        """返回指定模型对应的 fake adapter。"""

        return self._mapping[model]


class _PromptRegistry:
    """返回 chat/task 测试 Prompt 的注册表 fake。"""

    def get(self, name: str) -> LoadedPrompt:
        """按 Prompt 名称返回合法 LoadedPrompt。"""

        if name == "task-template":
            return LoadedPrompt(
                prompt_id="task-template@v1",
                name="task-template",
                version="v1",
                content="task template",
            )
        return LoadedPrompt(
            prompt_id="chat-default@v1",
            name="chat-default",
            version="v1",
            content="chat system",
        )


class _ContextBuilder:
    """原样透传会话消息的上下文构建器 fake。"""

    async def build(self, messages: list[Any], **kwargs: Any) -> ContextBuilderResult:
        """构造 ContextBuilderResult，不访问外部模型或存储。"""

        return ContextBuilderResult(messages=list(messages), usage={})


class _MutableRiskTool(Tool):
    """可调整风险等级并记录执行次数的测试工具。"""

    def __init__(self, name: str, risk_level: ToolRiskLevel) -> None:
        """初始化工具名称、风险等级与执行记录。"""

        self._name = name
        self._risk_level = risk_level
        self.requests: list[dict[str, Any]] = []

    @property
    def name(self) -> str:
        """返回工具名称。"""

        return self._name

    @property
    def description(self) -> str:
        """返回工具描述。"""

        return f"{self._name} test tool"

    @property
    def parameters(self) -> dict[str, Any]:
        """返回空对象参数 schema。"""

        return {"type": "object", "properties": {}}

    @property
    def risk_level(self) -> ToolRiskLevel:
        """返回当前风险等级。"""

        return self._risk_level

    def set_risk_level(self, risk_level: ToolRiskLevel) -> None:
        """调整工具风险等级，用于模拟审批后风险已被人工降级。"""

        self._risk_level = risk_level

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """记录执行参数并返回固定结果。"""

        self.requests.append(dict(kwargs))
        return ToolExecutionResult(content=f"{self._name} executed")


class _NoopTaskAgent:
    """聊天测试中不应被调用的任务 Agent fake。"""

    async def resume_approval(self, request: TaskApprovalResumeRequest):
        """若被调用则说明 RunKind 分派错误。"""

        raise AssertionError(f"unexpected task resume request: {request!r}")


class _NoopChatService:
    """任务测试中不应被调用的聊天服务 fake。"""

    async def resume_approval(self, request):
        """若被调用则说明 RunKind 分派错误。"""

        raise AssertionError(f"unexpected chat resume request: {request!r}")


def _path_policy() -> CrossPlatformPathPolicy:
    """构造本地持久化路径策略。"""

    return CrossPlatformPathPolicy()


def _lock_factory() -> LockFactory:
    """构造测试用文件锁工厂。"""

    return LockFactory(acquire_timeout_ms=1000)


def _atomic_writer() -> TempFileAtomicWriter:
    """构造测试用原子写入器。"""

    return TempFileAtomicWriter(fsync_on_write=False)


def _store(root: Path) -> LocalFileRunStoreAdapter:
    """构造使用本地临时目录的 Run store。"""

    return LocalFileRunStoreAdapter(
        root=root,
        lock_factory=_lock_factory(),
        path_policy=_path_policy(),
        atomic_writer=_atomic_writer(),
    )


def _approval_store(root: Path) -> LocalFileApprovalStateStore:
    """构造真实本地文件审批状态存储。"""

    return LocalFileApprovalStateStore(
        root=root,
        lock_factory=_lock_factory(),
        path_policy=_path_policy(),
        atomic_writer=_atomic_writer(),
        ttl_seconds=3600,
    )


def _session_store(root: Path) -> LocalFileSessionContextAdapter:
    """构造真实本地文件会话上下文存储。"""

    return LocalFileSessionContextAdapter(
        root=root,
        lock_factory=_lock_factory(),
        path_policy=_path_policy(),
        atomic_writer=_atomic_writer(),
    )


def _service(
    store: LocalFileRunStoreAdapter,
    *,
    approval_resumer=None,
) -> RunApplicationService:
    """构造只依赖本地 store 的 Run 应用服务。"""

    return RunApplicationService(
        run_store=store,
        event_store=store,
        capacity_policy=RunCapacityPolicy(max_queued_runs=10, max_running_runs=10),
        event_retention_policy=EventRetentionPolicy(max_event_count=100, ttl_seconds=3600),
        workflow_serializer=WorkflowSerializerAdapter(),
        approval_resumer=approval_resumer,
        event_stream_wait_seconds=0,
    )


def _guardrail_policy(*, enforce_high_risk_tools: bool = True) -> StaticAgentGuardrailPolicy:
    """构造真实静态 guardrail 策略。"""

    return StaticAgentGuardrailPolicy(
        GuardrailPolicy(
            mode=GuardrailMode.ENFORCE,
            enforce_high_risk_tools=enforce_high_risk_tools,
        )
    )


def _agent(
    *,
    tool_registry: ToolRegistry,
    approval_store: LocalFileApprovalStateStore,
    recorder: RunGuardrailRecorder,
    guardrail_policy: StaticAgentGuardrailPolicy | None = None,
) -> ReActAgentAdapter:
    """构造接入真实审批存储与 Run guardrail recorder 的 ReAct agent。"""

    return ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_ContextBuilder(),
        approval_store=approval_store,
        guardrail_policy=guardrail_policy or _guardrail_policy(),
        run_guardrail_recorder=recorder,
    )


def _tool_registry(tool: Tool) -> ToolRegistry:
    """构造只注册单个工具的工具注册表。"""

    registry = ToolRegistry()
    registry.register(tool)
    return registry


def _chat_service(
    *,
    root: Path,
    agent: ReActAgentAdapter,
    model: _QueueModel,
    tool_registry: ToolRegistry,
    approval_store: LocalFileApprovalStateStore,
    max_tool_rounds: int = 3,
) -> ChatServiceAdapter:
    """构造真实 ChatServiceAdapter。"""

    session_store = _session_store(root)
    model_registry = _ModelRegistry({"model-chat": model}, "model-chat")
    prompt_registry = _PromptRegistry()
    loaded_prompt = prompt_registry.get("chat-default")
    tool_schemas = tool_registry.get_schemas()
    segment_policy = SegmentExecutionPolicy(auto_continue_enabled=True)

    return ChatServiceAdapter(
        session_store=session_store,
        model_registry=model_registry,
        prompt_registry=prompt_registry,
        context_builder=_ContextBuilder(),
        agent=agent,
        tool_calling_enabled=True,
        max_tool_rounds=max_tool_rounds,
        tool_schemas=tool_schemas,
        approval_store=approval_store,
        segment_policy=segment_policy,
        **make_chat_adapter_dependencies(
            session_store=session_store,
            model_registry=model_registry,
            loaded_prompt=loaded_prompt,
            agent=agent,
            tool_schemas=tool_schemas,
            max_tool_rounds=max_tool_rounds,
            approval_store=approval_store,
            segment_policy=segment_policy,
        ),
    )


def _task_agent(
    *,
    root: Path,
    agent: ReActAgentAdapter,
    model: _QueueModel,
    tool_registry: ToolRegistry,
    approval_store: LocalFileApprovalStateStore,
) -> TaskAgentAdapter:
    """构造真实 TaskAgentAdapter。"""

    return TaskAgentAdapter(
        agent=agent,
        tool_registry=tool_registry,
        model_registry=_ModelRegistry({"model-task": model}, "model-task"),
        compaction=_ContextBuilder(),
        session_store=_session_store(root),
        prompt_registry=_PromptRegistry(),
        approval_store=approval_store,
        max_rounds=3,
        segment_policy=SegmentExecutionPolicy(auto_continue_enabled=True),
    )


def _worker(
    *,
    store: LocalFileRunStoreAdapter,
    chat_service,
    task_agent,
    owner_id: str,
) -> RunWorker:
    """构造执行单个 run 段的 focused worker。"""

    return RunWorker(
        run_store=store,
        event_store=store,
        executor=RunExecutionCoordinator(
            chat_service=chat_service,
            task_agent=task_agent,
            segment_serializer=SegmentSerializerAdapter(),
        ),
        lease_seconds=60,
        heartbeat_interval_seconds=30,
        owner_id=owner_id,
    )


def _chat_request(*, client_request_id: str = "client-chat") -> RunCreateRequest:
    """构造聊天 Run 创建请求。"""

    payload = RunPayload(
        kind=RunKind.CHAT,
        session_id="session-chat",
        chat={"session_id": "session-chat", "message": "run high risk tool"},
        model="model-chat",
    )
    return RunCreateRequest(payload=payload, client_request_id=client_request_id)


def _task_request() -> RunCreateRequest:
    """构造任务 Run 创建请求。"""

    payload = RunPayload(
        kind=RunKind.TASK,
        session_id="session-task",
        task={
            "session_id": "session-task",
            "goal": "run high risk task tool",
            "model": "model-task",
        },
        model="model-task",
    )
    return RunCreateRequest(payload=payload, client_request_id="client-task")


def _tool_call_response(*, model: str, tool_name: str, call_id: str) -> LLMResponse:
    """构造请求调用单个工具的模型响应。"""

    return LLMResponse(
        content="",
        model=model,
        usage={"total_tokens": 13},
        tool_calls=[ToolCallRequest(id=call_id, name=tool_name, arguments="{}")],
    )


def _final_response(*, model: str, content: str = "approved done") -> LLMResponse:
    """构造最终文本模型响应。"""

    return LLMResponse(
        content=content,
        model=model,
        usage={"total_tokens": 3},
    )


async def test_chat_run_uses_real_guardrail_path_for_event_summary_approval_and_resume(
    tmp_path: Path,
) -> None:
    """聊天 Run 应通过真实 ReAct guardrail/HITL/worker 路径进入审批并恢复同一 Run。"""

    store = _store(tmp_path)
    approvals = _approval_store(tmp_path)
    tool = _MutableRiskTool("shell_exec", ToolRiskLevel.HIGH)
    registry = _tool_registry(tool)
    model = _QueueModel(
        [_tool_call_response(model="model-chat", tool_name="shell_exec", call_id="call-risk-1")]
    )
    recorder = RunGuardrailRecorder(
        run_store=store,
        observation_store=store,
        guardrail_serializer=GuardrailSerializerAdapter(),
    )
    react_agent = _agent(
        tool_registry=registry,
        approval_store=approvals,
        recorder=recorder,
    )
    chat_service = _chat_service(
        root=tmp_path,
        agent=react_agent,
        model=model,
        tool_registry=registry,
        approval_store=approvals,
    )
    service = _service(
        store,
        approval_resumer=RunApprovalResumer(
            chat_service=chat_service,
            task_agent=_NoopTaskAgent(),
        ),
    )

    created = await service.create_run(_chat_request())
    ran = await _worker(
        store=store,
        chat_service=chat_service,
        task_agent=_NoopTaskAgent(),
        owner_id="worker-chat",
    ).run_once()
    assert ran is True

    awaiting = await service.get_run(created.run_id)
    assert awaiting.status is RunStatus.AWAITING_APPROVAL
    assert awaiting.approval_id is not None
    assert tool.requests == []

    interrupt = await approvals.load("session-chat", awaiting.approval_id)
    assert interrupt is not None
    assert interrupt.metadata["source"] == "guardrail"
    assert interrupt.metadata["guardrail_action"] == "require_approval"
    assert interrupt.metadata["guardrail_reason"] == "tool_risk_gate_required"
    assert interrupt.metadata["risk_gate_required"] is True
    assert interrupt.actions[0].tool_name == "shell_exec"

    events = await service.list_events(created.run_id, after_cursor=None, limit=50)
    blocked_events = [
        event for event in events if event.event_type is RunEventType.GUARDRAIL_BLOCKED
    ]
    assert len(blocked_events) == 1
    blocked_event = blocked_events[0]
    assert blocked_event.payload["action"] == "require_approval"
    assert blocked_event.payload["reason"] == "tool_risk_gate_required"
    assert blocked_event.payload["tool_name"] == "shell_exec"
    assert blocked_event.payload["tool_risk_level"] == "high"
    assert blocked_event.payload["approval_id"] == awaiting.approval_id

    assert awaiting.guardrail_summary is not None
    assert awaiting.guardrail_summary["action"] == "require_approval"
    assert awaiting.guardrail_summary["evaluation_count"] >= 1
    assert awaiting.guardrail_summary["blocked_count"] == 1
    assert awaiting.guardrail_summary["approval_request_count"] == 1
    assert awaiting.guardrail_summary["last_event_cursor"] == blocked_event.cursor
    assert awaiting.guardrail_summary["runtime_stats"]["last_tool_name"] == "shell_exec"

    segment_done = [event for event in events if event.event_type is RunEventType.SEGMENT_DONE][-1]
    segment_metadata = segment_done.payload["segment_metadata"]
    assert segment_metadata["risk_gate_required"] is True

    original_payload = awaiting.payload
    tool.set_risk_level(ToolRiskLevel.LOW)
    model.append(_final_response(model="model-chat", content="approved and completed"))
    continued = await service.resume_approval_run(
        created.run_id,
        [ApprovalDecision(type="approve", tool_call_id="call-risk-1")],
        model="model-chat",
    )

    assert continued.run_id == created.run_id
    assert continued.status is RunStatus.SUCCEEDED
    assert continued.approval_id is None
    assert continued.payload == original_payload
    assert tool.requests == [{}]
    assert await approvals.load("session-chat", awaiting.approval_id) is None
    saved_context = await _session_store(tmp_path).load("session-chat")
    assert [message.role for message in saved_context.get_messages()].count("user") == 1


async def test_task_resume_uses_real_task_agent_and_regenerates_guardrail_approval_id(
    tmp_path: Path,
) -> None:
    """任务审批恢复应使用真实 TaskAgentAdapter，并由真实 guardrail/HITL 生成新 approval_id。"""

    store = _store(tmp_path)
    approvals = _approval_store(tmp_path)
    tool = _MutableRiskTool("task_write_file", ToolRiskLevel.HIGH)
    registry = _tool_registry(tool)
    model = _QueueModel(
        [
            _tool_call_response(
                model="model-task", tool_name="task_write_file", call_id="call-task-1"
            )
        ]
    )
    recorder = RunGuardrailRecorder(
        run_store=store,
        observation_store=store,
        guardrail_serializer=GuardrailSerializerAdapter(),
    )
    react_agent = _agent(
        tool_registry=registry,
        approval_store=approvals,
        recorder=recorder,
    )
    task_agent = _task_agent(
        root=tmp_path,
        agent=react_agent,
        model=model,
        tool_registry=registry,
        approval_store=approvals,
    )
    service = _service(
        store,
        approval_resumer=RunApprovalResumer(
            chat_service=_NoopChatService(),
            task_agent=task_agent,
        ),
    )

    created = await service.create_run(_task_request())
    ran = await _worker(
        store=store,
        chat_service=_NoopChatService(),
        task_agent=task_agent,
        owner_id="worker-task",
    ).run_once()
    assert ran is True

    awaiting = await service.get_run(created.run_id)
    assert awaiting.status is RunStatus.AWAITING_APPROVAL
    old_approval_id = awaiting.approval_id
    assert old_approval_id is not None
    old_interrupt = await approvals.load("session-task", old_approval_id)
    assert old_interrupt is not None
    assert old_interrupt.metadata["source"] == "guardrail"
    assert tool.requests == []

    resumed = await service.resume_approval_run(
        created.run_id,
        [ApprovalDecision(type="approve", tool_call_id="call-task-1")],
    )

    assert resumed.run_id == created.run_id
    assert resumed.status is RunStatus.AWAITING_APPROVAL
    assert resumed.approval_id is not None
    assert resumed.approval_id != old_approval_id
    assert resumed.payload == awaiting.payload
    assert resumed.payload.task == {
        "session_id": "session-task",
        "goal": "run high risk task tool",
        "model": "model-task",
    }
    assert tool.requests == []
    assert await approvals.load("session-task", old_approval_id) is None
    new_interrupt = await approvals.load("session-task", resumed.approval_id)
    assert new_interrupt is not None
    assert new_interrupt.metadata["source"] == "guardrail"
    assert new_interrupt.metadata["guardrail_reason"] == "tool_risk_gate_required"
    assert new_interrupt.approval_id == resumed.approval_id
    restored_context = ConversationContext.from_dict(new_interrupt.context_snapshot)
    user_messages = [
        message for message in restored_context.get_messages() if isinstance(message, UserMessage)
    ]
    assert [message.content for message in user_messages] == ["run high risk task tool"]

    events = await service.list_events(created.run_id, after_cursor=None, limit=50)
    blocked_events = [
        event for event in events if event.event_type is RunEventType.GUARDRAIL_BLOCKED
    ]
    assert len(blocked_events) == 2
    assert blocked_events[0].payload["approval_id"] == old_approval_id
    assert blocked_events[-1].payload["approval_id"] == resumed.approval_id
    assert blocked_events[-1].payload["action"] == "require_approval"
    assert blocked_events[-1].payload["reason"] == "tool_risk_gate_required"
    assert events[-1].event_type is RunEventType.APPROVAL_REQUIRED
    assert events[-1].payload["approval_id"] == resumed.approval_id
    assert resumed.guardrail_summary is not None
    assert resumed.guardrail_summary["last_event_cursor"] == blocked_events[-1].cursor
    assert resumed.guardrail_summary["blocked_count"] == 2
    assert resumed.guardrail_summary["approval_request_count"] == 2
    assert resumed.guardrail_summary["evaluation_count"] >= 2


async def test_guardrail_stop_risk_gate_reaches_segment_stop_reason(tmp_path: Path) -> None:
    """guardrail stop 导出的 risk_gate_required 应进入分段停止原因。"""

    store = _store(tmp_path)
    approvals = _approval_store(tmp_path)
    tool = _MutableRiskTool("critical_shell", ToolRiskLevel.CRITICAL)
    registry = _tool_registry(tool)
    model = _QueueModel(
        [_tool_call_response(model="model-chat", tool_name="critical_shell", call_id="call-stop-1")]
    )
    recorder = RunGuardrailRecorder(
        run_store=store,
        observation_store=store,
        guardrail_serializer=GuardrailSerializerAdapter(),
    )
    react_agent = _agent(
        tool_registry=registry,
        approval_store=approvals,
        recorder=recorder,
        guardrail_policy=_guardrail_policy(enforce_high_risk_tools=True),
    )
    chat_service = _chat_service(
        root=tmp_path,
        agent=react_agent,
        model=model,
        tool_registry=registry,
        approval_store=approvals,
        max_tool_rounds=1,
    )
    service = _service(store)

    created = await service.create_run(_chat_request(client_request_id="client-chat-stop"))
    ran = await _worker(
        store=store,
        chat_service=chat_service,
        task_agent=_NoopTaskAgent(),
        owner_id="worker-chat-stop",
    ).run_once()
    assert ran is True

    snapshot = await service.get_run(created.run_id)
    assert snapshot.status is RunStatus.PAUSED
    assert tool.requests == []
    events = await service.list_events(created.run_id, after_cursor=None, limit=50)
    blocked_event = next(
        event for event in events if event.event_type is RunEventType.GUARDRAIL_BLOCKED
    )
    assert blocked_event.payload["action"] == "stop"
    segment_done = [event for event in events if event.event_type is RunEventType.SEGMENT_DONE][-1]
    segment_metadata = segment_done.payload["segment_metadata"]
    assert segment_metadata["risk_gate_required"] is True
    assert segment_metadata["guardrail_reason"] == "tool_risk_gate_required"
    assert segment_metadata["segment_stop_reason"] == "risk_gate_required"


async def test_default_convergence_enabled_keeps_historical_snapshot_and_event_read_compatible(
) -> None:
    """默认开启收敛写路径时，历史 snapshot/event 仍应能被旧读取路径安全消费。"""

    assert RunRuntimeConfig().guardrail_runtime_convergence_enabled is True

    payload = RunPayload(
        kind=RunKind.CHAT,
        session_id="legacy-session",
        chat={"message": "legacy read"},
        model="legacy-model",
    )
    historical_snapshot = RunSnapshot(
        run_id="run-legacy",
        kind=RunKind.CHAT,
        status=RunStatus.AWAITING_APPROVAL,
        payload=payload,
        client_request_id="legacy-client",
        payload_hash=payload.stable_hash(),
        result={"status": "approval_required"},
        error=None,
        approval_id="approval-legacy",
        segment_metadata={"segment_count": 1, "risk_gate_required": True},
        latest_event_cursor=9,
        can_continue=True,
        terminal_reason=None,
        lease=None,
        created_at=_NOW,
        updated_at=_NOW,
        version=3,
        guardrail_summary={
            "mode": "enforce",
            "action": "require_approval",
            "reason": "tool_risk_gate_required",
            "evaluation_count": 1,
            "blocked_count": 1,
            "approval_request_count": 1,
            "last_event_cursor": 9,
        },
        collaboration_summary={
            "recent_steps": [{"action": "handoff", "target_agent": "legacy-reviewer"}],
            "handoff_count": 1,
        },
    )
    historical_event = RunEvent(
        run_id="run-legacy",
        cursor=9,
        event_type=RunEventType.GUARDRAIL_BLOCKED,
        payload={
            "action": "require_approval",
            "reason": "tool_risk_gate_required",
            "tool_name": "shell_exec",
            "approval_id": "approval-legacy",
        },
        created_at=_NOW,
    )

    snapshot_body = _snapshot_body(historical_snapshot).model_dump(mode="json")
    event_body = _event_body(historical_event).model_dump(mode="json")

    assert snapshot_body["guardrail_summary"] == historical_snapshot.guardrail_summary
    assert snapshot_body["collaboration_summary"]["latest_steps"] == [
        {"action": "handoff", "target_agent": "legacy-reviewer"}
    ]
    assert "recent_steps" not in snapshot_body["collaboration_summary"]
    assert snapshot_body["status"] == "awaiting_approval"
    assert event_body["event_type"] == "guardrail_blocked"
    assert event_body["payload"]["approval_id"] == "approval-legacy"
    assert event_body["payload"]["reason"] == "tool_risk_gate_required"
