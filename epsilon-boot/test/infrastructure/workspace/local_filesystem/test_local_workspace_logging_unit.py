"""LocalFilesystemWorkspace 结构化日志与脱敏单元测试（Phase 13.2）。

覆盖要点：

1. ``WorkspaceConfinementViolation`` 触发时结构化日志 ``extra`` 字段完整，
   含 ``workspace_backend_kind="local_filesystem"`` /
   ``violation_reason="symlink_escape"`` / 白名单 ``context`` 字段
   （证明 ``context`` 白名单成功透传）；
2. ``WorkspaceIoError`` 触发时 ``extra`` 含白名单字段；
3. 含 ``token=abcdef`` 的 ``requested_path`` 被替换为长度保留的 ``*``；
4. **关键负向断言**：
   - 领域异常 ``message`` **不含** ``workspace_root`` 宿主路径；
   - 领域异常 ``message`` **不含** ``trace_id`` / ``agent_id`` / ``tool_name``
     字面值；
5. 白名单外的 ``context`` 键（``secret`` 等）不进入 ``extra``。

测试使用 :func:`pytest.caplog` fixture 捕获结构化日志；需求 4.4 / 8.6 的
"路径泄露红线" 通过负向断言守住。
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path, PurePosixPath
from typing import Any

import pytest

from domain.workspace.exceptions import (
    ConfinementViolationReason,
    WorkspaceConfinementViolation,
    WorkspaceIoError,
    WorkspaceNotFoundError,
)
from domain.workspace.policy import WorkspacePolicy
from domain.workspace.value_objects import WorkspacePath
from infrastructure.workspace.local_filesystem.local_workspace import (
    LocalFilesystemWorkspace,
    log_confinement_violation,
    sanitize_context,
    sanitize_requested_path_for_log,
)

_SKIP_IF_WINDOWS = pytest.mark.skipif(
    sys.platform == "win32",
    reason="符号链接逃逸路径在 Windows 下依赖管理员权限",
)


def _make_ws(root: Path, *, follow_symlinks: bool = False) -> LocalFilesystemWorkspace:
    return LocalFilesystemWorkspace(
        root=root,
        follow_symlinks=follow_symlinks,
        policy=WorkspacePolicy(),
    )


# ---------------------------------------------------------------------------
# 1. _sanitize_context 白名单过滤（已在 7.12 覆盖；此处做 13.2 串联复核）
# ---------------------------------------------------------------------------


def test_sanitize_context_drops_non_whitelist_keys() -> None:
    result = sanitize_context(
        {
            "tool_name": "read_file",
            "trace_id": "t1",
            "agent_id": "a1",
            "secret": "leak",
            "password": "leak2",
            "unknown": "x",
        }
    )
    assert result == {"tool_name": "read_file", "trace_id": "t1", "agent_id": "a1"}
    assert "secret" not in result
    assert "password" not in result


# ---------------------------------------------------------------------------
# 2. _sanitize_requested_path_for_log：token/secret 值被等长 `*` 替换
# ---------------------------------------------------------------------------


class TestSanitizeRequestedPathForLog:
    def test_token_equals_value_is_masked(self) -> None:
        s = "/path?token=abcdef&other=keep"
        out = sanitize_requested_path_for_log(s)
        assert "abcdef" not in out
        assert "token=" in out
        assert "other=keep" in out
        # 长度保留（至少 3 个 `*`）
        assert "*" * len("abcdef") in out

    def test_secret_equals_value_is_masked(self) -> None:
        s = "/x?secret=xyz"
        out = sanitize_requested_path_for_log(s)
        assert "xyz" not in out
        assert "secret=" in out

    def test_password_case_insensitive(self) -> None:
        s = "/y?PASSWORD=1234"
        out = sanitize_requested_path_for_log(s)
        assert "1234" not in out
        assert "PASSWORD=" in out

    def test_api_key_variants_masked(self) -> None:
        s1 = "/z?api_key=aaa"
        s2 = "/z?api-key=bbb"
        assert "aaa" not in sanitize_requested_path_for_log(s1)
        assert "bbb" not in sanitize_requested_path_for_log(s2)

    def test_credential_masked(self) -> None:
        s = "/z?credential=zzz"
        out = sanitize_requested_path_for_log(s)
        assert "zzz" not in out
        assert "credential=" in out

    def test_plain_path_unchanged(self) -> None:
        s = "/a/b/c.md"
        out = sanitize_requested_path_for_log(s)
        assert out == s

    def test_short_value_padded_to_min_three_stars(self) -> None:
        """极短 value（长度 1）也至少替换成 3 个 `*`，便于识别。"""
        s = "/x?token=a"
        out = sanitize_requested_path_for_log(s)
        assert "a" not in out.split("token=")[1].split("&")[0]
        assert "***" in out


# ---------------------------------------------------------------------------
# 3. _log_confinement_violation：结构化字段齐全 + context 透传 + 脱敏
# ---------------------------------------------------------------------------


class TestLogConfinementViolation:
    def test_emits_warning_with_required_fields(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(
            logging.WARNING, logger="infrastructure.workspace.local_filesystem.local_workspace"
        )

        log_confinement_violation(
            operation="read",
            requested_path="../etc/passwd",
            resolved_workspace_path=None,
            violation_reason="absolute_outside",
            context={"tool_name": "read_file", "trace_id": "t1"},
        )

        rec = [r for r in caplog.records if r.message == "workspace_confinement_violation"]
        assert len(rec) == 1
        extra = rec[0]
        assert vars(extra)["workspace_backend_kind"] == "local_filesystem"
        assert vars(extra)["operation"] == "read"
        assert vars(extra)["violation_reason"] == "absolute_outside"
        assert vars(extra)["tool_name"] == "read_file"
        assert vars(extra)["trace_id"] == "t1"

    def test_emits_sanitized_requested_path(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(
            logging.WARNING, logger="infrastructure.workspace.local_filesystem.local_workspace"
        )

        log_confinement_violation(
            operation="read",
            requested_path="/hack?token=abcdef",
            resolved_workspace_path=None,
            violation_reason="absolute_outside",
            context={"tool_name": "read_file"},
        )

        rec = [r for r in caplog.records if r.message == "workspace_confinement_violation"][-1]
        path_in_log = str(vars(rec)["requested_path"])
        assert "abcdef" not in path_in_log
        assert "token=" in path_in_log

    def test_drops_non_whitelist_context_keys(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(
            logging.WARNING, logger="infrastructure.workspace.local_filesystem.local_workspace"
        )

        log_confinement_violation(
            operation="read",
            requested_path="/x",
            resolved_workspace_path=None,
            violation_reason="absolute_outside",
            context={"tool_name": "read_file", "secret": "leak", "password": "pwd"},
        )

        rec = [r for r in caplog.records if r.message == "workspace_confinement_violation"][-1]
        assert vars(rec)["tool_name"] == "read_file"
        # secret/password 必须不被记录到 extra
        assert not hasattr(rec, "secret")
        assert not hasattr(rec, "password")


# ---------------------------------------------------------------------------
# 4. 通过 _run_guards 触发越界（端到端 read → guard → log）
# ---------------------------------------------------------------------------


@_SKIP_IF_WINDOWS
@pytest.mark.asyncio
async def test_read_via_symlink_escape_logs_confinement_violation(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """``follow_symlinks=False`` 时，通过工作区内的符号链接读取外部文件会在
    守卫阶段抛 ``WorkspaceConfinementViolation``，并留下结构化日志。"""
    root = tmp_path / "ws"
    root.mkdir()
    external = tmp_path / "outside.txt"
    external.write_text("outside")
    link = root / "leak"
    link.symlink_to(external)

    ws = _make_ws(root, follow_symlinks=False)
    wp = ws.resolve_path("leak")

    caplog.set_level(
        logging.WARNING, logger="infrastructure.workspace.local_filesystem.local_workspace"
    )

    with pytest.raises(WorkspaceConfinementViolation):
        await ws.read(wp, context={"tool_name": "read_file", "trace_id": "t1"})

    matching = [r for r in caplog.records if r.message == "workspace_confinement_violation"]
    assert matching, "expected at least one workspace_confinement_violation log"
    rec = matching[-1]
    assert vars(rec)["workspace_backend_kind"] == "local_filesystem"
    assert vars(rec)["operation"] == "read"
    assert vars(rec)["tool_name"] == "read_file"
    assert vars(rec)["trace_id"] == "t1"
    assert vars(rec)["violation_reason"] == "symlink_escape"


# ---------------------------------------------------------------------------
# 5. WorkspaceIoError 路径：白名单字段进入 extra
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stat_permission_error_logs_with_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """通过 ``monkeypatch`` 让 ``os.stat`` 抛 ``PermissionError``，断言
    ``workspace_io_error`` 日志 ``extra`` 含白名单字段。"""
    root = tmp_path
    target = root / "a.txt"
    target.write_text("x")
    ws = _make_ws(root)
    wp = ws.resolve_path("a.txt")

    # 让 os.stat 抛权限错误（必须保留 IdentityGuard 的首次 stat，否则 guard
    # 会在 adapter 翻译分支之前先炸；见 2026-05-11 pytest 回归缺陷修复批次 D）。
    #
    # _guards.py 顶层 ``import os`` 与 ``local_workspace.os`` 是同一模块对象，
    # ``monkeypatch.setattr(_lw.os, "stat", ...)`` 会同时影响 IdentityGuard.check
    # 中的 ``os.stat(current)`` 调用；按"调用次数"区分：IdentityGuard 总是先调
    # 一次 ``os.stat(target)`` 用于跨设备校验（此次必须放行走 real_stat），
    # adapter ``stat()`` 随后调用第二次 ``os.stat(target)``，这一次才抛
    # PermissionError，触发 adapter 的 permission_denied 翻译分支。
    real_stat = os.stat
    call_counts: dict[str, int] = {}

    def fake_stat(
        path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
        *a: Any,
        **kw: Any,
    ) -> os.stat_result:
        key = str(path)
        if key == str(target):
            call_counts[key] = call_counts.get(key, 0) + 1
            # 首次（IdentityGuard.check 的跨设备校验）放行；第二次及之后
            # （adapter stat() 方法体内的那次 os.stat）抛 PermissionError。
            if call_counts[key] >= 2:
                raise PermissionError(13, "denied")
        return real_stat(path, *a, **kw)

    from infrastructure.workspace.local_filesystem import local_workspace as _lw

    monkeypatch.setattr(_lw.os, "stat", fake_stat)

    caplog.set_level(
        logging.WARNING, logger="infrastructure.workspace.local_filesystem.local_workspace"
    )

    with pytest.raises(WorkspaceIoError):
        await ws.stat(wp, context={"tool_name": "read_file", "trace_id": "t2"})

    matching = [r for r in caplog.records if r.message == "workspace_io_error"]
    assert matching
    rec = matching[-1]
    assert vars(rec)["tool_name"] == "read_file"
    assert vars(rec)["trace_id"] == "t2"


# ---------------------------------------------------------------------------
# 6. 负向断言：领域异常 message 不泄露 context / workspace_root 字段
# ---------------------------------------------------------------------------


class TestDomainErrorMessageNegativeAssertions:
    """需求 4.4 / 8.6 红线：领域错误 message 不含敏感 / 部署信息。"""

    def test_confinement_violation_message_no_context_keys(self) -> None:
        err = WorkspaceConfinementViolation(
            requested_path="../etc",
            reason=ConfinementViolationReason.ABSOLUTE_OUTSIDE,
        )
        msg = err.message
        for forbidden in ["tool_name", "trace_id", "agent_id"]:
            assert forbidden not in msg, f"`{forbidden}` 不得出现在 message"

    def test_io_error_message_no_context_keys(self) -> None:
        err = WorkspaceIoError(
            operation="read",
            workspace_path=WorkspacePath(_posix=PurePosixPath("/a")),
            reason="permission_denied",
            underlying_error_class="PermissionError",
        )
        msg = err.message
        for forbidden in ["tool_name", "trace_id", "agent_id"]:
            assert forbidden not in msg

    def test_not_found_error_message_no_context_keys(self) -> None:
        err = WorkspaceNotFoundError(workspace_path=WorkspacePath(_posix=PurePosixPath("/missing")))
        msg = err.message
        for forbidden in ["tool_name", "trace_id", "agent_id"]:
            assert forbidden not in msg

    def test_domain_errors_do_not_store_host_root_in_message(self) -> None:
        """领域错误 ``message`` 不含 ``/var/`` / ``/home/`` / ``/Users/`` 等
        典型宿主根前缀（避免被误拼入）。"""
        err = WorkspaceIoError(
            operation="read",
            workspace_path=WorkspacePath(_posix=PurePosixPath("/a")),
            reason="os_error",
        )
        msg = err.message
        for forbidden in ["/var/", "/home/", "/root/", "/Users/", "/tmp/"]:
            assert forbidden not in msg, f"宿主根 `{forbidden}` 不得出现在 message"
