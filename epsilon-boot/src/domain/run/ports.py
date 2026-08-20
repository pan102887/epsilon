"""Run 领域端口定义模块。

本模块定义后台 Run 运行时需要的存储、事件和进度回调端口。端口只使用
Protocol 表达能力边界，由基础设施层提供本地文件、Redis 或其他适配器实现。
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Protocol

from domain.run.value_objects import (
    CheckpointRetentionPolicy,
    DurableCheckpoint,
    EventRetentionPolicy,
    RunCreateRequest,
    RunEvent,
    RunEventType,
    RunSnapshot,
    RunStatus,
    ToolReplayPolicy,
    ToolResultLedgerEntry,
    ToolSideEffectLevel,
)
from domain.run.workflow import WorkflowDefinition

if TYPE_CHECKING:
    from domain.chat.context import ConversationContext
    from domain.model_access.value_objects import ToolCallRequest


@dataclass(frozen=True)
class ApprovalResumeStoreResult:
    """审批恢复结果在 Run 存储层的状态变更指令。

    Attributes:
        status: 审批恢复后同一 Run 的目标状态，queued 表示重新入队，
            awaiting_approval 表示恢复执行后再次命中审批。
        approval_id: 再次进入 awaiting_approval 时的新审批标识。
        result: queued、awaiting_approval、succeeded 或 cancelled 状态下保存的结果摘要。
        error: failed 状态下保存的错误摘要。
        terminal_reason: 终态原因，或重新入队时用于审计的恢复原因。
        guardrail_summary: 审批恢复后最新的护栏摘要。
        workflow_run_state: 审批恢复后最新的工作流运行状态。
        collaboration_summary: 审批恢复后最新的协作摘要。
    """

    status: Literal["queued", "awaiting_approval", "succeeded", "failed", "cancelled"]
    approval_id: str | None = None
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    terminal_reason: str | None = None
    guardrail_summary: dict[str, Any] | None = None
    workflow_run_state: dict[str, Any] | None = None
    collaboration_summary: dict[str, Any] | None = None


@dataclass(frozen=True)
class WorkflowSelection:
    """工作流选择结果。

    Attributes:
        workflow: 命中的工作流定义；未匹配且非显式请求时为 None。
        explicit: 是否来自调用方显式 workflow 参数。
        reason: 选择或跳过的安全原因摘要。
    """

    workflow: WorkflowDefinition | None
    explicit: bool
    reason: str


class WorkflowRegistryPort(Protocol):
    """工作流定义注册表端口。"""

    def list_definitions(self) -> list[WorkflowDefinition]:
        """返回所有启用或可诊断的工作流定义。"""
        ...

    def get_definition(self, name: str) -> WorkflowDefinition | None:
        """按稳定名称查询工作流定义。"""
        ...

    def require_definition(self, name: str) -> WorkflowDefinition:
        """按名称查询工作流定义，不存在时抛业务错误。"""
        ...


class WorkflowSelectorPort(Protocol):
    """工作流选择端口。"""

    def select(self, request: RunCreateRequest) -> WorkflowSelection:
        """根据显式参数、task_classification 与 payload 选择工作流。"""
        ...


class RunObservationStorePort(Protocol):
    """在同一原子区内追加运行时事件并更新快照摘要字段。"""

    async def record_runtime_observation(
        self,
        *,
        run_id: str,
        owner_id: str,
        event_type: RunEventType,
        payload: dict[str, Any],
        guardrail_summary: dict[str, Any] | None = None,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> tuple[RunSnapshot, RunEvent]:
        """原子追加事件并更新快照的 guardrail/workflow/collaboration 摘要。"""
        ...


class RunStorePort(Protocol):
    """后台 Run 快照与控制状态存储端口。"""

    async def create_run(self, request: RunCreateRequest) -> RunSnapshot:
        """创建 queued Run 快照，或按幂等键返回既有快照。"""
        ...

    async def get_run(self, run_id: str) -> RunSnapshot | None:
        """按 run_id 查询最新 Run 快照。"""
        ...

    async def get_by_client_request_id(self, client_request_id: str) -> RunSnapshot | None:
        """按客户端幂等键查询既有 Run 快照。"""
        ...

    async def count_by_status(self, statuses: Collection[RunStatus]) -> int:
        """统计指定状态集合中的 Run 数量。"""
        ...

    async def claim_next(self, *, owner_id: str, lease_seconds: int) -> RunSnapshot | None:
        """原子领取下一个 queued Run 并写入 worker 租约。"""
        ...

    async def refresh_lease(self, *, run_id: str, owner_id: str, lease_seconds: int) -> RunSnapshot:
        """刷新指定 owner 持有的 Run 租约。"""
        ...

    async def acquire_approval_resume_lease(
        self, *, run_id: str, owner_id: str, lease_seconds: int
    ) -> RunSnapshot:
        """为 awaiting_approval 审批恢复建立短生命周期观察写入租约。"""
        ...

    async def release_approval_resume_lease(self, *, run_id: str, owner_id: str) -> RunSnapshot:
        """释放仍由当前审批恢复 owner 持有的短租约。"""
        ...

    async def request_cancel(self, run_id: str) -> RunSnapshot:
        """请求取消 Run，并按状态机写入取消目标状态。"""
        ...

    async def mark_succeeded(
        self,
        *,
        run_id: str,
        owner_id: str,
        result: dict[str, Any],
        guardrail_summary: dict[str, Any] | None = None,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        """将 Run 标记为成功终态。"""
        ...

    async def mark_failed(
        self,
        *,
        run_id: str,
        owner_id: str,
        error: dict[str, Any],
        guardrail_summary: dict[str, Any] | None = None,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        """将 Run 标记为失败终态。"""
        ...

    async def mark_paused(
        self,
        *,
        run_id: str,
        owner_id: str,
        result: dict[str, Any],
        guardrail_summary: dict[str, Any] | None = None,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        """将 Run 标记为可继续暂停状态。"""
        ...

    async def mark_awaiting_approval(
        self,
        *,
        run_id: str,
        owner_id: str,
        approval_id: str,
        result: dict[str, Any],
        guardrail_summary: dict[str, Any] | None = None,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        """将 Run 标记为等待人工审批状态。"""
        ...

    async def mark_cancelled(
        self,
        *,
        run_id: str,
        owner_id: str,
        reason: str,
        guardrail_summary: dict[str, Any] | None = None,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        """将 Run 标记为取消终态。"""
        ...

    async def resolve_approval_resume(
        self,
        *,
        run_id: str,
        owner_id: str,
        result: ApprovalResumeStoreResult,
        guardrail_summary: dict[str, Any] | None = None,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        """校验审批恢复 owner 后原子完成入队或终态迁移。"""
        ...

    async def enqueue_continue(self, *, run_id: str, model: str | None = None) -> RunSnapshot:
        """将 paused 或审批恢复后的 Run 重新入队。"""
        ...

    async def list_expired_leased_runs(self, *, now: datetime) -> list[RunSnapshot]:
        """列出当前租约已过期、需要恢复评估的 Run 快照。"""
        ...

    async def enqueue_recovery(
        self,
        *,
        run_id: str,
        latest_checkpoint_id: str,
        recovery_attempt_count: int,
        guardrail_summary: dict[str, Any] | None = None,
        workflow_run_state: dict[str, Any] | None = None,
        collaboration_summary: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        """将满足恢复条件的 Run 重新入队并记录恢复元数据。"""
        ...

    async def mark_lost_expired_run(
        self,
        *,
        run_id: str,
        reason: str,
        recovery_error: dict[str, Any] | None = None,
    ) -> RunSnapshot:
        """将单个无法自动恢复的过期 Run 标记为 lost。"""
        ...

    async def mark_lost_expired_leases(self, *, now: datetime) -> list[RunSnapshot]:
        """扫描过期租约并将不可确认命运的 Run 标记为 lost。"""
        ...


class RunCheckpointStorePort(Protocol):
    """后台 Run 检查点与工具结果账本存储端口。"""

    async def save_checkpoint(self, checkpoint: DurableCheckpoint) -> DurableCheckpoint:
        """保存检查点并保证同一 run_id 内 sequence 单调。"""
        ...

    async def latest_checkpoint(self, run_id: str) -> DurableCheckpoint | None:
        """读取指定 Run 的最新兼容检查点。"""
        ...

    async def list_checkpoints(
        self, run_id: str, after_sequence: int | None, limit: int
    ) -> list[DurableCheckpoint]:
        """按 sequence 列出指定 Run 的检查点。"""
        ...

    async def put_tool_pending(self, entry: ToolResultLedgerEntry) -> ToolResultLedgerEntry:
        """在工具实际执行前写入 pending 账本记录。"""
        ...

    async def complete_tool_result(
        self,
        *,
        run_id: str,
        tool_execution_key: str,
        result: str,
        is_error: bool,
        metadata: dict[str, Any],
    ) -> ToolResultLedgerEntry:
        """将工具账本记录更新为 completed 或 error。"""
        ...

    async def get_tool_result(
        self, run_id: str, tool_execution_key: str
    ) -> ToolResultLedgerEntry | None:
        """按稳定工具执行键读取工具结果账本记录。"""
        ...

    async def list_tool_ledger(self, run_id: str) -> list[ToolResultLedgerEntry]:
        """列出指定 Run 的全部工具结果账本记录。"""
        ...

    async def trim_checkpoints(self, run_id: str, policy: CheckpointRetentionPolicy) -> None:
        """按保留策略裁剪检查点与工具账本。"""
        ...


class RunCheckpointSinkPort(Protocol):
    """Agent 执行边界向 checkpoint runtime 报告进度的端口。"""

    async def model_completed(
        self,
        *,
        context: ConversationContext,
        round_num: int,
        usage: dict[str, int],
        trace_summary: dict[str, Any],
        segment_metadata: dict[str, Any],
    ) -> DurableCheckpoint:
        """模型调用完成、工具执行前保存上下文检查点。"""
        ...

    async def before_tool_call(
        self,
        *,
        tool_call: ToolCallRequest,
        round_num: int,
        segment_index: int,
        replay_policy: ToolReplayPolicy,
        side_effect_level: ToolSideEffectLevel,
        idempotency_key: str | None,
    ) -> ToolResultLedgerEntry | None:
        """工具执行前写入 pending，或返回可复用的 completed 结果。"""
        ...

    async def after_tool_call(
        self,
        *,
        context: ConversationContext,
        tool_execution_key: str,
        result: str,
        is_error: bool,
        metadata: dict[str, Any],
        round_num: int,
        usage: dict[str, int],
    ) -> DurableCheckpoint:
        """工具执行完成后保存账本结果与最新上下文检查点。"""
        ...

    async def approval_interrupt(
        self,
        *,
        context: ConversationContext,
        round_num: int,
        usage: dict[str, int],
        approval_id: str,
    ) -> DurableCheckpoint:
        """进入人工审批前保存审批中断检查点。"""
        ...

    async def segment_done(
        self,
        *,
        context: ConversationContext,
        segment_metadata: dict[str, Any],
        usage: dict[str, int],
    ) -> DurableCheckpoint:
        """执行段完成后保存分段元数据检查点。"""
        ...


class RunEventStorePort(Protocol):
    """后台 Run 事件追加与 replay 查询端口。"""

    async def append_event(
        self, run_id: str, event_type: RunEventType, payload: dict[str, Any]
    ) -> RunEvent:
        """追加单条 Run 事件并分配单调递增 cursor。"""
        ...

    async def list_events(
        self, run_id: str, after_cursor: int | None, limit: int
    ) -> list[RunEvent]:
        """按 cursor 查询 Run 事件历史。"""
        ...

    async def wait_events(
        self, run_id: str, after_cursor: int | None, timeout_seconds: float
    ) -> list[RunEvent]:
        """等待并返回 after_cursor 之后的新事件。"""
        ...

    async def trim_events(self, run_id: str, policy: EventRetentionPolicy) -> None:
        """按事件保留策略裁剪历史事件。"""
        ...

    async def first_cursor(self, run_id: str) -> int | None:
        """返回当前保留窗口内最早事件 cursor。"""
        ...


class RunProgressSink(Protocol):
    """Run 执行协调器向事件系统报告分段进度的端口。"""

    async def segment_started(self, run_id: str, segment_index: int) -> None:
        """报告某个分段开始执行。"""
        ...

    async def segment_done(self, run_id: str, metadata: dict[str, Any]) -> None:
        """报告某个分段执行完成及其元数据。"""
        ...
