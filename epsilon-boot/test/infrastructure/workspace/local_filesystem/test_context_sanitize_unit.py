"""``_sanitize_context`` 白名单过滤 + 领域异常不含 ``context`` 字段负向断言。

覆盖范围（对应 tasks 7.12 / 需求 4.4 / 8.6 路径泄露红线）：

- 正向：白名单键保留；未知键被过滤；``None`` / 空字典容忍。
- **关键负向**：4 种领域错误（``WorkspaceIoError`` /
  ``WorkspaceNotFoundError`` / ``WorkspaceConfinementViolation`` /
  ``WorkspaceUnsupportedOperationError``）：

  1. 构造签名（``inspect.signature``）中**不得**存在 ``context`` 形参；
  2. 错误 ``message`` 字符串中**不得**出现 ``tool_name`` / ``trace_id`` /
     ``agent_id`` 字面量（即使这些词作为变量值传入 ``operation`` /
     ``reason`` 等字段也不会出现在 message 中）；
  3. 实例 ``__dict__`` 中**不得**存在 ``context`` 字段。
"""

from __future__ import annotations

import inspect
from pathlib import PurePosixPath

from domain.workspace.exceptions import (
    ConfinementViolationReason,
    WorkspaceConfinementViolation,
    WorkspaceIoError,
    WorkspaceNotFoundError,
    WorkspaceUnsupportedOperationError,
)
from domain.workspace.value_objects import WorkspacePath
from infrastructure.workspace.local_filesystem.local_workspace import (
    _LOG_CONTEXT_WHITELIST,
    _sanitize_context,
)


class TestSanitizeContextWhitelist:
    """白名单过滤行为（正向 + 常见异常输入）。"""

    def test_none_returns_empty_dict(self) -> None:
        """``None`` → ``{}``（容忍 optional 参数）。"""
        assert _sanitize_context(None) == {}

    def test_empty_dict_returns_empty_dict(self) -> None:
        """空 dict → ``{}``。"""
        assert _sanitize_context({}) == {}

    def test_single_whitelisted_key_preserved(self) -> None:
        """单个白名单键保留。"""
        assert _sanitize_context({"tool_name": "read_file"}) == {"tool_name": "read_file"}

    def test_all_three_whitelisted_keys_preserved(self) -> None:
        """三个白名单键全保留。"""
        inp = {"tool_name": "read_file", "trace_id": "t1", "agent_id": "a1"}
        assert _sanitize_context(inp) == inp

    def test_unknown_keys_filtered_out(self) -> None:
        """白名单之外的键（含疑似敏感的 ``secret`` / ``password``）全部过滤。"""
        out = _sanitize_context(
            {
                "tool_name": "read_file",
                "secret": "xxx",
                "password": "yyy",
                "api_key": "zzz",
            }
        )
        assert out == {"tool_name": "read_file"}

    def test_only_unknown_keys_returns_empty(self) -> None:
        """只含未知键 → ``{}``。"""
        assert _sanitize_context({"unknown_key": "value", "foo": "bar"}) == {}

    def test_whitelist_exact_members(self) -> None:
        """白名单集合与 design §组件与接口 2 / 需求 8.1 声明一致。"""
        assert frozenset({"tool_name", "trace_id", "agent_id"}) == _LOG_CONTEXT_WHITELIST


class TestDomainErrorsHaveNoContextField:
    """4 种领域错误的构造签名不得存在 ``context`` 形参（红线）。"""

    def test_workspace_io_error_signature(self) -> None:
        sig = inspect.signature(WorkspaceIoError.__init__)
        assert "context" not in sig.parameters

    def test_workspace_not_found_error_signature(self) -> None:
        sig = inspect.signature(WorkspaceNotFoundError.__init__)
        assert "context" not in sig.parameters

    def test_workspace_confinement_violation_signature(self) -> None:
        sig = inspect.signature(WorkspaceConfinementViolation.__init__)
        assert "context" not in sig.parameters

    def test_workspace_unsupported_operation_error_signature(self) -> None:
        sig = inspect.signature(WorkspaceUnsupportedOperationError.__init__)
        assert "context" not in sig.parameters


class TestDomainErrorMessagesDoNotLeakContextKeys:
    """``message`` 字符串中不得出现 ``tool_name`` / ``trace_id`` / ``agent_id`` 字面量。"""

    _FORBIDDEN_SUBSTRINGS = ("tool_name", "trace_id", "agent_id")

    def test_workspace_io_error_message_clean(self) -> None:
        wp = WorkspacePath(_posix=PurePosixPath("/notes.md"))
        err = WorkspaceIoError(
            operation="read",
            workspace_path=wp,
            reason="permission_denied",
            underlying_error_class="PermissionError",
        )
        for key in self._FORBIDDEN_SUBSTRINGS:
            assert key not in err.message, f"{key} leaked into WorkspaceIoError.message"

    def test_workspace_not_found_error_message_clean(self) -> None:
        wp = WorkspacePath(_posix=PurePosixPath("/notes.md"))
        err = WorkspaceNotFoundError(workspace_path=wp)
        for key in self._FORBIDDEN_SUBSTRINGS:
            assert key not in err.message

    def test_workspace_confinement_violation_message_clean(self) -> None:
        err = WorkspaceConfinementViolation(
            requested_path="../etc/passwd",
            reason=ConfinementViolationReason.ABSOLUTE_OUTSIDE,
        )
        for key in self._FORBIDDEN_SUBSTRINGS:
            assert key not in err.message

    def test_workspace_unsupported_operation_error_message_clean(self) -> None:
        err = WorkspaceUnsupportedOperationError(
            operation="stream_read",
            capability="supports_streaming",
        )
        for key in self._FORBIDDEN_SUBSTRINGS:
            assert key not in err.message


class TestDomainErrorsDoNotStoreContextAttribute:
    """实例的 ``__dict__`` 不得含 ``context`` 字段（构造参数未接受 context）。"""

    def test_workspace_io_error_no_context_attr(self) -> None:
        wp = WorkspacePath(_posix=PurePosixPath("/a"))
        err = WorkspaceIoError(operation="x", workspace_path=wp, reason="r")
        assert "context" not in err.__dict__

    def test_workspace_not_found_error_no_context_attr(self) -> None:
        wp = WorkspacePath(_posix=PurePosixPath("/a"))
        err = WorkspaceNotFoundError(workspace_path=wp)
        assert "context" not in err.__dict__

    def test_workspace_confinement_violation_no_context_attr(self) -> None:
        err = WorkspaceConfinementViolation(
            requested_path="x",
            reason=ConfinementViolationReason.NUL_BYTE,
        )
        assert "context" not in err.__dict__

    def test_workspace_unsupported_operation_error_no_context_attr(self) -> None:
        err = WorkspaceUnsupportedOperationError(operation="x", capability="y")
        assert "context" not in err.__dict__
