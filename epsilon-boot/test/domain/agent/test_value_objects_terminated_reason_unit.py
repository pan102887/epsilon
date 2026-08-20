"""``AgentResult.terminated_reason`` 默认值与字段集合单元测试模块。

覆盖需求 8.1, 8.2, 8.3, 8.4, NFR-2, NFR-5：

- (a) ``AgentResult(content="x", model="m")`` 默认构造 → ``terminated_reason == "completed"``
- (b) ``AgentResult(content="", model="m", terminated_reason="max_rounds")`` → 字段读取正确
- (c) ``AgentTerminationReason`` 类型别名取值集合为 ``{"completed", "max_rounds"}``
- (d) ``RoundOutcome`` 默认 ``terminated_reason == "completed"``；显式 ``"max_rounds"`` 构造可读
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from typing import get_args

import pytest

from domain.agent.agent_loop_policy import RoundOutcome
from domain.agent.value_objects import AgentResult, AgentTerminationReason
from domain.model_access.value_objects import LLMResponse


class TestAgentTerminationReasonTypeAlias:
    """AgentTerminationReason 类型别名的取值集合验证。"""

    def test_type_alias_values(self) -> None:
        """取值集合严格为 {"completed", "max_rounds", "token_budget_exceeded"}（v3 扩展）。"""
        values = set(get_args(AgentTerminationReason))
        assert values == {"completed", "max_rounds", "token_budget_exceeded"}


class TestAgentResultTerminatedReason:
    """AgentResult.terminated_reason 字段行为。"""

    def test_default_construction_yields_completed(self) -> None:
        """默认构造 → terminated_reason == "completed"。"""
        result = AgentResult(content="x", model="m")
        assert result.terminated_reason == "completed"

    def test_explicit_max_rounds_readable(self) -> None:
        """显式传入 "max_rounds" 可正确读取。"""
        result = AgentResult(content="", model="m", terminated_reason="max_rounds")
        assert result.terminated_reason == "max_rounds"
        assert result.content == ""
        assert result.model == "m"

    def test_frozen_immutable(self) -> None:
        """AgentResult 是 frozen dataclass，字段不可修改。"""
        result = AgentResult(content="x", model="m")
        with pytest.raises(FrozenInstanceError):
            result.terminated_reason = "max_rounds"  # type: ignore[misc]

    def test_existing_fields_unchanged(self) -> None:
        """既有字段集合不受新增 terminated_reason 影响。"""
        result = AgentResult(
            content="hello",
            model="gpt-4",
            usage={"total_tokens": 10},
            latency_ms=100.0,
            status="completed",
            approval=None,
        )
        assert result.content == "hello"
        assert result.model == "gpt-4"
        assert result.usage == {"total_tokens": 10}
        assert result.latency_ms == 100.0
        assert result.status == "completed"
        assert result.approval is None
        assert result.terminated_reason == "completed"


class TestRoundOutcomeTerminatedReason:
    """RoundOutcome.terminated_reason 字段行为。"""

    def test_default_is_completed(self) -> None:
        """默认构造 → terminated_reason == "completed"。"""
        response = LLMResponse(content="ok", model="m", tool_calls=[], usage={})
        outcome = RoundOutcome(
            kind="text",
            round_num=1,
            response=response,
            total_usage={},
        )
        assert outcome.terminated_reason == "completed"

    def test_explicit_max_rounds_readable(self) -> None:
        """显式传入 "max_rounds" 可正确读取。"""
        response = LLMResponse(content="", model="m", tool_calls=[], usage={})
        outcome = RoundOutcome(
            kind="final",
            round_num=3,
            response=response,
            total_usage={"total_tokens": 5},
            terminated_reason="max_rounds",
        )
        assert outcome.terminated_reason == "max_rounds"
        assert outcome.kind == "final"
        assert outcome.round_num == 3
