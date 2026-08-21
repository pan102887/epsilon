"""环境上下文提供器模块。

本模块生成可注入模型输入的 Codex 风格环境上下文。V1 使用固定的
display-safe 工作区提示，避免读取宿主环境变量或暴露本机真实绝对路径。
"""

import re
from collections.abc import Callable
from datetime import datetime
from typing import Protocol


class EnvironmentContextProvider(Protocol):
    """环境上下文提供器协议，仅供基础设施内部协作者使用。"""

    def build(self) -> str:
        """构建可注入模型输入的环境上下文文本。"""
        ...


class UnsafeEnvironmentContextError(RuntimeError):
    """环境上下文包含不允许暴露给模型的宿主路径或敏感内容。"""


class EnvironmentContextBuildError(RuntimeError):
    """环境上下文生成失败，阻止继续构建模型输入。"""


_HOST_ABSOLUTE_PATH_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?<![\w/])/(?:mnt|home|Users|var|tmp|root|opt|etc)/[^\s<>\"']+"),
    re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:\\[^\s<>\"']+"),
)


def _assert_no_host_absolute_path(text: str) -> None:
    """校验文本不包含常见宿主绝对路径。

    Args:
        text: 待注入模型输入的环境上下文文本。

    Raises:
        UnsafeEnvironmentContextError: 当文本命中常见宿主绝对路径模式时抛出。
            错误消息刻意不包含命中文本，避免异常链路二次泄露敏感路径。
    """
    if any(pattern.search(text) for pattern in _HOST_ABSOLUTE_PATH_PATTERNS):
        raise UnsafeEnvironmentContextError("环境上下文包含不允许暴露的宿主绝对路径")


def assert_no_host_absolute_path(text: str) -> None:
    """校验环境上下文不包含宿主机绝对路径。"""
    _assert_no_host_absolute_path(text)


class StaticEnvironmentContextProvider:
    """生成安全的静态环境上下文。"""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        workspace_label: str = "workspace:/",
    ) -> None:
        """初始化环境上下文提供器。

        Args:
            clock: 可注入的当前时间函数，默认使用 ``datetime.now``。
            workspace_label: 模型可见的工作区提示，默认固定为 ``workspace:/``。
        """
        self._clock = clock or datetime.now
        self._workspace_label = workspace_label

    def build(self) -> str:
        """生成不含宿主绝对路径、环境变量值或密钥的环境上下文。"""
        current_date = self._clock().date().isoformat()
        text = "\n".join(
            (
                "<environment_context>",
                f"current_date: {current_date}",
                f"workspace: {self._workspace_label}",
                (
                    "path_policy: Use workspace-relative POSIX paths. "
                    "Do not expose host absolute paths."
                ),
                "</environment_context>",
            )
        )
        _assert_no_host_absolute_path(text)
        return text
