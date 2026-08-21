"""审批日志脱敏工具模块。

提供 HITL 审批相关日志字段的脱敏、截断与结构化 extra 构造函数。
"""

from __future__ import annotations

import json
from typing import Any, cast

SENSITIVE_KEYS = frozenset({"api_key", "password", "secret", "token", "authorization"})
"""审批日志中需要脱敏的敏感键名集合。"""


def redact_approval_value(value: Any, *, max_length: int = 1200) -> str:
    """将审批日志值转换为脱敏字符串。

    Args:
        value: 任意待写日志值。
        max_length: 输出字符串最大长度。

    Returns:
        已对敏感键值脱敏并按长度截断的字符串。
    """
    redacted = _redact(value)
    if isinstance(redacted, str):
        text = redacted
    else:
        text = json.dumps(redacted, ensure_ascii=False, sort_keys=True)
    if len(text) > max_length:
        return text[:max_length] + "...(truncated)"
    return text


def approval_log_extra(
    session_id: str,
    approval_id: str,
    tool_names: list[str],
    action_count: int,
    round_num: int | None = None,
    decision_types: list[str] | None = None,
) -> dict[str, Any]:
    """构造审批日志结构化 extra 字段。"""
    extra: dict[str, Any] = {
        "session_id": session_id,
        "approval_id": approval_id,
        "tool_names": tool_names,
        "action_count": action_count,
    }
    if round_num is not None:
        extra["round_num"] = round_num
    if decision_types is not None:
        extra["decision_types"] = decision_types
    return extra


def _redact(value: Any) -> Any:
    """递归脱敏 dict/list/str。"""
    if isinstance(value, dict):
        mapping = cast(dict[object, object], value)
        return {
            key: "***" if str(key).lower() in SENSITIVE_KEYS else _redact(item)
            for key, item in mapping.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in cast(list[object], value)]
    if isinstance(value, tuple):
        return [_redact(item) for item in cast(tuple[object, ...], value)]
    return value
