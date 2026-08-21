"""Run 工作流领域模型。

本模块定义阶段六标准工作流、阶段运行状态和多 Agent 协作摘要。所有
类型均为纯领域值对象，不依赖 application、infrastructure、FastAPI、Redis
或外部 workflow runtime。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields, is_dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, cast

_STABLE_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_REQUIRED_PHASES = frozenset({"plan", "execute", "evaluate", "finalize"})


class StandardWorkflowName(StrEnum):
    """阶段六 v1 内置标准工作流名称。"""

    RESEARCH = "research"
    CODE_CHANGE = "code_change"
    REPORT = "report"
    BATCH_PROCESSING = "batch_processing"


class WorkflowPhase(StrEnum):
    """Run 层可观察工作流阶段。"""

    PLAN = "plan"
    EXECUTE = "execute"
    EVALUATE = "evaluate"
    REVISE = "revise"
    FINALIZE = "finalize"


class CollaborationAction(StrEnum):
    """多 Agent 协作动作类型。"""

    DELEGATION = "delegation"
    HANDOFF = "handoff"
    CHILD_RUN = "child_run"


class WorkflowCapabilityAction(StrEnum):
    """工作流角色能力判定的动作类型。"""

    TOOL = "tool"
    DELEGATION = "delegation"
    HANDOFF = "handoff"
    CHILD_RUN = "child_run"


@dataclass(frozen=True)
class WorkflowCapabilityCheck:
    """一次工作流角色能力判定请求。"""

    action: WorkflowCapabilityAction
    role: str | None
    target: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class WorkflowCapabilityDecision:
    """工作流角色能力判定结果。"""

    allowed: bool
    action: WorkflowCapabilityAction
    role: str | None
    target: str | None = None
    reason: str = ""


@dataclass(frozen=True)
class WorkflowApplicableCondition:
    """工作流适用条件。

    Attributes:
        run_kinds: 允许的 Run kind 字符串，空集合表示不限。
        task_classes: 允许的 guardrail task classification，空集合表示不限。
        payload_keywords: payload 文本命中关键字，空集合表示不限。
    """

    run_kinds: frozenset[str] = frozenset()
    task_classes: frozenset[str] = frozenset()
    payload_keywords: frozenset[str] = frozenset()


@dataclass(frozen=True)
class AgentRoleCapability:
    """工作流内 Agent 角色能力声明。

    默认能力采用最小权限：未显式声明的工具、委派目标、交接目标和子
    Run 创建权限均为拒绝。旧字段 ``can_delegate`` / ``can_handoff`` 保留，
    新增目标集合用于 P2 执行策略在真实动作前做确定性判定。
    """

    role: str
    agent_names: tuple[str, ...] = ()
    allowed_tool_names: frozenset[str] = frozenset()
    can_delegate: bool = False
    allowed_delegate_agents: frozenset[str] = frozenset()
    can_handoff: bool = False
    allowed_handoff_agents: frozenset[str] = frozenset()
    can_create_child_run: bool = False


@dataclass(frozen=True)
class WorkflowExecutionPolicy:
    """工作流执行策略。

    角色能力治理和 child run 编排默认关闭，确保未显式启用策略时沿用
    当前 in-run delegation / handoff 兼容行为。handoff、review 与 revise
    约束以纯数据形式表达，供应用层 orchestrator 执行，不在展示层重算。
    """

    role_capability_enabled: bool = False
    phase_handoff_required: dict[str, str] = field(default_factory=dict[str, str])
    review_required_phases: frozenset[str] = frozenset()
    revise_target_phase: dict[str, str] = field(default_factory=dict[str, str])
    child_run_enabled: bool = False

    def validate(self) -> None:
        """校验执行策略字段可稳定序列化。"""

        _validate_string_mapping(
            "execution_policy.phase_handoff_required", self.phase_handoff_required
        )
        _validate_string_frozenset(
            "execution_policy.review_required_phases",
            self.review_required_phases,
        )
        _validate_string_mapping("execution_policy.revise_target_phase", self.revise_target_phase)


@dataclass(frozen=True)
class CollaborationLimit:
    """多 Agent 协作限制策略。

    ``max_recursion_depth`` 必须与既有 ``AGENT_MAX_DELEGATION_DEPTH`` 取更
    严格值；本值对象只保存工作流侧上限，实际取最小值由接入点完成。
    """

    max_recursion_depth: int = 3
    max_parallel_delegations: int = 3
    max_handoff_count: int = 1
    max_revise_per_phase: int = 1
    max_child_runs: int = 0

    def validate(self) -> None:
        """校验协作限制值，非法时抛出 ``ValueError``。"""

        _require_non_negative_int(
            "collaboration_limit.max_recursion_depth",
            self.max_recursion_depth,
        )
        _require_positive_int(
            "collaboration_limit.max_parallel_delegations",
            self.max_parallel_delegations,
        )
        _require_non_negative_int(
            "collaboration_limit.max_handoff_count",
            self.max_handoff_count,
        )
        _require_non_negative_int(
            "collaboration_limit.max_revise_per_phase",
            self.max_revise_per_phase,
        )
        _require_non_negative_int(
            "collaboration_limit.max_child_runs",
            self.max_child_runs,
        )


@dataclass(frozen=True)
class WorkflowPhaseDefinition:
    """单个工作流阶段定义。"""

    phase: WorkflowPhase
    role: str | None = None
    max_attempts: int = 1
    summary: str = ""


@dataclass(frozen=True)
class WorkflowDefinition:
    """标准工作流定义。"""

    name: str
    description: str
    applicable: WorkflowApplicableCondition
    phases: tuple[WorkflowPhaseDefinition, ...]
    roles: tuple[AgentRoleCapability, ...]
    collaboration_limit: CollaborationLimit
    default_strategy_summary: str
    enabled: bool = True
    execution_policy: WorkflowExecutionPolicy = field(default_factory=WorkflowExecutionPolicy)

    def validate(self) -> None:
        """校验名称、必需阶段、角色引用和协作限制。

        Raises:
            ValueError: 当工作流定义内部字段不满足阶段六稳定序列化和编排
                要求时抛出。重复名称属于注册表集合级校验，不在单定义内判断。
        """

        _require_stable_name("workflow.name", self.name)
        if not self.description.strip():
            raise ValueError("workflow.description 不能为空")
        if not self.default_strategy_summary.strip():
            raise ValueError("workflow.default_strategy_summary 不能为空")
        if not self.phases:
            raise ValueError("workflow.phases 不能为空")
        if not self.roles:
            raise ValueError("workflow.roles 不能为空")

        role_names = _validate_roles(self.roles)
        phase_names = _validate_phases(self.phases, role_names)
        missing = _REQUIRED_PHASES - phase_names
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise ValueError(f"workflow.phases 缺少必需阶段: {missing_text}")

        _validate_applicable(self.applicable)
        self.collaboration_limit.validate()
        self.execution_policy.validate()


@dataclass(frozen=True)
class WorkflowPhaseRecord:
    """工作流阶段历史记录。"""

    phase: WorkflowPhase
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    summary: dict[str, Any] = field(default_factory=dict[str, Any])
    error: dict[str, Any] | None = None
    revise_count: int = 0


@dataclass(frozen=True)
class CollaborationStepTraceLink:
    """同一 Run 内的协作步骤追踪关系。"""

    link_id: str
    run_id: str
    phase: WorkflowPhase | None
    source_role: str | None
    target_role: str | None
    target_agent: str | None
    action: CollaborationAction
    task_summary: str
    result_summary: str | None
    depth: int
    created_at: datetime


@dataclass(frozen=True)
class ParentChildRunLink:
    """父子 Run 可观测关系。

    v1 定义序列化模型，但不要求把所有 delegation 改造成子 Run。
    """

    parent_run_id: str
    child_run_id: str
    role: str
    phase: WorkflowPhase
    reason: str
    created_at: datetime


@dataclass(frozen=True)
class ChildRunOrchestrationState:
    """父 Run 等待或对账 child run 的保守状态。"""

    parent_run_id: str
    child_run_id: str
    phase: WorkflowPhase
    role: str | None
    ownership_status: str
    reconciliation_status: str
    reason: str
    updated_at: datetime


@dataclass(frozen=True)
class CollaborationSummary:
    """Run 快照中的协作摘要。"""

    latest_steps: tuple[CollaborationStepTraceLink, ...] = ()
    child_links: tuple[ParentChildRunLink, ...] = ()
    delegation_count: int = 0
    handoff_count: int = 0
    max_depth_seen: int = 0
    limit_hit_reason: str | None = None


def canonicalize_collaboration_summary(
    value: CollaborationSummary | dict[str, Any] | None,
) -> dict[str, Any] | None:
    """把协作摘要归一为只包含 ``latest_steps`` 的规范字典。

    该函数用于兼容历史 ``recent_steps`` 字段：若输入同时存在
    ``latest_steps`` 与 ``recent_steps``，则以 ``latest_steps`` 为准；
    若仅存在 ``recent_steps``，则映射到 ``latest_steps``；新输出永不保留
    ``recent_steps``。
    """

    if value is None:
        return None
    if isinstance(value, CollaborationSummary):
        return _dataclass_to_json_safe_dict(value)
    payload = cast(dict[str, Any], _json_safe(value))
    raw_latest_steps = cast(object, payload.get("latest_steps"))
    if isinstance(raw_latest_steps, list):
        latest_steps = cast(list[object], raw_latest_steps)
    else:
        recent_steps = cast(object, payload.get("recent_steps"))
        latest_steps = cast(list[object], recent_steps) if isinstance(recent_steps, list) else []

    canonical = {key: item for key, item in payload.items() if key != "recent_steps"}
    canonical["latest_steps"] = latest_steps
    return canonical


@dataclass(frozen=True)
class WorkflowRunState:
    """绑定到 RunSnapshot 的工作流运行状态。"""

    workflow_name: str | None
    current_phase: WorkflowPhase | None
    phase_started_at: datetime | None
    phase_history: tuple[WorkflowPhaseRecord, ...] = ()
    phase_result_summary: dict[str, Any] | None = None
    phase_error_summary: dict[str, Any] | None = None
    revise_counts: dict[str, int] = field(default_factory=dict[str, int])
    active_role: str | None = None
    handoff_state: dict[str, Any] | None = None


def evaluate_role_capability(
    *,
    roles: tuple[AgentRoleCapability, ...],
    check: WorkflowCapabilityCheck,
) -> WorkflowCapabilityDecision:
    """按最小权限规则判定当前角色是否允许执行指定动作。

    角色未声明、动作目标未声明或能力布尔开关未开启时均默认拒绝，调用方
    可把返回的 ``reason`` 写入事件或审批元数据。该函数只使用纯领域值对象，
    不读取配置、存储或外部运行时。
    """

    role = (check.role or "").strip()
    capability = _capability_for_role(roles, role)
    if capability is None:
        return WorkflowCapabilityDecision(
            allowed=False,
            action=check.action,
            role=check.role,
            target=check.target,
            reason="role_capability_missing",
        )
    target = (check.target or "").strip()
    if check.action is WorkflowCapabilityAction.TOOL:
        allowed = bool(target and target in capability.allowed_tool_names)
        reason = "allowed" if allowed else "tool_not_allowed"
    elif check.action is WorkflowCapabilityAction.DELEGATION:
        allowed = bool(
            capability.can_delegate and target and target in capability.allowed_delegate_agents
        )
        reason = "allowed" if allowed else "delegate_agent_not_allowed"
    elif check.action is WorkflowCapabilityAction.HANDOFF:
        allowed = bool(
            capability.can_handoff and target and target in capability.allowed_handoff_agents
        )
        reason = "allowed" if allowed else "handoff_agent_not_allowed"
    elif check.action is WorkflowCapabilityAction.CHILD_RUN:
        allowed = bool(capability.can_create_child_run)
        reason = "allowed" if allowed else "child_run_not_allowed"
    else:  # pragma: no cover - StrEnum 当前已穷尽，保留防御分支。
        allowed = False
        reason = "unknown_capability_action"
    return WorkflowCapabilityDecision(
        allowed=allowed,
        action=check.action,
        role=role,
        target=check.target,
        reason=check.reason or reason,
    )


def _capability_for_role(
    roles: tuple[AgentRoleCapability, ...],
    role: str,
) -> AgentRoleCapability | None:
    """按 role 名称查找能力声明。"""

    for capability in roles:
        if capability.role == role:
            return capability
    return None


def _validate_roles(roles: tuple[AgentRoleCapability, ...]) -> frozenset[str]:
    """校验角色声明并返回角色名称集合。"""

    role_names: set[str] = set()
    for role in roles:
        _require_stable_name("workflow.roles.role", role.role)
        if role.role in role_names:
            raise ValueError(f"workflow.roles 存在重复角色: {role.role}")
        role_names.add(role.role)
        for agent_name in role.agent_names:
            _require_stable_name("workflow.roles.agent_names", agent_name)
        _validate_string_frozenset(
            "roles.allowed_tool_names",
            role.allowed_tool_names,
            stable_name=False,
        )
        _validate_string_frozenset("roles.allowed_delegate_agents", role.allowed_delegate_agents)
        _validate_string_frozenset("roles.allowed_handoff_agents", role.allowed_handoff_agents)
    return frozenset(role_names)


def _validate_phases(
    phases: tuple[WorkflowPhaseDefinition, ...],
    role_names: frozenset[str],
) -> frozenset[str]:
    """校验阶段定义并返回阶段名称集合。"""

    phase_names: set[str] = set()
    for item in phases:
        phase = _require_workflow_phase(item.phase)
        phase_names.add(phase.value)
        if item.role is not None:
            _require_stable_name("workflow.phases.role", item.role)
            if item.role not in role_names:
                raise ValueError(f"workflow.phases 引用未知 role: {item.role}")
        _require_positive_int("workflow.phases.max_attempts", item.max_attempts)
        _require_string("workflow.phases.summary", item.summary)
    return frozenset(phase_names)


def _validate_applicable(applicable: WorkflowApplicableCondition) -> None:
    """校验适用条件字段可稳定序列化。"""

    for field_name, values in (
        ("applicable.run_kinds", applicable.run_kinds),
        ("applicable.task_classes", applicable.task_classes),
        ("applicable.payload_keywords", applicable.payload_keywords),
    ):
        _validate_string_frozenset(field_name, values, stable_name=False)


def _validate_string_frozenset(
    field_name: str,
    values: object,
    *,
    stable_name: bool = True,
) -> None:
    """校验字符串 frozenset 字段，必要时要求 snake_case。"""

    if not isinstance(values, frozenset):
        raise ValueError(f"workflow.{field_name} 必须为 frozenset[str]")
    for value in cast(frozenset[object], values):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"workflow.{field_name} 包含非法空字符串")
        if stable_name:
            _require_stable_name(f"workflow.{field_name}", value)


def _validate_string_mapping(field_name: str, values: object) -> None:
    """校验字符串到字符串的策略映射字段。"""

    if not isinstance(values, dict):
        raise ValueError(f"workflow.{field_name} 必须为 dict[str, str]")
    for key, value in cast(dict[object, object], values).items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ValueError(f"workflow.{field_name} 必须为 dict[str, str]")
        _require_stable_name(f"workflow.{field_name}.key", key)
        _require_stable_name(f"workflow.{field_name}.value", value)


def _require_stable_name(field_name: str, value: object) -> None:
    """校验字段为稳定小写 snake_case 名称。"""

    if not isinstance(value, str) or not _STABLE_NAME_PATTERN.fullmatch(value):
        raise ValueError(f"{field_name} 必须为小写 snake_case 标识符")


def _require_workflow_phase(value: object) -> WorkflowPhase:
    """校验并返回工作流阶段枚举。"""

    if not isinstance(value, WorkflowPhase):
        raise ValueError("workflow.phases.phase 必须为 WorkflowPhase")
    return value


def _require_string(field_name: str, value: object) -> str:
    """校验并返回字符串字段。"""

    if not isinstance(value, str):
        raise ValueError(f"{field_name} 必须为字符串")
    return value


def _require_positive_int(field_name: str, value: object) -> None:
    """校验字段为正整数。"""

    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{field_name} 必须为正整数")


def _require_non_negative_int(field_name: str, value: object) -> None:
    """校验字段为非负整数。"""

    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError(f"{field_name} 必须为非负整数")


def _dataclass_to_json_safe_dict(value: Any) -> dict[str, Any]:
    """把 dataclass 值对象转换为 JSON-safe 字典。"""

    if not is_dataclass(value):
        raise TypeError("value 必须为 dataclass 实例")
    return {item.name: _json_safe(getattr(value, item.name)) for item in fields(value)}


def _json_safe(value: Any) -> Any:
    """把 enum、datetime、tuple、frozenset 和 dataclass 转换为 JSON-safe 值。"""

    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, datetime):
        return value.isoformat()
    if is_dataclass(value):
        return _dataclass_to_json_safe_dict(value)
    if isinstance(value, tuple):
        items = cast(tuple[object, ...], value)
        return [_json_safe(item) for item in items]
    if isinstance(value, frozenset):
        items = cast(frozenset[str], value)
        return [_json_safe(item) for item in sorted(items)]
    if isinstance(value, list):
        items = cast(list[object], value)
        return [_json_safe(item) for item in items]
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {str(key): _json_safe(item) for key, item in mapping.items()}
    return value
