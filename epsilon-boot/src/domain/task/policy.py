"""任务子域领域服务模块。

本模块承载 ``domain/task`` 子域内**无自然归属某单一值对象的跨对象业务判定**，
以 4 个零基础设施依赖的领域服务收敛既往散落在委派工具/委派适配器、
``TaskAgentAdapter``、``run_execution_coordinator``、``run_approval_resumer``
中的领域规则（对齐 ``docs/steering/ddd-tactical-modeling.md`` §4 领域服务放置
规则，命名与既有样板 ``domain/workspace/policy.py`` 一致）：

- ``DelegationDepthPolicy``：委派深度上限判定；
- ``TaskContinuationPolicy``：Agent 终止原因 → 是否暂停(PAUSED) 判定；
- ``TaskStatusMapping``：任务状态 → 领域中立结局类别映射；
- ``ApprovalResumePrecondition``：审批恢复前置条件校验。

**关键约束（不变量）**：本模块为纯判定构件——只返回布尔/枚举或以既有领域
异常表达校验失败，**不承载任何 I/O、日志、序列化、``RunStatus`` 装配**（这些留
在各调用点）；4 个服务均为无字段的无状态类。允许的 import 仅限
``collections.abc`` 与同层 ``domain.agent`` / 同子域 ``domain.task`` 的领域构件；
禁止引入 ``application`` / ``infrastructure`` / FastAPI / Pydantic / ``domain.run``。
"""

from __future__ import annotations

from collections.abc import Sequence

from domain.agent.exceptions import (
    ApprovalDecisionCountMismatchError,
    ApprovalDecisionNotAllowedError,
    ApprovalDecisionOrderMismatchError,
)
from domain.agent.value_objects import (
    AgentTerminationReason,
    ApprovalDecision,
    PendingActionRequest,
)
from domain.task.enums import TaskOutcomeKind
from domain.task.value_objects import TaskStatus

_PAUSE_REASONS: frozenset[str] = frozenset({"max_rounds", "token_budget_exceeded"})
"""应产生 PAUSED 分支的 Agent 终止原因集合。

与 ``TaskAgentAdapter._to_task_result`` 现有内联字面量
``("max_rounds", "token_budget_exceeded")`` 逐一等价，集中定义避免重复。
"""


class DelegationDepthPolicy:
    """委派深度上限判定领域服务。

    收敛散落在委派工具与委派适配器中的深度判据。刻意提供两个方法以
    保留调用点间既有的判据差异（见 requirement AC2.4），不借收敛之名统一：

    - exceeds_for_next_depth：三个委派工具与 handoff 适配器的「下一层是否超限」判据；
    - exceeds_for_current_depth：delegate_parallel 内部「当前深度是否超限」判据。

    本服务零基础设施依赖、不感知 workflow_context；effective_max_depth 的
    min(...) 计算仍由各调用点在传入前完成，本服务只做纯比较判定。
    """

    @staticmethod
    def exceeds_for_next_depth(current_depth: int, max_delegation_depth: int) -> bool:
        """判定「下一层委派」是否超限。

        等价于既有内联逻辑 ``next_depth = current_depth + 1; next_depth > max``。

        Args:
            current_depth: 当前委派深度（根 Agent 为 0）。
            max_delegation_depth: 已经过 min(...) 归一的有效最大深度。

        Returns:
            当 ``current_depth + 1 > max_delegation_depth`` 时为 True。
        """
        return current_depth + 1 > max_delegation_depth

    @staticmethod
    def exceeds_for_current_depth(current_depth: int, max_delegation_depth: int) -> bool:
        """判定「当前深度」是否超限（delegate_parallel 专用判据）。

        等价于 ``delegation_adapter.delegate_parallel._one`` 的既有逻辑
        ``delegation_depth > max_delegation_depth``；此处入参 ``current_depth``
        承载调用点传入的「子 Agent 实际执行深度」（即 next_depth）。

        Args:
            current_depth: 调用点传入的当前判定深度（在 delegate_parallel 场景
                下已是子 Agent 的实际执行深度 next_depth）。
            max_delegation_depth: 已经过 min(...) 归一的有效最大深度。

        Returns:
            当 ``current_depth > max_delegation_depth`` 时为 True。
        """
        return current_depth > max_delegation_depth


class TaskContinuationPolicy:
    """任务续跑判定领域服务。

    承载「Agent 终止原因 → 任务是否应暂停(PAUSED)」这一领域判定，
    与 TaskAgentAdapter._to_task_result 现有 ``terminated_reason not in
    ("max_rounds", "token_budget_exceeded")`` 逻辑逐一等价（本方法为其取反语义）。

    不承载 _can_continue_from_context 的上下文可继续性判定（该判定依赖
    ConversationContext / ToolRegistry，属基础设施，留在 TaskAgentAdapter）。
    """

    @staticmethod
    def should_pause(terminated_reason: AgentTerminationReason) -> bool:
        """判定该终止原因是否应产生 PAUSED 分支。

        Args:
            terminated_reason: Agent 运行终止原因。

        Returns:
            当 ``terminated_reason`` 属于 {max_rounds, token_budget_exceeded}
            时为 True（PAUSED）；否则为 False（SUCCESS 分支）。
        """
        return terminated_reason in _PAUSE_REASONS


class TaskStatusMapping:
    """任务状态到中立结局类别的映射领域服务。

    与 run_execution_coordinator._task_outcome 现有分支逐一等价：
    SUCCESS→SUCCEEDED、PAUSED→PAUSED、HUMAN_INTERVENTION_REQUIRED→
    AWAITING_APPROVAL、其余（含 FAILED）→FAILED。输出为中立枚举，
    不返回 RunStatus；到 RunStatus / ApprovalResumeStoreResult 的最终装配
    及 error 结构、terminal_reason、approval_id、can_continue 的构造留在应用层。
    """

    @staticmethod
    def outcome_of(status: TaskStatus) -> TaskOutcomeKind:
        """把任务状态映射为中立结局类别。

        Args:
            status: 任务执行状态枚举。

        Returns:
            对应的 TaskOutcomeKind；未显式覆盖的状态归为 FAILED，
            与既有 ``else -> RunStatus.FAILED`` 兜底逐一等价。
        """
        if status is TaskStatus.SUCCESS:
            return TaskOutcomeKind.SUCCEEDED
        if status is TaskStatus.PAUSED:
            return TaskOutcomeKind.PAUSED
        if status is TaskStatus.HUMAN_INTERVENTION_REQUIRED:
            return TaskOutcomeKind.AWAITING_APPROVAL
        return TaskOutcomeKind.FAILED


class ApprovalResumePrecondition:
    """审批恢复前置条件校验领域服务。

    以待恢复审批的既有动作序列（actions）与恢复请求的决策序列（decisions）
    为输入，逐一校验：决策数量匹配、决策顺序（tool_call_id 对齐）、决策类型
    属于该动作 allowed_decisions。与 TaskAgentAdapter._load_consumed_interrupt
    现有校验逐一等价，任一不满足即抛出既有领域异常，异常类型、参数、触发顺序
    与时机保持不变。

    不承载 ApprovalStateStorePort 的 load / is_expired / consume 等 I/O
    步骤（留在 TaskAgentAdapter），本服务零基础设施依赖、可脱离运行时单测。
    """

    @staticmethod
    def check(
        actions: Sequence[PendingActionRequest],
        decisions: Sequence[ApprovalDecision],
    ) -> None:
        """校验审批决策集合的合法性。

        Args:
            actions: 待审批动作序列（来自 ApprovalInterrupt.actions）。
            decisions: 恢复请求携带的决策序列（TaskApprovalResumeRequest.decisions）。

        Raises:
            ApprovalDecisionCountMismatchError: 决策数量与动作数量不一致。
            ApprovalDecisionOrderMismatchError: 决策 tool_call_id 与动作不对齐。
            ApprovalDecisionNotAllowedError: 决策类型不在动作 allowed_decisions 内。
        """
        if len(decisions) != len(actions):
            raise ApprovalDecisionCountMismatchError(len(actions), len(decisions))
        for action, decision in zip(actions, decisions, strict=True):
            if decision.tool_call_id != action.tool_call_id:
                raise ApprovalDecisionOrderMismatchError(
                    action.tool_call_id,
                    decision.tool_call_id,
                )
            if decision.type not in action.allowed_decisions:
                raise ApprovalDecisionNotAllowedError(
                    action.tool_name,
                    decision.type,
                    frozenset(action.allowed_decisions),
                )
