"""领域层 Agent Loop 编排构件模块（P2 首片）。

承载 ReAct Agent Loop 的纯编排叶子判定与轮次终止形态值对象，
均为可脱离运行时、给定输入即定输出的领域判定，零基础设施 / 框架 / Pydantic 依赖。

包含：

- ``RoundOutcome`` / ``RoundOutcomeKind``：Agent Loop 单轮终止形态值对象（领域通用语言）；
- ``compute_total_tokens`` / ``is_token_budget_exceeded``：token 预算计算与超限判定；
- ``detect_handoff``：会话上下文尾部 handoff 标记检测；
- ``outcome_to_agent_result``：轮次结果到对外 AgentResult 的纯翻译。

本模块不承载循环推进主体、工具执行、审批中断决策、流式累加、guardrail / trace /
序列化 / 日志——这些技术关注点留在 ``infrastructure/agent``（见 ADR-0010 / ADR-0011）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from domain.agent.exceptions import HandoffPerformed, ToolPermissionDeniedError
from domain.agent.guardrails import GuardrailAction, GuardrailDecision
from domain.agent.value_objects import (
    AgentConfig,
    AgentResult,
    AgentTerminationReason,
    ApprovalPolicy,
    ApprovalRequiredPayload,
    PendingActionRequest,
)
from domain.chat.context import ConversationContext, ToolMessage
from domain.model_access.value_objects import LLMResponse, ToolCallRequest

RoundOutcomeKind = Literal["text", "tool_calls", "approval", "final", "handoff"]
"""轮次终止形态类型。

取值范围：

- ``"text"``：模型返回纯文本回复，循环终止。
- ``"tool_calls"``：存在 tool_calls 且无需审批，调用方需执行工具。
- ``"approval"``：存在 tool_calls 且命中审批策略，循环终止。
- ``"final"``：已达 ``config.max_rounds`` 仍未自然终止。
- ``"handoff"``：上一轮工具执行触发了 ``HandoffPerformed`` 信号，控制权已
  转移给目标 Agent，当前 Agent Loop 终止；``handoff_target`` /
  ``handoff_content`` 字段携带目标 Agent 名与最终回复，4 个执行入口直接
  采纳为 ``AgentResult.content``，不再发起新一轮 LLM 调用。
"""


@dataclass(frozen=True)
class RoundOutcome:
    """ReAct Agent 单轮推进结果值对象。

    由 ``ReActAgentAdapter._iter_rounds`` 在每轮模型调用结束后产出，
    四个执行入口（run / run_streaming / run_events / resume）按 ``kind``
    分支构造各自的对外形态。

    Attributes:
        kind: 轮次终止形态。
            ``"text"`` 表示模型返回纯文本回复，循环终止；
            ``"tool_calls"`` 表示存在 tool_calls 且无需审批，调用方需在本轮
            内逐个执行工具，工具执行完成后由调用方继续 ``__anext__``；
            ``"approval"`` 表示存在 tool_calls 且命中审批策略，循环终止；
            ``"final"`` 表示已达 ``config.max_rounds`` 仍未自然终止。
        round_num: 当前轮次序号，从 1 开始。
        response: 当前轮次的 LLM 响应；``"approval"`` / ``"text"`` /
            ``"tool_calls"`` / ``"final"`` 四种 kind 均必填。
        total_usage: 截至本轮结束时累计的 token 用量。
        tool_calls: ``"tool_calls"`` 与 ``"approval"`` kind 下的待执行工具调用
            列表，按模型返回顺序；其它 kind 为空 tuple。
        approval: ``"approval"`` kind 下的审批载荷；其它 kind 为 ``None``。
        assistant_message_index: 本轮如果向 ``ConversationContext`` 追加了
            "携带 tool_calls 的 AssistantMessage"，记录该消息在
            ``ConversationContext.get_messages()`` 中的索引；
            供 ``TaskAgentAdapter._extract_trace`` 从事件时间索引读取真实
            时刻使用。``"text"`` / ``"final"`` kind 下为 ``None``。
        terminated_reason: 轮次推进终止原因，默认 ``"completed"``。仅在
            ``kind == "final"`` 时具有非默认值；``"text"`` / ``"tool_calls"`` /
            ``"approval"`` kind 下保持 ``"completed"``，本字段不被消费方读取。
            供 ``_iter_rounds`` 在循环耗尽分支按 last kind 区分两种 ``"final"``
            形态：自然耗尽（``"completed"``）vs ``max_rounds`` 命中
            （``"max_rounds"``）。四个执行入口（run / run_streaming /
            run_events / resume）透传该字段到 ``AgentResult.terminated_reason``。
    """

    kind: RoundOutcomeKind
    round_num: int
    response: LLMResponse
    total_usage: dict[str, int]
    tool_calls: tuple[ToolCallRequest, ...] = ()
    approval: ApprovalRequiredPayload | None = None
    assistant_message_index: int | None = None
    terminated_reason: AgentTerminationReason = "completed"
    """终止原因。仅在 ``kind == "final"`` 时具有非默认值；
    ``"text"`` / ``"tool_calls"`` / ``"approval"`` kind 下保持 ``"completed"``，
    本字段不被消费方读取。供 ``_iter_rounds`` 在循环耗尽分支按 last kind
    区分两种 ``"final"`` 形态：自然耗尽 vs ``max_rounds`` 命中。"""

    handoff_target: str | None = None
    """``kind == "handoff"`` 时携带的目标 Agent 名称；其它 kind 为 ``None``。"""

    handoff_content: str = ""
    """``kind == "handoff"`` 时携带的目标 Agent 最终回复文本；其它 kind 为空串。"""


def compute_total_tokens(total_usage: dict[str, int]) -> int:
    """按 ``Token_Budget_Computation_Rule`` 计算累计 token 用量。

    优先取 ``total_usage["total_tokens"]``；该键不存在或为 0 时回退
    到 ``total_usage.get("prompt_tokens", 0) + total_usage.get("completion_tokens", 0)``。
    """
    total = int(total_usage.get("total_tokens", 0) or 0)
    if total > 0:
        return total
    return int(total_usage.get("prompt_tokens", 0) or 0) + int(
        total_usage.get("completion_tokens", 0) or 0
    )


def is_token_budget_exceeded(config: AgentConfig, total_usage: dict[str, int]) -> bool:
    """判断当前累计 ``usage`` 是否超过 ``config.max_total_tokens``。"""
    if config.max_total_tokens is None:
        return False
    return compute_total_tokens(total_usage) > config.max_total_tokens


def detect_handoff(context: ConversationContext) -> tuple[str, str] | None:
    """扫描最近一组 ToolMessage，返回 (handoff_target, handoff_content) 或 None。

    Spec A R1.3 短路逻辑：``HandoffToAgentTool`` 抛 ``HandoffPerformed`` 后，
    ``_execute_tool_call`` 在 ``ToolMessage.metadata`` 写入 ``handoff_target``。
    ``_iter_rounds`` 在每轮入口（``round_num > start_round`` 时）调用本方法
    检查上一轮 tool 执行回写的若干 ToolMessage 中是否存在 handoff 标记；
    命中则父 Agent Loop 立即终止，不再发起新一轮 LLM 调用。

    实现细节：从消息列表尾部反向扫描，跳过最近一组**连续**的 ``ToolMessage``，
    遇到非 ``ToolMessage`` 立刻停止——此时跳过区间内的 ``ToolMessage`` 即"上一轮
    工具执行回写的全部消息"。该区间内任一 ToolMessage 带 ``handoff_target``
    即视为命中（同轮多个工具并发，handoff 可能出现在任意位置）。

    Args:
        context: 当前 ``ConversationContext``。

    Returns:
        命中时返回 ``(target_agent, tool_message_content)``；否则 ``None``。
    """
    messages = context.get_messages()
    for msg in reversed(messages):
        if not isinstance(msg, ToolMessage):
            break
        target = msg.metadata.get("handoff_target")
        if target:
            return str(target), msg.content
    return None


def outcome_to_agent_result(outcome: RoundOutcome) -> AgentResult:
    """将 ``RoundOutcome`` 翻译为 ``AgentResult``。

    按 ``kind`` 分支构造 ``AgentResult``：

    - ``"text"`` / ``"final"``：``status="completed"``，内容取
      ``outcome.response.content``。
    - ``"approval"``：``status="approval_required"``，内容为空字符串，
      ``approval`` 字段携带审批载荷。
    - ``"handoff"``（Spec A）：``status="completed"``，内容取
      ``outcome.handoff_content``（目标 Agent 最终回复），
      ``terminated_reason="completed"``；``model`` 取 ``outcome.response.model``
      （ADR-0010 疑点 2 当前实际行为，本片不修正）。

    ``"tool_calls"`` kind 不应进入本方法——调用方应在消费完工具执行后
    继续驱动生成器，而非直接翻译为 AgentResult。

    Args:
        outcome: 生成器产出的单轮推进结果。

    Returns:
        对外返回的 ``AgentResult`` 值对象。
    """
    if outcome.kind == "handoff":
        # 控制转移：目标 Agent 最终回复直接成为父 Agent final content
        return AgentResult(
            content=outcome.handoff_content,
            model=outcome.response.model if outcome.response else "",
            usage=outcome.total_usage,
            latency_ms=0.0,
            terminated_reason="completed",
        )
    if outcome.kind in ("text", "final"):
        return AgentResult(
            content=outcome.response.content,
            model=outcome.response.model,
            usage=outcome.total_usage,
            latency_ms=outcome.response.latency_ms,
            terminated_reason=outcome.terminated_reason,
        )
    # kind == "approval": HITL 中断不属于轮数超限
    return AgentResult(
        content="",
        model=outcome.response.model,
        usage=outcome.total_usage,
        latency_ms=outcome.response.latency_ms,
        status="approval_required",
        approval=outcome.approval,
        terminated_reason="completed",
    )


# ──────────────────────────────────────────────────────────────────────────────
# P2 第二片 Wave 1：工具执行策略纯函数
# ──────────────────────────────────────────────────────────────────────────────

ToolGuardrailBranch = Literal["proceed", "require_approval", "stop"]
"""工具 Guardrail 前置评估分支。

与 ``GuardrailAction`` 的映射关系：

- ``None`` (无 guardrail 决策) → ``"proceed"``
- ``GuardrailAction.REQUIRE_APPROVAL`` → ``"require_approval"``
- ``GuardrailAction.STOP`` → ``"stop"``
- 其它（``ALLOW`` / ``OBSERVE`` 等） → ``"proceed"``
"""


def interpret_tool_guardrail_decision(
    decision: GuardrailDecision | None,
) -> ToolGuardrailBranch:
    """将 guardrail 评估决策翻译为工具执行分支判定。

    纯函数，无副作用。与 ``react_agent_adapter.py``
    ``_execute_tool_call`` / ``_prepare_tool_calls_for_execution`` 中
    ``guardrail_decision.action`` 的三路分支逐一等价：

    - ``decision is None`` → ``"proceed"``（无 guardrail，直接执行）
    - ``decision.action is GuardrailAction.REQUIRE_APPROVAL`` → ``"require_approval"``
    - ``decision.action is GuardrailAction.STOP`` → ``"stop"``
    - 其它 action（``ALLOW`` / ``OBSERVE``） → ``"proceed"``

    Args:
        decision: guardrail 策略评估结果；``None`` 表示未触发任何 guardrail。

    Returns:
        ``ToolGuardrailBranch`` 分支指示。
    """
    if decision is None:
        return "proceed"
    if decision.action is GuardrailAction.REQUIRE_APPROVAL:
        return "require_approval"
    if decision.action is GuardrailAction.STOP:
        return "stop"
    return "proceed"


@dataclass(frozen=True)
class ToolExecutionClassification:
    """工具执行结果分类值对象。

    封装工具执行后的异常/信号分类结果，供基础设施层据此分支处理副作用
    （日志、metadata 写入、checkpoint 等）。

    Attributes:
        is_error: 是否为错误（``HandoffPerformed`` 不视为错误）。
        handoff_target: 若为 handoff 成功信号，目标 Agent 名称；否则 ``None``。
        content: 回灌给 LLM 的 ``ToolMessage.content`` 文本。
        error_class: 错误时记录的异常类名；非错误时为 ``None``。
    """

    is_error: bool
    handoff_target: str | None
    content: str
    error_class: str | None


def classify_tool_execution(
    exc: BaseException,
    *,
    handoff_signal: HandoffPerformed | None,
    timeout: float | None,
) -> ToolExecutionClassification:
    """将工具执行异常/信号分类为纯领域值对象。

    与 ``react_agent_adapter.py`` ``_execute_tool_call`` 的 ``except`` 分支
    逐一等价：

    - ``handoff_signal is not None``：成功 handoff，非错误，
      ``content=signal.content``，``handoff_target=signal.target_agent``。
    - ``ToolPermissionDeniedError``：``is_error=True``，
      ``error_class="ToolPermissionDeniedError"``，``content=str(exc)``。
    - ``TimeoutError``：``is_error=True``，``error_class="TimeoutError"``，
      ``content=f"工具执行超时（{timeout}s)"``。
    - 其它 ``Exception``：``is_error=True``，
      ``error_class=type(exc).__name__``，``content=str(exc)``。

    本函数**不**执行副作用（日志、metadata 写入等），副作用留在
    基础设施层 adapter 中，按分类结果逐一触发。

    Args:
        exc: 捕获的异常实例。当 ``handoff_signal`` 非 ``None`` 时，
            ``exc`` 应为同一 ``HandoffPerformed`` 实例。
        handoff_signal: 若捕获到 ``HandoffPerformed``，传入该信号实例；
            否则传 ``None``。
        timeout: 当前工具的超时配置（秒）；仅在 ``TimeoutError`` 分支
            用于格式化内容文本。

    Returns:
        ``ToolExecutionClassification`` 值对象。
    """
    if handoff_signal is not None:
        return ToolExecutionClassification(
            is_error=False,
            handoff_target=handoff_signal.target_agent,
            content=handoff_signal.content,
            error_class=None,
        )
    if isinstance(exc, ToolPermissionDeniedError):
        return ToolExecutionClassification(
            is_error=True,
            handoff_target=None,
            content=str(exc),
            error_class="ToolPermissionDeniedError",
        )
    if isinstance(exc, TimeoutError):
        return ToolExecutionClassification(
            is_error=True,
            handoff_target=None,
            content=f"工具执行超时（{timeout}s)",
            error_class="TimeoutError",
        )
    # 其它 Exception
    return ToolExecutionClassification(
        is_error=True,
        handoff_target=None,
        content=str(exc),
        error_class=type(exc).__name__,
    )


def collect_pending_actions(
    tool_calls: Sequence[ToolCallRequest],
    allowed_tool_names: frozenset[str],
    policies: Mapping[str, ApprovalPolicy],
) -> tuple[PendingActionRequest, ...]:
    """按模型 tool_calls 顺序收集需要审批的动作。

    纯函数，与 ``react_agent_adapter.py`` ``_collect_pending_actions``（L853）
    逐一等价。差异：

    - 不含 ``logger.warning``（日志副作用留在 adapter）；
    - ``policies`` 由调用方预解析为 ``Mapping[str, ApprovalPolicy]``，
      而非通过 ``self._approval_policy.policy_for(...)`` 延迟查询。

    逻辑：

    1. 遍历 ``tool_calls``，跳过 ``tool_call.name not in allowed_tool_names``
       的条目（调用方负责 warning 日志）。
    2. 对命中 ``policies[tool_call.name].interrupt == True`` 的条目，
       构造 ``PendingActionRequest``。
    3. 返回按原始顺序的 ``tuple[PendingActionRequest, ...]``。

    Args:
        tool_calls: 模型返回的工具调用序列。
        allowed_tool_names: 当前配置允许的工具名称集合。
        policies: 工具名 → ``ApprovalPolicy`` 的映射，由调用方
            从 ``ApprovalPolicyPort.policy_for`` 预查询构建。

    Returns:
        按原始顺序的待审批动作元组。
    """
    actions: list[PendingActionRequest] = []
    for tool_call in tool_calls:
        if tool_call.name not in allowed_tool_names:
            # 跳过未授权工具；调用方负责输出 warning 日志
            continue
        policy = policies.get(tool_call.name)
        if policy is not None and policy.interrupt:
            actions.append(
                PendingActionRequest(
                    tool_call_id=tool_call.id,
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                    allowed_decisions=policy.allowed_decisions,
                    reason=policy.risk_label,
                )
            )
    return tuple(actions)
