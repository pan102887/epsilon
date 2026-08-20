"""历史会话恢复 raise 策略单元测试（Task 5.3）。

对应 design 测试矩阵 T10：``monkeypatch`` 把 ``_HISTORY_RESTORE_STRATEGY``
设为 ``"raise"``，输入与 T8 相同 → 抛 ``InvalidToolCallIdError(source=
"history_restore", ...)``，``exc.details["skipped_count"] == 1``。
"""

from __future__ import annotations

import pytest

from domain.chat import context as ctx_module
from domain.chat.context import BaseMessage
from domain.model_access.exceptions import InvalidToolCallIdError


@pytest.fixture(autouse=True)
def force_raise_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    """模块级 _HISTORY_RESTORE_STRATEGY 强制为 'raise'。"""
    monkeypatch.setattr(ctx_module, "_HISTORY_RESTORE_STRATEGY", "raise")


def test_t10_raise_strategy_raises_invalid_tool_call_id_error() -> None:
    """T10: raise 策略下抛 InvalidToolCallIdError，details.skipped_count=1。

    注：``ddd-tactical-remediation`` 需求 A 移除了领域层 ``logging`` 依赖，
    原「抛异常前发出 WARN 日志」的日志断言随之删除；该告警承载的全部信号
    （source/skipped_count/session_id/raw_id_value/tool_name）完整保留在
    ``InvalidToolCallIdError.details`` 中，由下方异常断言逐字段验证，信号无损失。
    """
    data = {
        "role": "assistant",
        "content": "x",
        "tool_calls": [
            {"id": "", "name": "web_search", "arguments": "{}"},
        ],
        "metadata": {"session_id": "sess-yyy"},
    }

    with pytest.raises(InvalidToolCallIdError) as exc_info:
        BaseMessage.from_dict(data)

    exc = exc_info.value
    assert exc.details["source"] == "history_restore"
    assert exc.details["skipped_count"] == 1
    assert exc.details["session_id"] == "sess-yyy"
    assert exc.details["raw_id_value"] == ""
    assert exc.details["tool_name"] == "web_search"
