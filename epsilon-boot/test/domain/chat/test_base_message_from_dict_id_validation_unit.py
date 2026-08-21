"""历史会话恢复过滤策略单元测试（Task 5.2）。

对应 design 测试矩阵 T8 / T9 / T11：

- T8：``tool_calls=[{"id":"","name":"x","arguments":"{}"}]`` 默认 filter →
  ``AssistantMessage.tool_calls == []``，``caplog`` 含 ``skipped_count=1``
- T9：``tool_calls=[{"id":None,"name":"x","arguments":"{}"}, {"id":"ok",...}]``
  filter → 保留第 2 项
- T11：合法历史快照（所有 id 非空） → 反序列化结果与现有完全一致（回归保护）

用 ``monkeypatch`` 把模块级 ``_HISTORY_RESTORE_STRATEGY`` 强制为 ``"filter"``，
避免依赖配置文件。
"""

from __future__ import annotations

import logging

import pytest

from domain.chat import context as ctx_module
from domain.chat.context import AssistantMessage, BaseMessage


@pytest.fixture(autouse=True)
def force_filter_strategy(monkeypatch: pytest.MonkeyPatch) -> None:
    """模块级 _HISTORY_RESTORE_STRATEGY 强制为 'filter'。"""
    monkeypatch.setattr(ctx_module, "history_restore_strategy", "filter")


def test_t8_filter_strategy_drops_empty_id() -> None:
    """T8: id='' 的项在 filter 策略下被过滤，返回 tool_calls == []。

    注：``ddd-tactical-remediation`` 移除了领域层 ``logging`` 依赖（需求 A），
    filter 分支原有的内部诊断 WARN 日志随之删除，本用例仅保留其对外可观测
    行为断言（过滤结果），不再断言领域层日志记录。
    """
    data = {
        "role": "assistant",
        "content": "hello",
        "tool_calls": [
            {"id": "", "name": "web_search", "arguments": "{}"},
        ],
    }

    msg = BaseMessage.from_dict(data)
    assert isinstance(msg, AssistantMessage)
    assert msg.tool_calls == []


def test_t9_filter_keeps_valid_drops_invalid() -> None:
    """T9: id=None 的项过滤，id='ok' 的项保留。"""
    data = {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": None, "name": "x", "arguments": "{}"},
            {"id": "ok", "name": "y", "arguments": '{"a":1}'},
        ],
    }
    msg = BaseMessage.from_dict(data)
    assert isinstance(msg, AssistantMessage)
    assert len(msg.tool_calls) == 1
    assert msg.tool_calls[0].id == "ok"
    assert msg.tool_calls[0].name == "y"


def test_t11_legal_snapshot_unchanged_no_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """T11: 所有 id 合法时反序列化等价于现有行为，无 WARN 日志（回归保护）。"""
    data = {
        "role": "assistant",
        "content": "hi",
        "tool_calls": [
            {"id": "call_a", "name": "tool_a", "arguments": '{"x":1}'},
            {"id": "call_b", "name": "tool_b", "arguments": '{"y":2}'},
        ],
    }
    caplog.set_level(logging.WARNING, logger="domain.chat.context")

    msg = BaseMessage.from_dict(data)
    assert isinstance(msg, AssistantMessage)
    assert len(msg.tool_calls) == 2
    assert msg.tool_calls[0].id == "call_a"
    assert msg.tool_calls[1].id == "call_b"

    history_warnings = [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "tool_call 违约" in r.getMessage()
    ]
    assert history_warnings == []


def test_filter_strategy_preserves_metadata_and_drops_invalid() -> None:
    """filter 策略下含 session_id 的会话被过滤，metadata 原样保留。

    注：``ddd-tactical-remediation`` 需求 A 移除了领域层 ``logging``，原「session_id
    回填到 WARN extra」的日志断言不再适用；session_id 信号的等价保留由 raise
    分支单测（``test_t10_raise_strategy...`` 断言 ``exc.details["session_id"]``）覆盖。
    本用例改为断言 filter 分支的对外可观测行为：非法项被过滤、metadata 不丢失。
    """
    data = {
        "role": "assistant",
        "content": "x",
        "tool_calls": [
            {"id": "", "name": "web_search", "arguments": "{}"},
        ],
        "metadata": {"session_id": "sess-xxx"},
    }

    msg = BaseMessage.from_dict(data)
    assert isinstance(msg, AssistantMessage)
    assert msg.tool_calls == []
    assert msg.metadata == {"session_id": "sess-xxx"}


def test_no_tool_calls_field_unchanged() -> None:
    """RG5：无 tool_calls 字段时反序列化路径不变。"""
    data = {"role": "assistant", "content": "pure text"}
    msg = BaseMessage.from_dict(data)
    assert isinstance(msg, AssistantMessage)
    assert msg.tool_calls == []
    assert msg.content == "pure text"
