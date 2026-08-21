"""ReActAgentAdapter 审批恢复（HITL resume）矩阵特征化测试模块。

本模块锁定的对外可观测行为面为「(c) 审批中断/恢复语义」，性质为
characterization（回归基线）——只照 ``ReActAgentAdapter`` 当前实际行为写断言，
异常类型/参数/触发时机以当前生产代码为准，与直觉不符只登记不修复。补齐三处缺口：

- G2 ``test_resume_edit_executes_with_edited_arguments``：``resume`` 携带
  ``edit`` 决策续跑，编辑后的参数被采纳并执行，最终 ``status == "completed"``。
- G3 ``test_resume_decision_count_mismatch_raises``：空决策 vs 1 个待审批动作，
  抛 ``ApprovalDecisionCountMismatchError``（code 60023）。
- G3 ``test_resume_decision_order_mismatch_raises``：决策 ``tool_call_id`` 与
  待审批动作不对齐，抛 ``ApprovalDecisionOrderMismatchError``（code 60024）。
- G3 ``test_resume_policy_reapproval_returns_approval_required``：恢复后下一轮
  再次命中 ``ApprovalPolicy.interrupt=True`` 的工具调用，``resume`` 返回
  ``status == "approval_required"`` 且新 ``approval_id`` 不同于原批次。

harness 直接复用 ``test_react_agent_hitl_unit`` 中已建立的 ``FakeContextBuilder``
/ ``StaticPolicy`` / ``MemoryApprovalStore`` / ``RecordingTool`` / ``FakeModel``
同构替身与 ``_adapter`` / ``_config`` 构造器，不重建等价替身以免与既有断言语义分歧。
``ApprovalDecisionNotAllowedError``（60025）已由既有 ``test_react_agent_hitl_unit``
锁定，本文件不重复添加。
"""

from __future__ import annotations

import pytest

from domain.agent.exceptions import (
    ApprovalDecisionCountMismatchError,
    ApprovalDecisionOrderMismatchError,
)
from domain.agent.value_objects import (
    ApprovalDecision,
    ApprovalInterrupt,
    EditedAction,
    PendingActionRequest,
)
from domain.chat.context import AssistantMessage, ConversationContext
from domain.model_access.value_objects import LLMResponse, ToolCallRequest
from test.infrastructure.agent.test_react_agent_hitl_unit import (
    FakeModel,
    MemoryApprovalStore,
    RecordingTool,
    hitl_adapter,
    hitl_config,
)


def _seed_context_with_pending_tool_call() -> ConversationContext:
    """构造含"待审批 ``write_file`` 工具调用"的最小上下文。

    与既有 ``test_react_agent_hitl_unit`` 的 approve/reject 用例同构：注入
    system + user 消息，并追加一条携带 ``call-1`` tool_calls 的 AssistantMessage，
    供 ``_apply_approval_decisions`` 的 ``_latest_tool_calls_by_id`` 查找原始调用。
    """
    context = ConversationContext()
    context.add_system_message("system")
    context.add_user_message("write")
    context.replace_messages(
        [
            *context.get_messages(),
            AssistantMessage(
                content="",
                tool_calls=[ToolCallRequest("call-1", "write_file", '{"path":"a.txt"}')],
            ),
        ]
    )
    return context


def _pending_action() -> PendingActionRequest:
    """构造单条 ``write_file`` 待审批动作（allowed=approve/edit/reject）。"""
    return PendingActionRequest(
        "call-1",
        "write_file",
        '{"path":"a.txt"}',
        frozenset({"approve", "edit", "reject"}),
    )


def _interrupt(context: ConversationContext) -> ApprovalInterrupt:
    """基于给定上下文构造 round_num=1 的审批中断状态。"""
    return ApprovalInterrupt(
        session_id="s1",
        approval_id="a1",
        actions=(_pending_action(),),
        context_snapshot=context.to_dict(),
        round_num=1,
        model="gpt-test",
        usage_so_far={"total_tokens": 2},
    )


async def test_resume_edit_executes_with_edited_arguments() -> None:
    """锁定 edit 决策续跑：编辑后参数被采纳，最终 completed。

    构造 round_num=1 的审批中断，携带 ``ApprovalDecision("edit", "call-1",
    edited_action=EditedAction("write_file", '{"path":"edited.txt"}'))`` 调用
    ``resume``。据 ``_apply_approval_decisions`` edit 分支实际行为，编辑后参数
    经 ``cast_params``/``validate_params`` 校验通过后执行，故
    ``RecordingTool.requests == [{"path": "edited.txt"}]``；恢复后下一轮模型
    返回纯文本 ``"done"``，``result.status == "completed"``、
    ``result.content == "done"``。
    """
    store = MemoryApprovalStore()
    tool = RecordingTool()
    adapter = hitl_adapter(store, tool)
    context = _seed_context_with_pending_tool_call()
    interrupt = _interrupt(context)
    model = FakeModel([LLMResponse(content="done", model="gpt-test", usage={"total_tokens": 3})])

    result = await adapter.resume(
        context,
        hitl_config(),
        model,  # type: ignore[arg-type]
        interrupt,
        (
            ApprovalDecision(
                "edit",
                "call-1",
                edited_action=EditedAction("write_file", '{"path":"edited.txt"}'),
            ),
        ),
    )

    assert tool.requests == [{"path": "edited.txt"}]
    assert result.status == "completed"
    assert result.content == "done"


async def test_resume_decision_count_mismatch_raises() -> None:
    """锁定决策数量不匹配：空决策 vs 1 动作 → ApprovalDecisionCountMismatchError。

    ``interrupt.actions`` 有 1 项而 ``resume`` 传入空决策序列，据
    ``_apply_approval_decisions`` 前置校验抛
    ``ApprovalDecisionCountMismatchError``（code 60023），且构造参数
    ``expected_count == 1``、``actual_count == 0``；工具不应被执行。
    """
    store = MemoryApprovalStore()
    tool = RecordingTool()
    adapter = hitl_adapter(store, tool)
    context = _seed_context_with_pending_tool_call()
    interrupt = _interrupt(context)
    model = FakeModel([LLMResponse(content="done", model="gpt-test")])

    with pytest.raises(ApprovalDecisionCountMismatchError) as exc_info:
        await adapter.resume(
            context,
            hitl_config(),
            model,  # type: ignore[arg-type]
            interrupt,
            (),
        )

    assert exc_info.value.code == 60023
    assert exc_info.value.expected_count == 1
    assert exc_info.value.actual_count == 0
    assert tool.requests == []


async def test_resume_decision_order_mismatch_raises() -> None:
    """锁定决策顺序不匹配：tool_call_id 不对齐 → ApprovalDecisionOrderMismatchError。

    决策 ``tool_call_id`` 为 ``"call-2"`` 而待审批动作为 ``"call-1"``，据
    ``_apply_approval_decisions`` 校验抛 ``ApprovalDecisionOrderMismatchError``
    （code 60024），且 ``expected_tool_call_id == "call-1"``、
    ``actual_tool_call_id == "call-2"``；工具不应被执行。
    """
    store = MemoryApprovalStore()
    tool = RecordingTool()
    adapter = hitl_adapter(store, tool)
    context = _seed_context_with_pending_tool_call()
    interrupt = _interrupt(context)
    model = FakeModel([LLMResponse(content="done", model="gpt-test")])

    with pytest.raises(ApprovalDecisionOrderMismatchError) as exc_info:
        await adapter.resume(
            context,
            hitl_config(),
            model,  # type: ignore[arg-type]
            interrupt,
            (ApprovalDecision("approve", "call-2"),),
        )

    assert exc_info.value.code == 60024
    assert exc_info.value.expected_tool_call_id == "call-1"
    assert exc_info.value.actual_tool_call_id == "call-2"
    assert tool.requests == []


async def test_resume_policy_reapproval_returns_approval_required() -> None:
    """锁定策略型恢复后再次审批：resume 再次 approval_required 且 approval_id 更新。

    approve 首个待审批动作后，恢复的下一轮模型再次返回命中
    ``ApprovalPolicy.interrupt=True`` 的 ``write_file`` 工具调用，据 ``resume``
    经 ``_iter_rounds`` 的 approval 短路，``result.status == "approval_required"``、
    ``result.approval is not None``，且新 ``approval_id`` 不同于原中断的
    ``"a1"``（每次 ``_save_interrupt`` 生成新的 uuid）。区别于既有
    ``test_react_agent_guardrail_runtime`` 已覆盖的 guardrail 型再审批。
    """
    store = MemoryApprovalStore()
    tool = RecordingTool()
    adapter = hitl_adapter(store, tool)
    context = _seed_context_with_pending_tool_call()
    interrupt = _interrupt(context)
    model = FakeModel(
        [
            LLMResponse(
                content="",
                model="gpt-test",
                usage={"total_tokens": 3},
                tool_calls=[ToolCallRequest("call-2", "write_file", '{"path":"b.txt"}')],
            )
        ]
    )

    result = await adapter.resume(
        context,
        hitl_config(),
        model,  # type: ignore[arg-type]
        interrupt,
        (ApprovalDecision("approve", "call-1"),),
    )

    assert result.status == "approval_required"
    assert result.approval is not None
    assert result.approval.approval_id != "a1"
