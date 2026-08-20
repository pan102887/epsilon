"""AgentLoopOrchestrator 领域侧单元测试模块。

使用 fake ``AgentLoopEffects`` 驱动，仅 import domain.*，覆盖：
- text 纯文本终止
- tool_calls 协作（多轮工具→文本）
- approval 审批中断
- handoff 短路
- token_budget 跨轮超限
- max_rounds 耗尽
- Terminal_Round_Boundary_Assert
- last_response is None 边界
"""

from __future__ import annotations

from collections.abc import Mapping

import pytest

from domain.agent.agent_loop_orchestration import AgentLoopOrchestrator
from domain.agent.agent_loop_policy import RoundOutcome
from domain.agent.ports import ModelRoundResult
from domain.agent.value_objects import (
    AgentConfig,
    ApprovalPolicy,
    ApprovalRequiredPayload,
    PendingActionRequest,
)
from domain.chat.context import ConversationContext, ToolMessage
from domain.model_access.value_objects import LLMResponse, ToolCallRequest

# ──────────────────────────────────────────────────────────────────────────────
# Fake implementations
# ──────────────────────────────────────────────────────────────────────────────


def _config(
    *,
    max_rounds: int = 10,
    max_total_tokens: int | None = None,
    tool_schemas: list[dict[str, object]] | None = None,
) -> AgentConfig:
    """构造最小 AgentConfig。"""
    schemas = tool_schemas or [
        {"type": "function", "function": {"name": "tool_a", "parameters": {}}}
    ]
    return AgentConfig(
        system_prompt="test",
        tool_schemas=schemas,
        model="gpt-test",
        max_rounds=max_rounds,
        prompt_id="test-prompt@v1",
        max_total_tokens=max_total_tokens,
    )


def _text_response(content: str = "done", usage: dict[str, int] | None = None) -> LLMResponse:
    """构造纯文本 LLMResponse。"""
    return LLMResponse(
        content=content,
        model="gpt-test",
        usage=usage or {"total_tokens": 10},
        tool_calls=[],
    )


def _tool_response(
    tool_calls: list[ToolCallRequest] | None = None,
    usage: dict[str, int] | None = None,
) -> LLMResponse:
    """构造含 tool_calls 的 LLMResponse。"""
    calls = tool_calls or [ToolCallRequest(id="tc-1", name="tool_a", arguments="{}")]
    return LLMResponse(
        content="",
        model="gpt-test",
        usage=usage or {"total_tokens": 10},
        tool_calls=calls,
    )


def _context() -> ConversationContext:
    """构造最小 ConversationContext。"""
    ctx = ConversationContext()
    ctx.session_id = "s1"
    ctx.add_user_message("hello")
    return ctx


class FakeEffects:
    """Fake AgentLoopEffects 实现。

    通过 ``responses`` 列表控制 ``perform_model_round`` 的返回值序列。
    """

    def __init__(
        self,
        responses: list[LLMResponse],
        *,
        pending_actions: tuple[PendingActionRequest, ...] = (),
        guardrail_approval: ApprovalRequiredPayload | None = None,
    ) -> None:
        self._responses = list(responses)
        self._response_index = 0
        self._pending_actions = pending_actions
        self._guardrail_approval = guardrail_approval
        self.prepared = False
        self.terminated_calls: list[dict[str, object]] = []
        self.checkpoint_model_calls: list[int] = []
        self.checkpoint_approval_calls: list[str] = []

    async def prepare_runtime(
        self,
        context: object,
        config: object,
        *,
        preserve_guardrail_runtime: bool,
    ) -> None:
        self.prepared = True

    async def perform_model_round(
        self,
        context: object,
        config: object,
        model_access: object,
        *,
        round_num: int,
        total_usage: dict[str, int],
    ) -> ModelRoundResult:
        response = self._responses[self._response_index]
        self._response_index += 1
        # 简单模拟 merge_usage：累加 total_tokens
        merged = dict(total_usage)
        for k, v in (response.usage or {}).items():
            merged[k] = merged.get(k, 0) + v
        return ModelRoundResult(response=response, total_usage=merged)

    def record_assistant_with_tool_calls(
        self,
        context: object,
        response: LLMResponse,
    ) -> int:
        return 0

    def resolve_approval_policies(
        self,
        tool_calls: tuple[ToolCallRequest, ...],
        config: object,
    ) -> Mapping[str, ApprovalPolicy]:
        # 如果设置了 _pending_actions，返回一个能命中 interrupt 的 policy
        if self._pending_actions:
            return {
                tc.name: ApprovalPolicy(
                    tool_name=tc.name,
                    interrupt=True,
                    allowed_decisions=frozenset({"approve", "reject"}),
                )
                for tc in tool_calls
            }
        return {
            tc.name: ApprovalPolicy(
                tool_name=tc.name,
                interrupt=False,
                allowed_decisions=frozenset({"approve", "reject"}),
            )
            for tc in tool_calls
        }

    async def save_interrupt(
        self,
        context: object,
        config: object,
        actions: tuple[PendingActionRequest, ...],
        round_num: int,
        model: str,
        usage_so_far: dict[str, int],
    ) -> ApprovalRequiredPayload:
        return ApprovalRequiredPayload(
            session_id="s1",
            approval_id="approval-1",
            actions=actions,
            prompt_id="test-prompt@v1",
        )

    async def prepare_tool_calls_for_execution(
        self,
        context: object,
        config: object,
        tool_calls: tuple[ToolCallRequest, ...],
        round_num: int,
        model: str,
        usage_so_far: dict[str, int],
    ) -> tuple[tuple[ToolCallRequest, ...], ApprovalRequiredPayload | None]:
        return tool_calls, self._guardrail_approval

    async def checkpoint_model_completed(
        self,
        context: object,
        round_num: int,
        total_usage: dict[str, int],
        response: LLMResponse,
    ) -> None:
        self.checkpoint_model_calls.append(round_num)

    async def checkpoint_approval_interrupt(
        self,
        context: object,
        round_num: int,
        total_usage: dict[str, int],
        approval_id: str,
    ) -> None:
        self.checkpoint_approval_calls.append(approval_id)

    def record_terminated(
        self,
        reason: str,
        round_num: int,
        total_usage: dict[str, int],
        config: object,
        *,
        tool_call_count: int = 0,
        handoff_target: str = "",
    ) -> None:
        self.terminated_calls.append({
            "reason": reason,
            "round_num": round_num,
            "tool_call_count": tool_call_count,
            "handoff_target": handoff_target,
        })


# ──────────────────────────────────────────────────────────────────────────────
# Tests
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_text_response_terminates() -> None:
    """模型返回纯文本时，产出 kind='text' 后终止。"""
    effects = FakeEffects([_text_response("hello")])
    orchestrator = AgentLoopOrchestrator()

    outcomes: list[RoundOutcome] = []
    async for outcome in orchestrator.iter_rounds(
        _context(),
        _config(),
        None,  # type: ignore[arg-type]
        effects=effects,  # type: ignore[arg-type]
    ):
        outcomes.append(outcome)

    assert len(outcomes) == 1
    assert outcomes[0].kind == "text"
    assert outcomes[0].response.content == "hello"
    assert outcomes[0].round_num == 1
    assert effects.prepared


@pytest.mark.asyncio
async def test_tool_calls_then_text() -> None:
    """tool_calls 后调用方回写 ToolMessage，下一轮纯文本终止。"""
    effects = FakeEffects([_tool_response(), _text_response("final")])
    orchestrator = AgentLoopOrchestrator()
    ctx = _context()

    outcomes: list[RoundOutcome] = []
    async for outcome in orchestrator.iter_rounds(
        ctx,
        _config(),
        None,  # type: ignore[arg-type]
        effects=effects,  # type: ignore[arg-type]
    ):
        outcomes.append(outcome)
        if outcome.kind == "tool_calls":
            # 模拟调用方回写 ToolMessage
            ctx.add_tool_result("tool_a", "ok", "tc-1")

    assert len(outcomes) == 2
    assert outcomes[0].kind == "tool_calls"
    assert outcomes[1].kind == "text"
    assert outcomes[1].response.content == "final"


@pytest.mark.asyncio
async def test_approval_interrupt() -> None:
    """命中审批策略时产出 kind='approval' 后终止。"""
    pending = (
        PendingActionRequest(
            tool_call_id="tc-1",
            tool_name="tool_a",
            arguments="{}",
            allowed_decisions=frozenset({"approve", "reject"}),
        ),
    )
    effects = FakeEffects([_tool_response()], pending_actions=pending)
    orchestrator = AgentLoopOrchestrator()

    outcomes: list[RoundOutcome] = []
    async for outcome in orchestrator.iter_rounds(
        _context(),
        _config(),
        None,  # type: ignore[arg-type]
        effects=effects,  # type: ignore[arg-type]
    ):
        outcomes.append(outcome)

    assert len(outcomes) == 1
    assert outcomes[0].kind == "approval"
    assert outcomes[0].approval is not None
    assert effects.checkpoint_approval_calls == ["approval-1"]


@pytest.mark.asyncio
async def test_handoff_shortcircuit() -> None:
    """上一轮工具执行产生 handoff 标记时，下一轮入口检测到后短路。"""
    effects = FakeEffects([_tool_response(), _text_response("unused")])
    orchestrator = AgentLoopOrchestrator()
    ctx = _context()

    outcomes: list[RoundOutcome] = []
    async for outcome in orchestrator.iter_rounds(
        ctx,
        _config(),
        None,  # type: ignore[arg-type]
        effects=effects,  # type: ignore[arg-type]
    ):
        outcomes.append(outcome)
        if outcome.kind == "tool_calls":
            # 模拟 handoff 标记：通过 add_tool_result 后设置 metadata
            idx = ctx.add_tool_result("tool_a", "handoff reply", "tc-1")
            msg = ctx.get_messages()[idx]
            assert isinstance(msg, ToolMessage)
            msg.metadata["handoff_target"] = "agent_b"

    assert len(outcomes) == 2
    assert outcomes[0].kind == "tool_calls"
    assert outcomes[1].kind == "handoff"
    assert outcomes[1].handoff_target == "agent_b"
    assert outcomes[1].handoff_content == "handoff reply"
    assert effects.terminated_calls[0]["reason"] == "handoff"


@pytest.mark.asyncio
async def test_token_budget_exceeded_after_tools() -> None:
    """token 预算超限：tool_calls 轮标记后下一轮入口终止。"""
    # 第一轮用量已超 budget（设 max_total_tokens=5，response 返回 100）
    effects = FakeEffects([
        _tool_response(usage={"total_tokens": 100}),
        _text_response("never reached"),
    ])
    orchestrator = AgentLoopOrchestrator()
    ctx = _context()

    outcomes: list[RoundOutcome] = []
    async for outcome in orchestrator.iter_rounds(
        ctx,
        _config(max_total_tokens=5),
        None,  # type: ignore[arg-type]
        effects=effects,  # type: ignore[arg-type]
    ):
        outcomes.append(outcome)
        if outcome.kind == "tool_calls":
            ctx.add_tool_result("tool_a", "ok", "tc-1")

    assert len(outcomes) == 2
    assert outcomes[0].kind == "tool_calls"
    assert outcomes[1].kind == "final"
    assert outcomes[1].terminated_reason == "token_budget_exceeded"
    assert effects.terminated_calls[0]["reason"] == "token_budget_exceeded"


@pytest.mark.asyncio
async def test_max_rounds_exhausted() -> None:
    """max_rounds 耗尽：循环跑完所有轮次后产出 kind='final'。"""
    # 2 轮都返回 tool_calls
    effects = FakeEffects([_tool_response(), _tool_response()])
    orchestrator = AgentLoopOrchestrator()
    ctx = _context()

    outcomes: list[RoundOutcome] = []
    async for outcome in orchestrator.iter_rounds(
        ctx,
        _config(max_rounds=2),
        None,  # type: ignore[arg-type]
        effects=effects,  # type: ignore[arg-type]
    ):
        outcomes.append(outcome)
        if outcome.kind == "tool_calls":
            ctx.add_tool_result("tool_a", "ok", "tc-1")

    assert len(outcomes) == 3
    assert outcomes[0].kind == "tool_calls"
    assert outcomes[1].kind == "tool_calls"
    assert outcomes[2].kind == "final"
    assert outcomes[2].terminated_reason == "max_rounds"
    assert effects.terminated_calls[0]["reason"] == "max_rounds"


@pytest.mark.asyncio
async def test_terminal_round_boundary_assert() -> None:
    """Terminal_Round_Boundary_Assert：最后一轮 text 时 assert 不触发。

    仅当循环耗尽且上一轮非 tool_calls（或 caller 未回写 ToolMessage）
    时触发 assert。此测试验证正常 text 终止不会错误触发。
    """
    effects = FakeEffects([_text_response()])
    orchestrator = AgentLoopOrchestrator()

    outcomes: list[RoundOutcome] = []
    async for outcome in orchestrator.iter_rounds(
        _context(),
        _config(max_rounds=1),
        None,  # type: ignore[arg-type]
        effects=effects,  # type: ignore[arg-type]
    ):
        outcomes.append(outcome)

    assert len(outcomes) == 1
    assert outcomes[0].kind == "text"


@pytest.mark.asyncio
async def test_last_response_none_boundary() -> None:
    """last_response is None 边界：terminal_round=0 时不产出 outcome。"""
    effects = FakeEffects([])
    orchestrator = AgentLoopOrchestrator()

    outcomes: list[RoundOutcome] = []
    async for outcome in orchestrator.iter_rounds(
        _context(),
        _config(),
        None,  # type: ignore[arg-type]
        effects=effects,  # type: ignore[arg-type]
        terminal_round=0,
    ):
        outcomes.append(outcome)

    assert len(outcomes) == 0


@pytest.mark.asyncio
async def test_guardrail_approval_interrupt() -> None:
    """guardrail 前置评估触发审批时，产出 kind='approval' 后终止。"""
    guardrail_payload = ApprovalRequiredPayload(
        session_id="s1",
        approval_id="grd-1",
        actions=(
            PendingActionRequest(
                tool_call_id="tc-1",
                tool_name="tool_a",
                arguments="{}",
                allowed_decisions=frozenset({"approve", "reject"}),
            ),
        ),
        prompt_id="test-prompt@v1",
    )
    effects = FakeEffects([_tool_response()], guardrail_approval=guardrail_payload)
    orchestrator = AgentLoopOrchestrator()

    outcomes: list[RoundOutcome] = []
    async for outcome in orchestrator.iter_rounds(
        _context(),
        _config(),
        None,  # type: ignore[arg-type]
        effects=effects,  # type: ignore[arg-type]
    ):
        outcomes.append(outcome)

    assert len(outcomes) == 1
    assert outcomes[0].kind == "approval"
    assert outcomes[0].approval is not None
    assert outcomes[0].approval.approval_id == "grd-1"
