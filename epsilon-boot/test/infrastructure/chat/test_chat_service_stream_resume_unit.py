"""ChatServiceAdapter.stream_resume_approval 流式审批恢复测试模块。

覆盖流式恢复通路（Slice A）：自然完成产出 assistant_delta/assistant_done，
恢复后再次中断产出 approval_required，与 resume_approval 共用内核（Property5），
以及 tool_call_id 不匹配 / 数量不匹配 / 重复恢复的错误传播（Property1）。
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.exceptions import (
    ApprovalConsumedError,
    ApprovalDecisionCountMismatchError,
    ApprovalDecisionOrderMismatchError,
)
from domain.agent.value_objects import (
    AgentResult,
    ApprovalDecision,
    ApprovalInterrupt,
    ApprovalRequiredPayload,
    PendingActionRequest,
)
from domain.chat.context import ConversationContext, UserMessage
from domain.chat.value_objects import ApprovalResumeRequestVO, ContextBuilderResult
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies


class MemoryApprovalStore:
    """测试用审批状态存储，consume 后置空以模拟原子消费。"""

    def __init__(self, interrupt: ApprovalInterrupt | None = None) -> None:
        self.interrupt = interrupt
        self._consumed = False
        self.consume_calls: list[tuple[str, str]] = []

    async def load(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        return self.interrupt

    async def consume(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        self.consume_calls.append((session_id, approval_id))
        if self._consumed:
            return None
        self._consumed = True
        return self.interrupt

    async def delete(self, session_id: str, approval_id: str) -> None:
        self.interrupt = None

    async def delete_session(self, session_id: str) -> None:
        self.interrupt = None

    async def list_pending_by_session(self, session_id: str) -> list:
        return []


def _action(tool_call_id: str = "call-1") -> PendingActionRequest:
    """构造单个待审批动作（write_file，允许 approve/reject）。"""
    return PendingActionRequest(
        tool_call_id,
        "write_file",
        "{}",
        frozenset({"approve", "reject"}),
    )


def _interrupt(action: PendingActionRequest) -> ApprovalInterrupt:
    """构造包含单个动作的审批中断状态。"""
    context = ConversationContext()
    context.add_user_message("hello")
    return ApprovalInterrupt("s1", "a1", (action,), context.to_dict(), 1, "gpt-test")


def _adapter(agent: MagicMock, approval_store: MemoryApprovalStore) -> ChatServiceAdapter:
    """构造仅装配审批依赖的 ChatServiceAdapter（其余端口以 MagicMock 桩替代）。"""
    session_store = MagicMock()
    session_store.load = AsyncMock(return_value=ConversationContext())
    session_store.save = AsyncMock()
    session_store.delete = AsyncMock()
    model_registry = MagicMock()
    model_registry.get_default_model.return_value = "gpt-test"
    model_registry.get_adapter_for_model.return_value = MagicMock()
    prompt_registry = MagicMock(
        get=MagicMock(
            return_value=LoadedPrompt(
                prompt_id="chat-default@v1",
                name="chat-default",
                version="v1",
                content="system",
            )
        )
    )
    loaded_prompt = prompt_registry.get.return_value
    tool_schemas = [{"type": "function", "function": {"name": "write_file"}}]
    return ChatServiceAdapter(
        session_store=session_store,
        model_registry=model_registry,
        prompt_registry=prompt_registry,
        context_builder=MagicMock(
            build=AsyncMock(
                return_value=ContextBuilderResult(
                    messages=[UserMessage(content="builder message")],
                    environment_injected=True,
                )
            )
        ),
        agent=agent,
        tool_calling_enabled=True,
        max_tool_rounds=3,
        tool_schemas=tool_schemas,
        approval_store=approval_store,  # type: ignore[arg-type]
        **make_chat_adapter_dependencies(
            session_store=session_store,
            model_registry=model_registry,
            loaded_prompt=loaded_prompt,
            agent=agent,
            tool_schemas=tool_schemas,
            max_tool_rounds=3,
            approval_store=approval_store,  # type: ignore[arg-type]
        ),
    )


async def _collect(adapter: ChatServiceAdapter, request: ApprovalResumeRequestVO) -> list:
    """消费 stream_resume_approval 产出的全部事件为列表。"""
    return [event async for event in adapter.stream_resume_approval(request)]


async def test_stream_resume_completed_emits_delta_then_done() -> None:
    """(a) 自然完成时依次产出 assistant_delta 与 assistant_done。"""
    agent = MagicMock()
    agent.resume = AsyncMock(
        return_value=AgentResult(content="done", model="gpt-test", usage={"total_tokens": 7})
    )
    adapter = _adapter(agent, MemoryApprovalStore(_interrupt(_action())))

    events = await _collect(
        adapter,
        ApprovalResumeRequestVO("s1", "a1", (ApprovalDecision("approve", "call-1"),)),
    )

    assert [event.kind for event in events] == ["assistant_delta", "assistant_done"]
    assert events[0].content == "done"
    assert events[1].usage == {"total_tokens": 7}
    assert events[1].metadata["terminated_reason"] == "completed"


async def test_stream_resume_reinterrupt_emits_approval_required() -> None:
    """(b) 恢复后再次中断产出 approval_required，metadata 含新的批次信息。"""
    new_action = _action("call-2")
    agent = MagicMock()
    agent.resume = AsyncMock(
        return_value=AgentResult(
            content="",
            model="gpt-test",
            status="approval_required",
            approval=ApprovalRequiredPayload("s1", "a2", (new_action,), "chat-default@v1"),
        )
    )
    adapter = _adapter(agent, MemoryApprovalStore(_interrupt(_action())))

    events = await _collect(
        adapter,
        ApprovalResumeRequestVO("s1", "a1", (ApprovalDecision("approve", "call-1"),)),
    )

    assert [event.kind for event in events] == ["approval_required"]
    metadata = events[0].metadata
    assert metadata["session_id"] == "s1"
    assert metadata["approval_id"] == "a2"
    assert metadata["action_summaries"][0]["tool_call_id"] == "call-2"
    assert metadata["action_summaries"][0]["tool_name"] == "write_file"


async def test_stream_resume_reuses_kernel_calls_resume_once() -> None:
    """(c) 流式恢复与 resume_approval 共用内核，agent.resume 只被调用一次（Property5）。"""
    agent = MagicMock()
    agent.resume = AsyncMock(return_value=AgentResult(content="done", model="gpt-test"))
    adapter = _adapter(agent, MemoryApprovalStore(_interrupt(_action())))

    await _collect(
        adapter,
        ApprovalResumeRequestVO("s1", "a1", (ApprovalDecision("approve", "call-1"),)),
    )

    agent.resume.assert_awaited_once()


async def test_stream_resume_order_mismatch_raises_and_not_executed() -> None:
    """tool_call_id 不匹配抛 ApprovalDecisionOrderMismatchError(60024)，不 approve 静默执行。"""
    agent = MagicMock()
    agent.resume = AsyncMock(return_value=AgentResult(content="done", model="gpt-test"))
    adapter = _adapter(agent, MemoryApprovalStore(_interrupt(_action("call-1"))))

    with pytest.raises(ApprovalDecisionOrderMismatchError) as exc_info:
        await _collect(
            adapter,
            ApprovalResumeRequestVO("s1", "a1", (ApprovalDecision("approve", "call-wrong"),)),
        )

    assert exc_info.value.code == 60024
    agent.resume.assert_not_awaited()


async def test_stream_resume_count_mismatch_raises() -> None:
    """决策数量与动作数不一致抛 ApprovalDecisionCountMismatchError(60023)。"""
    agent = MagicMock()
    agent.resume = AsyncMock(return_value=AgentResult(content="done", model="gpt-test"))
    adapter = _adapter(agent, MemoryApprovalStore(_interrupt(_action("call-1"))))

    with pytest.raises(ApprovalDecisionCountMismatchError) as exc_info:
        await _collect(
            adapter,
            ApprovalResumeRequestVO(
                "s1",
                "a1",
                (
                    ApprovalDecision("approve", "call-1"),
                    ApprovalDecision("approve", "call-2"),
                ),
            ),
        )

    assert exc_info.value.code == 60023
    agent.resume.assert_not_awaited()


async def test_stream_resume_repeated_consume_raises_consumed() -> None:
    """重复恢复（第二次 consume 返回 None）抛 ApprovalConsumedError(60022)。"""
    agent = MagicMock()
    agent.resume = AsyncMock(return_value=AgentResult(content="done", model="gpt-test"))
    store = MemoryApprovalStore(_interrupt(_action("call-1")))
    adapter = _adapter(agent, store)

    request = ApprovalResumeRequestVO("s1", "a1", (ApprovalDecision("approve", "call-1"),))
    await _collect(adapter, request)

    with pytest.raises(ApprovalConsumedError) as exc_info:
        await _collect(adapter, request)

    assert exc_info.value.code == 60022
