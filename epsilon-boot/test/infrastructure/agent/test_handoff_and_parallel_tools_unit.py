"""HandoffToAgentTool / DelegateParallelTool 单元测试。

覆盖 Spec A 新工具：

- ``HandoffToAgentTool``：成功 → 抛 ``HandoffPerformed``；失败/越界/未注册 →
  返回错误字符串；schema 校验。
- ``DelegateParallelTool``：schema 校验 + 聚合输出格式。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.exceptions import (
    AgentNotFoundError,
    DelegationDepthExceededError,
    HandoffPerformed,
)
from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import (
    DelegationResult,
    HandoffResult,
    NamedAgentConfig,
)
from domain.chat.context import ConversationContext, UserMessage
from domain.model_access.value_objects import ToolCallRequest
from infrastructure.agent.agent_registry_adapter import AgentRegistryAdapter
from infrastructure.agent.delegate_parallel_tool import DelegateParallelTool
from infrastructure.agent.handoff_context import (
    reset_parent_context,
    set_parent_context,
)
from infrastructure.agent.handoff_to_agent_tool import HandoffToAgentTool


def _make_named(name: str) -> NamedAgentConfig:
    return NamedAgentConfig(
        name=name,
        description=f"agent {name}",
        system_prompt=f"你是 {name}",
        prompt_id="chat-default@v1",
    )


# ---------------------------------------------------------------------------
# HandoffToAgentTool
# ---------------------------------------------------------------------------


def _make_handoff_tool(
    delegation: MagicMock,
    *,
    current_depth: int = 0,
    max_depth: int = 3,
) -> HandoffToAgentTool:
    registry = AgentRegistryAdapter()
    registry.register(_make_named("specialist"))
    return HandoffToAgentTool(
        agent_registry=registry,
        delegation=delegation,
        current_delegation_depth=current_depth,
        max_delegation_depth=max_depth,
    )


@pytest.mark.asyncio
async def test_handoff_tool_raises_handoff_performed_on_success() -> None:
    """成功路径：``DelegationPort.handoff`` 返回 ``success=True`` →
    工具抛出 ``HandoffPerformed``，由 ReActAgentAdapter 捕获终止 Loop。"""
    delegation = MagicMock()
    delegation.handoff = AsyncMock(
        return_value=HandoffResult(
            target_agent="specialist",
            content="目标 Agent 最终回复",
            success=True,
            usage={"total_tokens": 10},
            model="gpt-4o",
        )
    )

    # 父 ConversationContext 通过 ContextVar 传入
    parent_ctx = ConversationContext()
    parent_ctx.add_user_message("帮我处理")
    token = set_parent_context(parent_ctx)
    try:
        tool = _make_handoff_tool(delegation)
        with pytest.raises(HandoffPerformed) as exc_info:
            await tool.execute(agent_name="specialist")
    finally:
        reset_parent_context(token)

    signal = exc_info.value
    assert signal.target_agent == "specialist"
    assert signal.content == "目标 Agent 最终回复"
    assert signal.usage == {"total_tokens": 10}
    assert signal.model == "gpt-4o"

    # 父消息快照被传入
    delegation.handoff.assert_awaited_once()
    args, kwargs = delegation.handoff.call_args
    # 第二个 positional arg 是消息快照
    snapshot = args[1] if len(args) > 1 else kwargs["context_messages"]
    assert len(snapshot) == 1
    assert isinstance(snapshot[0], UserMessage)
    assert kwargs["delegation_depth"] == 1
    assert kwargs["max_delegation_depth"] == 3


@pytest.mark.asyncio
async def test_handoff_tool_returns_error_string_when_depth_exceeded() -> None:
    """深度超限时返回错误字符串，不抛 ``DelegationDepthExceededError``。"""
    delegation = MagicMock()
    delegation.handoff = AsyncMock()

    tool = _make_handoff_tool(delegation, current_depth=3, max_depth=3)
    parent_ctx = ConversationContext()
    token = set_parent_context(parent_ctx)
    try:
        result = await tool.execute(agent_name="specialist")
    finally:
        reset_parent_context(token)

    assert "委派深度超限" in result.content
    assert result.content == "无法 handoff 给 'specialist': 委派深度超限 (3 → 4 > 3)"
    assert isinstance(result, ToolExecutionResult)
    assert result.metadata == {"target_agent": "specialist", "success": False}
    delegation.handoff.assert_not_called()


@pytest.mark.asyncio
async def test_handoff_tool_returns_error_string_when_target_not_registered() -> None:
    """目标 Agent 未注册时返回错误字符串（让 LLM 自我纠正）。"""
    registry = AgentRegistryAdapter()
    delegation = MagicMock()
    delegation.handoff = AsyncMock(
        side_effect=AgentNotFoundError(
            agent_name="ghost",
            registered_names=[],
        )
    )

    tool = HandoffToAgentTool(
        agent_registry=registry,
        delegation=delegation,
    )
    parent_ctx = ConversationContext()
    token = set_parent_context(parent_ctx)
    try:
        result = await tool.execute(agent_name="ghost")
    finally:
        reset_parent_context(token)

    assert "ghost" in result.content
    assert "未找到" in result.content


@pytest.mark.asyncio
async def test_handoff_tool_returns_error_when_parent_context_missing() -> None:
    """非 Agent Loop 场景（父上下文 ContextVar 未设置）→ 错误字符串，不抛信号。"""
    delegation = MagicMock()
    delegation.handoff = AsyncMock()

    tool = _make_handoff_tool(delegation)
    # 不调用 set_parent_context
    result = await tool.execute(agent_name="specialist")

    assert "Handoff is unavailable" in result.content
    delegation.handoff.assert_not_called()


@pytest.mark.asyncio
async def test_handoff_tool_returns_error_when_handoff_result_unsuccessful() -> None:
    """``HandoffResult.success=False`` → 工具返回错误字符串而不抛信号。"""
    delegation = MagicMock()
    delegation.handoff = AsyncMock(
        return_value=HandoffResult(
            target_agent="specialist",
            content="子 Agent max_rounds",
            success=False,
            usage={},
            model="m",
        )
    )

    tool = _make_handoff_tool(delegation)
    parent_ctx = ConversationContext()
    token = set_parent_context(parent_ctx)
    try:
        result = await tool.execute(agent_name="specialist")
    finally:
        reset_parent_context(token)

    assert "失败" in result.content
    assert "specialist" in result.content
    # 错误返回路径的 metadata：handoff 未真正发生，success 恒为 False（design §3.13）。
    assert isinstance(result, ToolExecutionResult)
    assert result.metadata["target_agent"] == "specialist"
    assert result.metadata["success"] is False
    assert set(result.metadata.keys()) == {"target_agent", "success"}


@pytest.mark.asyncio
async def test_handoff_tool_swallows_unexpected_runtime_exception() -> None:
    """委派端口抛未预期异常 → 错误字符串而非透出。"""
    delegation = MagicMock()
    delegation.handoff = AsyncMock(side_effect=ValueError("boom"))

    tool = _make_handoff_tool(delegation)
    parent_ctx = ConversationContext()
    token = set_parent_context(parent_ctx)
    try:
        result = await tool.execute(agent_name="specialist")
    finally:
        reset_parent_context(token)

    assert "Handoff 执行失败" in result.content
    assert "boom" in result.content


def test_handoff_tool_schema_only_requires_agent_name() -> None:
    """工具 schema：仅 agent_name 必填，无 input_data / task_goal。"""
    tool = _make_handoff_tool(MagicMock())
    schema = tool.parameters
    assert schema["type"] == "object"
    assert schema["required"] == ["agent_name"]
    assert "agent_name" in schema["properties"]


def test_handoff_tool_description_lists_registered_agents() -> None:
    """description 动态包含已注册 Agent 名称。"""
    tool = _make_handoff_tool(MagicMock())
    desc = tool.description
    assert "specialist" in desc


# ---------------------------------------------------------------------------
# DelegateParallelTool
# ---------------------------------------------------------------------------


def _make_parallel_tool(delegation) -> DelegateParallelTool:
    registry = AgentRegistryAdapter()
    for n in ("a1", "a2"):
        registry.register(_make_named(n))
    return DelegateParallelTool(
        agent_registry=registry,
        delegation=delegation,
    )


@pytest.mark.asyncio
async def test_delegate_parallel_tool_aggregates_results_with_check_marks() -> None:
    """多条委派结果按输入顺序聚合，单条形态 ``[✓/✗] <agent>\\n<content>``。"""
    delegation = MagicMock()
    delegation.delegate_parallel = AsyncMock(
        return_value=[
            DelegationResult(content="ok-1", success=True),
            DelegationResult(content="err-2", success=False),
        ]
    )

    tool = _make_parallel_tool(delegation)
    result = await tool.execute(
        requests=[
            {"agent_name": "a1", "task_goal": "g1"},
            {"agent_name": "a2", "task_goal": "g2"},
        ]
    )
    output = result.content
    assert "[✓] a1\nok-1" in output
    assert "[✗] a2\nerr-2" in output
    # 顺序：a1 在 a2 前
    assert output.index("a1") < output.index("a2")
    # metadata 结构化字段
    assert result.metadata["targets"] == ["a1", "a2"]
    assert result.metadata["results_count"] == 2
    assert result.metadata["success_count"] == 1
    # metadata 字段类型与键集合对齐 design §3.12。
    assert isinstance(result.metadata["targets"], list)
    assert all(isinstance(t, str) for t in result.metadata["targets"])
    assert isinstance(result.metadata["results_count"], int)
    assert isinstance(result.metadata["success_count"], int)
    assert set(result.metadata.keys()) == {"targets", "results_count", "success_count"}


@pytest.mark.asyncio
async def test_delegate_parallel_tool_raises_when_depth_exceeded() -> None:
    """整体深度超限抛 ``DelegationDepthExceededError``（与单 delegate 工具语义一致）。"""
    delegation = MagicMock()
    delegation.delegate_parallel = AsyncMock()

    registry = AgentRegistryAdapter()
    registry.register(_make_named("a1"))
    tool = DelegateParallelTool(
        agent_registry=registry,
        delegation=delegation,
        current_delegation_depth=3,
        max_delegation_depth=3,
    )
    with pytest.raises(DelegationDepthExceededError):
        await tool.execute(
            requests=[{"agent_name": "a1", "task_goal": "g"}],
        )


def test_delegate_parallel_tool_schema_min_max_items() -> None:
    """schema 包含 minItems=1 / maxItems=8。"""
    tool = _make_parallel_tool(MagicMock())
    schema = tool.parameters
    requests = schema["properties"]["requests"]
    assert requests["minItems"] == 1
    assert requests["maxItems"] == 8


def test_delegate_parallel_tool_validate_params_rejects_empty_requests() -> None:
    """验证空 requests 列表被 validate_params 拒绝。"""
    tool = _make_parallel_tool(MagicMock())
    errors = tool.validate_params({"requests": []})
    assert any("至少包含" in e for e in errors)


def test_delegate_parallel_tool_validate_params_rejects_overflow() -> None:
    """验证超过 maxItems 的 requests 被拒绝。"""
    tool = _make_parallel_tool(MagicMock())
    requests = [{"agent_name": "a1", "task_goal": "g"} for _ in range(9)]
    errors = tool.validate_params({"requests": requests})
    assert any("最多包含" in e for e in errors)


def test_delegate_parallel_tool_validate_params_rejects_missing_required() -> None:
    """验证子项缺少 agent_name / task_goal 被报错。"""
    tool = _make_parallel_tool(MagicMock())
    errors = tool.validate_params(
        {
            "requests": [
                {"agent_name": "a1"},
                {"task_goal": "g"},
            ]
        }
    )
    assert any(("agent_name" in e and "task_goal" in e) or "task_goal" in e for e in errors)
    assert any("agent_name" in e for e in errors)


@pytest.mark.asyncio
async def test_delegate_parallel_tool_run_full_pipeline() -> None:
    """``Tool.run(ToolCallRequest)`` 端到端：JSON 解析 → 校验 → execute → 聚合输出。"""
    delegation = MagicMock()
    delegation.delegate_parallel = AsyncMock(
        return_value=[DelegationResult(content="ok", success=True)]
    )
    tool = _make_parallel_tool(delegation)

    request = ToolCallRequest(
        id="call_1",
        name="delegate_parallel",
        arguments=json.dumps({"requests": [{"agent_name": "a1", "task_goal": "g"}]}),
    )
    out = await tool.run(request)
    assert "[✓] a1\nok" in out.content
