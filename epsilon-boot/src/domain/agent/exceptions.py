"""Agent 工具异常定义模块。

定义了工具执行过程中可能发生的各类异常，用于标准化工具层的错误处理。
所有异常继承自项目的 BizException 基类，错误码使用 6xxxx 段。
"""

import re
from typing import Any

from common.exceptions import BizException

_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)(token|password|secret|authorization|api_key)\s*=\s*[^,\s;]+"
)


def _safe_message_part(value: str) -> str:
    """返回适合放入业务错误 message 的脱敏片段。"""
    if "/" in value or "\\" in value:
        return "(已脱敏)"
    return _SENSITIVE_VALUE_PATTERN.sub(r"\1=***", value)


class ToolExecutionError(BizException):
    """工具执行异常基类。

    所有工具执行相关的异常都应继承自此类。当 Tool 的 execute 方法抛出
    非 ToolExecutionError 异常时，run 方法会将其包装为此类型。

    Attributes:
        code: 错误码（默认 60001）
        message: 错误描述信息
        tool_name: 出错的工具名称
    """

    def __init__(self, message: str, tool_name: str, code: int = 60001):
        super().__init__(code=code, message=message)
        self.tool_name = tool_name


class ToolNotFoundError(ToolExecutionError):
    """工具未找到异常。

    当在 ToolRegistry 中查找不存在的工具时抛出。

    Attributes:
        tool_name: 未找到的工具名称
    """

    def __init__(self, tool_name: str):
        super().__init__(
            message=f"工具 {tool_name} 未找到",
            tool_name=tool_name,
            code=60002,
        )


class ToolParameterValidationError(ToolExecutionError):
    """工具参数校验失败异常。

    当工具参数不符合 schema 约束时抛出，包含所有校验错误的详细信息。

    Attributes:
        tool_name: 校验失败的工具名称
        errors: 校验错误信息列表
    """

    def __init__(self, tool_name: str, errors: list[str]):
        message = f"工具 {tool_name} 参数校验失败: {'; '.join(errors)}"
        super().__init__(
            message=message,
            tool_name=tool_name,
            code=60003,
        )
        self.errors = errors


class ToolPermissionDeniedError(ToolExecutionError):
    """工具权限拒绝异常。

    当请求的工具名称不在当前允许的工具集合内时抛出。
    用于区分"工具不存在"（ToolNotFoundError, 60002）和
    "工具未授权"（ToolPermissionDeniedError, 60004）两种错误场景。

    Attributes:
        tool_name: 被拒绝的工具名称
        allowed_tools: 当前允许的工具名称集合
    """

    def __init__(self, tool_name: str, allowed_tools: frozenset[str]) -> None:
        allowed_list = ", ".join(sorted(allowed_tools)) if allowed_tools else "(空)"
        message = f"工具 {tool_name} 未授权，当前允许的工具: [{allowed_list}]"
        super().__init__(
            message=message,
            tool_name=tool_name,
            code=60004,
        )
        self.allowed_tools = allowed_tools


class AgentNotFoundError(BizException):
    """Agent 未找到异常。

    当在 AgentRegistry 中查找不存在的 Agent 名称时抛出。

    Attributes:
        agent_name: 未找到的 Agent 名称
    """

    def __init__(self, agent_name: str, registered_names: list[str]) -> None:
        registered_list = ", ".join(registered_names) if registered_names else "(空)"
        message = f"Agent '{agent_name}' 未找到，当前已注册: [{registered_list}]"
        super().__init__(code=60010, message=message)
        self.agent_name = agent_name


class DelegationDepthExceededError(BizException):
    """委派深度超限异常。

    当 delegation_depth 达到 max_delegation_depth 时抛出。

    Attributes:
        current_depth: 当前委派深度
        max_depth: 最大允许深度
    """

    def __init__(self, current_depth: int, max_depth: int, target_agent: str) -> None:
        message = (
            f"委派深度超限: 当前深度 {current_depth}，最大深度 {max_depth}，"
            f"目标 Agent '{target_agent}'"
        )
        super().__init__(code=60011, message=message)
        self.current_depth = current_depth
        self.max_depth = max_depth


class ApprovalNotFoundError(BizException):
    """审批状态不存在异常。"""

    def __init__(self, session_id: str, approval_id: str) -> None:
        super().__init__(code=60020, message="审批状态不存在或已被清理")
        self.session_id = session_id
        self.approval_id = approval_id


class ApprovalExpiredError(BizException):
    """审批状态已过期异常。"""

    def __init__(self, session_id: str, approval_id: str) -> None:
        super().__init__(code=60021, message="审批状态已过期，请重新发起请求")
        self.session_id = session_id
        self.approval_id = approval_id


class ApprovalConsumedError(BizException):
    """审批状态已消费异常。"""

    def __init__(self, session_id: str, approval_id: str) -> None:
        super().__init__(code=60022, message="审批状态已被处理，不能重复恢复")
        self.session_id = session_id
        self.approval_id = approval_id


class ApprovalDecisionCountMismatchError(BizException):
    """审批决策数量与待审批动作数量不一致异常。"""

    def __init__(self, expected_count: int, actual_count: int) -> None:
        super().__init__(
            code=60023,
            message=f"审批决策数量不匹配，期望 {expected_count} 个，实际 {actual_count} 个",
        )
        self.expected_count = expected_count
        self.actual_count = actual_count


class ApprovalDecisionOrderMismatchError(BizException):
    """审批决策顺序与待审批动作顺序不一致异常。"""

    def __init__(self, expected_tool_call_id: str, actual_tool_call_id: str) -> None:
        super().__init__(code=60024, message="审批决策顺序与待审批动作不一致")
        self.expected_tool_call_id = expected_tool_call_id
        self.actual_tool_call_id = actual_tool_call_id


class ApprovalDecisionNotAllowedError(BizException):
    """审批决策类型不在允许集合内异常。"""

    def __init__(
        self,
        tool_name: str,
        decision_type: str,
        allowed_decisions: frozenset[str],
    ) -> None:
        allowed_text = ", ".join(sorted(allowed_decisions)) if allowed_decisions else "(空)"
        safe_tool_name = _safe_message_part(tool_name)
        safe_decision_type = _safe_message_part(decision_type)
        super().__init__(
            code=60025,
            message=(
                f"工具 {safe_tool_name} 不允许审批决策 {safe_decision_type}，"
                f"允许值: [{allowed_text}]"
            ),
        )
        self.tool_name = tool_name
        self.decision_type = decision_type
        self.allowed_decisions = allowed_decisions


class ApprovalEditToolNameMismatchError(BizException):
    """编辑审批试图修改工具名异常。"""

    def __init__(self, expected_tool_name: str, actual_tool_name: str) -> None:
        super().__init__(code=60026, message="编辑后的工具名称必须与原工具名称一致")
        self.expected_tool_name = expected_tool_name
        self.actual_tool_name = actual_tool_name


class ApprovalEditInvalidArgumentsError(BizException):
    """编辑审批参数非法异常。"""

    def __init__(self, tool_name: str, reason: str = "") -> None:
        message = f"工具 {_safe_message_part(tool_name)} 的编辑参数不合法"
        if reason:
            message = f"{message}: {_safe_message_part(reason)}"
        super().__init__(code=60027, message=message)
        self.tool_name = tool_name
        self.reason = reason


class ApprovalRespondNotAllowedError(BizException):
    """人工回复审批不可用或内容非法异常。"""

    def __init__(self, tool_name: str) -> None:
        super().__init__(
            code=60028,
            message=f"工具 {_safe_message_part(tool_name)} 不允许使用人工回复决策",
        )
        self.tool_name = tool_name


class HitlConfigInvalidError(BizException):
    """HITL 配置非法异常。"""

    def __init__(self, reason: str) -> None:
        super().__init__(code=60029, message=f"HITL 配置非法: {_safe_message_part(reason)}")
        self.reason = reason


class InvalidApprovalActionError(BizException):
    """审批动作值对象构造非法异常。

    当 ``PendingActionRequest`` / ``ApprovalDecision`` 在 ``__post_init__``
    中检测到 ``tool_call_id`` 为 ``None`` 或空字符串时抛出。该异常归属
    ``domain/agent`` 子域，错误码 ``60040``，与既有审批相关异常
    （``ApprovalNotFoundError`` 60020 / ``ApprovalExpiredError`` 60021…）
    同段，但分类独立，application 层可基于 ``isinstance`` 单独捕获。

    与 ``InvalidToolCallIdError``（``domain/model_access`` 侧）显式分
    类型：模型解析侧的 id 违约由 Provider 行为引发，审批前置校验侧的
    违约由上游 application 层送入的值对象构造引发；两者的排障路径
    与责任方不同，**不共享继承**。

    Attributes:
        code: 错误码，固定为 ``60040``。
        message: 中文错误描述，含违约值对象名与字段名。
        details: 统一诊断字段集（审批侧特化字段）。
        value_object: 违约值对象类型名（便于断言）。
        field: 违约字段名。
        raw_value: 原始违约值。
    """

    def __init__(
        self,
        value_object: str,
        field: str,
        raw_value: object,
        *,
        tool_name: str | None = None,
    ) -> None:
        """构造审批动作值对象非法异常。

        Args:
            value_object: 违约值对象名（``"PendingActionRequest"`` /
                ``"ApprovalDecision"``）。
            field: 违约字段名（本期固定为 ``"tool_call_id"``）。
            raw_value: 原始违约值（保留 ``None`` 与空串的类型差异）。
            tool_name: 违约值对象关联的工具名称；``ApprovalDecision``
                不携带 ``tool_name`` 时填 ``None``。
        """
        message = f"{value_object}.{field} 不能为空（raw_value={raw_value!r}）"
        super().__init__(code=60040, message=message)
        self.details: dict[str, Any] = {
            "source": "approval_resume",
            "provider": None,
            "model": None,
            "tool_name": tool_name,
            "tool_call_index": None,
            "raw_id_value": raw_value,
            "value_object": value_object,
            "field": field,
        }
        self.value_object = value_object
        self.field = field
        self.raw_value = raw_value


class ToolCircuitOpenError(ToolExecutionError):
    """工具熔断器处于 OPEN 状态时抛出，阻止对持续故障工具的调用。

    Attributes:
        tool_name: 被熔断的工具名称
    """

    def __init__(self, tool_name: str) -> None:
        super().__init__(
            message=f"工具 {tool_name} 熔断器已打开，暂时拒绝调用",
            tool_name=tool_name,
            code=60030,
        )


class HandoffPerformed(Exception):
    """Handoff 控制转移成功信号。

    本类是"以异常承载控制流"的成功信号，**不是错误**。
    ``HandoffToAgentTool.execute`` 在目标 Agent 执行成功后抛出此信号，
    ``ReActAgentAdapter._execute_tool_call`` 捕获后写入 ``ToolMessage`` 并
    打标 ``metadata["handoff_target"]``，使 ``_iter_rounds`` 在后续轮次
    入口检测到 handoff 标记后立即终止当前 Agent Loop，不再发起新一轮 LLM 调用。

    使用异常做信号的理由：

    - ``Tool.execute`` 协议返回 ``str``，无法在不污染字符串载荷的情况下
      携带结构化控制信号；
    - Python 标准库已有 ``StopIteration`` / ``GeneratorExit`` 等"以异常
      承载控制流"先例；
    - 命名 ``HandoffPerformed``（已发生 / 已完成）而非 ``HandoffError`` 或
      ``HandoffSignal``，凸显"成功信号"语义，降低误读为错误的概率。

    与 ``ToolExecutionError`` 区分：``HandoffPerformed`` **不**继承
    ``ToolExecutionError`` / ``BizException``，**不**视为工具失败；
    ``ReActAgentAdapter._execute_tool_call`` 捕获到该信号时不会把
    ``ToolMessage.metadata["error"]`` 设为 ``True``。

    Attributes:
        target_agent: 接管控制权的目标 Agent 名称。
        content: 目标 Agent 的最终回复文本，作为父 Agent ``AgentResult.content``。
        usage: 目标 Agent 累计 token 用量。
        model: 目标 Agent 实际使用的模型名称。
    """

    def __init__(
        self,
        target_agent: str,
        content: str,
        usage: dict[str, int] | None = None,
        model: str = "",
    ) -> None:
        super().__init__(f"Handoff to '{target_agent}'")
        self.target_agent = target_agent
        self.content = content
        self.usage = usage or {}
        self.model = model
