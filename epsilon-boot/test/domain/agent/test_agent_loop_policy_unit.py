"""领域层 ``agent_loop_policy`` 纯函数与 ``RoundOutcome`` 值对象单元测试模块。

锁定 ``domain.agent.agent_loop_policy`` 的 4 个纯编排函数
（``compute_total_tokens`` / ``is_token_budget_exceeded`` / ``detect_handoff`` /
``outcome_to_agent_result``）与 ``RoundOutcome`` 值对象的行为，性质为**脱离运行时**
的领域单测：仅 import ``domain.*``，不依赖 ``application`` / ``infrastructure`` /
框架运行时即可执行（追溯需求 4.1/4.2/4.3/4.4，design Property 1、2、5、8）。

覆盖 design 正确性属性要求的全部分支：

- ``compute_total_tokens``：``total_tokens`` 命中 / 为 0 回退 / 缺失回退 / 空 dict；
- ``is_token_budget_exceeded``：``max_total_tokens is None`` 恒 False / 恰好等于不超 / 超限；
- ``detect_handoff``：命中 / 未命中 / 尾部非 ToolMessage 停止 / 多 ToolMessage 任意位置命中；
- ``outcome_to_agent_result``：``handoff`` / ``text`` / ``final`` / ``approval`` 各 kind
  分支，含 ``handoff`` 分支 ``model`` 取父模型的 ADR-0010 疑点 2 断言；
- ``RoundOutcome``：``handoff_target`` / ``handoff_content`` / ``tool_calls`` 默认值与 frozen
  不可变（不与 ``test_value_objects_terminated_reason_unit.py`` 已覆盖处重复断言）。
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from domain.agent.agent_loop_policy import (
    RoundOutcome,
    compute_total_tokens,
    detect_handoff,
    is_token_budget_exceeded,
    outcome_to_agent_result,
)
from domain.agent.value_objects import AgentConfig, ApprovalRequiredPayload
from domain.chat.context import ConversationContext, ToolMessage
from domain.model_access.value_objects import LLMResponse


def _make_config(max_total_tokens: int | None) -> AgentConfig:
    """构造一份仅关注 ``max_total_tokens`` 的合法 ``AgentConfig``。"""
    return AgentConfig(
        system_prompt="你是助手",
        tool_schemas=[],
        model="m",
        max_rounds=3,
        prompt_id="chat-default@v1",
        max_total_tokens=max_total_tokens,
    )


class TestComputeTotalTokens:
    """``compute_total_tokens`` 分支覆盖（Property 1）。"""

    def test_total_tokens_hit(self) -> None:
        """``total_tokens`` > 0 时直接返回该值。"""
        assert compute_total_tokens({"total_tokens": 100}) == 100

    def test_total_tokens_zero_falls_back(self) -> None:
        """``total_tokens`` 为 0 时回退 prompt + completion。"""
        assert (
            compute_total_tokens(
                {"total_tokens": 0, "prompt_tokens": 3, "completion_tokens": 5}
            )
            == 8
        )

    def test_total_tokens_missing_falls_back(self) -> None:
        """``total_tokens`` 缺失时回退 prompt + completion。"""
        assert compute_total_tokens({"prompt_tokens": 3, "completion_tokens": 5}) == 8

    def test_empty_dict_returns_zero(self) -> None:
        """空 dict 回退为 0。"""
        assert compute_total_tokens({}) == 0


class TestIsTokenBudgetExceeded:
    """``is_token_budget_exceeded`` 分支覆盖（Property 1）。"""

    def test_none_budget_always_false(self) -> None:
        """``max_total_tokens is None`` 时恒返回 False。"""
        config = _make_config(None)
        assert is_token_budget_exceeded(config, {"total_tokens": 10_000}) is False

    def test_exactly_equal_not_exceeded(self) -> None:
        """累计恰好等于上限时不算超限（严格大于才超）。"""
        config = _make_config(100)
        assert is_token_budget_exceeded(config, {"total_tokens": 100}) is False

    def test_over_budget_true(self) -> None:
        """累计超过上限时返回 True。"""
        config = _make_config(100)
        assert is_token_budget_exceeded(config, {"total_tokens": 101}) is True


class TestDetectHandoff:
    """``detect_handoff`` 分支覆盖（Property 1）。"""

    def test_hit_at_tail(self) -> None:
        """尾部 ToolMessage 命中 ``handoff_target`` → 返回 (target, content)。"""
        context = ConversationContext()
        context.add_user_message("hi")
        context.append_message(
            ToolMessage(
                content="目标回复",
                tool_name="handoff_to_agent",
                metadata={"handoff_target": "worker"},
            )
        )
        assert detect_handoff(context) == ("worker", "目标回复")

    def test_miss_when_no_marker(self) -> None:
        """尾部 ToolMessage 无 ``handoff_target`` → None。"""
        context = ConversationContext()
        context.append_message(
            ToolMessage(content="普通结果", tool_name="calc", metadata={})
        )
        assert detect_handoff(context) is None

    def test_stops_at_non_tool_message(self) -> None:
        """尾部为非 ToolMessage 时立即停止扫描 → None。"""
        context = ConversationContext()
        context.append_message(
            ToolMessage(
                content="旧回复",
                tool_name="handoff_to_agent",
                metadata={"handoff_target": "worker"},
            )
        )
        context.add_assistant_message("最终回复")
        assert detect_handoff(context) is None

    def test_hit_in_non_tail_position_among_multiple(self) -> None:
        """同一组连续 ToolMessage 中 handoff 出现在非末尾位置也命中。"""
        context = ConversationContext()
        context.add_assistant_message("触发工具")
        context.append_message(
            ToolMessage(
                content="转移回复",
                tool_name="handoff_to_agent",
                metadata={"handoff_target": "worker"},
            )
        )
        context.append_message(
            ToolMessage(content="并发工具结果", tool_name="calc", metadata={})
        )
        assert detect_handoff(context) == ("worker", "转移回复")


class TestOutcomeToAgentResult:
    """``outcome_to_agent_result`` 各 kind 翻译分支覆盖（Property 1、5）。"""

    def test_handoff_takes_parent_model(self) -> None:
        """handoff 分支：content 取 handoff_content、model 取父模型（疑点 2 锁定）。"""
        response = LLMResponse(content="父模型文本", model="parent-model")
        outcome = RoundOutcome(
            kind="handoff",
            round_num=2,
            response=response,
            total_usage={"total_tokens": 7},
            handoff_target="worker",
            handoff_content="目标 Agent 最终回复",
        )
        result = outcome_to_agent_result(outcome)
        assert result.content == "目标 Agent 最终回复"
        # ADR-0010 疑点 2：model 取 outcome.response.model（父模型），本片不修正
        assert result.model == outcome.response.model == "parent-model"
        assert result.latency_ms == 0.0
        assert result.terminated_reason == "completed"

    def test_text_passes_through_terminated_reason(self) -> None:
        """text 分支：content 取 response.content，透传 terminated_reason。"""
        response = LLMResponse(content="纯文本回复", model="m", latency_ms=12.0)
        outcome = RoundOutcome(
            kind="text",
            round_num=1,
            response=response,
            total_usage={"total_tokens": 3},
        )
        result = outcome_to_agent_result(outcome)
        assert result.content == "纯文本回复"
        assert result.model == "m"
        assert result.latency_ms == 12.0
        assert result.terminated_reason == "completed"

    def test_final_passes_through_max_rounds(self) -> None:
        """final 分支：透传 terminated_reason="max_rounds"。"""
        response = LLMResponse(content="", model="m")
        outcome = RoundOutcome(
            kind="final",
            round_num=3,
            response=response,
            total_usage={},
            terminated_reason="max_rounds",
        )
        result = outcome_to_agent_result(outcome)
        assert result.terminated_reason == "max_rounds"

    def test_final_passes_through_token_budget_exceeded(self) -> None:
        """final 分支：透传 terminated_reason="token_budget_exceeded"。"""
        response = LLMResponse(content="", model="m")
        outcome = RoundOutcome(
            kind="final",
            round_num=2,
            response=response,
            total_usage={},
            terminated_reason="token_budget_exceeded",
        )
        result = outcome_to_agent_result(outcome)
        assert result.terminated_reason == "token_budget_exceeded"

    def test_approval_branch(self) -> None:
        """approval 分支：空 content + status="approval_required" + 携 approval。"""
        response = LLMResponse(content="将调用工具", model="m", latency_ms=5.0)
        approval = ApprovalRequiredPayload(
            session_id="s1",
            approval_id="a1",
            actions=(),
            prompt_id="chat-default@v1",
        )
        outcome = RoundOutcome(
            kind="approval",
            round_num=1,
            response=response,
            total_usage={"total_tokens": 4},
            approval=approval,
        )
        result = outcome_to_agent_result(outcome)
        assert result.content == ""
        assert result.status == "approval_required"
        assert result.approval is approval
        assert result.terminated_reason == "completed"
        assert result.latency_ms == 5.0


class TestRoundOutcomeDefaults:
    """``RoundOutcome`` 字段默认值与不可变性（Property 2，仅补既有测试未覆盖字段）。"""

    def test_handoff_and_tool_calls_defaults(self) -> None:
        """``handoff_target`` / ``handoff_content`` / ``tool_calls`` 默认值。"""
        response = LLMResponse(content="ok", model="m")
        outcome = RoundOutcome(
            kind="text",
            round_num=1,
            response=response,
            total_usage={},
        )
        assert outcome.handoff_target is None
        assert outcome.handoff_content == ""
        assert outcome.tool_calls == ()
        assert outcome.approval is None
        assert outcome.assistant_message_index is None

    def test_frozen_immutable(self) -> None:
        """``RoundOutcome`` 为 frozen dataclass，字段不可修改。"""
        response = LLMResponse(content="ok", model="m")
        outcome = RoundOutcome(
            kind="text",
            round_num=1,
            response=response,
            total_usage={},
        )
        with pytest.raises(FrozenInstanceError):
            outcome.handoff_content = "changed"  # type: ignore[misc]
