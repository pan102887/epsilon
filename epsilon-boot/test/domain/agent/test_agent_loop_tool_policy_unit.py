"""单元测试：agent_loop_policy 工具执行策略纯函数（Wave 1）。

仅 import domain 层，覆盖 interpret_tool_guardrail_decision /
classify_tool_execution / collect_pending_actions 全分支。
"""

from __future__ import annotations

from domain.agent.agent_loop_policy import (
    ToolExecutionClassification,
    ToolGuardrailBranch,
    classify_tool_execution,
    collect_pending_actions,
    interpret_tool_guardrail_decision,
)
from domain.agent.exceptions import HandoffPerformed, ToolPermissionDeniedError
from domain.agent.guardrails import GuardrailAction, GuardrailDecision
from domain.agent.value_objects import ApprovalPolicy, PendingActionRequest
from domain.model_access.value_objects import ToolCallRequest

# ─────────────────────────────────────────────────────────────────────────────
# interpret_tool_guardrail_decision
# ─────────────────────────────────────────────────────────────────────────────


class TestInterpretToolGuardrailDecision:
    """覆盖四条分支路径。"""

    def test_none_returns_proceed(self) -> None:
        """decision=None → 'proceed'。"""
        result: ToolGuardrailBranch = interpret_tool_guardrail_decision(None)
        assert result == "proceed"

    def test_require_approval(self) -> None:
        """action=REQUIRE_APPROVAL → 'require_approval'。"""
        decision = GuardrailDecision(action=GuardrailAction.REQUIRE_APPROVAL)
        result = interpret_tool_guardrail_decision(decision)
        assert result == "require_approval"

    def test_stop(self) -> None:
        """action=STOP → 'stop'。"""
        decision = GuardrailDecision(action=GuardrailAction.STOP)
        result = interpret_tool_guardrail_decision(decision)
        assert result == "stop"

    def test_allow_returns_proceed(self) -> None:
        """action=ALLOW → 'proceed'。"""
        decision = GuardrailDecision(action=GuardrailAction.ALLOW)
        result = interpret_tool_guardrail_decision(decision)
        assert result == "proceed"

    def test_observe_returns_proceed(self) -> None:
        """action=OBSERVE → 'proceed'。"""
        decision = GuardrailDecision(action=GuardrailAction.OBSERVE)
        result = interpret_tool_guardrail_decision(decision)
        assert result == "proceed"


# ─────────────────────────────────────────────────────────────────────────────
# classify_tool_execution
# ─────────────────────────────────────────────────────────────────────────────


class TestClassifyToolExecution:
    """覆盖四条异常分支。"""

    def test_handoff_signal(self) -> None:
        """HandoffPerformed → 非错误，携带 target_agent 和 content。"""
        signal = HandoffPerformed(target_agent="sub-agent", content="done")
        result = classify_tool_execution(
            signal,
            handoff_signal=signal,
            timeout=30.0,
        )
        assert result == ToolExecutionClassification(
            is_error=False,
            handoff_target="sub-agent",
            content="done",
            error_class=None,
        )

    def test_tool_permission_denied(self) -> None:
        """ToolPermissionDeniedError → is_error + error_class。"""
        exc = ToolPermissionDeniedError(
            tool_name="dangerous_tool",
            allowed_tools=frozenset({"safe_tool"}),
        )
        result = classify_tool_execution(
            exc,
            handoff_signal=None,
            timeout=30.0,
        )
        assert result.is_error is True
        assert result.handoff_target is None
        assert result.error_class == "ToolPermissionDeniedError"
        assert "dangerous_tool" in result.content

    def test_timeout_error(self) -> None:
        """TimeoutError → is_error + 超时内容格式化。"""
        exc = TimeoutError("timed out")
        result = classify_tool_execution(
            exc,
            handoff_signal=None,
            timeout=60.0,
        )
        assert result.is_error is True
        assert result.handoff_target is None
        assert result.error_class == "TimeoutError"
        assert result.content == "工具执行超时（60.0s)"

    def test_timeout_error_with_none_timeout(self) -> None:
        """TimeoutError + timeout=None → content 中显示 None。"""
        exc = TimeoutError("timed out")
        result = classify_tool_execution(
            exc,
            handoff_signal=None,
            timeout=None,
        )
        assert result.is_error is True
        assert result.error_class == "TimeoutError"
        assert result.content == "工具执行超时（Nones)"

    def test_generic_exception(self) -> None:
        """其它 Exception → is_error + 类名。"""
        exc = RuntimeError("something broke")
        result = classify_tool_execution(
            exc,
            handoff_signal=None,
            timeout=30.0,
        )
        assert result.is_error is True
        assert result.handoff_target is None
        assert result.error_class == "RuntimeError"
        assert result.content == "something broke"

    def test_value_error(self) -> None:
        """ValueError → 类名为 ValueError。"""
        exc = ValueError("bad value")
        result = classify_tool_execution(
            exc,
            handoff_signal=None,
            timeout=10.0,
        )
        assert result.is_error is True
        assert result.error_class == "ValueError"
        assert result.content == "bad value"


# ─────────────────────────────────────────────────────────────────────────────
# collect_pending_actions
# ─────────────────────────────────────────────────────────────────────────────


def _make_tool_call(tool_call_id: str, name: str, arguments: str = "{}") -> ToolCallRequest:
    """辅助构造 ToolCallRequest。"""
    return ToolCallRequest(id=tool_call_id, name=name, arguments=arguments)


class TestCollectPendingActions:
    """覆盖全分支。"""

    def test_empty_tool_calls(self) -> None:
        """空 tool_calls → 空元组。"""
        result = collect_pending_actions(
            tool_calls=[],
            allowed_tool_names=frozenset({"a"}),
            policies={},
        )
        assert result == ()

    def test_tool_not_in_allowed(self) -> None:
        """工具不在 allowed_tool_names 中 → 跳过。"""
        tc = _make_tool_call("id1", "forbidden_tool", '{"x":1}')
        result = collect_pending_actions(
            tool_calls=[tc],
            allowed_tool_names=frozenset({"safe_tool"}),
            policies={
                "forbidden_tool": ApprovalPolicy(
                    tool_name="forbidden_tool",
                    interrupt=True,
                    allowed_decisions=frozenset({"approve"}),
                ),
            },
        )
        assert result == ()

    def test_tool_allowed_but_no_policy(self) -> None:
        """工具在 allowed 中但无审批策略 → 不收集。"""
        tc = _make_tool_call("id1", "safe_tool", '{"x":1}')
        result = collect_pending_actions(
            tool_calls=[tc],
            allowed_tool_names=frozenset({"safe_tool"}),
            policies={},
        )
        assert result == ()

    def test_tool_allowed_policy_no_interrupt(self) -> None:
        """工具有策略但 interrupt=False → 不收集。"""
        tc = _make_tool_call("id1", "safe_tool", '{"x":1}')
        policy = ApprovalPolicy(
            tool_name="safe_tool",
            interrupt=False,
            allowed_decisions=frozenset({"approve"}),
        )
        result = collect_pending_actions(
            tool_calls=[tc],
            allowed_tool_names=frozenset({"safe_tool"}),
            policies={"safe_tool": policy},
        )
        assert result == ()

    def test_tool_allowed_policy_interrupt(self) -> None:
        """工具有策略且 interrupt=True → 收集为 PendingActionRequest。"""
        tc = _make_tool_call("id1", "risky_tool", '{"cmd":"rm"}')
        policy = ApprovalPolicy(
            tool_name="risky_tool",
            interrupt=True,
            allowed_decisions=frozenset({"approve", "reject"}),
            risk_label="高危操作",
        )
        result = collect_pending_actions(
            tool_calls=[tc],
            allowed_tool_names=frozenset({"risky_tool"}),
            policies={"risky_tool": policy},
        )
        assert len(result) == 1
        assert result[0] == PendingActionRequest(
            tool_call_id="id1",
            tool_name="risky_tool",
            arguments='{"cmd":"rm"}',
            allowed_decisions=frozenset({"approve", "reject"}),
            reason="高危操作",
        )

    def test_multiple_tools_order_preserved(self) -> None:
        """多个工具按原始顺序收集。"""
        tc1 = _make_tool_call("id1", "tool_a", '{"a":1}')
        tc2 = _make_tool_call("id2", "tool_b", '{"b":2}')
        tc3 = _make_tool_call("id3", "tool_c", '{"c":3}')
        policy_a = ApprovalPolicy(
            tool_name="tool_a",
            interrupt=True,
            allowed_decisions=frozenset({"approve"}),
            risk_label="A",
        )
        policy_b = ApprovalPolicy(
            tool_name="tool_b",
            interrupt=False,
            allowed_decisions=frozenset({"approve"}),
        )
        policy_c = ApprovalPolicy(
            tool_name="tool_c",
            interrupt=True,
            allowed_decisions=frozenset({"approve", "edit"}),
            risk_label="C",
        )
        result = collect_pending_actions(
            tool_calls=[tc1, tc2, tc3],
            allowed_tool_names=frozenset({"tool_a", "tool_b", "tool_c"}),
            policies={"tool_a": policy_a, "tool_b": policy_b, "tool_c": policy_c},
        )
        assert len(result) == 2
        assert result[0].tool_name == "tool_a"
        assert result[1].tool_name == "tool_c"

    def test_mixed_allowed_and_not_allowed(self) -> None:
        """混合场景：部分工具不在 allowed 中。"""
        tc1 = _make_tool_call("id1", "allowed_tool", '{"x":1}')
        tc2 = _make_tool_call("id2", "not_allowed", '{"y":2}')
        policy = ApprovalPolicy(
            tool_name="allowed_tool",
            interrupt=True,
            allowed_decisions=frozenset({"approve", "reject"}),
            risk_label="需审批",
        )
        result = collect_pending_actions(
            tool_calls=[tc1, tc2],
            allowed_tool_names=frozenset({"allowed_tool"}),
            policies={"allowed_tool": policy, "not_allowed": policy},
        )
        assert len(result) == 1
        assert result[0].tool_name == "allowed_tool"
