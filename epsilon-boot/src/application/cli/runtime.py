"""CLI 运行时门面模块，封装共享 Agent Runtime 的交互入口。"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from typing import Any

from application.container_config import configure_container
from application.run.run_application_service import RunApplicationService
from common.container import Container
from common.container import container as default_container
from domain.agent.ports import (
    ApprovalPolicyPort,
    ApprovalStateStorePort,
    ArtifactStorePort,
    TraceStorePort,
)
from domain.agent.tools import ToolRegistry
from domain.agent.trace_value_objects import SessionTrace
from domain.agent.value_objects import (
    AgentStreamEvent,
    ApprovalDecision,
    ApprovalInterruptSummary,
    ApprovalPolicy,
    PendingActionRequest,
)
from domain.chat.ports import ChatServicePort, SessionContextStorePort, SessionIndexPort
from domain.chat.value_objects import ApprovalResumeRequestVO, ChatRequestVO, SessionMetadata
from domain.model_access.ports import ModelRegistryPort
from domain.model_access.value_objects import StreamingChunk, ToolCallRequest
from domain.run.value_objects import (
    RunCreateRequest,
    RunEvent,
    RunKind,
    RunPayload,
    RunSnapshot,
)
from domain.task.ports import TaskAgentPort
from domain.task.value_objects import Task, TaskResult
from domain.workspace.ports import Workspace

from .session import TuiSessionState
from .workflow import (
    CodingDiffSnapshot,
    CodingFilesSnapshot,
    CodingStatusSnapshot,
    CodingTestsSnapshot,
    extract_file_snapshot,
    extract_test_records,
    latest_trace_kind,
)

logger = logging.getLogger(__name__)
_container_configured = False


@dataclass(frozen=True)
class DoctorResult:
    """本地 CLI 诊断使用的轻量健康快照。"""

    session_id: str
    model: str
    agent_mode: str
    workspace: str


@dataclass(frozen=True)
class ResumeSessionResult:
    """恢复会话命令的运行时结果。"""

    found: bool
    metadata: SessionMetadata | None = None
    approval_summaries: list[ApprovalInterruptSummary] | None = None
    missing_reason: str | None = None


@dataclass(frozen=True)
class ExecJsonResult:
    """`epsilon exec --json` 的结构化输出载体。"""

    status: str
    content: str
    model: str
    prompt_id: str
    usage: dict[str, int]
    latency_ms: float
    terminated_reason: str
    can_continue: bool
    approval_id: str | None
    trace_ref: dict[str, Any]
    artifact_ref: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        """转换为可 JSON 序列化的字典。"""
        return asdict(self)


class CliRuntime:
    """启动共享资源并暴露面向 CLI/TUI 的运行操作。"""

    def __init__(self, *, di_container: Container = default_container) -> None:
        self._container = di_container
        self._started = False
        self.chat_service: ChatServicePort | None = None
        self.task_agent: TaskAgentPort | None = None
        self.model_registry: ModelRegistryPort | None = None
        self.workspace: Workspace | None = None
        self.run_service: RunApplicationService | None = None
        self.session_store: SessionContextStorePort | None = None
        self.session_index: SessionIndexPort | None = None
        self.approval_store: ApprovalStateStorePort | None = None
        self.approval_policy: ApprovalPolicyPort | None = None
        self.trace_store: TraceStorePort | None = None
        self.artifact_store: ArtifactStorePort | None = None
        self.tool_registry: ToolRegistry | None = None
        self._known_runs: dict[str, RunSnapshot] = {}

    async def __aenter__(self) -> CliRuntime:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        await self.stop()

    async def start(self) -> None:
        """配置并启动共享依赖注入容器。"""
        global _container_configured
        if not _container_configured:
            configure_container()
            _container_configured = True

        await self._container.start()
        self.chat_service = await self._container.resolve(ChatServicePort)
        self.task_agent = await self._container.resolve(TaskAgentPort)
        self.model_registry = await self._container.resolve(ModelRegistryPort)
        self.workspace = await self._container.resolve(Workspace)
        self.run_service = await self._container.resolve(RunApplicationService)
        self.session_store = await self._container.resolve(SessionContextStorePort)
        self.session_index = await self._container.resolve(SessionIndexPort)
        self.approval_store = await self._container.resolve(ApprovalStateStorePort)
        self.approval_policy = await self._container.resolve(ApprovalPolicyPort)
        self.trace_store = await self._container.resolve(TraceStorePort)
        self.artifact_store = await self._container.resolve(ArtifactStorePort)
        self.tool_registry = await self._container.resolve(ToolRegistry)
        self._started = True

    async def stop(self) -> None:
        """在本运行时已启动资源时停止共享容器资源。"""
        if self._started:
            await self._container.stop()
            self._started = False

    def _require_chat_service(self) -> ChatServicePort:
        if self.chat_service is None:
            raise RuntimeError("CLI Runtime 尚未启动")
        return self.chat_service

    def _require_task_agent(self) -> TaskAgentPort:
        if self.task_agent is None:
            raise RuntimeError("CLI Runtime 尚未启动")
        return self.task_agent

    def _require_model_registry(self) -> ModelRegistryPort:
        if self.model_registry is None:
            raise RuntimeError("CLI Runtime 尚未启动")
        return self.model_registry

    def _require_run_service(self) -> RunApplicationService:
        if self.run_service is None:
            raise RuntimeError("CLI Runtime 尚未启动")
        return self.run_service

    def _require_session_store(self) -> SessionContextStorePort:
        if self.session_store is None:
            raise RuntimeError("CLI Runtime 尚未启动")
        return self.session_store

    def _require_session_index(self) -> SessionIndexPort:
        if self.session_index is None:
            raise RuntimeError("CLI Runtime 尚未启动")
        return self.session_index

    def _require_approval_policy(self) -> ApprovalPolicyPort:
        if self.approval_policy is None:
            raise RuntimeError("CLI Runtime 尚未启动")
        return self.approval_policy

    def _require_tool_registry(self) -> ToolRegistry:
        if self.tool_registry is None:
            raise RuntimeError("CLI Runtime 尚未启动")
        return self.tool_registry

    async def stream_main_agent(
        self,
        message: str,
        state: TuiSessionState,
    ) -> AsyncIterator[StreamingChunk]:
        """为当前 TUI 会话流式返回主 Agent 回复。"""
        request = ChatRequestVO(
            session_id=state.session_id,
            message=message,
            stream=True,
            model=state.model,
        )
        async for chunk in self._require_chat_service().stream_chat(request):
            yield chunk

    async def stream_main_agent_events(
        self,
        message: str,
        state: TuiSessionState,
    ) -> AsyncIterator[AgentStreamEvent]:
        """为当前 TUI 会话流式返回结构化主 Agent 事件。"""
        request = ChatRequestVO(
            session_id=state.session_id,
            message=message,
            stream=True,
            model=state.model,
        )
        chat_service = self._require_chat_service()
        stream_events = getattr(chat_service, "stream_chat_events", None)
        if stream_events is not None:
            async for event in stream_events(request):
                yield event
            return

        async for chunk in chat_service.stream_chat(request):
            if chunk.delta_content:
                yield AgentStreamEvent(
                    kind="assistant_delta",
                    content=chunk.delta_content,
                )
            if chunk.finished:
                yield AgentStreamEvent(kind="assistant_done", usage=chunk.usage)

    async def resume_main_agent_events(
        self,
        session_id: str,
        approval_id: str,
        decisions: list[ApprovalDecision],
        *,
        model: str | None = None,
    ) -> AsyncIterator[AgentStreamEvent]:
        """为当前 TUI 会话提交审批决策并流式续播主 Agent 事件。

        与 stream_main_agent_events 对称：构造 ApprovalResumeRequestVO 委托
        ChatServicePort.stream_resume_approval，逐个转发 AgentStreamEvent。
        """
        request = ApprovalResumeRequestVO(
            session_id=session_id,
            approval_id=approval_id,
            decisions=tuple(decisions),
            model=model,
        )
        async for event in self._require_chat_service().stream_resume_approval(request):
            yield event

    async def load_pending_actions(
        self,
        session_id: str,
        approval_id: str,
    ) -> tuple[PendingActionRequest, ...]:
        """读取指定审批批次的完整待审批动作（含 arguments），供面板渲染与 edit 预填。

        通过 ApprovalStateStorePort.load 只读取不消费；批次不存在或已过期时
        返回空元组，由调用方回退到无 arguments 的事件 metadata 摘要展示。
        """
        if self.approval_store is None:
            return ()
        interrupt = await self.approval_store.load(session_id, approval_id)
        return interrupt.actions if interrupt is not None else ()

    def policy_for(self, tool_name: str) -> ApprovalPolicy:
        """按工具名返回后端审批策略，供 Approval_Mode 判定使用（不硬编码分级）。"""
        return self._require_approval_policy().policy_for(tool_name)

    async def list_pending_approvals(
        self,
        session_id: str,
    ) -> list[ApprovalInterruptSummary]:
        """列出指定会话未过期的待审批中断摘要，供 /approval 概览展示。

        通过 ApprovalStateStorePort.list_pending_by_session 只读查询，不消费
        也不删除任何审批状态；审批状态存储未装配（approval_store is None）时
        返回空列表。
        """
        if self.approval_store is None:
            return []
        return await self.approval_store.list_pending_by_session(session_id)

    async def stream_chat(
        self,
        message: str,
        state: TuiSessionState,
    ) -> AsyncIterator[StreamingChunk]:
        """为旧调用方保留的聊天流式兼容别名。"""
        async for chunk in self.stream_main_agent(message, state):
            yield chunk

    async def clear_session(self, session_id: str) -> None:
        """清理指定会话的持久化对话上下文。"""
        await self._require_chat_service().clear_session(session_id)

    async def list_sessions(self, limit: int = 20) -> list[SessionMetadata]:
        """列出最近可恢复会话。"""
        return await self._require_session_index().list_recent(limit)

    async def resume_session(self, session_id: str) -> ResumeSessionResult:
        """校验并返回恢复会话所需信息。"""
        metadata = await self._require_session_index().get(session_id)
        if metadata is None:
            return ResumeSessionResult(
                found=False,
                missing_reason="missing_index",
            )

        if not await self._require_session_store().exists(session_id):
            try:
                await self._require_session_index().delete(session_id)
            except Exception:
                logger.warning(
                    "清理 stale session index 失败 session_id=%s",
                    session_id,
                    exc_info=True,
                )
            return ResumeSessionResult(
                found=False,
                metadata=metadata,
                missing_reason="expired_or_missing",
            )

        approval_summaries: list[ApprovalInterruptSummary] = []
        if self.approval_store is not None:
            approval_summaries = await self.approval_store.list_pending_by_session(session_id)
        return ResumeSessionResult(
            found=True,
            metadata=metadata,
            approval_summaries=approval_summaries,
        )

    async def delete_session(self, session_id: str) -> bool:
        """显式删除会话上下文、审批状态和索引；返回删除前是否存在。"""
        metadata = await self._require_session_index().get(session_id)
        existed = metadata is not None
        if not existed:
            existed = await self._require_session_store().exists(session_id)
        await self._require_chat_service().clear_session(session_id)
        return existed

    async def execute_once(self, goal: str, *, model: str | None = None) -> TaskResult:
        """执行一次非交互式任务。"""
        return await self._require_task_agent().execute(Task(goal=goal, model=model))

    async def execute_once_json(self, goal: str, *, model: str | None = None) -> ExecJsonResult:
        """执行一次非交互式任务并映射为脚本友好的 JSON 结果。"""
        result = await self.execute_once(goal, model=model)
        trace_ref: dict[str, Any] = {
            "available": bool(result.trace),
            "step_count": len(result.trace),
        }
        artifact_ref: dict[str, Any] = {"available": self.artifact_store is not None}
        return ExecJsonResult(
            status=result.status.value,
            content=result.content,
            model=result.model,
            prompt_id=result.prompt_id,
            usage=result.usage,
            latency_ms=result.latency_ms,
            terminated_reason=result.terminated_reason,
            can_continue=result.can_continue,
            approval_id=result.approval_id,
            trace_ref=trace_ref,
            artifact_ref=artifact_ref,
        )

    async def create_chat_run(self, message: str, state: TuiSessionState) -> RunSnapshot:
        """为当前 TUI 会话创建后台聊天 Run。"""
        payload = RunPayload(
            kind=RunKind.CHAT,
            session_id=state.session_id,
            chat={"message": message},
            model=state.model,
        )
        request = RunCreateRequest(
            payload=payload,
            client_request_id=self._client_request_id("chat", state=state, content=message),
            created_by="tui",
        )
        return self._remember_run(await self._require_run_service().create_run(request))

    async def create_task_run(self, goal: str, state: TuiSessionState) -> RunSnapshot:
        """为当前 TUI 会话创建后台任务 Run。"""
        payload = RunPayload(
            kind=RunKind.TASK,
            session_id=state.session_id,
            task={"goal": goal},
            model=state.model,
        )
        request = RunCreateRequest(
            payload=payload,
            client_request_id=self._client_request_id("task", state=state, content=goal),
            created_by="tui",
        )
        return self._remember_run(await self._require_run_service().create_run(request))

    async def get_run(self, run_id: str) -> RunSnapshot:
        """返回指定后台 Run 的最新快照。"""
        return self._remember_run(await self._require_run_service().get_run(run_id))

    def watch_run_events(self, run_id: str, after_cursor: int | None) -> AsyncIterator[RunEvent]:
        """从共享 Run 应用服务订阅后台 Run 事件。"""
        return self._require_run_service().stream_events(run_id, after_cursor)

    async def continue_run(self, run_id: str, model: str | None = None) -> RunSnapshot:
        """继续一个已暂停的后台 Run。"""
        return self._remember_run(await self._require_run_service().continue_run(run_id, model))

    async def resume_approval_run(
        self,
        run_id: str,
        decisions: list[ApprovalDecision],
        model: str | None = None,
    ) -> RunSnapshot:
        """恢复一个等待审批的后台 Run。"""
        return self._remember_run(
            await self._require_run_service().resume_approval_run(
                run_id,
                decisions,
                model,
            )
        )

    async def cancel_run(self, run_id: str) -> RunSnapshot:
        """请求取消一个后台 Run。"""
        return self._remember_run(await self._require_run_service().request_cancel(run_id))

    def list_known_runs(self) -> list[RunSnapshot]:
        """返回当前 TUI 运行时已见过的 Run 快照列表。"""
        return sorted(
            self._known_runs.values(),
            key=lambda snapshot: snapshot.updated_at,
            reverse=True,
        )

    def list_models(self) -> list[str]:
        """返回已注册模型的标识符列表。"""
        return [model.id for model in self._require_model_registry().list_models()]

    def default_model(self) -> str:
        """返回当前配置的默认模型。"""
        return self._require_model_registry().get_default_model()

    def workspace_hint(self) -> str:
        """返回适合展示的工作区路径提示。"""
        if self.workspace is not None and hasattr(self.workspace, "display_root_hint"):
            return str(self.workspace.display_root_hint())
        return "unknown"

    def doctor(self, state: TuiSessionState) -> DoctorResult:
        """构建最小运行时诊断快照。"""
        return DoctorResult(
            session_id=state.session_id,
            model=state.model or self.default_model(),
            agent_mode="main_agent",
            workspace=self.workspace_hint(),
        )

    async def coding_status(self, state: TuiSessionState) -> CodingStatusSnapshot:
        """构建 coding workflow `/status` 只读快照。"""
        trace = await self._load_session_trace(state.session_id)
        pending = await self.list_pending_approvals(state.session_id)
        return CodingStatusSnapshot(
            session_id=state.session_id,
            model=state.model or self.default_model(),
            workspace=self.workspace_hint(),
            pending_approval_count=len(pending),
            trace_step_count=len(trace.steps) if trace is not None else 0,
            latest_trace_kind=latest_trace_kind(trace),
        )

    async def coding_diff(self) -> CodingDiffSnapshot:
        """通过受控 git_diff 工具读取当前工作区 diff。"""
        registry = self._require_tool_registry()
        if not registry.has("git_diff"):
            return CodingDiffSnapshot(
                content="",
                available=False,
                error="git_diff 工具未注册，无法读取 diff",
            )
        try:
            result = await registry.execute(
                ToolCallRequest(
                    id="cli-git-diff",
                    name="git_diff",
                    arguments=json.dumps({"max_chars": 60000}),
                )
            )
        except Exception as exc:
            return CodingDiffSnapshot(
                content="",
                available=False,
                error=str(exc),
            )
        return CodingDiffSnapshot(
            content=result.content,
            available=True,
            truncated=bool(result.metadata.get("truncated")),
        )

    async def coding_tests(self, state: TuiSessionState) -> CodingTestsSnapshot:
        """从当前会话 trace 中提取最近测试/验证命令。"""
        return extract_test_records(await self._load_session_trace(state.session_id))

    async def coding_files(self, state: TuiSessionState) -> CodingFilesSnapshot:
        """从当前会话 trace 中提取本轮 coding workflow 触达文件。"""
        return extract_file_snapshot(await self._load_session_trace(state.session_id))

    async def _load_session_trace(self, session_id: str) -> SessionTrace | None:
        """读取当前会话 trace；trace store 未装配时返回 None。"""
        if self.trace_store is None:
            return None
        return await self.trace_store.get_session_trace(session_id)

    @staticmethod
    def _client_request_id(kind: str, *, state: TuiSessionState, content: str) -> str:
        model = state.model or ""
        digest = hashlib.sha256(
            f"{kind}\n{state.session_id}\n{model}\n{content}".encode()
        ).hexdigest()[:24]
        return f"tui:{kind}:{state.session_id}:{digest}"

    def _remember_run(self, snapshot: RunSnapshot) -> RunSnapshot:
        """缓存 Run 快照以支持 `/runs`，避免新增存储列表接口。"""
        self._known_runs[snapshot.run_id] = snapshot
        return snapshot
