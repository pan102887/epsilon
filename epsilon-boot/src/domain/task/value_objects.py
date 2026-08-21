"""任务领域值对象模块。

本模块定义面向任务的 Agent 入口所需的不可变值对象，包括：

- TaskStatus：任务执行状态枚举，表示成功、失败、暂停或需要人工介入
- Task：任务值对象，封装一次 Agent 执行的完整任务定义
- TraceEntry：执行轨迹条目值对象，记录 Agent 执行过程中的单步操作
- TaskResult：任务执行结果值对象，封装 Agent 执行任务后的结构化结果

所有值对象均使用 frozen dataclass 定义，确保不可变性。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from domain.agent.segmented_execution import SegmentRunMetadata
from domain.agent.value_objects import AgentTerminationReason, ApprovalDecision

_PROMPT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9\-]*@v[1-9]\d*$")
"""合法 ``prompt_id`` 正则：小写+连字符的 name + ``@`` + ``v<正整数>``。

用于 :class:`TaskResult` 的 ``__post_init__`` 校验，与其他领域层值对象
（``domain/prompt/value_objects.py`` / ``domain/agent/value_objects.py`` /
``domain/chat/value_objects.py``）保持同义。
"""


def _input_dict() -> dict[str, Any]:
    return {}


def _constraint_list() -> list[str]:
    return []


def _usage_dict() -> dict[str, int]:
    return {}


def _trace_list() -> list[TraceEntry]:
    return []


class TaskStatus(Enum):
    """任务执行状态枚举。

    定义 Agent 执行任务后的可能状态，调用方根据状态进行分支处理。

    Members:
        SUCCESS: 任务执行成功，content 包含执行结果
        FAILED: 任务执行失败，content 包含错误信息
        PAUSED: 任务命中阶段边界暂停，可在满足前置条件时继续
        HUMAN_INTERVENTION_REQUIRED: 需要人工介入，content 包含原因说明
    """

    SUCCESS = "success"
    FAILED = "failed"
    PAUSED = "paused"
    HUMAN_INTERVENTION_REQUIRED = "human_intervention_required"


@dataclass(frozen=True)
class Task:
    """任务值对象。

    封装一次 Agent 执行的完整任务定义，包含目标描述、输入数据、约束条件和期望输出格式。
    使用 frozen dataclass 确保不可变性。

    Attributes:
        goal: 任务目标描述，不可为空或纯空白字符
        input_data: 输入数据字典，默认空字典
        constraints: 约束条件列表，默认空列表
        output_format: 期望输出格式描述，默认 None
        model: 可选模型名称，默认 None（使用系统默认模型）
        session_id: 可选会话标识，默认 None（不关联对话上下文）
        tool_names: 可选工具名称子集，默认 None（使用全量工具）
        delegation_depth: 委派深度，默认 0 表示根 Agent 执行（无委派），
            每次委派子任务时 depth + 1，不可为负数
    """

    goal: str
    input_data: dict[str, Any] = field(default_factory=_input_dict)
    constraints: list[str] = field(default_factory=_constraint_list)
    output_format: str | None = None
    model: str | None = None
    session_id: str | None = None
    tool_names: frozenset[str] | None = None
    delegation_depth: int = 0

    def __post_init__(self) -> None:
        """校验任务参数的合法性。

        Raises:
            ValueError: 当 goal 为空字符串或纯空白字符时抛出
            ValueError: 当 delegation_depth 为负数时抛出
        """
        if not self.goal or not self.goal.strip():
            raise ValueError("goal 不能为空或纯空白字符")
        if self.delegation_depth < 0:
            raise ValueError(f"delegation_depth 不能为负数，当前值: {self.delegation_depth}")


@dataclass(frozen=True)
class TraceEntry:
    """执行轨迹条目值对象。

    记录 Agent 执行过程中的单步操作，用于事后审查执行轨迹和排查问题。

    Attributes:
        step: 步骤序号，从 1 开始
        action: 操作类型，如 "tool_call"、"tool_result"、"llm_response"
        detail: 操作详情描述
        timestamp_ms: 时间戳（毫秒）
    """

    step: int
    action: str
    detail: str
    timestamp_ms: float


@dataclass(frozen=True)
class TaskApprovalResumeRequest:
    """任务审批恢复请求值对象。

    封装任务路径在人工审批通过后恢复执行所需的最小输入，
    保持与既有聊天审批恢复请求的领域建模风格一致。

    Attributes:
        session_id: 任务会话唯一标识符，不可为空。
        approval_id: 待恢复的审批批次标识，不可为空。
        decisions: 与待审批动作顺序一致的审批决策元组。
        model: 可选模型名称，未指定时使用系统默认模型。
    """

    session_id: str
    approval_id: str
    decisions: tuple[ApprovalDecision, ...]
    model: str | None = None

    def __post_init__(self) -> None:
        """校验审批恢复请求标识字段。"""
        if not self.session_id:
            raise ValueError("session_id 不能为空")
        if not self.approval_id:
            raise ValueError("approval_id 不能为空")


@dataclass(frozen=True)
class TaskResult:
    """任务执行结果值对象。

    封装 Agent 执行任务后的结构化结果，包含执行状态、执行轨迹和 token 用量。

    **决策 #3**：``prompt_id`` 字段位于带默认值字段之前，保持无默认值必填；
    所有 ``TaskStatus`` 分支（SUCCESS / FAILED / PAUSED / HUMAN_INTERVENTION_REQUIRED）
    均必须由 ``TaskAgentAdapter.execute`` 显式透传
    ``self._task_template_prompt_id``；违反即构造失败。

    Attributes:
        content: 执行结果内容（成功时为 Agent 回复，失败时为错误信息）
        status: 执行状态枚举
        model: 实际使用的模型名称
        prompt_id: 本任务使用的 Prompt 标识符（形如 ``task-template@v1``）；
            必填，对所有 ``TaskStatus`` 分支均强校验非空且格式合法。
        usage: token 用量，默认空字典
        trace: 执行轨迹列表，默认空列表
        latency_ms: 总执行耗时（毫秒），默认 0.0
        terminated_reason: Agent 运行终止原因，默认 completed
        can_continue: 是否可基于当前任务上下文继续执行
        approval_id: 等待人工审批时的审批批次标识，非审批态下为 None
    """

    content: str
    status: TaskStatus
    model: str
    prompt_id: str
    usage: dict[str, int] = field(default_factory=_usage_dict)
    trace: list[TraceEntry] = field(default_factory=_trace_list)
    latency_ms: float = 0.0
    terminated_reason: AgentTerminationReason = "completed"
    can_continue: bool = False
    segment_metadata: SegmentRunMetadata = field(default_factory=SegmentRunMetadata)
    approval_id: str | None = None

    def __post_init__(self) -> None:
        """fail-fast 校验 ``prompt_id`` 非空且符合 ``name@v<N>`` 格式。

        所有 ``TaskStatus`` 分支（SUCCESS / FAILED / PAUSED / HUMAN_INTERVENTION_REQUIRED）
        均必须显式透传由 ``TaskAgentAdapter._task_template_prompt_id`` 缓存的
        非空值；违反即构造失败，与决策 #3 对齐。

        Raises:
            ValueError: 当 ``prompt_id`` 为空或不符合 ``name@v<N>`` 格式时抛出；
                错误消息指明所有 ``TaskStatus`` 分支均需透传。
        """
        if not self.prompt_id or not _PROMPT_ID_PATTERN.match(self.prompt_id):
            raise ValueError(
                "prompt_id 非法；所有 TaskStatus 分支（SUCCESS / FAILED / "
                "PAUSED / HUMAN_INTERVENTION_REQUIRED）均需透传合法 'name@v<N>' 值，"
                f"当前值: {self.prompt_id!r}"
            )


@dataclass(frozen=True)
class TaskContinueRequest:
    """任务继续请求值对象。

    封装客户端基于已有任务会话上下文继续 Agent 执行的请求。继续请求
    不携带原始任务目标，避免把“继续执行”误建模为新的用户消息。

    Attributes:
        session_id: 任务会话唯一标识符，不可为空。
        model: 可选模型名称，未指定时使用系统默认模型。

    Raises:
        ValueError: 当 session_id 为空时抛出。
    """

    session_id: str
    model: str | None = None

    def __post_init__(self) -> None:
        """校验继续请求标识字段。"""
        if not self.session_id:
            raise ValueError("session_id 不能为空")
