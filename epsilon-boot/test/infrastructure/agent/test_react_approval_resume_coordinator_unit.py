"""ReactApprovalResumeCoordinator 的单元测试。"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

import pytest

from domain.agent.exceptions import (
    ApprovalDecisionCountMismatchError,
    ApprovalDecisionNotAllowedError,
    ApprovalDecisionOrderMismatchError,
    ApprovalEditInvalidArgumentsError,
    ApprovalEditToolNameMismatchError,
)
from domain.agent.value_objects import (
    AgentConfig,
    ApprovalDecision,
    ApprovalDecisionType,
    ApprovalInterrupt,
    EditedAction,
    PendingActionRequest,
)
from domain.chat.context import ConversationContext
from domain.model_access.value_objects import ToolCallRequest
from infrastructure.agent.react_approval_resume_coordinator import (
    ReactApprovalResumeCoordinator,
)

BOOT_ROOT = Path(__file__).resolve().parents[3]
COORDINATOR_PATH = (
    BOOT_ROOT / "src" / "infrastructure" / "agent" / "react_approval_resume_coordinator.py"
)
FORBIDDEN_IMPORTS = {
    "application",
    "infrastructure.agent.react_agent_adapter",
}


@dataclass(frozen=True)
class _RuntimeCall:
    """记录 fake runtime 收到的回调。"""

    kind: str
    tool_call: ToolCallRequest | None = None
    action: PendingActionRequest | None = None
    decision: ApprovalDecision | None = None
    round_num: int = 0
    usage: dict[str, int] | None = None


class _FakeRuntime:
    """记录审批恢复协作者调用顺序的 fake runtime。"""

    def __init__(self) -> None:
        """初始化调用记录。"""
        self.calls: list[_RuntimeCall] = []
        self.invalid_tool_names: set[str] = set()

    async def execute_approved_tool_call(
        self,
        context: ConversationContext,
        tool_call: ToolCallRequest,
        config: AgentConfig,
        *,
        round_num: int,
        usage: dict[str, int],
    ) -> None:
        """记录通过或编辑后的工具执行请求。"""
        assert context is not None
        assert config.model == "test-model"
        self.calls.append(
            _RuntimeCall(
                "execute",
                tool_call=tool_call,
                round_num=round_num,
                usage=usage,
            )
        )

    def validate_edited_tool_call(self, tool_name: str, arguments: object) -> None:
        """模拟注册工具参数校验。"""

        if tool_name in self.invalid_tool_names:
            raise ApprovalEditInvalidArgumentsError(tool_name, "schema mismatch")
        assert arguments is not None

    async def record_rejected_tool_call(
        self,
        context: ConversationContext,
        action: PendingActionRequest,
        decision: ApprovalDecision,
        *,
        round_num: int,
        usage: dict[str, int],
    ) -> None:
        """记录拒绝工具调用请求。"""
        assert context is not None
        self.calls.append(
            _RuntimeCall(
                "reject",
                action=action,
                decision=decision,
                round_num=round_num,
                usage=usage,
            )
        )


@pytest.mark.asyncio
async def test_apply_decisions_sequences_approve_edit_and_reject() -> None:
    """按中断动作顺序执行 approve、edit、reject 三类决策。"""
    runtime = _FakeRuntime()
    coordinator = ReactApprovalResumeCoordinator(runtime)
    context = _context_with_tool_calls()
    interrupt = _interrupt(
        _action("call-1", "lookup", '{"q":"old"}'),
        _action("call-2", "write_file", '{"path":"a.txt"}'),
        _action("call-3", "delete_file", '{"path":"b.txt"}'),
    )

    await coordinator.apply_decisions(
        context=context,
        config=_config(),
        interrupt=interrupt,
        decisions=(
            ApprovalDecision(type="approve", tool_call_id="call-1"),
            ApprovalDecision(
                type="edit",
                tool_call_id="call-2",
                edited_action=EditedAction(name="write_file", arguments='{"path":"edited.txt"}'),
            ),
            ApprovalDecision(type="reject", tool_call_id="call-3", message="不要删除"),
        ),
    )

    assert [(call.kind, _call_id(call)) for call in runtime.calls] == [
        ("execute", "call-1"),
        ("execute", "call-2"),
        ("reject", "call-3"),
    ]
    assert runtime.calls[0].tool_call is not None
    assert runtime.calls[0].tool_call.arguments == '{"q":"latest"}'
    assert runtime.calls[1].tool_call == ToolCallRequest(
        id="call-2",
        name="write_file",
        arguments='{"path":"edited.txt"}',
    )
    assert runtime.calls[2].decision is not None
    assert runtime.calls[2].decision.message == "不要删除"
    assert runtime.calls[2].action is not None
    assert runtime.calls[2].action.tool_name == "delete_file"
    assert all(call.round_num == 3 for call in runtime.calls)
    assert all(call.usage == {"total_tokens": 5} for call in runtime.calls)


@pytest.mark.asyncio
async def test_apply_decisions_falls_back_to_interrupt_action_when_context_lacks_call() -> None:
    """上下文没有原始 tool_call 时使用中断动作重建调用。"""
    runtime = _FakeRuntime()
    coordinator = ReactApprovalResumeCoordinator(runtime)
    context = ConversationContext()

    await coordinator.apply_decisions(
        context=context,
        config=_config(),
        interrupt=_interrupt(_action("missing", "lookup", '{"q":"snapshot"}')),
        decisions=(ApprovalDecision(type="approve", tool_call_id="missing"),),
    )

    assert runtime.calls == [
        _RuntimeCall(
            "execute",
            tool_call=ToolCallRequest(id="missing", name="lookup", arguments='{"q":"snapshot"}'),
            round_num=3,
            usage={"total_tokens": 5},
        )
    ]


@pytest.mark.asyncio
async def test_apply_decisions_reject_uses_default_original_tool_call() -> None:
    """reject 分支把原始工具调用和决策交给 runtime 记录。"""
    runtime = _FakeRuntime()
    coordinator = ReactApprovalResumeCoordinator(runtime)
    context = _context_with_tool_calls()

    await coordinator.apply_decisions(
        context=context,
        config=_config(),
        interrupt=_interrupt(_action("call-3", "delete_file", '{"path":"b.txt"}')),
        decisions=(ApprovalDecision(type="reject", tool_call_id="call-3"),),
    )

    assert runtime.calls == [
        _RuntimeCall(
            "reject",
            action=_action("call-3", "delete_file", '{"path":"b.txt"}'),
            decision=ApprovalDecision(type="reject", tool_call_id="call-3"),
            round_num=3,
            usage={"total_tokens": 5},
        )
    ]


@pytest.mark.asyncio
async def test_apply_decisions_validates_count_order_and_allowed_decision() -> None:
    """恢复前校验决策数量、顺序和动作允许集合。"""
    coordinator = ReactApprovalResumeCoordinator(_FakeRuntime())
    context = ConversationContext()
    interrupt = _interrupt(_action("call-1", "lookup", "{}"))

    with pytest.raises(ApprovalDecisionCountMismatchError):
        await coordinator.apply_decisions(
            context=context,
            config=_config(),
            interrupt=interrupt,
            decisions=(),
        )

    with pytest.raises(ApprovalDecisionOrderMismatchError):
        await coordinator.apply_decisions(
            context=context,
            config=_config(),
            interrupt=interrupt,
            decisions=(ApprovalDecision(type="approve", tool_call_id="other"),),
        )

    approve_only = _interrupt(_action("call-1", "lookup", "{}", frozenset({"approve"})))
    with pytest.raises(ApprovalDecisionNotAllowedError):
        await coordinator.apply_decisions(
            context=context,
            config=_config(),
            interrupt=approve_only,
            decisions=(ApprovalDecision(type="reject", tool_call_id="call-1"),),
        )


@pytest.mark.asyncio
async def test_apply_decisions_validates_edit_payload() -> None:
    """edit 决策必须携带同名工具和合法 JSON 参数。"""
    coordinator = ReactApprovalResumeCoordinator(_FakeRuntime())
    context = ConversationContext()
    interrupt = _interrupt(_action("call-1", "lookup", "{}"))

    with pytest.raises(ApprovalEditInvalidArgumentsError):
        await coordinator.apply_decisions(
            context=context,
            config=_config(),
            interrupt=interrupt,
            decisions=(ApprovalDecision(type="edit", tool_call_id="call-1"),),
        )

    with pytest.raises(ApprovalEditToolNameMismatchError):
        await coordinator.apply_decisions(
            context=context,
            config=_config(),
            interrupt=interrupt,
            decisions=(
                ApprovalDecision(
                    type="edit",
                    tool_call_id="call-1",
                    edited_action=EditedAction(name="other", arguments="{}"),
                ),
            ),
        )

    with pytest.raises(ApprovalEditInvalidArgumentsError):
        await coordinator.apply_decisions(
            context=context,
            config=_config(),
            interrupt=interrupt,
            decisions=(
                ApprovalDecision(
                    type="edit",
                    tool_call_id="call-1",
                    edited_action=EditedAction(name="lookup", arguments="{bad json"),
                ),
            ),
        )


@pytest.mark.asyncio
async def test_apply_decisions_does_not_call_runtime_after_validation_failure() -> None:
    """校验失败时不触发任何运行时副作用回调。"""
    runtime = _FakeRuntime()
    coordinator = ReactApprovalResumeCoordinator(runtime)

    with pytest.raises(ApprovalEditInvalidArgumentsError):
        await coordinator.apply_decisions(
            context=ConversationContext(),
            config=_config(),
            interrupt=_interrupt(_action("call-1", "lookup", "{}")),
            decisions=(
                ApprovalDecision(
                    type="edit",
                    tool_call_id="call-1",
                    edited_action=EditedAction(name="lookup", arguments="{bad json"),
                ),
            ),
        )

    assert runtime.calls == []


@pytest.mark.asyncio
async def test_apply_decisions_uses_runtime_tool_schema_validation_for_edit() -> None:
    """edit JSON 合法后仍复用 runtime 的工具参数校验。"""

    runtime = _FakeRuntime()
    runtime.invalid_tool_names.add("lookup")
    coordinator = ReactApprovalResumeCoordinator(runtime)

    with pytest.raises(ApprovalEditInvalidArgumentsError, match="schema mismatch"):
        await coordinator.apply_decisions(
            context=ConversationContext(),
            config=_config(),
            interrupt=_interrupt(_action("call-1", "lookup", "{}")),
            decisions=(
                ApprovalDecision(
                    type="edit",
                    tool_call_id="call-1",
                    edited_action=EditedAction(name="lookup", arguments='{"q":"new"}'),
                ),
            ),
        )

    assert runtime.calls == []


def test_latest_tool_calls_by_id_uses_latest_context_value_for_same_id() -> None:
    """同一 tool_call_id 多次出现时返回最后一次 assistant 调用。"""
    context = _context_with_tool_calls()

    latest = ReactApprovalResumeCoordinator.latest_tool_calls_by_id(context)

    assert latest["call-1"] == ToolCallRequest(
        id="call-1",
        name="lookup",
        arguments='{"q":"latest"}',
    )
    assert latest["call-3"] == ToolCallRequest(
        id="call-3",
        name="delete_file",
        arguments='{"path":"b.txt"}',
    )


def test_coordinator_does_not_import_application_or_concrete_adapter() -> None:
    """协作者模块不得导入 application 或具体 ReActAgentAdapter。"""
    tree = ast.parse(COORDINATOR_PATH.read_text(encoding="utf-8"))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)

    violations = {
        module
        for module in imports
        if module in FORBIDDEN_IMPORTS or module.startswith("application.")
    }
    assert violations == set()


def _context_with_tool_calls() -> ConversationContext:
    """构造含多轮 assistant tool_calls 的上下文。"""
    context = ConversationContext()
    context.add_user_message("run")
    context.add_assistant_message_with_tool_calls(
        "",
        [
            ToolCallRequest(id="call-1", name="lookup", arguments='{"q":"old"}'),
            ToolCallRequest(id="call-2", name="write_file", arguments='{"path":"a.txt"}'),
        ],
    )
    context.add_tool_result("lookup", "old-result", "call-1")
    context.add_assistant_message_with_tool_calls(
        "",
        [
            ToolCallRequest(id="call-1", name="lookup", arguments='{"q":"latest"}'),
            ToolCallRequest(id="call-3", name="delete_file", arguments='{"path":"b.txt"}'),
        ],
    )
    return context


def _call_id(call: _RuntimeCall) -> str:
    """返回 fake runtime 调用关联的 tool_call_id。"""

    if call.tool_call is not None:
        return call.tool_call.id
    assert call.action is not None
    return call.action.tool_call_id


def _config() -> AgentConfig:
    """构造测试用 AgentConfig。"""

    return AgentConfig(
        system_prompt="你是助手",
        tool_schemas=[],
        model="test-model",
        max_rounds=3,
        prompt_id="chat-default@v1",
    )


def _action(
    tool_call_id: str,
    tool_name: str,
    arguments: str,
    allowed_decisions: frozenset[ApprovalDecisionType] = frozenset(
        {"approve", "edit", "reject"}
    ),
) -> PendingActionRequest:
    """构造待审批动作。"""
    return PendingActionRequest(
        tool_call_id=tool_call_id,
        tool_name=tool_name,
        arguments=arguments,
        allowed_decisions=allowed_decisions,
    )


def _interrupt(*actions: PendingActionRequest) -> ApprovalInterrupt:
    """构造审批中断。"""
    return ApprovalInterrupt(
        session_id="session-1",
        approval_id="approval-1",
        actions=tuple(actions),
        context_snapshot={"messages": []},
        round_num=3,
        model="gpt-test",
        usage_so_far={"total_tokens": 5},
    )
