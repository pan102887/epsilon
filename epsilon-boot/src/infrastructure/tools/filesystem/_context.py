"""受控文件工具的观测上下文辅助模块。

本模块提供两个轻量 helper，供 4 个受控文件工具在调用 ``Workspace`` I/O 前
构造白名单 ``context`` 字典使用：

- :func:`_current_trace_id_or_none`：读取当前链路 ``trace_id``；
- :func:`_current_agent_id_or_none`：读取当前 ``agent_id``。

**当前实现**：仓库尚未建立统一的 trace / agent ContextVar 机制
（``common.logging.trace_context`` 等未提供），本 helper 恒返回 ``None``。
调用方（工具层）在 ``None`` 时应**跳过**向 ``context`` 写入对应键，避免
让后端收到显式 ``None`` 值后误入白名单。

未来若接入真实 trace context，只需把本文件两个函数改为从 ContextVar
读取即可，工具层实现和对应的单元测试断言无需修改（断言集中关注
``context["tool_name"]`` 的存在，``trace_id`` / ``agent_id`` 为"有则透传、
无则缺省"的兼容契约）。

依赖白名单：仅 ``typing.Optional``（或 PEP 604 ``| None``）。禁止 import
``os`` / ``pathlib`` / 具体后端实现。
"""

from __future__ import annotations


def _current_trace_id_or_none() -> str | None:
    """返回当前请求链路的 ``trace_id``；不可用时返回 ``None``。

    当前实现：仓库暂无统一 trace ContextVar，恒返回 ``None``。调用方在
    ``None`` 时应跳过向 ``context`` 字典写入 ``"trace_id"`` 键。

    Returns:
        字符串形式的 ``trace_id``；无可用值时返回 ``None``。
    """
    return None


def _current_agent_id_or_none() -> str | None:
    """返回当前调用方 Agent 标识；不可用时返回 ``None``。

    当前实现：仓库暂无统一 agent ContextVar，恒返回 ``None``。调用方在
    ``None`` 时应跳过向 ``context`` 字典写入 ``"agent_id"`` 键。

    Returns:
        字符串形式的 ``agent_id``；无可用值时返回 ``None``。
    """
    return None


def current_trace_id_or_none() -> str | None:
    """返回当前请求链路的 trace ID。"""
    return _current_trace_id_or_none()


def current_agent_id_or_none() -> str | None:
    """返回当前调用方 Agent ID。"""
    return _current_agent_id_or_none()
