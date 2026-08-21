"""本地 TUI 审批模式判定纯函数模块。

本模块提供 ``evaluate_approval_mode`` 纯函数，依据本会话级审批模式
（``ask`` / ``auto`` / ``manual``）与后端审批策略，判定收到的一批待审批
动作是否可自动放行。模块保持无副作用、无 I/O、不依赖 Textual，便于单测。

判定所需的风险信息唯一来源为注入的 ``policy_for`` 回调（背后由
``ApprovalPolicyPort.policy_for`` 提供），本模块严禁硬编码任何工具名或
风险分级，从而不绕过后端对高风险工具的强制审批红线。
"""

from __future__ import annotations

from collections.abc import Callable

from domain.agent.value_objects import ApprovalDecision, ApprovalPolicy, PendingActionRequest

APPROVAL_MODES = frozenset({"ask", "auto", "manual"})
"""本地审批模式取值域。

由本模块与 ``commands.py`` 的 ``/approval mode`` 校验共用同一定义，避免
两处取值域漂移；取值语义见 ``evaluate_approval_mode`` docstring。
"""


def evaluate_approval_mode(
    mode: str,
    actions: tuple[PendingActionRequest, ...],
    policy_for: Callable[[str], ApprovalPolicy],
) -> list[ApprovalDecision] | None:
    """根据本地审批模式决定是否自动放行整批待审批动作。

    返回值语义：

    - ``None``：需要打开 ApprovalScreen 请求人工决策；
    - ``list[ApprovalDecision]``：可自动提交的、与 ``actions`` 顺序一致的
      全 ``approve`` 决策序列。

    判定规则（对应需求 6）：

    - ``mode == "manual"``：始终返回 ``None``，对所有可中断工具要求人工审批。
    - ``mode == "auto"``：仅当 **每个** action 经 ``policy_for`` 判定
      ``interrupt is False`` 且 ``"approve" in allowed_decisions`` 时，返回
      与 ``actions`` 顺序一致的全 ``approve`` 决策序列；只要任一 action 的
      策略 ``interrupt is True``（高风险红线）即返回 ``None`` 强制打开面板。
    - ``mode`` 其它值（含 ``"ask"`` 与非法值）：返回 ``None``，按后端策略走
      人工审批。

    ``auto`` 档为面向未来的扩展位：当前后端低风险工具不会产生审批中断，故
    正常路径几乎只会命中 ``None``；其安全约束确保未来放开低风险中断时也
    不会整批绕过高风险红线。判定所需风险信息唯一来源为 ``policy_for``，
    本函数严禁硬编码工具名或风险分级。

    Args:
        mode: 本会话级审批模式字符串。
        actions: 待审批动作序列，顺序与模型 tool_calls 一致。
        policy_for: 按工具名返回 ``ApprovalPolicy`` 的回调，风险来源唯一入口。

    Returns:
        需要人工决策时返回 ``None``；可自动放行时返回与 ``actions`` 顺序
        一致的全 ``approve`` ``ApprovalDecision`` 序列。
    """
    if mode != "auto":
        return None
    decisions: list[ApprovalDecision] = []
    for action in actions:
        policy = policy_for(action.tool_name)
        if policy.interrupt is not False or "approve" not in action.allowed_decisions:
            return None
        decisions.append(
            ApprovalDecision(type="approve", tool_call_id=action.tool_call_id)
        )
    return decisions
