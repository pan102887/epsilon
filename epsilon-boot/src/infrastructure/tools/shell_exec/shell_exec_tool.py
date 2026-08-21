"""Shell 命令执行工具模块（Workspace 受控改造版）。

提供 :class:`ShellExecTool` 工具类，基于 ``asyncio.create_subprocess_exec`` 实现
异步 Shell 命令执行，为 LLM Agent 提供**工作区边界内**的本地操作系统级命令
执行能力。

设计约束（对齐 ``docs/spec/workspace/design.md`` §组件与接口 5）：

- 构造签名注入 :class:`domain.workspace.ports.Workspace` 端口；工具层自身
  **不接触**宿主绝对路径，仅在子进程创建的 ``cwd`` 参数这一行以
  :class:`domain.workspace.ports.LocallyMaterializable` 的 ``materialize_cwd``
  受控物化为宿主目录字符串（需求 6.6 / 6.7）。
- ``execute`` 开头进行 ``local_materialization`` 能力守卫：后端不支持本地
  物化时立即抛 :class:`ToolExecutionError`，避免后续尝试构造子进程
  （需求 6.7）。
- ``working_dir`` 参数语义由"任意宿主路径"改为**工作区相对逻辑路径**；
  经 ``Workspace.resolve_path`` 归一化并被 ``materialize_cwd`` 再次校验后
  才会传递给 ``subprocess`` 的 ``cwd`` 参数（需求 6.9 / 6.11）。
- 既有的环境变量剥离规则（KEY / SECRET / PASSWORD / TOKEN / CREDENTIAL）
  **保持不变**（需求 6.12）。

依赖白名单（守住 Property 6）：工具层本身不 import ``pathlib`` 或具体后端
实现类；``os`` 仅用于环境变量拷贝与敏感词过滤，**不**用于路径构造。
"""

from __future__ import annotations

import asyncio
import os
import re
import sys
from typing import Any

from domain.agent.exceptions import ToolExecutionError
from domain.agent.guardrails import ToolRiskLevel
from domain.agent.tools import Tool, ToolExecutionResult
from domain.workspace.exceptions import (
    WorkspaceConfinementViolation,
    WorkspaceIoError,
    WorkspaceNotFoundError,
)
from domain.workspace.ports import LocallyMaterializable, Workspace


def get_shell_command(command: str) -> list[str]:
    """根据运行时操作系统选择 shell 执行方式。

    Args:
        command: 待执行的 Shell 命令字符串。

    Returns:
        适用于当前平台的可执行参数列表。
        Linux/macOS: ``["bash", "-c", command]``
        Windows: ``["powershell", "-Command", command]``
    """
    if sys.platform == "win32":
        return ["powershell", "-Command", command]
    return ["bash", "-c", command]


# 敏感关键词列表（不区分大小写匹配）
_SENSITIVE_KEYWORDS: list[str] = ["KEY", "SECRET", "PASSWORD", "TOKEN", "CREDENTIAL"]

# 危险命令文本片段；文本匹配使用 casefold，避免依赖 Shell 方言。
_DANGEROUS_COMMAND_TEXT_FRAGMENTS: tuple[str, ...] = (
    "mkfs",
    "dd if=",
    "/etc/shadow",
    "~/.ssh/id_rsa",
    ".env",
)

# 危险命令正则标记：rm -rf、dd ... if=、remote script execution、fork bomb。
_DANGEROUS_COMMAND_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\brm\s+-[A-Za-z]*r[A-Za-z]*f\b", re.IGNORECASE),
    re.compile(r"\bdd\b[^|;&\n]*\bif\s*=", re.IGNORECASE),
    re.compile(
        r"\b(?:curl|wget)\b(?:(?![;&\n]).)*\|\s*(?:sh|bash)\b",
        re.IGNORECASE,
    ),
    re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:", re.IGNORECASE),
)

# 平台特定的保留环境变量列表
_UNIX_PRESERVED_VARS: set[str] = {"PATH", "HOME", "LANG", "USER", "SHELL", "TERM"}
_WIN_PRESERVED_VARS: set[str] = {
    "Path",
    "USERPROFILE",
    "USERNAME",
    "SystemRoot",
    "TEMP",
    "TMP",
    "PATHEXT",
    "COMSPEC",
}

SENSITIVE_KEYWORDS = _SENSITIVE_KEYWORDS
UNIX_PRESERVED_VARS = _UNIX_PRESERVED_VARS
WIN_PRESERVED_VARS = _WIN_PRESERVED_VARS


def _blocked_command_reason(command: str) -> str | None:
    """返回 Shell 命令命中安全阻断的原因，未命中时返回 None。"""
    command_text = command.casefold()

    if any(fragment in command_text for fragment in ("/etc/shadow", "~/.ssh/id_rsa", ".env")):
        return "blocked-command: sensitive file read"

    if any(fragment in command_text for fragment in ("mkfs", "dd if=")):
        return "blocked-command: destructive command"

    pattern_reasons: tuple[str, ...] = (
        "blocked-command: destructive command",
        "blocked-command: destructive command",
        "blocked-command: remote script execution",
        "blocked-command: destructive command",
    )
    for pattern, reason in zip(_DANGEROUS_COMMAND_PATTERNS, pattern_reasons, strict=True):
        if pattern.search(command):
            return reason

    return None


def blocked_command_reason(command: str) -> str | None:
    """返回 Shell 命令的安全阻断原因，供诊断和测试使用。"""
    return _blocked_command_reason(command)


def _reject_dangerous_command(command: str, *, tool_name: str) -> None:
    """在创建子进程前拒绝危险 Shell 命令片段。"""
    reason = _blocked_command_reason(command)
    if reason is None:
        return

    raise ToolExecutionError(
        message=f"安全护栏 {reason}，拒绝执行该 Shell 命令",
        tool_name=tool_name,
    )


def sanitize_env() -> dict[str, str]:
    """创建清理后的环境变量副本。

    复制当前进程环境变量，移除名称中包含敏感关键词的变量，
    但保留平台特定的系统必要变量。

    敏感关键词包括：KEY、SECRET、PASSWORD、TOKEN、CREDENTIAL（不区分大小写匹配）。
    平台保留变量：

    - Linux/macOS: PATH、HOME、LANG、USER、SHELL、TERM
    - Windows: Path、USERPROFILE、USERNAME、SystemRoot、TEMP、TMP、PATHEXT、COMSPEC

    Returns:
        清理后的环境变量字典。
    """
    preserved = _WIN_PRESERVED_VARS if sys.platform == "win32" else _UNIX_PRESERVED_VARS
    clean_env: dict[str, str] = {}

    for name, value in os.environ.items():
        # 保留列表中的变量直接保留
        if name in preserved:
            clean_env[name] = value
            continue
        # 检查是否包含敏感关键词（不区分大小写）
        name_upper = name.upper()
        if any(kw in name_upper for kw in _SENSITIVE_KEYWORDS):
            continue
        clean_env[name] = value

    return clean_env


class ShellExecTool(Tool):
    """Shell 命令执行工具（Workspace 受控版）。

    基于 ``asyncio.create_subprocess_exec`` 实现异步 Shell 命令执行，
    为 LLM Agent 提供**工作区边界内**的本地操作系统级命令执行能力。

    内置多层安全防护：工作区边界校验、执行超时限制、环境变量清理、
    工作目录锁定、输出大小限制。支持跨平台运行（Linux/macOS 使用 bash,
    Windows 使用 PowerShell）。

    Attributes:
        _workspace: 注入的 :class:`Workspace` 端口实例。
        _timeout: 默认命令执行超时秒数。
        _max_output_size: stdout/stderr 合并输出大小上限（字节）。
        _default_working_dir: 默认工作目录（工作区相对逻辑路径）；空串表示
            使用工作区根 ``"/"``。
    """

    def __init__(
        self,
        workspace: Workspace,
        *,
        timeout: int = 30,
        max_output_size: int = 51200,
        default_working_dir: str = "",
    ) -> None:
        """初始化 :class:`ShellExecTool`。

        Args:
            workspace: 注入的 :class:`Workspace` 端口实例。工具通过该实例
                完成路径解析与宿主目录物化，自身不接触宿主绝对路径。
            timeout: 默认命令执行超时秒数，当 execute 未传 timeout 时使用。
            max_output_size: stdout/stderr 合并输出大小上限（字节），超过时截断。
            default_working_dir: 默认工作目录，**工作区相对逻辑路径**；空串
                表示使用工作区根 ``"/"``。经过 ``Workspace.resolve_path`` 归
                一化后使用。
        """
        self._workspace: Workspace = workspace
        self._timeout: int = timeout
        self._max_output_size: int = max_output_size
        self._default_working_dir: str = default_working_dir or ""

    @property
    def name(self) -> str:
        """工具的唯一名称。"""
        return "shell_exec"

    @property
    def risk_level(self) -> ToolRiskLevel:
        """Shell 执行工具为 critical 风险。"""
        return ToolRiskLevel.CRITICAL

    @property
    def description(self) -> str:
        """动态中文功能描述，拼入工作区根以引导 LLM 使用相对路径。"""
        workspace_root = self._workspace.display_root_hint()
        return (
            "Run a shell command in a controlled workspace environment for tasks "
            "such as repository inspection, file processing, and local test scripts. "
            "The tool enforces timeout, sanitized environment variables, workspace "
            f"confinement, and working-directory isolation. Paths are resolved "
            f"relative to workspace root {workspace_root} and must use POSIX / separators."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        """符合 JSON Schema 规范的参数描述字典。"""
        return {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute.",
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        "Execution timeout in seconds. Defaults to the configured value."
                    ),
                },
                "working_dir": {
                    "type": "string",
                    "description": (
                        "Workspace-relative working directory using POSIX / separators. "
                        "It must stay inside the workspace. Leave empty to use the "
                        "configured default or the workspace root."
                    ),
                },
            },
            "required": ["command"],
        }

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """执行 Shell 命令并返回结果。

        执行流程（严格按需求 6.6 / 6.7 / 6.9 / 6.11 / 6.12）：

        1. 读取 ``Workspace.capabilities()``，若 ``local_materialization=False``
           立即抛 :class:`ToolExecutionError`，不进入子进程创建；
        2. 选择 ``working_dir``：显式传入 > ``_default_working_dir`` >
           工作区根 ``"/"``；
        3. ``Workspace.resolve_path`` 归一化到逻辑路径；越界抛
           :class:`WorkspaceConfinementViolation` → 翻译为
           :class:`ToolExecutionError`；
        4. ``Workspace.materialize_cwd(ws_path)`` 取宿主绝对路径，作为子进程
           ``cwd`` 参数；
        5. 其余子进程创建、超时控制、输出截断逻辑与既有实现一致；
        6. 环境变量仍经 :func:`sanitize_env` 剥离敏感键（需求 6.12）。

        **关键红线**：``materialize_cwd`` 返回的宿主路径仅用于 ``cwd``，
        不得回填到任何对 LLM 可见的参数或成功消息中（需求 7.4）。

        Args:
            **kwargs: 工具参数，包含必填的 ``command`` 和可选的 ``timeout``
                / ``working_dir``。

        Returns:
            :class:`ToolExecutionResult`，``content`` 为包含退出码、stdout 和
            stderr 的格式化结果字符串；``metadata`` 含以下键：

            - ``command_summary`` (str): 命令前 128 字符。
            - ``working_dir`` (str): 工作区相对 POSIX 路径（如 ``"/"``）。
            - ``exit_code`` (int): 进程退出码。
            - ``stdout_bytes`` (int): stdout 原始字节数（截断前）。
            - ``stderr_bytes`` (int): stderr 原始字节数（截断前）。
            - ``truncated`` (bool): 输出是否被截断。

        Raises:
            ToolExecutionError: 后端不支持本地物化、路径越界、路径不存在、
                命令执行超时、子进程创建失败或其他异常时抛出。
        """
        command: str = kwargs["command"]
        _reject_dangerous_command(command, tool_name=self.name)
        timeout: int = kwargs.get("timeout", self._timeout)

        # 1. local_materialization 能力守卫（需求 6.6 / 6.7）
        caps = self._workspace.capabilities()
        if not caps.local_materialization:
            raise ToolExecutionError(
                message="当前工作区后端不支持本地命令执行",
                tool_name=self.name,
            )
        if not isinstance(self._workspace, LocallyMaterializable):
            raise ToolExecutionError(
                message="当前工作区后端不支持本地命令执行",
                tool_name=self.name,
            )

        # 2. 选择待解析的工作目录逻辑路径
        requested_working_dir: str = kwargs.get("working_dir") or self._default_working_dir or "/"

        # 3/4. 解析并物化（边界二次校验）
        try:
            ws_path = self._workspace.resolve_path(requested_working_dir)
            host_cwd: str = self._workspace.materialize_cwd(ws_path)
        except WorkspaceConfinementViolation as e:
            raise ToolExecutionError(
                message=f"工作目录 {requested_working_dir} 超出工作区边界",
                tool_name=self.name,
            ) from e
        except WorkspaceNotFoundError as e:
            raise ToolExecutionError(
                message=f"工作目录 {requested_working_dir} 不存在",
                tool_name=self.name,
            ) from e
        except WorkspaceIoError as e:
            raise ToolExecutionError(
                message=f"工作目录 {requested_working_dir} 不可用",
                tool_name=self.name,
            ) from e

        # 记录 workspace 相对逻辑路径供 trace 使用（绝不使用 host_cwd 宿主路径）。
        logical_working_dir: str = ws_path.to_posix()

        try:
            # 5. 获取平台对应的 shell 命令参数
            shell_args = get_shell_command(command)

            # 6. 清理环境变量（需求 6.12 红线保持）
            clean_env = sanitize_env()

            # 7. 创建异步子进程（cwd 使用 materialize_cwd 返回的宿主路径）
            process = await asyncio.create_subprocess_exec(
                *shell_args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=clean_env,
                cwd=host_cwd,
            )

            # 8. 等待执行完成（受超时限制）
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            except TimeoutError as exc:
                process.kill()
                await process.communicate()
                raise ToolExecutionError(
                    message=f"命令执行超时（{timeout} 秒）: {command[:100]}",
                    tool_name=self.name,
                ) from exc

            # 9. 解码输出
            stdout_text = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

            # 原始字节数（截断前），供 trace metadata 使用。
            stdout_raw_bytes = len(stdout_bytes) if stdout_bytes else 0
            stderr_raw_bytes = len(stderr_bytes) if stderr_bytes else 0

            # 10. 合并输出并检查大小
            combined = stdout_text + stderr_text
            combined_bytes = combined.encode("utf-8")
            original_size = len(combined_bytes)

            truncated = original_size > self._max_output_size
            if truncated:
                clipped = combined_bytes[: self._max_output_size].decode(
                    "utf-8", errors="replace"
                )
                clipped += f"\n[输出已截断，原始大小: {original_size} bytes]"
                stdout_text = clipped
                stderr_text = ""

            # 11. 格式化结果
            content = (
                f"Exit Code: {process.returncode}\n\n"
                f"[stdout]\n{stdout_text}\n\n"
                f"[stderr]\n{stderr_text}"
            )
            return ToolExecutionResult(
                content=content,
                metadata={
                    "command_summary": command[:128],
                    "working_dir": logical_working_dir,
                    "exit_code": process.returncode,
                    "stdout_bytes": stdout_raw_bytes,
                    "stderr_bytes": stderr_raw_bytes,
                    "truncated": truncated,
                },
            )

        except ToolExecutionError:
            raise
        except Exception as e:
            raise ToolExecutionError(
                message=f"Shell 命令执行失败: {e}",
                tool_name=self.name,
            ) from e
