"""Handoff 领域判定策略单元测试。

本测试只覆盖 ``domain.agent.handoff_policy`` 的纯判定，不设置 workflow
ContextVar，不构造 ToolExecutionResult，也不使用 DelegationPort 或事件存储 fake。
"""

from domain.agent.handoff_policy import decide_handoff
from domain.run.workflow import CollaborationLimit
from domain.run.workflow_context import WorkflowCollaborationContext


def _workflow_context(
    *,
    max_recursion_depth: int = 3,
    max_handoff_count: int = 1,
    handoff_count: int = 0,
) -> WorkflowCollaborationContext:
    """构造仅包含 handoff policy 所需字段的 workflow 协作上下文。"""

    return WorkflowCollaborationContext(
        run_id="run-1",
        workflow_name=None,
        phase=None,
        source_role=None,
        limit=CollaborationLimit(
            max_recursion_depth=max_recursion_depth,
            max_handoff_count=max_handoff_count,
        ),
        depth=0,
        handoff_count=handoff_count,
        delegation_count=0,
    )


def test_without_workflow_context_uses_configured_max_depth() -> None:
    """无 workflow context 时只使用配置侧 max_delegation_depth。"""

    decision = decide_handoff(
        current_depth=1,
        max_delegation_depth=4,
        workflow_context=None,
    )

    assert decision.allowed is True
    assert decision.next_depth == 2
    assert decision.effective_max_depth == 4
    assert decision.reason is None


def test_workflow_recursion_limit_takes_stricter_max_depth() -> None:
    """workflow max_recursion_depth 更严格时使用较小值。"""

    decision = decide_handoff(
        current_depth=0,
        max_delegation_depth=5,
        workflow_context=_workflow_context(max_recursion_depth=2),
    )

    assert decision.allowed is True
    assert decision.next_depth == 1
    assert decision.effective_max_depth == 2
    assert decision.reason is None


def test_depth_exceeded_returns_depth_reason() -> None:
    """下一层深度超出有效上限时返回 depth 拒绝原因。"""

    decision = decide_handoff(
        current_depth=3,
        max_delegation_depth=3,
        workflow_context=None,
    )

    assert decision.allowed is False
    assert decision.next_depth == 4
    assert decision.effective_max_depth == 3
    assert decision.reason == "handoff_depth_exceeded"


def test_handoff_count_exceeded_reason_preserves_exact_format() -> None:
    """handoff 次数超限时保留既有 reason 字符串格式。"""

    decision = decide_handoff(
        current_depth=0,
        max_delegation_depth=3,
        workflow_context=_workflow_context(max_handoff_count=1, handoff_count=1),
    )

    assert decision.allowed is False
    assert decision.next_depth == 1
    assert decision.effective_max_depth == 3
    assert decision.reason == "handoff_count_exceeded:2>1"


def test_allowed_path_returns_no_reason() -> None:
    """深度与 handoff 次数均未超限时允许 handoff。"""

    decision = decide_handoff(
        current_depth=1,
        max_delegation_depth=3,
        workflow_context=_workflow_context(
            max_recursion_depth=3,
            max_handoff_count=2,
            handoff_count=1,
        ),
    )

    assert decision.allowed is True
    assert decision.next_depth == 2
    assert decision.effective_max_depth == 3
    assert decision.reason is None
