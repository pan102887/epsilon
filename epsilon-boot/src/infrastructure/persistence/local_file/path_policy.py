"""跨平台路径合法性策略（``Cross_Platform_Path_Policy``）。

本模块提供纯函数式的路径合法性校验与归一工具。所有方法不持有 I/O 状态、
同一输入得到同一输出，可安全地跨线程 / 跨进程复用。

职责：

- 通过 ``hash_session_id`` 把任意 ``session_id`` 映射为不可逆的两段十六进制
  串，天然规避 Windows 保留名 / 非法字符 / 大小写敏感冲突；
- 通过 ``check_dirname`` 校验单段目录或文件名合法性；
- 通过 ``check_absolute_path_length`` 在 Windows 平台拦截 260 字符上限；
- 通过 ``ensure_within_root`` 阻止 ``..`` 逃逸出 ``LOCAL_PERSISTENCE_ROOT``。

需求：4.1、4.2、4.3、4.4、4.6、12.2、12.4。
"""

import hashlib
import os
import re
from pathlib import Path


class PathPolicyViolation(ValueError):
    """路径策略校验失败。

    错误消息均以中文呈现；异常类型继承 ``ValueError`` 以便调用方做更宽泛的
    捕获，但在组合根层通常被翻译为 ``ConfigurationError`` 或直接向上抛出。
    """


class CrossPlatformPathPolicy:
    """跨平台路径合法性策略。

    纯函数式；不持有 I/O 状态；同一输入得到同一输出。
    """

    # 需求 4.2：Windows 非法字符集合（含 NUL）。``/`` 与 ``\\`` 均被拒绝，
    # 防止调用方把路径分隔符混入单段 ``name`` 导致越权。
    _ILLEGAL_CHARS: re.Pattern[str] = re.compile(r"[\x00/\\:*?\"<>|]")
    # 需求 4.3：Windows 保留名（大小写无关）。
    _RESERVED_NAMES: frozenset[str] = frozenset(
        {"CON", "PRN", "AUX", "NUL"}
        | {f"COM{i}" for i in range(1, 10)}
        | {f"LPT{i}" for i in range(1, 10)}
    )
    # 需求 4.4：Windows 默认绝对路径长度上限。
    _WIN_MAX_PATH: int = 260

    def hash_session_id(self, session_id: str) -> tuple[str, str]:
        """将 ``session_id`` 哈希为 ``(bucket, stem)`` 二元组。

        使用 SHA-256 十六进制小写串；前 2 位作为 bucket 目录，后 62 位作为
        文件名 stem。最终文件名为 ``<bucket>/<stem>.json``；共 64 字符十六进制，
        Windows 保留名不可能完全匹配（保留名最长 4 字符且非纯十六进制）。

        Args:
            session_id: 原始会话 ID，允许任意 Unicode 字符。

        Returns:
            ``(bucket, stem)`` 二元组，均为十六进制小写串。
        """
        digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
        return digest[:2], digest[2:]

    def check_dirname(self, name: str) -> None:
        """校验一段目录或文件名合法性。

        Args:
            name: 单段目录或文件名（不含分隔符）。

        Raises:
            PathPolicyViolation: 当 ``name`` 含 NUL / ``/`` / ``\\`` / ``:``
                / ``*`` / ``?`` / ``"`` / ``<`` / ``>`` / ``|`` 任一字符，
                或去除扩展名后的前缀命中 Windows 保留名时抛出。
        """
        if self._ILLEGAL_CHARS.search(name):
            raise PathPolicyViolation(
                f'路径片段含非法字符：{name!r}（拒绝 NUL / / \\ : * ? " < > |）'
            )
        if name.split(".", 1)[0].upper() in self._RESERVED_NAMES:
            raise PathPolicyViolation(f"路径片段命中 Windows 保留名：{name}")

    def check_absolute_path_length(self, absolute_path: Path) -> None:
        """Windows 下无长路径支持时检查 260 字符上限。

        仅当 ``os.name == "nt"`` 时触发检查；其他平台直接返回。

        Args:
            absolute_path: 规范化后的绝对路径。

        Raises:
            PathPolicyViolation: 在 Windows 平台下 ``str(absolute_path)``
                长度超过 ``_WIN_MAX_PATH``（260）时抛出，错误消息包含实际
                长度与上限，并提示"启用 Windows 长路径或缩短 LOCAL_PERSISTENCE_ROOT"。
        """
        if os.name == "nt" and len(str(absolute_path)) > self._WIN_MAX_PATH:
            raise PathPolicyViolation(
                f"路径过长（{len(str(absolute_path))} > {self._WIN_MAX_PATH}），"
                "请启用 Windows 长路径或缩短 LOCAL_PERSISTENCE_ROOT"
            )

    def ensure_within_root(self, root: Path, candidate: Path) -> Path:
        """确认 ``candidate`` 在 ``root`` 之内（阻止 ``..`` 逃逸）。

        将 ``root / candidate`` 规范化后尝试 ``relative_to(root.resolve())``；
        若规范化结果不在 ``root`` 之下则抛出 ``PathPolicyViolation``。

        Args:
            root: 本地持久化根目录的绝对路径。
            candidate: 待校验的相对路径（或可拼接到 ``root`` 的绝对路径）。

        Returns:
            规范化后落在 ``root`` 之内的绝对路径。

        Raises:
            PathPolicyViolation: ``candidate`` 规范化后逃逸出 ``root`` 时抛出。
        """
        resolved = (root / candidate).resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError as exc:
            raise PathPolicyViolation(f"路径越出 LOCAL_PERSISTENCE_ROOT：{candidate}") from exc
        return resolved
