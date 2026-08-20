"""ChatServiceAdapter HITL 编排测试模块。"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from application.cli.approval_mode import evaluate_approval_mode
from domain.agent.value_objects import (
    AgentResult,
    ApprovalDecision,
    ApprovalInterrupt,
    ApprovalRequiredPayload,
    PendingActionRequest,
)
from domain.chat.context import ConversationContext, UserMessage
from domain.chat.value_objects import ApprovalResumeRequestVO, ChatRequestVO, ContextBuilderResult
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.agent.approval_policy_provider import StaticApprovalPolicyProvider
from infrastructure.chat.chat_service_adapter import ChatServiceAdapter
from test.infrastructure.chat.chat_adapter_test_utils import make_chat_adapter_dependencies


class MemoryApprovalStore:
    """测试用审批状态存储。"""

    def __init__(self, interrupt: ApprovalInterrupt | None = None) -> None:
        self.interrupt = interrupt
        self.deleted_sessions: list[str] = []

    async def save(self, interrupt: ApprovalInterrupt) -> None:
        self.interrupt = interrupt

    async def load(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        return self.interrupt

    async def consume(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        interrupt = self.interrupt
        self.interrupt = None
        return interrupt

    async def delete(self, session_id: str, approval_id: str) -> None:
        self.interrupt = None

    async def delete_session(self, session_id: str) -> None:
        self.deleted_sessions.append(session_id)

    async def list_pending_by_session(self, session_id: str) -> list:
        return []


def _action() -> PendingActionRequest:
    return PendingActionRequest(
        "call-1",
        "write_file",
        "{}",
        frozenset({"approve", "reject"}),
    )


def _adapter(
    agent: MagicMock, approval_store: MemoryApprovalStore
) -> tuple[ChatServiceAdapter, MagicMock]:
    session_store = MagicMock()
    session_store.load = AsyncMock(return_value=ConversationContext())
    session_store.save = AsyncMock()
    session_store.delete = AsyncMock()
    model_registry = MagicMock()
    model_registry.get_default_model.return_value = "gpt-test"
    model_registry.get_adapter_for_model.return_value = MagicMock()
    loaded_prompt = LoadedPrompt(
        prompt_id="chat-default@v1",
        name="chat-default",
        version="v1",
        content="system",
    )
    tool_schemas = [{"type": "function", "function": {"name": "write_file"}}]
    prompt_registry = MagicMock(
        get=MagicMock(
            return_value=loaded_prompt
        )
    )
    return (
        ChatServiceAdapter(
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
        ),
        session_store,
    )


async def test_chat_approval_required_does_not_save_session() -> None:
    """验证 chat 审批中断不保存普通 session。"""
    action = _action()
    agent = MagicMock()
    agent.run = AsyncMock(
        return_value=AgentResult(
            content="",
            model="gpt-test",
            status="approval_required",
            approval=ApprovalRequiredPayload("s1", "a1", (action,), "chat-default@v1"),
        )
    )
    adapter, session_store = _adapter(agent, MemoryApprovalStore())

    response = await adapter.chat(ChatRequestVO("s1", "hello"))

    assert response.status == "approval_required"
    assert response.approval_id == "a1"
    assert response.action_requests == (action,)
    session_store.save.assert_not_called()


async def test_resume_completed_saves_session() -> None:
    """验证 resume completed 后保存 session。"""
    context = ConversationContext()
    context.add_user_message("hello")
    interrupt = ApprovalInterrupt("s1", "a1", (_action(),), context.to_dict(), 1, "gpt-test")
    agent = MagicMock()
    agent.resume = AsyncMock(return_value=AgentResult(content="done", model="gpt-test"))
    adapter, session_store = _adapter(agent, MemoryApprovalStore(interrupt))

    response = await adapter.resume_approval(
        ApprovalResumeRequestVO(
            session_id="s1",
            approval_id="a1",
            decisions=(ApprovalDecision("approve", "call-1"),),
        )
    )

    assert response.status == "completed"
    assert response.reply == "done"
    session_store.save.assert_awaited_once()


async def test_clear_session_deletes_approval_state() -> None:
    """验证 clear_session 同时清理审批状态。"""
    agent = MagicMock()
    store = MemoryApprovalStore()
    adapter, _ = _adapter(agent, store)

    await adapter.clear_session("s1")

    assert store.deleted_sessions == ["s1"]


async def test_auto_mode_high_risk_still_interrupts_end_to_end() -> None:
    """验证 auto 模式下高风险动作仍强制中断，不被本地审批模式绕过（需求 6.5）。

    端到端串接后端真实 ``StaticApprovalPolicyProvider`` 与本地
    ``evaluate_approval_mode``：即使会话审批模式为 ``auto``，只要动作命中
    后端判定 ``interrupt=True`` 的高风险工具（write_file），
    ``evaluate_approval_mode`` 必须返回 ``None`` 以强制打开审批面板；同时
    确认 ChatServiceAdapter 对该高风险工具仍产出 ``approval_required``。
    """
    action = _action()
    agent = MagicMock()
    agent.run = AsyncMock(
        return_value=AgentResult(
            content="",
            model="gpt-test",
            status="approval_required",
            approval=ApprovalRequiredPayload("s1", "a1", (action,), "chat-default@v1"),
        )
    )
    adapter, session_store = _adapter(agent, MemoryApprovalStore())

    response = await adapter.chat(ChatRequestVO("s1", "hello"))

    assert response.status == "approval_required"
    session_store.save.assert_not_called()

    policy_provider = StaticApprovalPolicyProvider(enabled=True, interrupt_on="")
    # 高风险工具被后端判定为 interrupt=True。
    assert policy_provider.policy_for("write_file").interrupt is True
    # auto 模式下 evaluate_approval_mode 对高风险动作仍返回 None（强制面板）。
    decisions = evaluate_approval_mode("auto", (action,), policy_provider.policy_for)
    assert decisions is None


async def test_resume_missing_state_raises() -> None:
    """验证 resume 状态不存在时抛错。"""
    agent = MagicMock()
    adapter, _ = _adapter(agent, MemoryApprovalStore())

    with pytest.raises(Exception, match="审批状态不存在"):
        await adapter.resume_approval(
            ApprovalResumeRequestVO("s1", "a1", (ApprovalDecision("approve", "call-1"),))
        )
