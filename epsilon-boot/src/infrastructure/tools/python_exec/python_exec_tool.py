"""Python 脚本安全执行工具模块（Workspace 受控改造版）。

提供 AST 静态代码分析功能和 :class:`PythonExecTool` 工具类实现。

AST 分析器在代码执行前检查导入语句和函数调用，拦截不在白名单中的模块导入、
相对导入以及危险函数调用，确保代码在沙箱环境中安全执行。

Workspace 受控改造要点（对齐 ``docs/spec/workspace/design.md`` §组件与接口 5）：

- 构造签名注入 :class:`domain.workspace.ports.Workspace`；
- ``execute`` 开头进行 ``local_materialization`` 能力守卫（需求 6.7）；
- 子进程 ``cwd`` 通过 ``workspace.resolve_path("/") → materialize_cwd(ws_path)``
  取得宿主绝对路径，不再通过 ``tempfile.gettempdir()`` 构造（需求 6.11）；
- 临时 ``.py`` 文件在 ``host_cwd`` 下创建，确保脚本与子进程 cwd 一致；
- **AST 静态分析 / allowed_modules / 内存限制 / 敏感环境变量剥离等既有沙箱
  逻辑保持不变**（需求 6.10）。

模块级常量:
    BLOCKED_CALLS: 禁止在沙箱中调用的危险函数名称集合。
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import logging
import os
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
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
from infrastructure.tools.python_exec.python_exec_config import DEFAULT_ALLOWED_MODULES
from infrastructure.tools.shell_exec.shell_exec_tool import sanitize_env

logger = logging.getLogger(__name__)

BLOCKED_CALLS: frozenset[str] = frozenset(
    {
        "exec",
        "eval",
        "compile",
        "__import__",
        "globals",
        "locals",
        "getattr",
        "setattr",
        "delattr",
        "open",
        "breakpoint",
        "exit",
        "quit",
    }
)
"""禁止在沙箱中调用的危险函数名称集合。"""


@dataclass(frozen=True)
class AnalysisResult:
    """AST 静态分析结果值对象。

    用于表示 ``analyze_code`` 函数对 Python 代码片段的分析结果。
    作为不可变数据类，保证分析结果在传递过程中不会被意外修改。

    Attributes:
        ok: 分析是否通过，``True`` 表示代码安全可执行。
        reason: 拒绝原因描述，``ok=True`` 时为空字符串。
    """

    ok: bool
    reason: str = ""


def analyze_code(
    code: str,
    allowed_modules: frozenset[str] = DEFAULT_ALLOWED_MODULES,
    blocked_calls: frozenset[str] = BLOCKED_CALLS,
) -> AnalysisResult:
    """对 Python 代码片段执行 AST 静态安全分析。

    在代码实际执行前，通过解析 AST 检查以下安全约束：

    1. 代码必须是合法的 Python 语法（能被 ``ast.parse`` 成功解析）。
    2. 所有 ``import`` 和 ``from ... import`` 语句引用的顶层模块必须在白名单中。
    3. 不允许相对导入（``from . import ...``）。
    4. 不允许调用黑名单中的危险函数。

    Args:
        code: 待分析的 Python 代码字符串。
        allowed_modules: 允许导入的模块名白名单，默认为 ``DEFAULT_ALLOWED_MODULES``。
        blocked_calls: 禁止调用的函数名黑名单，默认为 ``BLOCKED_CALLS``。

    Returns:
        ``AnalysisResult`` 实例。若所有检查通过，返回 ``ok=True``；
        否则返回 ``ok=False`` 并在 ``reason`` 中描述拒绝原因。
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as e:
        msg = f"语法错误: {e.msg}"
        if e.lineno is not None:
            msg += f" (行 {e.lineno}"
            if e.offset is not None:
                msg += f", 列 {e.offset}"
            msg += ")"
        return AnalysisResult(ok=False, reason=msg)

    for node in ast.walk(tree):
        # 检查 import 语句
        if isinstance(node, ast.Import):
            for alias in node.names:
                top_level = alias.name.split(".")[0]
                if top_level not in allowed_modules:
                    return AnalysisResult(
                        ok=False,
                        reason=f"禁止导入模块: {alias.name}",
                    )

        # 检查 from ... import 语句
        elif isinstance(node, ast.ImportFrom):
            if node.level > 0:
                return AnalysisResult(
                    ok=False,
                    reason="禁止使用相对导入",
                )
            if node.module is not None:
                top_level = node.module.split(".")[0]
                if top_level not in allowed_modules:
                    return AnalysisResult(
                        ok=False,
                        reason=f"禁止导入模块: {node.module}",
                    )

        # 检查函数调用
        elif isinstance(node, ast.Call):
            func_name: str | None = None
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                func_name = node.func.attr

            if func_name is not None and func_name in blocked_calls:
                return AnalysisResult(
                    ok=False,
                    reason=f"禁止调用函数: {func_name}",
                )

    return AnalysisResult(ok=True)


def _create_memory_limiter(max_bytes: int) -> Callable[[], None] | None:
    """创建子进程内存限制函数。

    在 Linux/macOS 上返回一个可调用对象，用作 ``preexec_fn`` 参数，
    在子进程启动时通过 ``resource.setrlimit`` 设置虚拟内存上限。
    在 Windows 上由于不支持 ``resource`` 模块，返回 ``None`` 并记录警告日志。

    Args:
        max_bytes: 内存限制的字节数。

    Returns:
        Linux/macOS 上返回设置内存限制的可调用对象；Windows 上返回 ``None``。
    """
    if sys.platform == "win32":
        logger.warning("Windows 平台不支持 resource.setrlimit，跳过内存限制")
        return None

    def _set_memory_limit() -> None:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (max_bytes, max_bytes))

    return _set_memory_limit


class PythonExecTool(Tool):
    """Python 脚本安全执行工具（Workspace 受控版）。

    基于 AST 静态分析和 asyncio 异步子进程实现 Python 脚本的安全受控执行，
    为 LLM Agent 提供在**工作区边界内**的沙箱环境中运行 Python 代码片段
    的能力。

    执行流程：

    1. 提取并校验参数（``code`` / ``timeout``）
    2. ``Workspace.capabilities().local_materialization`` 能力守卫
    3. 调用 ``analyze_code`` 进行 AST 静态安全分析（既有语义，需求 6.10）
    4. 通过 ``workspace.resolve_path("/") → materialize_cwd`` 取宿主 cwd
    5. 将代码写入 ``host_cwd`` 下的临时 ``.py`` 文件
    6. 通过 ``asyncio.create_subprocess_exec`` 启动子进程，``cwd=host_cwd``
    7. ``asyncio.wait_for`` 做超时控制 / 内存限制 / 敏感环境变量剥离
    8. 对输出进行截断处理和格式化
    9. ``finally`` 清理临时文件

    内置多层安全防护：Workspace 边界、AST 静态分析、进程隔离、环境变量清理、
    超时限制、内存限制（Linux/macOS）、输出大小限制。

    Attributes:
        _workspace: 注入的 :class:`Workspace` 端口实例。
        _timeout: 默认脚本执行超时秒数。
        _max_output_size: stdout/stderr 合并输出大小上限（字节）。
        _max_memory_mb: 子进程内存限制（MB）。
        _allowed_modules: 允许在沙箱中导入的模块名集合。
    """

    def __init__(
        self,
        workspace: Workspace,
        *,
        timeout: int = 30,
        max_output_size: int = 51200,
        max_memory_mb: int = 256,
        allowed_modules: frozenset[str] = DEFAULT_ALLOWED_MODULES,
    ) -> None:
        """初始化 :class:`PythonExecTool`。

        Args:
            workspace: 注入的 :class:`Workspace` 端口实例。工具通过该实例完成
                路径解析与宿主 cwd 物化，自身不接触宿主绝对路径。
            timeout: 默认脚本执行超时秒数，当 execute 未传 timeout 时使用。
            max_output_size: stdout/stderr 合并输出大小上限（字节），超过时截断。
            max_memory_mb: 子进程内存限制（MB），仅在 Linux/macOS 上生效。
            allowed_modules: 允许在沙箱中导入的模块名集合，默认为
                ``DEFAULT_ALLOWED_MODULES``。
        """
        self._workspace: Workspace = workspace
        self._timeout: int = timeout
        self._max_output_size: int = max_output_size
        self._max_memory_mb: int = max_memory_mb
        self._allowed_modules: frozenset[str] = allowed_modules

    @property
    def name(self) -> str:
        """工具的唯一名称。"""
        return "python_exec"

    @property
    def risk_level(self) -> ToolRiskLevel:
        """Python 执行工具为 critical 风险。"""
        return ToolRiskLevel.CRITICAL

    @property
    def description(self) -> str:
        """动态中文功能描述，拼入工作区根以引导 LLM 使用相对路径。"""
        workspace_root = self._workspace.display_root_hint()
        return (
            "Run a Python snippet in a controlled sandbox for data processing, "
            "calculation, text transformation, and lightweight local verification. "
            "The tool applies AST checks, timeout, memory limits, sanitized "
            f"environment variables, and workspace confinement. The script runs at "
            f"workspace root {workspace_root}; use POSIX / separators for file paths."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        """符合 JSON Schema 规范的参数描述字典。"""
        return {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python code snippet to execute.",
                },
                "timeout": {
                    "type": "integer",
                    "description": (
                        "Execution timeout in seconds. Defaults to the configured value."
                    ),
                },
            },
            "required": ["code"],
        }

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """执行 Python 代码片段并返回结果。

        执行流程（严格按需求 6.6 / 6.7 / 6.10 / 6.11）：

        1. AST 静态安全分析（保留既有语义，不受 Workspace 影响）；
        2. ``Workspace.capabilities().local_materialization`` 守卫；
        3. ``resolve_path("/") → materialize_cwd(ws_path)`` 取宿主 cwd；
        4. 临时 ``.py`` 文件写入 ``host_cwd``，子进程 ``cwd=host_cwd``；
        5. 内存限制 / 敏感环境变量剥离 / 超时控制 / 输出截断保持不变。

        Args:
            **kwargs: 工具参数，包含必填的 ``code`` 和可选的 ``timeout``。

        Returns:
            :class:`ToolExecutionResult`，``content`` 为包含退出码、stdout 和
            stderr 的格式化结果字符串，格式为
            ``Exit Code: {code}\\n\\n[stdout]\\n{stdout}\\n\\n[stderr]\\n{stderr}``；
            ``metadata`` 含以下键：

            - ``code_summary`` (str): 代码前 128 字符。
            - ``exit_code`` (int): 进程退出码。
            - ``stdout_bytes`` (int): stdout 原始字节数（截断前）。
            - ``stderr_bytes`` (int): stderr 原始字节数（截断前）。
            - ``memory_limited`` (bool): 是否启用了内存限制。
            - ``truncated`` (bool): 输出是否被截断。

        Raises:
            ToolExecutionError: 后端不支持本地物化、AST 分析拒绝代码、
                工作区根不可用、执行超时或其他异常时抛出。
        """
        code: str = kwargs["code"]
        timeout: int = kwargs.get("timeout", self._timeout)

        # 1. AST 静态安全分析（既有语义，需求 6.10，位置保持在入口前端）
        result = analyze_code(code, allowed_modules=self._allowed_modules)
        if not result.ok:
            raise ToolExecutionError(
                message=f"代码安全检查未通过: {result.reason}",
                tool_name=self.name,
            )

        # 2. local_materialization 能力守卫（需求 6.6 / 6.7）
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

        # 3. 通过 Workspace 取宿主 cwd（子进程工作目录锁定在工作区根）
        try:
            ws_root = self._workspace.resolve_path("/")
            host_cwd: str = self._workspace.materialize_cwd(ws_root)
        except WorkspaceConfinementViolation as e:
            raise ToolExecutionError(
                message="工作区根路径不可用",
                tool_name=self.name,
            ) from e
        except WorkspaceNotFoundError as e:
            raise ToolExecutionError(
                message="工作区根路径不存在",
                tool_name=self.name,
            ) from e
        except WorkspaceIoError as e:
            raise ToolExecutionError(
                message="工作区根路径不可用",
                tool_name=self.name,
            ) from e

        # 4. 写入临时文件并执行
        tmp_path: str | None = None
        try:
            # 创建临时 .py 文件（落在 host_cwd 下，与子进程 cwd 一致）
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".py",
                dir=host_cwd,
                delete=False,
                encoding="utf-8",
            ) as tmp_file:
                tmp_file.write(code)
                tmp_path = tmp_file.name

            # 5. 构建内存限制函数
            max_bytes = self._max_memory_mb * 1024 * 1024
            memory_limiter = _create_memory_limiter(max_bytes)
            # Windows 上 _create_memory_limiter 返回 None，即未启用内存限制。
            memory_limited = memory_limiter is not None

            # 6. 清理环境变量
            clean_env = sanitize_env()

            # 7. 创建异步子进程
            process = await asyncio.create_subprocess_exec(
                sys.executable,
                tmp_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=clean_env,
                cwd=host_cwd,
                preexec_fn=memory_limiter,
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
                    message=f"脚本执行超时（{timeout} 秒）: {code[:100]}",
                    tool_name=self.name,
                ) from exc

            # 9. 解码输出
            stdout_text = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr_text = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""

            # 原始字节数（截断前），供 trace metadata 使用。
            stdout_raw_bytes = len(stdout_bytes) if stdout_bytes else 0
            stderr_raw_bytes = len(stderr_bytes) if stderr_bytes else 0

            # 10. 合并输出并检查大小（与 ShellExecTool 保持一致的截断逻辑）
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
                    "code_summary": code[:128],
                    "exit_code": process.returncode,
                    "stdout_bytes": stdout_raw_bytes,
                    "stderr_bytes": stderr_raw_bytes,
                    "memory_limited": memory_limited,
                    "truncated": truncated,
                },
            )

        except ToolExecutionError:
            raise
        except Exception as e:
            raise ToolExecutionError(
                message=f"Python 脚本执行失败: {e}",
                tool_name=self.name,
            ) from e
        finally:
            # 清理临时文件
            if tmp_path is not None:
                with contextlib.suppress(OSError):
                    os.unlink(tmp_path)
