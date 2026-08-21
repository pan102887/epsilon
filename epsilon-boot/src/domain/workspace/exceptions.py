"""Workspace 领域错误定义模块。

定义 Workspace 抽象层的后端无关领域错误。所有错误继承自 ``_WorkspaceError``
基类（进一步继承自 ``common.exceptions.BizException``），错误码统一使用
``605xx`` 段以避免与工具层 ``600xx`` 冲突。

**关键约束（守住需求 4.4 / 8.6 路径泄露红线）**：

- 4 种领域错误构造参数均**不包含** ``context`` 字段；调用方的观测上下文
  （``tool_name`` / ``trace_id`` / ``agent_id`` 等白名单字段）只进入
  ``logger.*(extra=...)``，**永远不参与异常 message 拼装**。
- 错误消息中的 ``workspace_path`` 形参一律使用逻辑路径字符串（``WorkspacePath``
  的 POSIX 形式），不得混入宿主绝对路径。
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from common.exceptions import BizException

if TYPE_CHECKING:
    # 仅用于类型注解，运行时不引入循环依赖。
    from domain.workspace.value_objects import WorkspacePath


class ConfinementViolationReason(StrEnum):
    """逻辑路径越界或非法字符的具体原因。

    每个枚举值同时作为结构化日志字段 ``violation_reason`` 的取值，便于
    日志聚合与测试用例逐一覆盖。
    """

    NUL_BYTE = "nul_byte"
    BACKSLASH = "backslash"
    WINDOWS_DRIVE = "windows_drive"
    UNC_PATH = "unc_path"
    ABSOLUTE_OUTSIDE = "absolute_outside"
    SYMLINK_ESCAPE = "symlink_escape"
    CROSS_DEVICE = "cross_device"


class _WorkspaceError(BizException):
    """Workspace 领域错误基类。

    所有 Workspace 领域错误均继承自此类，错误码使用 ``605xx`` 段。本类本身
    不直接抛出，仅用于类型归类与 ``except _WorkspaceError`` 聚合处理。
    """


WorkspaceError = _WorkspaceError


class WorkspaceConfinementViolation(_WorkspaceError):
    """逻辑路径越界或含非法字符。

    构造参数中**不含** ``context`` 字段——观测上下文须由调用方单独写入
    ``logger.*(extra=...)``，不可拼入 ``message``。

    Attributes:
        requested_path: 原始请求字符串（未被 Policy 裁剪）。
        reason: 具体违规原因。
        resolved_workspace_path: Policy 部分归一化后的路径（若可用）；
            ``None`` 表示尚未归一化即被拒绝。
    """

    def __init__(
        self,
        requested_path: str,
        reason: ConfinementViolationReason,
        resolved_workspace_path: WorkspacePath | None = None,
    ) -> None:
        super().__init__(
            code=60501,
            message=f"路径 {requested_path} 超出工作区边界（{reason.value}）",
        )
        self.requested_path = requested_path
        self.reason = reason
        self.resolved_workspace_path = resolved_workspace_path


class WorkspaceNotFoundError(_WorkspaceError):
    """请求的 ``WorkspacePath`` 在后端不存在。

    构造参数中**不含** ``context`` 字段。

    Attributes:
        workspace_path: 发生错误的逻辑路径。
    """

    def __init__(self, workspace_path: WorkspacePath) -> None:
        super().__init__(
            code=60502,
            message=f"路径 {workspace_path} 不存在",
        )
        self.workspace_path = workspace_path


class WorkspaceIoError(_WorkspaceError):
    """后端 I/O 失败的统一包装。

    构造参数中**不含** ``context`` 字段。``underlying_error_class`` 保留
    底层异常类名，仅供服务端日志/诊断使用；``message`` 对 LLM 友好、不含
    宿主绝对路径。

    Attributes:
        operation: 触发失败的逻辑操作名称（``exists``/``read``/``write``…）。
        workspace_path: 发生错误的逻辑路径。
        reason:
            失败的简要原因（``permission_denied``/``decode_failed``/
            ``cross_device``/``lock_failed``/``no_match`` 等）。
        underlying_error_class: 底层异常类名（如 ``FileNotFoundError``），
            默认为空串；仅用于服务端诊断。
    """

    def __init__(
        self,
        operation: str,
        workspace_path: WorkspacePath,
        reason: str,
        underlying_error_class: str = "",
    ) -> None:
        super().__init__(
            code=60503,
            message=f"{operation} 操作失败：{reason}",
        )
        self.operation = operation
        self.workspace_path = workspace_path
        self.reason = reason
        self.underlying_error_class = underlying_error_class


class WorkspaceUnsupportedOperationError(_WorkspaceError):
    """当前后端不支持调用方请求的能力。

    构造参数中**不含** ``context`` 字段。

    Attributes:
        operation: 被拒绝的逻辑操作名称。
        capability: 缺失的能力名称（对应 ``WorkspaceCapabilities`` 的字段名）。
    """

    def __init__(
        self,
        operation: str,
        capability: str,
    ) -> None:
        super().__init__(
            code=60504,
            message=f"当前工作区后端不支持 {capability}（操作：{operation}）",
        )
        self.operation = operation
        self.capability = capability
