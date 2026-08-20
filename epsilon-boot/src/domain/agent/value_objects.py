"""Agent 值对象模块。

本模块定义 Agent 抽象层所需的不可变值对象，包括：

- AgentConfig：Agent 执行配置值对象，封装单次 Agent 执行所需的全部配置参数
- AgentResult：Agent 同步执行结果值对象，封装 Agent 执行完成后的返回数据
- 审批相关值对象：封装 HITL 工具审批策略、中断状态与恢复决策
- NamedAgentConfig：命名 Agent 配置值对象，封装一个命名 Agent 的完整定义
- DelegationResult：委派结果值对象，封装委派执行的结果内容和成功/失败状态

所有值对象均使用 frozen dataclass 定义，确保不可变性。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from domain.agent.exceptions import InvalidApprovalActionError

_PROMPT_ID_PATTERN = re.compile(r"^[a-z][a-z0-9\-]*@v[1-9]\d*$")
"""合法 ``prompt_id`` 正则：小写+连字符的 name + ``@`` + ``v<正整数>``。

用于 :class:`AgentConfig` 与 :class:`NamedAgentConfig` 的 ``__post_init__``
校验，保持与 ``domain/prompt/value_objects.py`` 中同名常量语义一致。
"""


@dataclass(frozen=True, kw_only=True)
class AgentConfig:
    """Agent 执行配置值对象。

    封装单次 Agent 执行所需的全部配置参数，由编排层在每次请求时构造。
    使用 ``frozen=True`` 确保不可变性；使用 ``kw_only=True`` 强制所有字段
    采用关键字参数传入，从而允许新增的必填字段 ``prompt_id`` 与既有的
    带默认值字段 ``allowed_tool_names`` 并存（dataclass"默认字段必须后置"
    规则在 kw_only 语义下被绕过）。

    Attributes:
        system_prompt: 系统提示词，per-Agent 独立；由 ReActAgentAdapter 在首轮前
            以幂等方式注入到 ConversationContext 中（已存在 SystemMessage 则跳过）
        tool_schemas: 工具 schema 列表，格式为 OpenAI function calling schema
        model: 可选的模型名称，None 时使用默认模型
        max_rounds: Agent Loop 最大迭代轮次，必须 > 0
        prompt_id: 本次调用使用的 Prompt 标识符，形如 ``chat-default@v3``；
            由 Prompt 消费方从 ``LoadedPrompt.prompt_id`` 直接赋值；
            无默认值，未显式传入即构造失败（``TypeError``）。
        allowed_tool_names: 允许调用的工具名称集合，默认从 tool_schemas 自动提取
    """

    system_prompt: str
    tool_schemas: list[dict[str, Any]]
    model: str | None
    max_rounds: int
    prompt_id: str
    allowed_tool_names: frozenset[str] = field(default=frozenset())
    tool_timeout_seconds: float | None = None
    """工具执行超时（秒）全局默认。

    - ``None``：不引入 ``asyncio.wait_for`` 包裹（沿用 v2 行为）；
    - ``> 0``：所有未在 ``Tool.timeout_seconds`` 中显式覆盖的工具均按此超时
      上限执行；超时后视为 ``is_error=True`` 且 ``ToolMessage.metadata["error"]``
      为 ``True``，回灌内容为 ``"工具执行超时（{N}s)"``。

    覆盖优先级：``Tool.timeout_seconds`` > 本字段 > ``None``（不超时）。
    """

    max_total_tokens: int | None = None
    """累计 token 预算上限（v3 新增）。

    - ``None``：不引入预算检查（沿用 v2 行为）；
    - ``> 0``：每轮模型调用后通过 ``Token_Budget_Computation_Rule`` 计算的
      累计 token 用量超过本字段值时，本轮结束后立即终止，不再发起更多模型
      调用，``AgentResult.terminated_reason == "token_budget_exceeded"``，
      调用方据此决策是否升档预算续跑或告知用户。

    与 ``max_rounds`` 共存：先命中者优先终止，``Token_Budget_Exceeded_Warning``
    与 ``Max_Rounds_Termination_Warning`` 在同一执行内**互斥**。
    """

    def __post_init__(self) -> None:
        """校验配置参数的合法性，自动提取 allowed_tool_names 默认值。

        当 allowed_tool_names 为空 frozenset 且 tool_schemas 非空时，
        从 tool_schemas 中自动提取工具名称。

        Raises:
            ValueError: 当 max_rounds 小于等于 0 时抛出
            ValueError: 当 prompt_id 不符合 ``name@v<N>`` 格式时抛出
            ValueError: 当 ``tool_timeout_seconds`` 非 None 且 ``<= 0`` 时抛出
            ValueError: 当 ``max_total_tokens`` 非 None 且 ``<= 0`` 时抛出
        """
        if self.max_rounds <= 0:
            raise ValueError(f"max_rounds 必须大于 0，当前值: {self.max_rounds}")
        if not _PROMPT_ID_PATTERN.match(self.prompt_id):
            raise ValueError(f"prompt_id 非法，期望形如 'name@v<N>'，当前值: {self.prompt_id!r}")
        if self.tool_timeout_seconds is not None and self.tool_timeout_seconds <= 0:
            raise ValueError("tool_timeout_seconds 必须大于 0")
        if self.max_total_tokens is not None and self.max_total_tokens <= 0:
            raise ValueError("max_total_tokens 必须大于 0")

        # 自动提取默认值：当 allowed_tool_names 为空且 tool_schemas 非空时
        if not self.allowed_tool_names and self.tool_schemas:
            names = frozenset(
                schema["function"]["name"]
                for schema in self.tool_schemas
                if "function" in schema and "name" in schema["function"]
            )
            # frozen dataclass 需要使用 object.__setattr__
            object.__setattr__(self, "allowed_tool_names", names)


ApprovalDecisionType = Literal["approve", "edit", "reject"]
"""审批决策类型。

``approve`` 表示按原始工具调用执行；``edit`` 表示使用人工编辑后的参数
执行；``reject`` 表示拒绝工具调用。
"""


AgentRunStatus = Literal["completed", "approval_required"]
"""Agent 同步运行状态。"""


AgentTerminationReason = Literal["completed", "max_rounds", "token_budget_exceeded"]
"""Agent 运行终止原因。

刻画"为何停止"，与 :data:`AgentRunStatus`（``"completed"`` /
``"approval_required"``）正交：``status="approval_required"`` 时
``terminated_reason`` 保持 ``"completed"``（HITL 中断不属于"轮数超限"，
由 ``status`` 单独表达）。

取值：

- ``"completed"``：模型自然给出最终回复，或工具调用循环正常收尾。
- ``"max_rounds"``：循环达到 ``config.max_rounds`` 上限时最后一轮仍返回
  ``tool_calls``、工具已被执行但模型尚未对工具结果给出最终回复。调用方
  （顶层编排 / 自主续跑循环）应据此决策续跑或终止。
- ``"token_budget_exceeded"``（v3 新增）：循环累计 ``usage`` 达到
  ``config.max_total_tokens`` 上限时，本轮结束后立即终止，不再发起更多
  模型调用。具体判定规则见 ``Token_Budget_Computation_Rule``：优先取
  ``total_usage["total_tokens"]``，缺失时回退到
  ``total_usage["prompt_tokens"] + total_usage["completion_tokens"]``。
  调用方应据此决策是否升档预算续跑或告知用户。

本类型对齐 OpenAI Assistants（``incomplete_details.reason``）、LangGraph
（``GraphRecursionError``）、CrewAI（``max_iter`` failed）、AutoGPT 等业内
主流 Agent 框架的"暴露超限信号、不做内部补救"共识。
"""


@dataclass(frozen=True, slots=True)
class ApprovalInterruptSummary:
    """用于会话恢复提示的审批中断摘要。

    摘要只包含 TUI 恢复会话时展示 pending approval 所需的轻量字段，
    不包含上下文快照、工具参数等较大或敏感载荷。

    Attributes:
        session_id: 审批中断所属会话 ID。
        approval_id: 审批批次 ID。
        action_count: 待审批动作数量，必须为非负整数。
        created_at_epoch: 审批中断创建时间戳（秒）。
        expires_at_epoch: 审批中断过期时间戳（秒）。
        expired: 该摘要在读取时是否已过期。
        tool_names: 待审批动作涉及的工具名称集合。
    """

    session_id: str
    approval_id: str
    action_count: int
    created_at_epoch: float
    expires_at_epoch: float
    expired: bool
    tool_names: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """校验审批中断摘要字段。"""
        if not self.session_id:
            raise ValueError("session_id 不能为空")
        if not self.approval_id:
            raise ValueError("approval_id 不能为空")
        if type(self.action_count) is not int:
            raise ValueError("action_count 必须为 int")
        if self.action_count < 0:
            raise ValueError("action_count 必须为非负整数")
        if isinstance(self.created_at_epoch, bool) or not isinstance(
            self.created_at_epoch, (int, float)
        ):
            raise ValueError("created_at_epoch 必须为数字")
        if isinstance(self.expires_at_epoch, bool) or not isinstance(
            self.expires_at_epoch, (int, float)
        ):
            raise ValueError("expires_at_epoch 必须为数字")
        if not isinstance(self.expired, bool):
            raise ValueError("expired 必须为 bool")


@dataclass(frozen=True)
class ApprovalPolicy:
    """工具审批策略值对象。

    Attributes:
        tool_name: 工具名称
        interrupt: 是否在执行该工具前中断等待人工审批
        allowed_decisions: 该工具允许的审批决策集合
        risk_label: 面向审计和展示的风险说明
    """

    tool_name: str
    interrupt: bool
    allowed_decisions: frozenset[ApprovalDecisionType]
    risk_label: str = ""


@dataclass(frozen=True)
class PendingActionRequest:
    """待审批工具动作值对象。

    Attributes:
        tool_call_id: 模型返回的工具调用 ID
        tool_name: 工具名称
        arguments: 原始工具参数 JSON 字符串
        allowed_decisions: 当前动作允许的审批决策集合
        reason: 触发审批的可读原因

    Raises:
        InvalidApprovalActionError: 当 ``tool_call_id`` 为 ``None`` 或
            空字符串时抛出，由 ``__post_init__`` 前置校验拦截，避免错误
            延迟到 ``react_agent_adapter.py`` 重新构造 ``ToolCallRequest``
            的位置（详见 ``domain/agent/exceptions.py`` /
            design §审批前置校验改造）。
    """

    tool_call_id: str
    tool_name: str
    arguments: str
    allowed_decisions: frozenset[ApprovalDecisionType]
    reason: str = ""

    def __post_init__(self) -> None:
        """前置校验 ``tool_call_id`` 非空。

        Raises:
            InvalidApprovalActionError: 见类 docstring。
        """
        if not self.tool_call_id:
            raise InvalidApprovalActionError(
                value_object="PendingActionRequest",
                field="tool_call_id",
                raw_value=self.tool_call_id,
                tool_name=self.tool_name or None,
            )


@dataclass(frozen=True)
class EditedAction:
    """人工编辑后的工具动作。

    Attributes:
        name: 编辑后的工具名称；恢复时必须与原工具名一致
        arguments: 编辑后的工具参数 JSON 字符串
    """

    name: str
    arguments: str


@dataclass(frozen=True)
class ApprovalDecision:
    """审批恢复决策值对象。

    Attributes:
        type: 决策类型
        tool_call_id: 决策对应的工具调用 ID
        edited_action: ``edit`` 决策对应的编辑后动作
        message: ``reject`` 决策携带的人工说明

    Raises:
        InvalidApprovalActionError: 当 ``tool_call_id`` 为 ``None`` 或
            空字符串时抛出，由 ``__post_init__`` 前置校验拦截。错误
            暴露在 application 层入口构造时，而非延迟到
            ``react_agent_adapter.py`` 适配器内部（详见
            design §审批前置校验改造）。
    """

    type: ApprovalDecisionType
    tool_call_id: str
    edited_action: EditedAction | None = None
    message: str = ""

    def __post_init__(self) -> None:
        """前置校验 ``tool_call_id`` 非空。

        Raises:
            InvalidApprovalActionError: 见类 docstring。
        """
        if not self.tool_call_id:
            raise InvalidApprovalActionError(
                value_object="ApprovalDecision",
                field="tool_call_id",
                raw_value=self.tool_call_id,
            )


@dataclass(frozen=True)
class ApprovalInterrupt:
    """Agent 工具审批中断状态。

    该对象保存恢复 ReAct Loop 所需的最小状态：会话、审批批次、待审批动作、
    上下文快照、轮次、模型和累计用量。基础设施层可将该对象序列化到 file
    或 redis，领域层不感知具体存储。
    """

    session_id: str
    approval_id: str
    actions: tuple[PendingActionRequest, ...]
    context_snapshot: dict[str, Any]
    round_num: int
    model: str
    usage_so_far: dict[str, int] = field(default_factory=dict)
    created_at_epoch: float = 0.0
    expires_at_epoch: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def is_expired(self, now_epoch: float) -> bool:
        """判断审批中断在给定时间点是否已过期。

        Args:
            now_epoch: 当前 Unix 时间戳，单位秒。

        Returns:
            当 ``expires_at_epoch`` 大于 0 且 ``now_epoch`` 已达到或超过过期
            时间时返回 True；否则返回 False。
        """
        return self.expires_at_epoch > 0 and now_epoch >= self.expires_at_epoch


@dataclass(frozen=True)
class ApprovalRequiredPayload:
    """Agent 返回给上层编排的审批中断载荷。

    Attributes:
        session_id: 会话 ID
        approval_id: 审批批次 ID
        actions: 待审批动作列表，顺序与模型 tool_calls 一致
        prompt_id: 本次调用使用的 Prompt 标识
        metadata: 面向上层协议转换的附加元数据
    """

    session_id: str
    approval_id: str
    actions: tuple[PendingActionRequest, ...]
    prompt_id: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ApprovalResume:
    """审批恢复请求值对象。

    Attributes:
        session_id: 会话 ID
        approval_id: 审批批次 ID
        decisions: 按待审批动作顺序排列的审批决策
        model: 可选的恢复执行模型覆盖值
    """

    session_id: str
    approval_id: str
    decisions: tuple[ApprovalDecision, ...]
    model: str | None = None


@dataclass(frozen=True)
class AgentResult:
    """Agent 同步执行结果值对象。

    封装 Agent 执行完成后的返回数据。

    Attributes:
        content: 最终回复文本内容。``terminated_reason="max_rounds"`` 时
            通常为空字符串（最后一轮 tool_calls 响应的 ``content`` 通常为空）。
        model: 实际使用的模型名称
        usage: 所有轮次累计的 token 用量
        latency_ms: 最后一轮的请求延迟（毫秒）
        status: Agent 运行状态，默认 completed 以兼容既有构造
        approval: 审批中断载荷；仅在 status 为 approval_required 时使用
        terminated_reason: Agent 运行终止原因，默认 ``"completed"``。
            ``"max_rounds"`` 表示循环达到 ``config.max_rounds`` 上限时
            最后一轮仍返回 ``tool_calls``、工具已被执行但模型尚未对
            工具结果给出最终回复；调用方（顶层编排 / 自主续跑循环）
            应据此决策续跑或终止。该字段与 ``status`` 正交：
            ``status="approval_required"`` 时 ``terminated_reason``
            保持 ``"completed"``（HITL 中断由 ``status`` 单独表达，
            不属于"轮数超限"）。
    """

    content: str
    model: str
    usage: dict[str, int] = field(default_factory=dict)
    latency_ms: float = 0.0
    status: AgentRunStatus = "completed"
    approval: ApprovalRequiredPayload | None = None
    terminated_reason: AgentTerminationReason = "completed"


AgentStreamEventKind = Literal[
    "status",
    # 累加文本片段：v3 起 ReAct 内部全程 stream，每个 ``assistant_delta`` 为
    # 真分片（非整段）。客户端必须按累加方式渲染，不要假设每个分片都是单字符
    # 或固定长度。
    "assistant_delta",
    "assistant_done",
    "tool_start",
    "tool_result",
    "tool_error",
    "approval_required",
    "error",
    # v3 新增：最后一轮 stream 阶段，工具调用 ``arguments`` JSON 真分片。
    # 由 ``run_events`` / ``_stream_events_final_round`` 在 SDK 流式 delta
    # 上观察到 ``chunk.tool_calls`` 中间分片时逐片产出；``content`` 恒为
    # 空串，``usage`` 恒为 ``None``。中间轮次累积期间**不**产出此 kind
    # （决策 7 约束累积期间不对外发事件）。同一 ``tool_call_id`` 的多个
    # delta 严格按 SDK 产出顺序到达，``tool_call_id`` / ``tool_name`` 仅
    # 首个 delta 携带非 ``None``。
    "tool_arguments_delta",
]


@dataclass(frozen=True)
class AgentStreamEvent:
    """Structured event emitted by interactive Agent streaming.

    ``StreamingChunk`` remains the compatibility text stream. This value object
    carries the richer lifecycle needed by terminal UIs: tool calls, status
    updates, final usage, and errors.
    """

    kind: AgentStreamEventKind
    content: str = ""
    tool_name: str | None = None
    tool_call_id: str | None = None
    arguments: str | None = None
    usage: dict[str, int] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NamedAgentConfig:
    """命名 Agent 配置值对象。

    封装一个命名 Agent 的完整定义，包括名称、描述、系统提示词、
    对应 Prompt 标识符、可用工具子集和模型选择。

    **决策 #1**：本类**不**启用 ``kw_only=True``，``prompt_id`` 字段以
    ``str = ""`` 默认空串形式追加在原带默认字段（``tool_names`` /
    ``model``）之前；``__post_init__`` 对空串 / 格式非法均 fail-fast，
    与 :class:`AgentConfig` 一致的 ``^[a-z][a-z0-9\\-]*@v[1-9]\\d*$`` 校验。

    Attributes:
        name: Agent 唯一标识名称，不可为空或纯空白
        description: Agent 职责和能力描述，不可为空或纯空白
        system_prompt: 系统提示词
        prompt_id: 本 Agent 使用的 Prompt 标识符，形如 ``<prompt-name>@v<N>``；
            默认空串仅用于兼容 dataclass "有默认字段必须排在无默认字段之后"
            的规则，``__post_init__`` 对空串与格式非法均 fail-fast。
        tool_names: 可用工具名称子集，None 表示使用全量工具
        model: 使用的模型名称，None 表示使用系统默认模型
    """

    name: str
    description: str
    system_prompt: str
    prompt_id: str = ""
    tool_names: frozenset[str] | None = None
    model: str | None = None

    def __post_init__(self) -> None:
        """校验 name、description、prompt_id 字段合法性。

        Raises:
            ValueError: 当 name 或 description 为空字符串或纯空白字符时抛出
            ValueError: 当 prompt_id 为空或不符合 ``name@v<N>`` 格式时抛出
        """
        if not self.name or not self.name.strip():
            raise ValueError("name 不能为空或纯空白字符")
        if not self.description or not self.description.strip():
            raise ValueError("description 不能为空或纯空白字符")
        if not self.prompt_id or not _PROMPT_ID_PATTERN.match(self.prompt_id):
            raise ValueError(f"prompt_id 非法，期望形如 'name@v<N>'，当前值: {self.prompt_id!r}")


@dataclass(frozen=True)
class DelegationResult:
    """委派结果值对象。

    封装委派执行的结果内容和成功/失败状态。
    使用 frozen dataclass 确保不可变性。

    Attributes:
        content: 结果内容（成功时为 Agent 回复，失败时为错误信息）
        success: 执行是否成功
    """

    content: str
    success: bool


@dataclass(frozen=True)
class DelegationRequest:
    """单条委派请求值对象（用于 ``DelegationPort.delegate_parallel``）。

    把 ``delegate(...)`` 的位置参数集合化为一个不可变值对象，便于将多条
    并行委派请求作为列表传入。

    Attributes:
        agent_name: 目标 Agent 唯一标识名称。
        task_goal: 子任务目标描述。
        input_data: 可选附加输入数据；默认空 dict。
    """

    agent_name: str
    task_goal: str
    input_data: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class HandoffResult:
    """Handoff（控制转移）结果值对象。

    与 ``DelegationResult`` 的关键差异：handoff 是"控制转移"语义，目标 Agent
    的最终回复将直接成为父 Agent 的最终回复（``AgentResult.content``），
    因此除了 ``content`` / ``success`` 还需要附带 ``target_agent`` /
    ``usage`` / ``model`` 元数据，供 Agent Loop 完成 ``AgentResult`` 翻译。

    Attributes:
        target_agent: 接管控制权的目标 Agent 名称。
        content: 目标 Agent 的最终回复文本。
        success: 控制转移是否成功完成（目标 Agent 自然终止）。
        usage: 目标 Agent 累计 token 用量；用于父侧 Agent Loop 的 usage 透传。
        model: 目标 Agent 实际使用的模型名称。
    """

    target_agent: str
    content: str
    success: bool
    usage: dict[str, int] = field(default_factory=dict)
    model: str = ""
