"""本地文件系统工作区适配器。

本模块实现 ``LocalFilesystemWorkspace``，即 ``domain.workspace.ports.Workspace``
Port 的本地文件系统后端，同时实现 ``LocallyMaterializable`` 子协议以对
``ShellExecTool`` / ``PythonExecTool`` 暴露宿主 ``cwd``。

**依赖白名单**（与 ``docs/spec/workspace/design.md`` §架构一致）：

- 仅允许依赖 Python 标准库 + ``domain.workspace.*`` + 同级 ``_guards`` /
  ``_common_impl``。
- **禁止**依赖 ``infrastructure.chat.*`` 等其他基础设施模块，**禁止**依赖
  工具层代码。

**结构化日志与观测上下文红线**（需求 4.4 / 8.6）：

- 所有 I/O 方法的末位 keyword-only 形参 ``context: dict | None = None``
  仅作为观测透传通道，**不改变** I/O 行为；
- 观测字段白名单 ``_LOG_CONTEXT_WHITELIST`` 限定为 ``tool_name`` /
  ``trace_id`` / ``agent_id``；
- ``context`` 仅可合并进 ``logger.*(extra=...)``，**绝不**拼入任何领域
  异常的 ``message``，**绝不**传入 4 种领域错误（``WorkspaceIoError`` /
  ``WorkspaceNotFoundError`` / ``WorkspaceConfinementViolation`` /
  ``WorkspaceUnsupportedOperationError``）的构造参数。

模块级常量：

- :data:`_LOG_CONTEXT_WHITELIST`：
  允许合并进结构化日志 ``extra`` 的 context 键白名单。
- :data:`_windows_warning_emitted`：
  Windows 下 ``edit`` 首次跳过 ``flock`` 时记录一次 warning 的哨兵。

模块级函数：

- :func:`_sanitize_context`：从 ``context`` 中按白名单过滤字段。
"""

from __future__ import annotations

import contextlib
import errno
import logging
import os
import platform
import re
import shutil
import stat as _stat
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol, cast

from domain.workspace.exceptions import (
    WorkspaceConfinementViolation,
    WorkspaceIoError,
    WorkspaceNotFoundError,
)
from domain.workspace.policy import WorkspacePolicy
from domain.workspace.ports import LocallyMaterializable, Workspace
from domain.workspace.value_objects import (
    WorkspaceCapabilities,
    WorkspacePath,
    WorkspaceStatEntry,
)
from infrastructure.workspace.local_filesystem._common_impl import (
    edit_with_fallback_match as _edit_with_fallback_match,
)
from infrastructure.workspace.local_filesystem._common_impl import (
    read_bytes_in_range as _read_bytes_in_range,
)
from infrastructure.workspace.local_filesystem._common_impl import (
    write_bytes_atomically as _write_bytes_atomically,
)
from infrastructure.workspace.local_filesystem._guards import (
    IdentityGuard,
    SymlinkGuard,
)

logger = logging.getLogger(__name__)


# ── 观测上下文白名单（需求 4.4 / 8.6） ──

_LOG_CONTEXT_WHITELIST: frozenset[str] = frozenset({"tool_name", "trace_id", "agent_id"})
LOG_CONTEXT_WHITELIST = _LOG_CONTEXT_WHITELIST
"""允许合并进结构化日志 ``extra`` 的 ``context`` 键白名单。

**红线**：此集合以外的任何 ``context`` 键都必须被 :func:`_sanitize_context`
过滤掉。领域异常的 ``message`` 中绝不允许出现此集合中任一字段的值。
"""


def _sanitize_context(context: Mapping[str, object] | None) -> dict[str, object]:
    """从 ``context`` 中仅提取白名单字段，容忍 ``None`` 与未知 key。

    本函数是 ``LocalFilesystemWorkspace`` 所有 I/O 方法合并观测上下文到
    结构化日志的**唯一入口**，其返回字典只会出现在 ``logger.*(extra=...)``
    里，绝不会进入领域异常的 ``message`` 或构造参数。

    Args:
        context: 调用方提供的观测上下文；可为 ``None`` / 空字典 /
            含未知 key 的字典。

    Returns:
        仅含 :data:`_LOG_CONTEXT_WHITELIST` 白名单键的字典；
        ``context`` 为空或不含白名单键时返回 ``{}``。
    """
    if not context:
        return {}
    return {k: v for k, v in context.items() if k in _LOG_CONTEXT_WHITELIST}


def sanitize_context(context: Mapping[str, object] | None) -> dict[str, object]:
    """返回允许写入结构化日志的上下文字段。"""
    return _sanitize_context(context)


# ── 路径敏感子串脱敏（需求 8.3） ──

_SENSITIVE_PATH_KEY_PATTERN = re.compile(
    r"(token|secret|password|api[_-]?key|credential)=([^&\s]+)",
    re.IGNORECASE,
)
"""匹配 ``requested_path`` 中形如 ``token=abcdef`` 的敏感 query 片段。

对 group(2)（即敏感值本身）做长度保留的 ``***`` 替换；其余子串保持不变。
本正则只用于**结构化日志脱敏**，不影响领域错误语义。
"""


def _sanitize_requested_path_for_log(requested_path: str) -> str:
    """对 ``requested_path`` 做日志级脱敏（需求 8.3）。

    若含 ``token= / secret= / password= / api_key= / api-key= / credential=`` 的
    query 片段，将其值替换为等长度的 ``*`` 字符（至少 3 个），其余内容保持不变。
    替换尽力保留原始长度以便调用方在日志中仍能观察 value 长度维度。

    Args:
        requested_path: 原始请求路径字符串。

    Returns:
        脱敏后的字符串，与原长度一致（或近似一致）。
    """

    def _replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = match.group(2)
        # 长度保留：等长 '*'，下限 3 保证可识别
        masked = "*" * max(3, len(value))
        return f"{key}={masked}"

    return _SENSITIVE_PATH_KEY_PATTERN.sub(_replace, requested_path)


def sanitize_requested_path_for_log(requested_path: str) -> str:
    """返回适合写入日志的脱敏请求路径。"""
    return _sanitize_requested_path_for_log(requested_path)


def _log_confinement_violation(
    *,
    operation: str,
    requested_path: str,
    resolved_workspace_path: str | None,
    violation_reason: str,
    context: Mapping[str, object] | None,
) -> None:
    """结构化日志：越界违规（需求 8.1）。

    字段固定包含：``workspace_backend_kind`` / ``operation`` /
    ``requested_path``（已脱敏）/ ``resolved_workspace_path``（可空）/
    ``violation_reason`` / + 白名单 ``context`` 字段。

    **红线**：``context`` 仅合并进 ``extra``，绝不进入异常 ``message``；
    ``requested_path`` 必经 :func:`_sanitize_requested_path_for_log` 脱敏。

    Args:
        operation: 触发违规的操作名（``read`` / ``write`` / ...）。
        requested_path: 原始入参字符串；记录前会做敏感子串替换。
        resolved_workspace_path: 若已解析出合法 WorkspacePath 的 POSIX 字符串。
        violation_reason: :class:`ConfinementViolationReason` 枚举值。
        context: 白名单观测字段，容忍 ``None``。
    """
    logger.warning(
        "workspace_confinement_violation",
        extra={
            "workspace_backend_kind": "local_filesystem",
            "operation": operation,
            "requested_path": _sanitize_requested_path_for_log(requested_path),
            "resolved_workspace_path": resolved_workspace_path,
            "violation_reason": violation_reason,
            **_sanitize_context(context),
        },
    )


def log_confinement_violation(
    *,
    operation: str,
    requested_path: str,
    resolved_workspace_path: str | None,
    violation_reason: str,
    context: Mapping[str, object] | None,
) -> None:
    """记录工作区边界违规的脱敏结构化日志。"""
    _log_confinement_violation(
        operation=operation,
        requested_path=requested_path,
        resolved_workspace_path=resolved_workspace_path,
        violation_reason=violation_reason,
        context=context,
    )


# ── Windows `edit` 无锁降级的一次性 warning 哨兵 ──

_windows_warning_emitted = False
"""Windows 下 ``edit`` 首次跳过 ``fcntl.flock`` 时记录一次 warning 的哨兵。

本进程生命周期内首次触发 Windows 降级分支时置 ``True``，避免每次 ``edit``
都重复打印同一条警告（降低日志噪声，保留一次性告警的可观测性）。
"""


def reset_windows_warning_sentinel() -> None:
    """Reset the process-local Windows fallback warning state for isolated tests."""
    global _windows_warning_emitted
    _windows_warning_emitted = False


class LocalFilesystemWorkspace(Workspace, LocallyMaterializable):
    """基于本地文件系统的 ``Workspace`` 实现。

    启动期完成一次性 root 校验（由上游 ``_create_local_filesystem_workspace``
    工厂负责），构造时缓存 ``SymlinkGuard`` 与 ``IdentityGuard`` 两个守卫
    实例；所有 I/O 入口先经两守卫做二次越界防御，再执行宿主 I/O。

    字节级实现位于同级 ``_common_impl.py``；历史 ``common.tools.common_tools``
    薄壳已删除。所有 I/O 方法遵循统一日志模式：

        logger.<level>(
            "<event_name>",
            extra={
                "workspace_backend_kind": "local_filesystem",
                "operation": "<method_name>",
                "workspace_path": path.to_posix(),
                **_sanitize_context(context),
            },
        )

    错误分支额外合并 ``"underlying_error_class": type(e).__name__``。
    """

    def __init__(
        self,
        *,
        root: Path,
        follow_symlinks: bool,
        policy: WorkspacePolicy,
    ) -> None:
        """初始化本地工作区。

        调用方（工厂层）必须保证 ``root`` 已由 ``Path.resolve()`` 规范化、
        存在且为可读写目录，否则 ``IdentityGuard`` 构造时对 ``os.stat(root)``
        的调用会原样向上穿透。

        Args:
            root: 工作区宿主绝对目录。
            follow_symlinks: 是否允许跟随符号链接；直接决定
                ``_capabilities.supports_symlinks``。
            policy: ``WorkspacePolicy`` 纯函数对象，用于 ``resolve_path``。
        """
        self._root: Path = root
        self._follow_symlinks: bool = follow_symlinks
        self._policy: WorkspacePolicy = policy
        self._symlink_guard: SymlinkGuard = SymlinkGuard(
            root=root,
            follow_symlinks=follow_symlinks,
        )
        self._identity_guard: IdentityGuard = IdentityGuard(root=root)
        self._capabilities: WorkspaceCapabilities = WorkspaceCapabilities(
            supports_symlinks=follow_symlinks,
            supports_atomic_write=True,
            supports_append=True,
            supports_streaming=False,
            supports_large_files=True,
            local_materialization=True,
        )

    # ── Workspace 纯函数 / 元数据方法 ──

    def resolve_path(self, requested: str) -> WorkspacePath:
        """委托 ``WorkspacePolicy.resolve`` 做纯函数式归一化。

        Args:
            requested: 原始请求字符串。

        Returns:
            归一化后的合法 ``WorkspacePath``。
        """
        return self._policy.resolve(requested)

    def _run_guards(
        self,
        *,
        host_path: Path,
        operation: str,
        logical_path: WorkspacePath,
        context: Mapping[str, object] | None,
    ) -> None:
        """运行两守卫并在越界时落结构化日志（需求 8.1 / 8.3）。

        当 ``SymlinkGuard`` / ``IdentityGuard`` 之一抛出
        :class:`WorkspaceConfinementViolation` 时，先通过
        :func:`_log_confinement_violation` 落一条 ``warning`` 级别结构化
        日志，再原样向上抛出原异常。本方法**不吞异常**，只补充观测字段。

        Args:
            host_path: 待校验的宿主路径（已由 :meth:`_to_host_path` 构造）。
            operation: 当前调用的操作名（``read`` / ``write`` / ...）。
            logical_path: 对应的逻辑路径，用于日志的 ``resolved_workspace_path``。
            context: 调用方透传的观测上下文。

        Raises:
            WorkspaceConfinementViolation: 守卫原抛异常，不改写 ``message``。
        """
        try:
            self._symlink_guard.check(host_path)
            self._identity_guard.check(host_path)
        except WorkspaceConfinementViolation as exc:
            _log_confinement_violation(
                operation=operation,
                requested_path=exc.requested_path,
                resolved_workspace_path=logical_path.to_posix(),
                violation_reason=exc.reason.value,
                context=context,
            )
            raise

    def capabilities(self) -> WorkspaceCapabilities:
        """返回本后端在实例生命周期内恒定的能力声明。"""
        return self._capabilities

    def display_root_hint(self) -> str:
        """返回宿主绝对路径字符串，供工具 ``description`` 动态拼接。

        决策 3-B：用户在设计审批阶段明确接受此字符串进入 LLM 上下文，以
        换取 LLM 对相对路径更准确的心智；实现者不得返回凭证、签名等敏感
        信息。
        """
        return str(self._root)

    # ── Workspace I/O 方法（7.2 - 7.7 实现） ──

    async def exists(
        self,
        path: WorkspacePath,
        *,
        context: Mapping[str, object] | None = None,
    ) -> bool:
        """判定 ``path`` 是否存在。

        ``host_path.exists()`` 在跨平台下对祖先不可读的场景可能原生抛
        ``PermissionError``（``OSError`` 的子类），此时翻译为
        ``WorkspaceIoError(reason="permission_denied")``。

        Args:
            path: 要查询的逻辑路径。
            context: 观测上下文白名单字段，默认 ``None``。

        Returns:
            存在为 ``True``，否则 ``False``。

        Raises:
            WorkspaceConfinementViolation: 触发两守卫的越界判定。
            WorkspaceIoError: 权限受限或其他 ``OSError``；``reason`` 对应
                ``permission_denied`` / ``os_error``。
        """
        host_path = self._to_host_path(path)
        self._run_guards(
            host_path=host_path,
            operation="exists",
            logical_path=path,
            context=context,
        )
        try:
            return host_path.exists()
        except PermissionError as e:
            logger.warning(
                "workspace_io_error",
                extra={
                    "workspace_backend_kind": "local_filesystem",
                    "operation": "exists",
                    "workspace_path": path.to_posix(),
                    "underlying_error_class": type(e).__name__,
                    **_sanitize_context(context),
                },
            )
            raise WorkspaceIoError(
                operation="exists",
                workspace_path=path,
                reason="permission_denied",
                underlying_error_class=type(e).__name__,
            ) from e
        except OSError as e:
            logger.warning(
                "workspace_io_error",
                extra={
                    "workspace_backend_kind": "local_filesystem",
                    "operation": "exists",
                    "workspace_path": path.to_posix(),
                    "underlying_error_class": type(e).__name__,
                    **_sanitize_context(context),
                },
            )
            raise WorkspaceIoError(
                operation="exists",
                workspace_path=path,
                reason="os_error",
                underlying_error_class=type(e).__name__,
            ) from e

    async def stat(
        self,
        path: WorkspacePath,
        *,
        context: Mapping[str, object] | None = None,
    ) -> WorkspaceStatEntry:
        """返回 ``path`` 的元数据。

        ``FileNotFoundError`` 翻译为 ``WorkspaceNotFoundError``；其他
        ``OSError`` 翻译为 ``WorkspaceIoError``。

        Args:
            path: 要查询的逻辑路径。
            context: 观测上下文白名单字段，默认 ``None``。

        Returns:
            ``WorkspaceStatEntry``。

        Raises:
            WorkspaceConfinementViolation: 触发守卫。
            WorkspaceNotFoundError: 路径不存在。
            WorkspaceIoError: 其他 I/O 失败。
        """
        host_path = self._to_host_path(path)
        self._run_guards(
            host_path=host_path,
            operation="stat",
            logical_path=path,
            context=context,
        )
        try:
            st = os.stat(host_path)
        except FileNotFoundError as e:
            logger.info(
                "workspace_not_found",
                extra={
                    "workspace_backend_kind": "local_filesystem",
                    "operation": "stat",
                    "workspace_path": path.to_posix(),
                    **_sanitize_context(context),
                },
            )
            raise WorkspaceNotFoundError(workspace_path=path) from e
        except OSError as e:
            logger.warning(
                "workspace_io_error",
                extra={
                    "workspace_backend_kind": "local_filesystem",
                    "operation": "stat",
                    "workspace_path": path.to_posix(),
                    "underlying_error_class": type(e).__name__,
                    **_sanitize_context(context),
                },
            )
            raise WorkspaceIoError(
                operation="stat",
                workspace_path=path,
                reason="os_error",
                underlying_error_class=type(e).__name__,
            ) from e
        return WorkspaceStatEntry(
            path=path,
            is_file=_stat.S_ISREG(st.st_mode),
            is_dir=_stat.S_ISDIR(st.st_mode),
            size=st.st_size,
            mtime=st.st_mtime,
        )

    async def read(
        self,
        path: WorkspacePath,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
        context: Mapping[str, object] | None = None,
    ) -> bytes:
        """读取 ``path`` 的字节内容，可选按 UTF-8 行范围切片。

        错误翻译（与 design §组件与接口 2 对齐）：

        - ``FileNotFoundError`` → ``WorkspaceNotFoundError``（日志级别
          ``info``，因为 LLM 频繁尝试不存在路径是正常现象）；
        - ``UnicodeDecodeError`` → ``WorkspaceIoError(reason="decode_failed")``
          （典型场景是二进制文件 + 行范围）；
        - ``PermissionError`` → ``WorkspaceIoError(reason="permission_denied")``；
        - 其他 ``OSError`` → ``WorkspaceIoError(reason="os_error")``。

        Args:
            path: 要读取的逻辑路径。
            start_line: 起始行号（闭区间，1 起），``None`` 表示从头。
            end_line: 结束行号（闭区间），``None`` 表示到末尾。
            context: 观测上下文白名单字段，默认 ``None``。

        Returns:
            读取到的字节串。
        """
        host_path = self._to_host_path(path)
        self._run_guards(
            host_path=host_path,
            operation="read",
            logical_path=path,
            context=context,
        )
        try:
            return _read_bytes_in_range(host_path, start_line, end_line)
        except FileNotFoundError as e:
            logger.info(
                "workspace_not_found",
                extra={
                    "workspace_backend_kind": "local_filesystem",
                    "operation": "read",
                    "workspace_path": path.to_posix(),
                    **_sanitize_context(context),
                },
            )
            raise WorkspaceNotFoundError(workspace_path=path) from e
        except UnicodeDecodeError as e:
            logger.warning(
                "workspace_io_error",
                extra={
                    "workspace_backend_kind": "local_filesystem",
                    "operation": "read",
                    "workspace_path": path.to_posix(),
                    "underlying_error_class": type(e).__name__,
                    **_sanitize_context(context),
                },
            )
            raise WorkspaceIoError(
                operation="read",
                workspace_path=path,
                reason="decode_failed",
                underlying_error_class=type(e).__name__,
            ) from e
        except PermissionError as e:
            logger.warning(
                "workspace_io_error",
                extra={
                    "workspace_backend_kind": "local_filesystem",
                    "operation": "read",
                    "workspace_path": path.to_posix(),
                    "underlying_error_class": type(e).__name__,
                    **_sanitize_context(context),
                },
            )
            raise WorkspaceIoError(
                operation="read",
                workspace_path=path,
                reason="permission_denied",
                underlying_error_class=type(e).__name__,
            ) from e
        except OSError as e:
            logger.warning(
                "workspace_io_error",
                extra={
                    "workspace_backend_kind": "local_filesystem",
                    "operation": "read",
                    "workspace_path": path.to_posix(),
                    "underlying_error_class": type(e).__name__,
                    **_sanitize_context(context),
                },
            )
            raise WorkspaceIoError(
                operation="read",
                workspace_path=path,
                reason="os_error",
                underlying_error_class=type(e).__name__,
            ) from e

    async def write(
        self,
        path: WorkspacePath,
        content: bytes,
        *,
        context: Mapping[str, object] | None = None,
    ) -> int:
        """将 ``content`` 原子写入 ``path``，返回写入字节数。

        越界守卫作用域：``SymlinkGuard`` 检查 ``host_path.parent``（允许
        目标自身尚未创建）；``IdentityGuard`` 仍对完整 ``host_path`` 做
        回溯式 ``st_dev`` 比较。

        错误翻译：

        - ``OSError(errno=EXDEV)`` → ``WorkspaceIoError(reason="cross_device")``
          （跨设备 rename，原子性无法保证）；
        - ``PermissionError`` → ``WorkspaceIoError(reason="permission_denied")``；
        - 其他 ``OSError`` → ``WorkspaceIoError(reason="os_error")``。

        Args:
            path: 目标逻辑路径。
            content: 待写入字节串。
            context: 观测上下文白名单字段，默认 ``None``。

        Returns:
            实际写入的字节数（等于 ``len(content)``）。
        """
        host_path = self._to_host_path(path)
        # write 允许目标自身不存在：SymlinkGuard 仅检查父级是否含符号链接。
        # 越界时也走 _log_confinement_violation 补一条结构化日志（需求 8.1）。
        try:
            self._symlink_guard.check(host_path.parent)
            self._identity_guard.check(host_path)
        except WorkspaceConfinementViolation as exc:
            _log_confinement_violation(
                operation="write",
                requested_path=exc.requested_path,
                resolved_workspace_path=path.to_posix(),
                violation_reason=exc.reason.value,
                context=context,
            )
            raise
        try:
            return _write_bytes_atomically(host_path, content)
        except OSError as e:
            # 优先识别跨设备 rename：errno.EXDEV
            if getattr(e, "errno", None) == errno.EXDEV:
                logger.warning(
                    "workspace_io_error",
                    extra={
                        "workspace_backend_kind": "local_filesystem",
                        "operation": "write",
                        "workspace_path": path.to_posix(),
                        "underlying_error_class": type(e).__name__,
                        **_sanitize_context(context),
                    },
                )
                raise WorkspaceIoError(
                    operation="write",
                    workspace_path=path,
                    reason="cross_device",
                    underlying_error_class=type(e).__name__,
                ) from e
            if isinstance(e, PermissionError):
                logger.warning(
                    "workspace_io_error",
                    extra={
                        "workspace_backend_kind": "local_filesystem",
                        "operation": "write",
                        "workspace_path": path.to_posix(),
                        "underlying_error_class": type(e).__name__,
                        **_sanitize_context(context),
                    },
                )
                raise WorkspaceIoError(
                    operation="write",
                    workspace_path=path,
                    reason="permission_denied",
                    underlying_error_class=type(e).__name__,
                ) from e
            logger.warning(
                "workspace_io_error",
                extra={
                    "workspace_backend_kind": "local_filesystem",
                    "operation": "write",
                    "workspace_path": path.to_posix(),
                    "underlying_error_class": type(e).__name__,
                    **_sanitize_context(context),
                },
            )
            raise WorkspaceIoError(
                operation="write",
                workspace_path=path,
                reason="os_error",
                underlying_error_class=type(e).__name__,
            ) from e

    async def edit(
        self,
        path: WorkspacePath,
        old_content: bytes,
        new_content: bytes,
        *,
        context: Mapping[str, object] | None = None,
    ) -> int:
        """首匹配替换（精确 + 行级模糊回退），带 ``fcntl.flock`` 进程间互斥。

        算法（design §组件与接口 2 的 ``edit`` 关键内部算法）：

        1. 两守卫检查宿主路径；
        2. POSIX：``os.open(host_path, O_RDWR)`` → ``fcntl.flock(fd, LOCK_EX)``
           → 校验 ``os.fstat(fd).st_ino == os.stat(host_path).st_ino``（若其他
           writer 已在我们等锁期间完成 ``os.replace`` 并换了 inode，就需要
           释放旧锁、关闭旧 fd 并重开新 fd 重新加锁，直到锁-inode 一致）；
        3. Windows：跳过加锁（``platform.system() == "Windows"``）并**首次
           触发时**记录一次 ``warning`` 级别日志；
        4. 在临界区内：``os.read`` 读出全部字节 → ``_edit_with_fallback_match``
           求出新字节；
        5. 调用 ``_write_bytes_atomically`` 完成原子替换；
        6. ``finally`` 关闭 fd（自动释放 flock）。

        **关键安全补强**：``_write_bytes_atomically`` 使用 ``os.replace`` 换
        inode，如果只在初次 ``os.open`` 的 fd 上 ``flock``，并发 writer 会
        因为"锁在旧 inode 上、名字已指向新 inode"而获得 false 并发权（观察到
        "A Y" 这类旧-新混合结果）。因此在步骤 2 加入 inode 一致性校验，
        直到持有的 fd 对应的 inode 与路径当前 inode 一致为止，才进入步骤 4
        的临界区。本校验是对 design §组件与接口 2 的文字算法的**忠实落地**，
        不改变对外契约。

        错误翻译：

        - 未匹配（``_edit_with_fallback_match`` 返回 ``None``）→
          ``WorkspaceIoError(reason="no_match")``；
        - ``flock`` ``EAGAIN`` / ``EINTR``（``BlockingIOError`` /
          ``InterruptedError`` / ``OSError(errno in {EAGAIN, EINTR})``）→
          ``WorkspaceIoError(reason="lock_failed")``；
        - ``FileNotFoundError`` → ``WorkspaceNotFoundError``；
        - ``_write_bytes_atomically`` 跨设备 rename → ``WorkspaceIoError(
          reason="cross_device")``；
        - 其他 ``OSError`` → ``WorkspaceIoError(reason="os_error")``。

        Args:
            path: 目标逻辑路径。
            old_content: 要替换的原始字节串。
            new_content: 替换后的字节串。
            context: 观测上下文白名单字段，默认 ``None``。

        Returns:
            替换后写入的字节数。
        """
        host_path = self._to_host_path(path)
        self._run_guards(
            host_path=host_path,
            operation="edit",
            logical_path=path,
            context=context,
        )

        is_windows = platform.system() == "Windows"
        fd = self._acquire_edit_fd(
            host_path=host_path, path=path, is_windows=is_windows, context=context
        )
        try:
            # 临界区：读 → 匹配 → 原子写
            try:
                chunks: list[bytes] = []
                while True:
                    chunk = os.read(fd, 65536)
                    if not chunk:
                        break
                    chunks.append(chunk)
                current_bytes = b"".join(chunks)
            except OSError as e:
                logger.warning(
                    "workspace_io_error",
                    extra={
                        "workspace_backend_kind": "local_filesystem",
                        "operation": "edit",
                        "workspace_path": path.to_posix(),
                        "underlying_error_class": type(e).__name__,
                        **_sanitize_context(context),
                    },
                )
                raise WorkspaceIoError(
                    operation="edit",
                    workspace_path=path,
                    reason="os_error",
                    underlying_error_class=type(e).__name__,
                ) from e

            try:
                new_bytes = _edit_with_fallback_match(current_bytes, old_content, new_content)
            except ValueError as e:
                # old_content 为空 → _common_impl 抛 ValueError
                logger.warning(
                    "workspace_io_error",
                    extra={
                        "workspace_backend_kind": "local_filesystem",
                        "operation": "edit",
                        "workspace_path": path.to_posix(),
                        "underlying_error_class": type(e).__name__,
                        **_sanitize_context(context),
                    },
                )
                raise WorkspaceIoError(
                    operation="edit",
                    workspace_path=path,
                    reason="empty_old_content",
                    underlying_error_class=type(e).__name__,
                ) from e

            if new_bytes is None:
                logger.warning(
                    "workspace_io_error",
                    extra={
                        "workspace_backend_kind": "local_filesystem",
                        "operation": "edit",
                        "workspace_path": path.to_posix(),
                        "underlying_error_class": "NoMatch",
                        **_sanitize_context(context),
                    },
                )
                raise WorkspaceIoError(
                    operation="edit",
                    workspace_path=path,
                    reason="no_match",
                )

            # Windows 兼容：``os.replace(tmp, host_path)`` 在 Windows 上若
            # 目标文件仍被当前进程其他 fd 打开会失败（``ERROR_SHARING_VIOLATION``）。
            # 由于 Windows 分支已退化为"无锁"，此处提前关闭 fd 不会削弱互斥性；
            # POSIX 侧仍保留到 ``finally`` 中统一关闭，确保 ``fcntl.flock`` 覆盖
            # 整个临界区。
            if is_windows:
                with contextlib.suppress(OSError):
                    os.close(fd)
                fd = -1
            try:
                return _write_bytes_atomically(host_path, new_bytes)
            except OSError as e:
                if getattr(e, "errno", None) == errno.EXDEV:
                    logger.warning(
                        "workspace_io_error",
                        extra={
                            "workspace_backend_kind": "local_filesystem",
                            "operation": "edit",
                            "workspace_path": path.to_posix(),
                            "underlying_error_class": type(e).__name__,
                            **_sanitize_context(context),
                        },
                    )
                    raise WorkspaceIoError(
                        operation="edit",
                        workspace_path=path,
                        reason="cross_device",
                        underlying_error_class=type(e).__name__,
                    ) from e
                logger.warning(
                    "workspace_io_error",
                    extra={
                        "workspace_backend_kind": "local_filesystem",
                        "operation": "edit",
                        "workspace_path": path.to_posix(),
                        "underlying_error_class": type(e).__name__,
                        **_sanitize_context(context),
                    },
                )
                raise WorkspaceIoError(
                    operation="edit",
                    workspace_path=path,
                    reason="os_error",
                    underlying_error_class=type(e).__name__,
                ) from e
        finally:
            if fd >= 0:
                # 关闭 fd 自动释放 flock；关闭失败不覆盖主异常
                with contextlib.suppress(OSError):
                    os.close(fd)

    def _acquire_edit_fd(
        self,
        *,
        host_path: Path,
        path: WorkspacePath,
        is_windows: bool,
        context: Mapping[str, object] | None,
    ) -> int:
        """以 acquire-verify 循环为 ``edit`` 取得与当前 inode 一致的已加锁 fd。

        POSIX 算法：

        1. ``os.open(host_path, O_RDWR)`` → ``fcntl.flock(fd, LOCK_EX)``；
        2. ``os.fstat(fd).st_ino`` vs ``os.stat(host_path).st_ino``：一致则
           返回 fd；不一致说明我们等锁期间有别的 writer 把文件换成了新 inode
           （``os.replace`` 原子性），我们的锁守的是**旧** inode，没有真正
           互斥；此时关闭 fd 回到步骤 1 重试；
        3. 路径在步骤 2 的 ``os.stat`` 查不到 → ``FileNotFoundError`` →
           翻译为 ``WorkspaceNotFoundError``。

        Windows 退化：只做 ``os.open``，跳过加锁与 inode 校验，记录一次
        ``warning``。

        Returns:
            已加锁且与当前 ``host_path`` inode 一致的 fd。

        Raises:
            WorkspaceNotFoundError: ``host_path`` 不存在。
            WorkspaceIoError: ``flock`` ``EAGAIN`` / ``EINTR``
                （``reason="lock_failed"``）或其他 ``OSError``
                （``reason="os_error"``）。
        """
        if is_windows:
            global _windows_warning_emitted
            if not _windows_warning_emitted:
                _windows_warning_emitted = True
                logger.warning(
                    "Windows 不支持 fcntl.flock，edit 将在无锁下进行",
                    extra={
                        "workspace_backend_kind": "local_filesystem",
                        "operation": "edit",
                        "workspace_path": path.to_posix(),
                        **_sanitize_context(context),
                    },
                )
            try:
                return os.open(host_path, os.O_RDWR)
            except FileNotFoundError as e:
                logger.info(
                    "workspace_not_found",
                    extra={
                        "workspace_backend_kind": "local_filesystem",
                        "operation": "edit",
                        "workspace_path": path.to_posix(),
                        **_sanitize_context(context),
                    },
                )
                raise WorkspaceNotFoundError(workspace_path=path) from e

        # POSIX acquire-verify loop
        import fcntl as fcntl_module  # 延迟 import：Windows 分支不需要

        class _FcntlModule(Protocol):
            """当前适配器使用的 ``fcntl`` 最小类型表面。"""

            LOCK_EX: int

            def flock(self, fd: int, operation: int) -> None:
                """对文件描述符加锁。"""
                ...

        fcntl = cast(_FcntlModule, fcntl_module)

        while True:
            try:
                fd = os.open(host_path, os.O_RDWR)
            except FileNotFoundError as e:
                logger.info(
                    "workspace_not_found",
                    extra={
                        "workspace_backend_kind": "local_filesystem",
                        "operation": "edit",
                        "workspace_path": path.to_posix(),
                        **_sanitize_context(context),
                    },
                )
                raise WorkspaceNotFoundError(workspace_path=path) from e

            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX)
                except (BlockingIOError, InterruptedError) as e:
                    logger.warning(
                        "workspace_io_error",
                        extra={
                            "workspace_backend_kind": "local_filesystem",
                            "operation": "edit",
                            "workspace_path": path.to_posix(),
                            "underlying_error_class": type(e).__name__,
                            **_sanitize_context(context),
                        },
                    )
                    raise WorkspaceIoError(
                        operation="edit",
                        workspace_path=path,
                        reason="lock_failed",
                        underlying_error_class=type(e).__name__,
                    ) from e
                except OSError as e:
                    if getattr(e, "errno", None) in (errno.EAGAIN, errno.EINTR):
                        logger.warning(
                            "workspace_io_error",
                            extra={
                                "workspace_backend_kind": "local_filesystem",
                                "operation": "edit",
                                "workspace_path": path.to_posix(),
                                "underlying_error_class": type(e).__name__,
                                **_sanitize_context(context),
                            },
                        )
                        raise WorkspaceIoError(
                            operation="edit",
                            workspace_path=path,
                            reason="lock_failed",
                            underlying_error_class=type(e).__name__,
                        ) from e
                    raise

                # inode 一致性校验：等锁过程中路径是否被 replace？
                try:
                    current_ino = os.fstat(fd).st_ino
                    path_ino = os.stat(host_path).st_ino
                except FileNotFoundError as e:
                    logger.info(
                        "workspace_not_found",
                        extra={
                            "workspace_backend_kind": "local_filesystem",
                            "operation": "edit",
                            "workspace_path": path.to_posix(),
                            **_sanitize_context(context),
                        },
                    )
                    raise WorkspaceNotFoundError(workspace_path=path) from e

                if current_ino == path_ino:
                    return fd  # 锁与 inode 一致，可以进入临界区

                # 不一致：关闭 fd（flock 随之释放），循环重试
                with contextlib.suppress(OSError):
                    os.close(fd)
                # 继续 while True 外层循环：重新 open + lock + verify
            except BaseException:
                # 任何异常路径下保证 fd 被关闭
                with contextlib.suppress(OSError):
                    os.close(fd)
                raise

    async def list_dir(
        self,
        path: WorkspacePath,
        *,
        recursive: bool = True,
        context: Mapping[str, object] | None = None,
    ) -> list[WorkspaceStatEntry]:
        """列出 ``path`` 下的条目。

        使用 ``os.scandir``（而非 ``Path.iterdir``）以减少额外 ``stat`` 次数
        并利用 ``entry.stat(follow_symlinks=False)`` 的 ``DirEntry`` 缓存。
        ``recursive=True`` 时采用**迭代式 DFS**（栈，非递归）遍历子目录，
        避免极深目录造成 Python 栈溢出。

        每个条目的逻辑 ``WorkspacePath`` 由 ``path.join(entry.name)`` 基于
        父逻辑路径递归拼出，拼接过程由 ``WorkspacePath.join`` 自洽校验
        （任务 2.1 的非法字符拦截）。

        错误翻译：

        - ``FileNotFoundError`` → ``WorkspaceNotFoundError``；
        - ``NotADirectoryError`` → ``WorkspaceIoError(reason="not_a_directory")``；
        - ``PermissionError`` → ``WorkspaceIoError(reason="permission_denied")``；
        - 其他 ``OSError`` → ``WorkspaceIoError(reason="os_error")``。

        Args:
            path: 要列出的逻辑目录路径。
            recursive: 是否递归遍历子目录，默认 ``True``。
            context: 观测上下文白名单字段，默认 ``None``。

        Returns:
            条目元数据列表；顺序为 ``os.scandir`` 的平台相关顺序（本模块
            不再做稳定排序，以避免对每条目做额外字典排序开销）。
        """
        host_path = self._to_host_path(path)
        self._run_guards(
            host_path=host_path,
            operation="list_dir",
            logical_path=path,
            context=context,
        )

        results: list[WorkspaceStatEntry] = []
        # 迭代式 DFS：每个栈元素是 (logical_ws_path, host_path) 对
        stack: list[tuple[WorkspacePath, Path]] = [(path, host_path)]
        try:
            while stack:
                current_ws, current_host = stack.pop()
                # 使用 scandir + with 保证 fd 回收
                with os.scandir(current_host) as it:
                    for entry in it:
                        child_ws = current_ws.join(entry.name)
                        try:
                            st = entry.stat(follow_symlinks=False)
                            is_file = entry.is_file(follow_symlinks=False)
                            is_dir = entry.is_dir(follow_symlinks=False)
                            size: int | None = st.st_size
                            mtime: float | None = st.st_mtime
                        except OSError:
                            # 单条目 stat 失败：降级为 None（不阻断整批）
                            is_file = False
                            is_dir = False
                            size = None
                            mtime = None
                        results.append(
                            WorkspaceStatEntry(
                                path=child_ws,
                                is_file=is_file,
                                is_dir=is_dir,
                                size=size,
                                mtime=mtime,
                            )
                        )
                        if recursive and is_dir:
                            stack.append((child_ws, current_host / entry.name))
        except FileNotFoundError as e:
            logger.info(
                "workspace_not_found",
                extra={
                    "workspace_backend_kind": "local_filesystem",
                    "operation": "list_dir",
                    "workspace_path": path.to_posix(),
                    **_sanitize_context(context),
                },
            )
            raise WorkspaceNotFoundError(workspace_path=path) from e
        except NotADirectoryError as e:
            logger.warning(
                "workspace_io_error",
                extra={
                    "workspace_backend_kind": "local_filesystem",
                    "operation": "list_dir",
                    "workspace_path": path.to_posix(),
                    "underlying_error_class": type(e).__name__,
                    **_sanitize_context(context),
                },
            )
            raise WorkspaceIoError(
                operation="list_dir",
                workspace_path=path,
                reason="not_a_directory",
                underlying_error_class=type(e).__name__,
            ) from e
        except PermissionError as e:
            logger.warning(
                "workspace_io_error",
                extra={
                    "workspace_backend_kind": "local_filesystem",
                    "operation": "list_dir",
                    "workspace_path": path.to_posix(),
                    "underlying_error_class": type(e).__name__,
                    **_sanitize_context(context),
                },
            )
            raise WorkspaceIoError(
                operation="list_dir",
                workspace_path=path,
                reason="permission_denied",
                underlying_error_class=type(e).__name__,
            ) from e
        except OSError as e:
            logger.warning(
                "workspace_io_error",
                extra={
                    "workspace_backend_kind": "local_filesystem",
                    "operation": "list_dir",
                    "workspace_path": path.to_posix(),
                    "underlying_error_class": type(e).__name__,
                    **_sanitize_context(context),
                },
            )
            raise WorkspaceIoError(
                operation="list_dir",
                workspace_path=path,
                reason="os_error",
                underlying_error_class=type(e).__name__,
            ) from e
        return results

    async def delete(
        self,
        path: WorkspacePath,
        *,
        context: Mapping[str, object] | None = None,
    ) -> None:
        """删除 ``path``。

        本方法**不对 LLM 直接暴露**，仅供后端内部使用（例如 ``edit`` 的
        回滚路径或后台清理）。工具层不应把 ``delete`` 作为 LLM 可调用的
        独立操作注册。

        算法：``host_path.is_dir()`` → ``shutil.rmtree`` 否则 ``os.unlink``；
        ``FileNotFoundError`` → ``WorkspaceNotFoundError``；其他 ``OSError``
        → ``WorkspaceIoError``。

        Args:
            path: 要删除的逻辑路径。
            context: 观测上下文白名单字段，默认 ``None``。
        """
        host_path = self._to_host_path(path)
        self._run_guards(
            host_path=host_path,
            operation="delete",
            logical_path=path,
            context=context,
        )
        try:
            if host_path.is_dir():
                shutil.rmtree(host_path)
            else:
                os.unlink(host_path)
        except FileNotFoundError as e:
            logger.info(
                "workspace_not_found",
                extra={
                    "workspace_backend_kind": "local_filesystem",
                    "operation": "delete",
                    "workspace_path": path.to_posix(),
                    **_sanitize_context(context),
                },
            )
            raise WorkspaceNotFoundError(workspace_path=path) from e
        except PermissionError as e:
            logger.warning(
                "workspace_io_error",
                extra={
                    "workspace_backend_kind": "local_filesystem",
                    "operation": "delete",
                    "workspace_path": path.to_posix(),
                    "underlying_error_class": type(e).__name__,
                    **_sanitize_context(context),
                },
            )
            raise WorkspaceIoError(
                operation="delete",
                workspace_path=path,
                reason="permission_denied",
                underlying_error_class=type(e).__name__,
            ) from e
        except OSError as e:
            logger.warning(
                "workspace_io_error",
                extra={
                    "workspace_backend_kind": "local_filesystem",
                    "operation": "delete",
                    "workspace_path": path.to_posix(),
                    "underlying_error_class": type(e).__name__,
                    **_sanitize_context(context),
                },
            )
            raise WorkspaceIoError(
                operation="delete",
                workspace_path=path,
                reason="os_error",
                underlying_error_class=type(e).__name__,
            ) from e

    # ── LocallyMaterializable ──

    def materialize_cwd(self, path: WorkspacePath) -> str:
        """返回可直接作为子进程 ``cwd`` 的宿主目录绝对路径。

        本方法是本地后端对工具层暴露的**唯一**物理路径出口，其返回值
        绝不能被放回工具的对外参数或成功消息中（守住需求 4.4 / 8.6 路径
        泄露红线）。

        算法（同步，无 ``context``）：

        1. ``_to_host_path`` 构造宿主 ``Path``；
        2. ``SymlinkGuard.check`` + ``IdentityGuard.check`` 做二次越界防御；
        3. ``host_path.is_dir()`` 校验；非目录抛
           ``WorkspaceIoError(reason="not_a_directory")``；
        4. 返回 ``str(host_path)``。

        ``LocallyMaterializable`` 协议不接受 ``context`` 参数，本方法因此
        不走 ``logger.*(extra=...)`` 的 context 合并；仍然不得把任何调用方
        上下文拼入异常消息。

        Args:
            path: 要物化的逻辑目录路径。

        Returns:
            宿主绝对路径字符串。

        Raises:
            WorkspaceConfinementViolation: 两守卫越界判定。
            WorkspaceIoError: 目标不是目录（``reason="not_a_directory"``）。
        """
        host_path = self._to_host_path(path)
        # materialize_cwd 是同步方法、无 context 形参；越界日志以 context=None
        # 形式落一条（结构化字段仍齐全，仅缺 tool_name / trace_id 白名单字段）。
        self._run_guards(
            host_path=host_path,
            operation="materialize_cwd",
            logical_path=path,
            context=None,
        )
        if not host_path.is_dir():
            raise WorkspaceIoError(
                operation="materialize_cwd",
                workspace_path=path,
                reason="not_a_directory",
            )
        return str(host_path)

    # ── 内部工具 ──

    def _to_host_path(self, path: WorkspacePath) -> Path:
        """把 ``WorkspacePath`` 拼接到 ``root`` 下，返回宿主 ``Path``。

        本方法是所有 I/O 方法获得宿主 ``Path`` 的**唯一**构造路径，不做
        任何 I/O；不变式由 ``WorkspacePath`` 自洽提供（已以 "/" 起始、
        不含 ``..``、不含非法字符）。

        Args:
            path: 合法 ``WorkspacePath``。

        Returns:
            ``self._root / path.to_posix().lstrip("/")``。
        """
        return self._root / path.to_posix().lstrip("/")

    def to_host_path(self, path: WorkspacePath) -> Path:
        """将已校验的逻辑路径映射为宿主路径。"""
        return self._to_host_path(path)
