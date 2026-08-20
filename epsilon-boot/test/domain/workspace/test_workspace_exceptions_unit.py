"""Workspace 领域错误单元测试模块。

覆盖 4 种领域错误的字段、错误码、继承关系，以及"构造签名不含 ``context``"
这一路径泄露红线（需求 4.4 / 8.6）。
"""

from __future__ import annotations

import inspect
from pathlib import PurePosixPath

from common.exceptions import BizException
from domain.workspace.exceptions import (
    ConfinementViolationReason,
    WorkspaceConfinementViolation,
    WorkspaceIoError,
    WorkspaceNotFoundError,
    WorkspaceUnsupportedOperationError,
    _WorkspaceError,
)
from domain.workspace.value_objects import WorkspacePath


def _wp(posix: str) -> WorkspacePath:
    """测试夹具：构造一个 ``WorkspacePath``。"""
    return WorkspacePath(_posix=PurePosixPath(posix))


class TestConfinementViolationReasonEnum:
    """``ConfinementViolationReason`` 枚举值完整性。"""

    def test_all_expected_members(self) -> None:
        """枚举应覆盖 7 种具体原因。"""
        expected = {
            "NUL_BYTE",
            "BACKSLASH",
            "WINDOWS_DRIVE",
            "UNC_PATH",
            "ABSOLUTE_OUTSIDE",
            "SYMLINK_ESCAPE",
            "CROSS_DEVICE",
        }
        actual = {m.name for m in ConfinementViolationReason}
        assert actual == expected

    def test_values_are_lower_snake_case(self) -> None:
        """枚举值使用小写 snake_case，便于直接作日志字段取值。"""
        assert ConfinementViolationReason.ABSOLUTE_OUTSIDE.value == "absolute_outside"
        assert ConfinementViolationReason.NUL_BYTE.value == "nul_byte"


class TestWorkspaceConfinementViolation:
    """``WorkspaceConfinementViolation`` 的字段与代码。"""

    def test_code_is_60501(self) -> None:
        exc = WorkspaceConfinementViolation(
            requested_path="../etc",
            reason=ConfinementViolationReason.ABSOLUTE_OUTSIDE,
        )
        assert exc.code == 60501

    def test_inherits_from_workspace_error_and_biz_exception(self) -> None:
        exc = WorkspaceConfinementViolation(
            requested_path="../",
            reason=ConfinementViolationReason.ABSOLUTE_OUTSIDE,
        )
        assert isinstance(exc, _WorkspaceError)
        assert isinstance(exc, BizException)

    def test_preserves_reason_enum(self) -> None:
        exc = WorkspaceConfinementViolation(
            requested_path="C:\\",
            reason=ConfinementViolationReason.WINDOWS_DRIVE,
        )
        assert exc.reason is ConfinementViolationReason.WINDOWS_DRIVE
        assert exc.requested_path == "C:\\"
        assert exc.resolved_workspace_path is None

    def test_optional_resolved_workspace_path_kept(self) -> None:
        wp = _wp("/a")
        exc = WorkspaceConfinementViolation(
            requested_path="/a/../..",
            reason=ConfinementViolationReason.ABSOLUTE_OUTSIDE,
            resolved_workspace_path=wp,
        )
        assert exc.resolved_workspace_path == wp

    def test_message_is_chinese_and_contains_reason_value(self) -> None:
        exc = WorkspaceConfinementViolation(
            requested_path="../etc",
            reason=ConfinementViolationReason.ABSOLUTE_OUTSIDE,
        )
        assert "超出工作区边界" in exc.message
        # 消息中含 reason.value，便于运维诊断。
        assert "absolute_outside" in exc.message

    def test_message_does_not_contain_host_root(self) -> None:
        """消息不得含任何宿主绝对路径（守住需求 4.4 / 8.6）。"""
        exc = WorkspaceConfinementViolation(
            requested_path="../etc",
            reason=ConfinementViolationReason.ABSOLUTE_OUTSIDE,
        )
        # 用业界常见的宿主根前缀做负向断言，不得泄露。
        for forbidden in ("/var/", "/home/", "/root/", "/Users/", "C:\\"):
            assert forbidden not in exc.message


class TestWorkspaceNotFoundError:
    """``WorkspaceNotFoundError`` 的字段与代码。"""

    def test_code_is_60502(self) -> None:
        exc = WorkspaceNotFoundError(workspace_path=_wp("/a"))
        assert exc.code == 60502

    def test_inherits_from_workspace_error(self) -> None:
        exc = WorkspaceNotFoundError(workspace_path=_wp("/a"))
        assert isinstance(exc, _WorkspaceError)
        assert isinstance(exc, BizException)

    def test_retains_workspace_path(self) -> None:
        wp = _wp("/a/b")
        exc = WorkspaceNotFoundError(workspace_path=wp)
        assert exc.workspace_path == wp

    def test_message_is_chinese(self) -> None:
        exc = WorkspaceNotFoundError(workspace_path=_wp("/a/b"))
        assert "不存在" in exc.message
        assert "/a/b" in exc.message


class TestWorkspaceIoError:
    """``WorkspaceIoError`` 的字段与代码。"""

    def test_code_is_60503(self) -> None:
        exc = WorkspaceIoError(
            operation="read",
            workspace_path=_wp("/a"),
            reason="permission_denied",
        )
        assert exc.code == 60503

    def test_inherits_from_workspace_error(self) -> None:
        exc = WorkspaceIoError(
            operation="read",
            workspace_path=_wp("/a"),
            reason="permission_denied",
        )
        assert isinstance(exc, _WorkspaceError)
        assert isinstance(exc, BizException)

    def test_retains_all_fields(self) -> None:
        wp = _wp("/a/b.txt")
        exc = WorkspaceIoError(
            operation="write",
            workspace_path=wp,
            reason="cross_device",
            underlying_error_class="OSError",
        )
        assert exc.operation == "write"
        assert exc.workspace_path == wp
        assert exc.reason == "cross_device"
        assert exc.underlying_error_class == "OSError"

    def test_underlying_error_class_defaults_to_empty_string(self) -> None:
        exc = WorkspaceIoError(
            operation="read",
            workspace_path=_wp("/a"),
            reason="decode_failed",
        )
        assert exc.underlying_error_class == ""

    def test_message_does_not_contain_host_root(self) -> None:
        exc = WorkspaceIoError(
            operation="read",
            workspace_path=_wp("/a"),
            reason="permission_denied",
        )
        for forbidden in ("/var/", "/home/", "/root/", "/Users/"):
            assert forbidden not in exc.message


class TestWorkspaceUnsupportedOperationError:
    """``WorkspaceUnsupportedOperationError`` 的字段与代码。"""

    def test_code_is_60504(self) -> None:
        exc = WorkspaceUnsupportedOperationError(
            operation="shell_exec",
            capability="local_materialization",
        )
        assert exc.code == 60504

    def test_inherits_from_workspace_error(self) -> None:
        exc = WorkspaceUnsupportedOperationError(
            operation="shell_exec",
            capability="local_materialization",
        )
        assert isinstance(exc, _WorkspaceError)
        assert isinstance(exc, BizException)

    def test_retains_all_fields(self) -> None:
        exc = WorkspaceUnsupportedOperationError(
            operation="write",
            capability="supports_atomic_write",
        )
        assert exc.operation == "write"
        assert exc.capability == "supports_atomic_write"

    def test_message_is_chinese(self) -> None:
        exc = WorkspaceUnsupportedOperationError(
            operation="write",
            capability="supports_atomic_write",
        )
        assert "不支持" in exc.message


class TestNoContextParameterInConstructors:
    """关键红线：4 种领域错误的构造签名**不得**包含 ``context`` 参数。

    观测上下文（``tool_name`` / ``trace_id`` / ``agent_id``）只应写入
    ``logger.*(extra=...)``，永不进入异常 message 或 ``__dict__``
    （守住需求 4.4 / 8.6）。
    """

    _ERROR_CLASSES = (
        WorkspaceConfinementViolation,
        WorkspaceNotFoundError,
        WorkspaceIoError,
        WorkspaceUnsupportedOperationError,
    )

    def test_no_context_param_in_any_constructor(self) -> None:
        for cls in self._ERROR_CLASSES:
            sig = inspect.signature(cls.__init__)
            assert "context" not in sig.parameters, (
                f"{cls.__name__}.__init__ 不得接受 context 参数，当前签名：{sig}"
            )

    def test_no_context_param_variants(self) -> None:
        """防御性：检查与 ``context`` 常见别名均未出现。"""
        banned = {"context", "ctx", "log_context", "trace_id", "agent_id", "tool_name"}
        for cls in self._ERROR_CLASSES:
            sig = inspect.signature(cls.__init__)
            leaked = set(sig.parameters.keys()) & banned
            assert not leaked, (
                f"{cls.__name__}.__init__ 不得接受观测上下文相关参数，但出现：{sorted(leaked)}"
            )
