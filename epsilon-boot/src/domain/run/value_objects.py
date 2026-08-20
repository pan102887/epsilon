"""Run 领域值对象定义模块。

本模块只描述后台长任务运行时的纯领域数据结构，不依赖应用层、
基础设施层或任何 Web/存储框架。值对象用于应用服务、存储端口、
worker 与 adapter 之间传递稳定的运行快照、事件和容量策略。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any


class RunStatus(StrEnum):
    """后台 Run 的生命周期状态枚举。"""

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    AWAITING_APPROVAL = "awaiting_approval"
    CANCEL_REQUESTED = "cancel_requested"
    CANCELLED = "cancelled"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    LOST = "lost"


class RunKind(StrEnum):
    """后台 Run 的业务入口类型。"""

    CHAT = "chat"
    TASK = "task"


class RunEventType(StrEnum):
    """后台 Run 事件流中的事件类型。"""

    RUN_CREATED = "run_created"
    RUN_QUEUED = "run_queued"
    RUN_CLAIMED = "run_claimed"
    RUN_HEARTBEAT = "run_heartbeat"
    SEGMENT_STARTED = "segment_started"
    SEGMENT_DONE = "segment_done"
    RUN_PAUSED = "run_paused"
    APPROVAL_REQUIRED = "approval_required"
    CANCEL_REQUESTED = "cancel_requested"
    RUN_CANCELLED = "run_cancelled"
    RUN_SUCCEEDED = "run_succeeded"
    RUN_FAILED = "run_failed"
    RUN_LOST = "run_lost"
    REPLAY_EXPIRED = "replay_expired"
    CHECKPOINT_SAVED = "checkpoint_saved"
    RUN_RECOVERY_QUEUED = "run_recovery_queued"
    RUN_RECOVERY_FAILED = "run_recovery_failed"
    TOOL_RESULT_REPLAYED = "tool_result_replayed"
    TASK_CLASSIFIED = "task_classified"
    GUARDRAIL_EVALUATED = "guardrail_evaluated"
    GUARDRAIL_BLOCKED = "guardrail_blocked"
    ROLE_CAPABILITY_REJECTED = "role_capability_rejected"
    WORKFLOW_SELECTED = "workflow_selected"
    WORKFLOW_SELECTION_SKIPPED = "workflow_selection_skipped"
    WORKFLOW_PHASE_STARTED = "workflow_phase_started"
    WORKFLOW_PHASE_COMPLETED = "workflow_phase_completed"
    WORKFLOW_PHASE_FAILED = "workflow_phase_failed"
    WORKFLOW_HANDOFF_RECORDED = "workflow_handoff_recorded"
    COLLABORATION_STEP_RECORDED = "collaboration_step_recorded"
    COLLABORATION_LIMIT_HIT = "collaboration_limit_hit"
    CHILD_RUN_LINKED = "child_run_linked"
    CHILD_RUN_WAITING = "child_run_waiting"
    CHILD_RUN_RECONCILED = "child_run_reconciled"


class CheckpointPhase(StrEnum):
    """持久化检查点保存时所处的执行阶段。"""

    RUN_CREATED = "run_created"
    MODEL_COMPLETED = "model_completed"
    TOOL_PENDING = "tool_pending"
    TOOL_COMPLETED = "tool_completed"
    APPROVAL_INTERRUPT = "approval_interrupt"
    SEGMENT_DONE = "segment_done"
    RECOVERY_QUEUED = "recovery_queued"


class ToolLedgerStatus(StrEnum):
    """工具结果账本中的持久化状态。"""

    PENDING = "pending"
    COMPLETED = "completed"
    ERROR = "error"


class ToolReplayPolicy(StrEnum):
    """恢复时对工具调用的重放策略。"""

    REPLAY_RESULT = "replay_result"
    REQUIRE_IDEMPOTENCY_KEY = "require_idempotency_key"
    MANUAL_REVIEW = "manual_review"
    NEVER_REPLAY = "never_replay"


class ToolSideEffectLevel(StrEnum):
    """工具副作用等级，用于恢复时做保守判定。"""

    NONE = "none"
    LOCAL_WRITE = "local_write"
    EXTERNAL_WRITE = "external_write"
    IRREVERSIBLE = "irreversible"


def _json_safe(value: Any) -> Any:
    """把 dataclass、枚举和时间转换为可稳定 JSON 编码的值。"""

    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    return value


@dataclass(frozen=True)
class RunPayload:
    """创建和继续 Run 所需的业务载荷。

    Attributes:
        kind: Run 的业务入口类型，仅允许聊天或任务。
        session_id: 与已有聊天/任务上下文关联的会话标识。
        chat: 聊天入口保存的请求上下文。
        task: 任务入口保存的请求上下文。
        model: 可选模型名称。
    """

    kind: RunKind
    session_id: str | None
    chat: dict[str, Any] | None = None
    task: dict[str, Any] | None = None
    model: str | None = None

    def stable_hash(self) -> str:
        """返回忽略 JSON key 顺序的稳定 SHA-256 摘要。

        该摘要用于 `client_request_id` 的幂等冲突检测，不把原始 payload
        写入异常消息，避免泄露完整用户输入或工具边界内容。
        """

        encoded = json.dumps(
            _json_safe(asdict(self)),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class RunCreateRequest:
    """创建后台 Run 的领域请求。"""

    payload: RunPayload
    client_request_id: str | None
    payload_hash: str | None = None
    created_by: str | None = None
    task_classification: str | None = None
    guardrail_summary: dict[str, Any] | None = None
    workflow_name: str | None = None
    workflow_run_state: dict[str, Any] | None = None
    collaboration_summary: dict[str, Any] | None = None

    def effective_payload_hash(self) -> str:
        """返回显式 payload_hash 或根据 payload 计算出的稳定摘要。"""

        return self.payload_hash or self.payload.stable_hash()


@dataclass(frozen=True)
class RunLease:
    """后台 worker 对 Run 的有时限执行权。"""

    owner_id: str
    lease_until: datetime
    heartbeat_at: datetime


@dataclass(frozen=True)
class RunSnapshot:
    """后台 Run 的最新可查询快照。

    快照包含状态、幂等信息、结果摘要、错误摘要、审批信息、分段元数据、
    最新事件游标、租约和版本号，供轮询查询与事件流降级展示使用。
    """

    run_id: str
    kind: RunKind
    status: RunStatus
    payload: RunPayload
    client_request_id: str | None
    payload_hash: str | None
    result: dict[str, Any] | None
    error: dict[str, Any] | None
    approval_id: str | None
    segment_metadata: dict[str, Any] | None
    latest_event_cursor: int | None
    can_continue: bool
    terminal_reason: str | None
    lease: RunLease | None
    created_at: datetime
    updated_at: datetime
    version: int
    latest_checkpoint_id: str | None = None
    recoverable: bool = False
    recovery_attempt_count: int = 0
    last_recovery_error: dict[str, Any] | None = None
    task_classification: str | None = None
    guardrail_summary: dict[str, Any] | None = None
    workflow_name: str | None = None
    workflow_run_state: dict[str, Any] | None = None
    collaboration_summary: dict[str, Any] | None = None


@dataclass(frozen=True)
class DurableCheckpoint:
    """可用于中断恢复的后台 Run 检查点。"""

    run_id: str
    checkpoint_id: str
    sequence: int
    phase: CheckpointPhase
    context_snapshot: dict[str, Any]
    round_num: int | None
    usage: dict[str, int]
    trace_summary: dict[str, Any]
    segment_metadata: dict[str, Any]
    tool_execution_key: str | None
    tool_result_ref: str | None
    schema_version: int
    sanitized: bool
    truncated_fields: tuple[str, ...]
    created_at: datetime


@dataclass(frozen=True)
class ToolExecutionKey:
    """标识一次逻辑工具调用的稳定键组成字段。"""

    run_id: str
    segment_index: int
    round_num: int
    tool_call_id: str
    tool_name: str
    arguments_digest: str

    def stable_key(self) -> str:
        """返回同一逻辑工具调用的稳定 SHA-256 key。"""

        encoded = "\n".join(
            (
                self.run_id,
                str(self.segment_index),
                str(self.round_num),
                self.tool_call_id,
                self.tool_name,
                self.arguments_digest,
            )
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def digest_arguments(arguments: str) -> str:
        """返回 JSON 参数的规范化 SHA-256 摘要。"""

        try:
            normalized = json.dumps(
                json.loads(arguments),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        except json.JSONDecodeError:
            normalized = arguments
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ToolResultLedgerEntry:
    """工具结果账本记录，用于恢复时避免重复执行。"""

    run_id: str
    tool_execution_key: str
    status: ToolLedgerStatus
    tool_name: str
    tool_call_id: str
    arguments_digest: str
    replay_policy: ToolReplayPolicy
    side_effect_level: ToolSideEffectLevel
    idempotency_key: str | None
    result: str | None
    is_error: bool
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class CheckpointRetentionPolicy:
    """检查点和工具账本的保留策略。"""

    max_checkpoint_count: int
    ttl_seconds: int
    max_payload_bytes: int
    max_tool_ledger_count: int


@dataclass(frozen=True)
class RecoveryDecision:
    """租约过期扫描对单个 Run 得出的恢复决策。"""

    recoverable: bool
    reason: str
    checkpoint_id: str | None = None
    error: dict[str, Any] | None = None


@dataclass(frozen=True)
class RunEvent:
    """后台 Run 生命周期中的一条可订阅事件。"""

    run_id: str
    cursor: int
    event_type: RunEventType
    payload: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class RunCapacityPolicy:
    """后台 Run 队列与并发容量策略。"""

    max_queued_runs: int
    max_running_runs: int


@dataclass(frozen=True)
class EventRetentionPolicy:
    """Run 事件历史保留策略。"""

    max_event_count: int
    ttl_seconds: int
