"""Agent 端口定义。

定义 Agent 抽象层的端口接口（Port），遵循六边形架构原则。

- AgentPort：描述"接收任务、自主执行、返回结果"的统一接口，
  支持同步和流式两种执行模式，由基础设施层提供具体的适配器实现。
- ApprovalPolicyPort：描述按工具名查询审批策略的能力。
- ApprovalStateStorePort：描述审批中断状态的保存、加载、消费与删除能力。
- AgentRegistryPort：命名 Agent 配置的注册、查找和列举能力。
- DelegationPort：委派能力抽象，将子任务委派给指定命名 Agent 执行并返回结果。
- RunGuardrailRecorderPort：把 guardrail 运行时观测写入 Run 事件与摘要的能力。
- TraceStorePort：结构化 Agent 追踪的持久化能力（以 StorageTier 为定位维度之一）。
- ArtifactStorePort：任务产物记录的持久化与查询能力（以 StorageTier 为定位维度之一）。
- ModelRoundResult：单轮模型调用结果值对象。
- AgentLoopEffects：Agent Loop 编排主体所需的副作用端口协议。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

# StorageTier 作为 Port 方法的 keyword-only 默认参数值（StorageTier.PROJECT），
# 属运行期需要求值的对象，因此在运行期导入；domain.storage 为同层领域子包
# （仅依赖标准库），不构成对 infrastructure 的反向依赖，也不引入循环导入。
# 而 ArtifactTrace 仅用于类型标注，仍置于下方 TYPE_CHECKING 块。
from domain.storage.storage_tier import StorageTier

if TYPE_CHECKING:
    from domain.agent.guardrails import (
        GuardrailDecision,
        GuardrailEvaluationContext,
        GuardrailObservation,
        TaskExecutionClass,
    )
    from domain.agent.trace_value_objects import (
        AgentStepTrace,
        ArtifactTrace,
        SessionTrace,
    )
    from domain.agent.value_objects import (
        AgentConfig,
        AgentResult,
        AgentStreamEvent,
        ApprovalDecision,
        ApprovalInterrupt,
        ApprovalInterruptSummary,
        ApprovalPolicy,
        ApprovalRequiredPayload,
        DelegationRequest,
        DelegationResult,
        HandoffResult,
        NamedAgentConfig,
        PendingActionRequest,
    )
    from domain.chat.context import BaseMessage, ConversationContext
    from domain.model_access.ports import ModelAccessPort
    from domain.model_access.value_objects import LLMResponse, StreamingChunk, ToolCallRequest
    from domain.run.value_objects import RunSnapshot


class AgentPort(Protocol):
    """Agent 端口协议。

    定义"接收任务、自主执行、返回结果"的统一接口。
    支持同步和流式两种执行模式。

    实现者负责执行 Agent Loop（推理→行动→观察循环），
    并在执行过程中原地修改传入的 ConversationContext。
    """

    async def run(
        self,
        context: ConversationContext,
        config: AgentConfig,
        model_access: ModelAccessPort,
    ) -> AgentResult:
        """同步执行 Agent Loop。

        循环调用 LLM 并执行工具，直到获得纯文本回复或达到最大轮次。
        执行过程中原地修改 context（追加 AssistantMessage 和 ToolMessage）。

        Args:
            context: 对话上下文，会被原地修改
            config: Agent 执行配置
            model_access: 模型访问端口实例

        Returns:
            AgentResult，包含最终回复和累计 token 用量
        """
        ...

    def run_streaming(
        self,
        context: ConversationContext,
        config: AgentConfig,
        model_access: ModelAccessPort,
    ) -> AsyncIterator[StreamingChunk]:
        """流式执行 Agent Loop。

        中间轮次使用同步调用执行工具，最终轮次以流式方式产出分片。
        执行过程中原地修改 context（追加 AssistantMessage 和 ToolMessage）。

        Args:
            context: 对话上下文，会被原地修改
            config: Agent 执行配置
            model_access: 模型访问端口实例

        Yields:
            StreamingChunk 分片
        """
        ...

    def run_events(
        self,
        context: ConversationContext,
        config: AgentConfig,
        model_access: ModelAccessPort,
    ) -> AsyncIterator[AgentStreamEvent]:
        """Stream structured Agent lifecycle events.

        This richer stream is intended for interactive clients that need to
        render tool calls and status separately from assistant text. Existing
        text-only callers should keep using ``run_streaming``.
        """
        ...

    async def resume(
        self,
        context: ConversationContext,
        config: AgentConfig,
        model_access: ModelAccessPort,
        interrupt: ApprovalInterrupt,
        decisions: tuple[ApprovalDecision, ...],
    ) -> AgentResult:
        """从审批中断点恢复执行 Agent Loop。

        Args:
            context: 从审批中断快照恢复的对话上下文
            config: Agent 执行配置
            model_access: 模型访问端口实例
            interrupt: 已加载并被应用层消费的审批中断状态
            decisions: 与待审批动作顺序一致的审批决策

        Returns:
            恢复执行后的 AgentResult，可能完成或再次进入 approval_required。
        """
        ...


class ApprovalPolicyPort(Protocol):
    """审批策略端口协议。

    领域层通过该协议按工具名查询运行期审批策略，具体配置解析由基础设施层实现。
    """

    def policy_for(self, tool_name: str) -> ApprovalPolicy:
        """返回指定工具的审批策略。

        Args:
            tool_name: 工具名称

        Returns:
            ApprovalPolicy；未配置工具应返回 interrupt=False 的策略。
        """
        ...


class AgentGuardrailPolicyPort(Protocol):
    """Agent 护栏策略端口协议。"""

    def classify_payload(self, payload: Any, *, has_tools: bool) -> TaskExecutionClass:
        """根据 payload 和工具可用性确定任务类型。"""
        ...

    def evaluate_run_start(
        self,
        context: GuardrailEvaluationContext,
    ) -> GuardrailDecision:
        """Run 或执行段开始前评估。"""
        ...

    def evaluate_model_completed(
        self,
        context: GuardrailEvaluationContext,
    ) -> GuardrailDecision:
        """模型调用完成后评估。"""
        ...

    def evaluate_tool_before_execution(
        self,
        context: GuardrailEvaluationContext,
    ) -> GuardrailDecision:
        """工具执行前评估。"""
        ...

    def evaluate_tool_after_execution(
        self,
        context: GuardrailEvaluationContext,
    ) -> GuardrailDecision:
        """工具执行后评估。"""
        ...


class ApprovalStateStorePort(Protocol):
    """审批状态存储端口协议。

    定义审批中断状态的持久化、读取、原子消费和清理能力。
    """

    async def save(self, interrupt: ApprovalInterrupt) -> None:
        """保存审批中断状态。"""
        ...

    async def load(
        self,
        session_id: str,
        approval_id: str,
    ) -> ApprovalInterrupt | None:
        """读取审批中断状态；不存在或不可用时返回 None。"""
        ...

    async def consume(
        self,
        session_id: str,
        approval_id: str,
    ) -> ApprovalInterrupt | None:
        """原子消费审批中断状态；已消费或不存在时返回 None。"""
        ...

    async def delete(self, session_id: str, approval_id: str) -> None:
        """幂等删除指定审批中断状态。"""
        ...

    async def delete_session(self, session_id: str) -> None:
        """幂等删除指定会话下的全部审批中断状态。"""
        ...

    async def list_pending_by_session(
        self,
        session_id: str,
    ) -> list[ApprovalInterruptSummary]:
        """列出指定会话未过期的审批中断摘要。

        Args:
            session_id: 会话唯一标识符。

        Returns:
            未过期审批中断摘要列表。实现不得消费或删除未过期审批状态。
        """
        ...


class RunGuardrailRecorderPort(Protocol):
    """把 guardrail 观测写入 Run 事件与摘要。"""

    async def record_observation(
        self,
        *,
        observation: GuardrailObservation,
    ) -> RunSnapshot | None:
        """在存在 Run 执行上下文时记录一次 guardrail 观测；非 Run 路径返回 None。"""
        ...


class AgentRegistryPort(Protocol):
    """Agent 注册表端口协议。

    定义命名 Agent 配置的注册、查找和列举能力。
    类似 ToolRegistry 管理 Tool 实例的模式，AgentRegistryPort 集中管理
    命名 Agent 的配置信息，每个命名 Agent 由唯一名称标识。

    遵循六边形架构原则，领域层仅定义协议接口，
    由基础设施层提供具体的适配器实现。
    """

    def register(self, config: NamedAgentConfig) -> None:
        """注册一个命名 Agent 配置。

        按 config.name 存入注册表，同名 Agent 重复注册时覆盖。

        Args:
            config: 命名 Agent 配置值对象
        """
        ...

    def get(self, name: str) -> NamedAgentConfig | None:
        """按名称查找已注册的命名 Agent 配置。

        Args:
            name: Agent 唯一标识名称

        Returns:
            对应的 NamedAgentConfig 实例，未找到时返回 None
        """
        ...

    def has(self, name: str) -> bool:
        """判断指定名称的 Agent 是否已注册。

        Args:
            name: Agent 唯一标识名称

        Returns:
            已注册返回 True，否则返回 False
        """
        ...

    def list_names(self) -> list[str]:
        """返回所有已注册 Agent 的名称列表。

        Returns:
            已注册 Agent 名称的列表
        """
        ...


class DelegationPort(Protocol):
    """委派能力端口协议。

    定义"将子任务委派给指定命名 Agent 执行并返回结果"的能力边界。
    遵循六边形架构原则，领域层仅定义协议接口，
    由基础设施层提供具体的适配器实现（DelegationAdapter）。

    该协议将委派的业务语义与基础设施实现解耦，
    调用方无需感知 TaskAgentPort、Task 构造等底层细节。
    """

    async def delegate(
        self,
        agent_name: str,
        task_goal: str,
        input_data: dict[str, Any] | None = None,
        delegation_depth: int = 0,
        max_delegation_depth: int = 3,
    ) -> DelegationResult:
        """将子任务委派给指定命名 Agent 执行。

        通过 agent_name 定位目标 Agent，将 task_goal 和可选的 input_data
        封装为子任务交由该 Agent 执行，并返回封装后的委派结果。

        Args:
            agent_name: 目标 Agent 的唯一标识名称，必须已在 AgentRegistryPort 中注册
            task_goal: 子任务的目标描述，作为 Agent 执行的指令输入
            input_data: 可选的附加输入数据字典，默认为 None（适配器内部转换为空字典）
            delegation_depth: 当前委派深度，用于递归委派时的深度追踪，默认为 0
            max_delegation_depth: 最大允许委派深度，超过此深度的委派将被拒绝，默认为 3

        Returns:
            DelegationResult 值对象，包含执行结果内容（content）和成功/失败状态（success）。
            成功时 content 为 Agent 的回复文本，失败时 content 为错误信息。

        Raises:
            AgentNotFoundError: 当 agent_name 对应的 Agent 未在注册表中注册时抛出
        """
        ...

    async def delegate_parallel(
        self,
        requests: list[DelegationRequest],
        delegation_depth: int = 0,
        max_delegation_depth: int = 3,
    ) -> list[DelegationResult]:
        """并行将多个子任务委派给指定命名 Agent 执行。

        遵循"错误隔离"语义：单条委派失败（包含目标 Agent 未注册、内部异常等）
        只影响对应位置的 ``DelegationResult.success=False``，其余条目继续
        执行；本方法不抛 ``AgentNotFoundError`` 或子任务执行异常，所有失败
        以 ``DelegationResult(success=False, content=<错误描述>)`` 形式返回。

        Args:
            requests: 待并行执行的委派请求列表，按需求顺序排列。
            delegation_depth: 当前委派深度，每个子任务统一在内部 +1 后再校验。
            max_delegation_depth: 最大允许委派深度。

        Returns:
            ``list[DelegationResult]``，长度与 ``requests`` 一致，顺序一一对应。
        """
        ...

    async def handoff(
        self,
        agent_name: str,
        context_messages: list[BaseMessage],
        delegation_depth: int = 0,
        max_delegation_depth: int = 3,
    ) -> HandoffResult:
        """把当前 Agent 的控制权完全转移给指定命名 Agent（OpenAI Agents SDK 风格）。

        与 :meth:`delegate` 的区别：``delegate`` 是"工具委派"，子 Agent 的结果
        以 ``ToolMessage`` 形式回灌给父 Agent 继续推理；``handoff`` 是"控制
        转移"，子 Agent 的最终回复**直接成为父 Agent 的最终回复**，父 Agent
        不再发起新一轮 LLM 调用。

        实现侧需要把 ``context_messages`` 作为目标 Agent 的初始上下文（不再注入
        额外的 user message），以"原样转交"的语义保留父侧对话记录。

        Args:
            agent_name: 目标 Agent 唯一标识名称。
            context_messages: 父 Agent 当前 ``ConversationContext`` 消息快照；
                目标 Agent 将基于此快照独立运行 ReAct Loop。
            delegation_depth: 当前委派深度。
            max_delegation_depth: 最大允许委派深度。

        Returns:
            ``HandoffResult``，含目标 Agent 名称、最终回复、成功标志、累计 usage 与模型名。

        Raises:
            AgentNotFoundError: 当 ``agent_name`` 未注册。
            DelegationDepthExceededError:
                当 ``delegation_depth + 1`` 超过 ``max_delegation_depth``。
        """
        ...


class TraceStorePort(Protocol):
    """结构化 Agent 追踪存储端口。

    定义 Agent 执行步骤追踪的持久化操作接口，由 Infrastructure 层提供具体实现。
    支持追加步骤、获取完整会话 trace 和列举最近 trace 三种操作。

    每个方法均以 ``tier`` 为定位维度之一（keyword-only、默认 ``StorageTier.PROJECT``），
    由基础设施层的解析器映射到具体后端/目录。既有不传 ``tier`` 的调用点取默认
    PROJECT tier，行为与引入本抽象前完全一致。
    """

    async def append_step(
        self,
        session_id: str,
        step: AgentStepTrace,
        *,
        tier: StorageTier = StorageTier.PROJECT,
    ) -> None:
        """追加一步到指定 session trace。

        Args:
            session_id: 会话唯一标识符
            step: Agent 步骤追踪对象
            tier: 存储等级定位维度，默认 PROJECT（兼容既有调用点）
        """
        ...

    async def get_session_trace(
        self,
        session_id: str,
        *,
        tier: StorageTier = StorageTier.PROJECT,
    ) -> SessionTrace | None:
        """获取完整 session trace。

        Args:
            session_id: 会话唯一标识符
            tier: 存储等级定位维度，默认 PROJECT（兼容既有调用点）

        Returns:
            SessionTrace 对象；不存在时返回 None。
        """
        ...

    async def list_traces(
        self,
        limit: int = 20,
        *,
        tier: StorageTier = StorageTier.PROJECT,
    ) -> list[SessionTrace]:
        """按时间倒序列出最近的 session trace 摘要。

        Args:
            limit: 最大返回条数
            tier: 存储等级定位维度，默认 PROJECT（兼容既有调用点）

        Returns:
            SessionTrace 列表（仅含元数据和 step_count，不含完整 steps）。
        """
        ...


class ArtifactStorePort(Protocol):
    """任务产物存储端口。

    定义 ArtifactTrace 的持久化与查询能力，``tier`` 作为定位维度之一
    （keyword-only、默认 ``StorageTier.PROJECT``）。由基础设施层提供本地文件
    后端与（未来）对象存储实现；写入方在 Port 为 None 时静默跳过、零行为变化。
    """

    async def append_artifact(
        self,
        session_id: str,
        artifact: ArtifactTrace,
        *,
        tier: StorageTier = StorageTier.PROJECT,
    ) -> None:
        """追加一条产物记录到对应 tier 的 Artifacts_Dir。

        Args:
            session_id: 会话唯一标识符
            artifact: 产物追踪值对象
            tier: 存储等级定位维度，默认 PROJECT

        IO 失败时须隔离故障（记录 warning 而不中断主流程）。
        """
        ...

    async def list_artifacts(
        self,
        session_id: str,
        *,
        tier: StorageTier = StorageTier.PROJECT,
    ) -> list[ArtifactTrace]:
        """列出指定会话已记录的产物。

        Args:
            session_id: 会话唯一标识符
            tier: 存储等级定位维度，默认 PROJECT

        Returns:
            ArtifactTrace 列表；不存在或读取失败时返回空列表。
        """
        ...


# ──────────────────────────────────────────────────────────────────────────────
# P2 第二片 Wave 2：Agent Loop 编排端口
# ──────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ModelRoundResult:
    """单轮模型调用结果值对象。

    由 ``AgentLoopEffects.perform_model_round`` 返回，承载模型响应与截至
    本轮结束时的累计 token 用量。将模型调用的基础设施细节（OTel span、
    stream 累加器、guardrail 统计）封装在端口实现内部，领域编排主体
    仅消费纯数据。

    Attributes:
        response: 本轮 LLM 响应。
        total_usage: 截至本轮结束时的累计 token 用量字典。
    """

    response: LLMResponse
    total_usage: dict[str, int]


class AgentLoopEffects(Protocol):
    """Agent Loop 编排主体所需的副作用端口协议。

    ``AgentLoopOrchestrator.iter_rounds`` 通过本协议与基础设施层交互，
    将模型调用、checkpoint、审批中断等副作用委托给实现方（``ReActAgentAdapter``）。
    领域编排主体零 OTel / infrastructure / 框架依赖。
    """

    async def prepare_runtime(
        self,
        context: ConversationContext,
        config: AgentConfig,
        *,
        preserve_guardrail_runtime: bool,
    ) -> None:
        """准备运行时环境：guardrail 累加器重置、abuse detector 重置、system prompt 注入。

        Args:
            context: 对话上下文（可能被原地追加 SystemMessage）。
            config: Agent 执行配置。
            preserve_guardrail_runtime: 是否保留当前 guardrail 统计累加器（审批恢复场景）。
        """
        ...

    async def perform_model_round(
        self,
        context: ConversationContext,
        config: AgentConfig,
        model_access: ModelAccessPort,
        *,
        round_num: int,
        total_usage: dict[str, int],
    ) -> ModelRoundResult:
        """执行单轮模型调用并返回结果。

        实现方在内部完成：context_builder.build → stream 累加 → OTel span
        关闭 → guardrail model_completed 评估。span 关闭后才返回，规避
        yield/contextvars 冲突。

        Args:
            context: 对话上下文。
            config: Agent 执行配置。
            model_access: 模型访问端口。
            round_num: 当前轮次号。
            total_usage: 进入本轮前的累计 token 用量（会被合并）。

        Returns:
            ``ModelRoundResult``，包含本轮 LLM 响应和合并后的累计用量。
        """
        ...

    def record_assistant_with_tool_calls(
        self,
        context: ConversationContext,
        response: LLMResponse,
    ) -> int:
        """将携带 tool_calls 的模型响应追加到上下文并返回消息索引。

        Args:
            context: 对话上下文，原地修改。
            response: 当前轮次模型响应。

        Returns:
            追加后的 AssistantMessage 在 ``context.get_messages()`` 中的索引。
        """
        ...

    def resolve_approval_policies(
        self,
        tool_calls: tuple[ToolCallRequest, ...],
        config: AgentConfig,
    ) -> Mapping[str, ApprovalPolicy]:
        """为 tool_calls 解析审批策略映射（含 warning 日志）。

        Args:
            tool_calls: 模型返回的工具调用序列。
            config: Agent 执行配置。

        Returns:
            工具名 → ApprovalPolicy 的映射。
        """
        ...

    async def save_interrupt(
        self,
        context: ConversationContext,
        config: AgentConfig,
        actions: tuple[PendingActionRequest, ...],
        round_num: int,
        model: str,
        usage_so_far: dict[str, int],
    ) -> ApprovalRequiredPayload:
        """创建并保存审批中断状态。

        Args:
            context: 对话上下文。
            config: Agent 执行配置。
            actions: 待审批动作序列。
            round_num: 当前轮次号。
            model: 模型名称。
            usage_so_far: 截至当前的累计 token 用量。

        Returns:
            审批载荷，供 ``RoundOutcome(kind="approval")`` 携带。
        """
        ...

    async def prepare_tool_calls_for_execution(
        self,
        context: ConversationContext,
        config: AgentConfig,
        tool_calls: tuple[ToolCallRequest, ...],
        round_num: int,
        model: str,
        usage_so_far: dict[str, int],
    ) -> tuple[tuple[ToolCallRequest, ...], ApprovalRequiredPayload | None]:
        """按原始工具顺序执行 guardrail 前置评估并筛选可执行工具。

        Args:
            context: 对话上下文。
            config: Agent 执行配置。
            tool_calls: 模型返回的工具调用序列。
            round_num: 当前轮次号。
            model: 模型名称。
            usage_so_far: 截至当前的累计 token 用量。

        Returns:
            (可执行工具调用元组, 审批载荷或 None)。当 guardrail 触发审批时
            第二个元素非 None。
        """
        ...

    async def checkpoint_model_completed(
        self,
        context: ConversationContext,
        round_num: int,
        total_usage: dict[str, int],
        response: LLMResponse,
    ) -> None:
        """模型调用完成后的 checkpoint 写入。

        Args:
            context: 对话上下文。
            round_num: 当前轮次号。
            total_usage: 截至当前的累计 token 用量。
            response: 本轮模型响应（用于提取 model、tool_call_count 等元数据）。
        """
        ...

    async def checkpoint_approval_interrupt(
        self,
        context: ConversationContext,
        round_num: int,
        total_usage: dict[str, int],
        approval_id: str,
    ) -> None:
        """审批中断时的 checkpoint 写入。

        Args:
            context: 对话上下文。
            round_num: 当前轮次号。
            total_usage: 截至当前的累计 token 用量。
            approval_id: 审批标识。
        """
        ...

    def record_terminated(
        self,
        reason: str,
        round_num: int,
        total_usage: dict[str, int],
        config: AgentConfig,
        *,
        tool_call_count: int = 0,
        handoff_target: str = "",
    ) -> None:
        """记录 Agent Loop 终止：OTel span + 日志。

        Args:
            reason: 终止原因（``"token_budget_exceeded"`` / ``"max_rounds"`` / ``"handoff"``）。
            round_num: 终止时的轮次号。
            total_usage: 累计 token 用量。
            config: Agent 执行配置（用于日志中的 max_total_tokens）。
            tool_call_count: 终止时未消费的 tool_call 数量（max_rounds 场景）。
            handoff_target: handoff 目标 Agent 名称（handoff 场景）。
        """
        ...
