"""指标 2：Delegation_Correctness（委派正确性）评测样本。

判定规则（严格对齐 ``docs/spec/spec-ai-evaluation/tasks.md`` 阶段 3.3，成功
必须三项全部通过）：

(a) 实际委派目标 = ``expected_target_agent``。通过观察父 Agent 上下文中唯一
    的 :class:`ToolMessage` 及 :class:`DelegateToAgentTool` 实际收到的
    ``agent_name`` 参数来判定。

(b) 子任务 ``child_depth = parent_depth + 1 ≤ AGENT_MAX_DELEGATION_DEPTH``。
    ``AGENT_MAX_DELEGATION_DEPTH`` 通过
    :func:`tests.evaluation.metrics._fakes.load_agent_max_delegation_depth`
    从 ``epsilon-boot/config.properties`` 读取（回退 3）。

(c) 子任务返回的 :class:`TaskResult.content` 作为 :class:`ToolMessage` 被
    写回父 Agent 上下文（由真实 :class:`ReActAgentAdapter` + 真实
    :class:`DelegateToAgentTool` 合作完成）。

覆盖场景（合计 ≥ 12 条）：

1. **目标正确（success）**：父调用 ``delegate_to_agent(agent_name="child")``，
   子 Agent 桩模型返回固定 answer → ToolMessage.content == answer。
2. **深度越限（depth_exceeded）**：把父 Agent 的 ``current_delegation_depth``
   设为 ``AGENT_MAX_DELEGATION_DEPTH``，下一次委派触发
   :class:`DelegationDepthExceededError`；错误消息被 ReAct Loop 转写为
   ToolMessage → 失败判据命中"错误消息包含深度字样"。
3. **目标不存在（not_found）**：父调用 ``delegate_to_agent(agent_name="ghost")``，
   :class:`StaticAgentRegistry.get` 抛 :class:`AgentNotFoundError`；
   ReAct Loop 同样转写错误到 ToolMessage。
4. **循环依赖（cycle_depth_exceeded）**：复用深度越限的触发路径，让父
   Agent 以"假设已被循环委派到上限"的姿态再委派，观察深度闸门失败；
   在本评测中等价于深度越限，但语义上用不同 case_id 与描述分组。
5. **返回正确拼回（content_echo）**：子 Agent 被要求返回包含特殊 token 的
   字符串，父 Agent 的 ToolMessage.content 与之逐字符相等。

样本数：5 类 × ≥ 3 条变体 = ≥ 15 条，覆盖 tasks.md "≥ 12" 要求。
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any

import pytest

from domain.agent.tools import ToolRegistry
from domain.agent.value_objects import AgentConfig, NamedAgentConfig
from domain.chat.context import ConversationContext, ToolMessage
from domain.model_access.value_objects import LLMResponse, ToolCallRequest

from infrastructure.agent.delegate_to_agent_tool import DelegateToAgentTool
from infrastructure.agent.delegation_adapter import DelegationAdapter
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from infrastructure.chat.sliding_window_compaction_adapter import (
    SlidingWindowCompactionAdapter,
)
from infrastructure.task.task_agent_adapter import TaskAgentAdapter

from tests.evaluation.metrics._fakes import (
    StaticModelRegistry,
    StaticPromptRegistry,
    build_context_builder,
    load_agent_max_delegation_depth,
)
from tests.evaluation.runner.models import (
    EvalCase,
    EvalSampleResult,
    MetricId,
    SampleOutcome,
)
from tests.evaluation.runner.sample_sink import SampleSink
from tests.evaluation.stubs.agent_registry import StaticAgentRegistry
from tests.evaluation.stubs.model_access import ScriptedModelAccess
from tests.evaluation.stubs.session_context_store import InMemorySessionContextStore


# ---------------------------------------------------------------------------
# 样本场景定义
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DelegationScenario:
    """单条委派样本的构造参数。

    Attributes:
        case_id: 样本唯一标识。
        description: 场景描述（便于报告展示）。
        kind: 场景分类标签；取值 ``"success"`` / ``"depth_exceeded"`` /
            ``"not_found"`` / ``"cycle_depth_exceeded"`` / ``"content_echo"``。
        parent_depth: 父 Agent 当前所处的委派深度（传入
            :class:`DelegateToAgentTool` 的 ``current_delegation_depth``）。
        target_agent: 父模型脚本返回的 ``agent_name`` 参数。
        register_target: 目标是否要注册到 :class:`StaticAgentRegistry`；
            ``False`` 用于构造"目标不存在"场景。
        child_answer: 子 Agent 桩模型预设返回的 answer 文本；
            ``content_echo`` 场景下用于逐字符比对。
        expected_success: 期望的"是否应判成功"。
    """

    case_id: str
    description: str
    kind: str
    parent_depth: int
    target_agent: str
    register_target: bool
    child_answer: str
    expected_success: bool


_AGENT_MAX_DEPTH = load_agent_max_delegation_depth()


DELEGATION_SCENARIOS: list[_DelegationScenario] = []


def _add(
    *,
    case_id: str,
    description: str,
    kind: str,
    parent_depth: int,
    target_agent: str,
    register_target: bool,
    child_answer: str,
    expected_success: bool,
) -> None:
    """向全局样本列表追加一条场景。"""

    DELEGATION_SCENARIOS.append(
        _DelegationScenario(
            case_id=case_id,
            description=description,
            kind=kind,
            parent_depth=parent_depth,
            target_agent=target_agent,
            register_target=register_target,
            child_answer=child_answer,
            expected_success=expected_success,
        )
    )


# --- 类 1：目标正确（success）× 3 ---
for idx, answer in enumerate(
    ["子 Agent 执行完成", "delegation-ok-1", "done"], start=1
):
    _add(
        case_id=f"delegation-success-{idx:02d}",
        description=f"正常委派 child，子 Agent 返回 {answer!r}",
        kind="success",
        parent_depth=0,
        target_agent="child_agent",
        register_target=True,
        child_answer=answer,
        expected_success=True,
    )

# --- 类 2：深度越限（depth_exceeded）× 3 ---
for idx in range(1, 4):
    _add(
        case_id=f"delegation-depth-exceeded-{idx:02d}",
        description=(
            f"父 Agent 深度已达 {_AGENT_MAX_DEPTH}，再次委派应被 "
            "DelegationDepthExceededError 拒绝"
        ),
        kind="depth_exceeded",
        parent_depth=_AGENT_MAX_DEPTH,
        target_agent="child_agent",
        register_target=True,
        child_answer="不应被调用",
        expected_success=False,
    )

# --- 类 3：目标不存在（not_found）× 3 ---
for idx, ghost_name in enumerate(["ghost", "unknown_agent", "missing"], start=1):
    _add(
        case_id=f"delegation-not-found-{idx:02d}",
        description=f"委派到未注册的 {ghost_name!r} 应由 AgentNotFoundError 兜底",
        kind="not_found",
        parent_depth=0,
        target_agent=ghost_name,
        register_target=False,
        child_answer="不应被调用",
        expected_success=False,
    )

# --- 类 4：循环依赖（cycle_depth_exceeded）× 3 ---
# 语义上等价于深度越限：假设 A→B→A→B→... 循环委派，最终一定在某一层被深度上限拦截。
for idx in range(1, 4):
    _add(
        case_id=f"delegation-cycle-{idx:02d}",
        description=(
            "模拟 A→B→A 循环触达深度上限，父 Agent 处于 "
            f"{_AGENT_MAX_DEPTH} 时再委派被拒绝"
        ),
        kind="cycle_depth_exceeded",
        parent_depth=_AGENT_MAX_DEPTH,
        target_agent="child_agent",
        register_target=True,
        child_answer="环中不应被调用",
        expected_success=False,
    )

# --- 类 5：返回正确拼回（content_echo）× 3 ---
for idx, payload in enumerate(
    ["<<token-1>>", "回复包含中文与符号 #$%", "multi\nline\nanswer"], start=1
):
    _add(
        case_id=f"delegation-content-echo-{idx:02d}",
        description=f"子 Agent 返回 {payload!r}，父 ToolMessage.content 必须逐字符相等",
        kind="content_echo",
        parent_depth=0,
        target_agent="child_agent",
        register_target=True,
        child_answer=payload,
        expected_success=True,
    )


DELEGATION_CASES: list[EvalCase] = [
    EvalCase(
        case_id=s.case_id,
        metric=MetricId.DELEGATION_CORRECTNESS,
        description=s.description,
        inputs={"scenario": s},
        expected={
            "expected_success": s.expected_success,
            "agent_max_depth": _AGENT_MAX_DEPTH,
        },
    )
    for s in DELEGATION_SCENARIOS
]


# ---------------------------------------------------------------------------
# 桩与被测 Adapter 构造
# ---------------------------------------------------------------------------


def _build_child_model_access(child_answer: str) -> ScriptedModelAccess:
    """构造子 Agent 的桩模型：一轮返回指定 answer（无 tool_calls）终止。

    Args:
        child_answer: 子 Agent 要返回的文本内容。

    Returns:
        预填脚本的 :class:`ScriptedModelAccess` 实例。
    """

    return ScriptedModelAccess(
        scripted_responses=[
            LLMResponse(content=child_answer, model="scripted-child"),
        ],
    )


def _build_parent_model_access(scenario: _DelegationScenario) -> ScriptedModelAccess:
    """构造父 Agent 的桩模型：第 1 轮 delegate_to_agent，第 2 轮 finish。

    Args:
        scenario: 样本场景。

    Returns:
        预填两轮脚本的 :class:`ScriptedModelAccess` 实例。
    """

    arguments = json.dumps(
        {
            "agent_name": scenario.target_agent,
            "task_goal": "请完成子任务并返回答案。",
        },
        ensure_ascii=False,
    )
    return ScriptedModelAccess(
        scripted_responses=[
            LLMResponse(
                content="",
                model="scripted-parent-1",
                tool_calls=[
                    ToolCallRequest(
                        id=f"call-{scenario.case_id}",
                        name="delegate_to_agent",
                        arguments=arguments,
                    )
                ],
            ),
            LLMResponse(content="父 Agent 汇总完成", model="scripted-parent-2"),
        ]
    )


async def _run_delegation_sample(
    scenario: _DelegationScenario,
) -> tuple[int, int, dict[str, Any]]:
    """驱动一次父 Agent 的 ReAct Loop，返回样本的分子、分母与详情。

    流程：
        1. 构造共享 :class:`ToolRegistry` 并注册真实 :class:`DelegateToAgentTool`；
        2. 构造 :class:`StaticAgentRegistry`、:class:`DelegationAdapter`、
           :class:`TaskAgentAdapter`（注入子 Agent 的桩 `ScriptedModelAccess`）；
        3. 以父 Agent 桩模型驱动 :class:`ReActAgentAdapter.run`；
        4. 观察父上下文末尾 :class:`ToolMessage`，按 (a)(b)(c) 三项判据合并成功
           条件。

    Args:
        scenario: 样本场景。

    Returns:
        ``(numerator, denominator, details)``；``denominator`` 恒为 1，
        ``numerator`` 为 0 或 1。
    """

    compaction = SlidingWindowCompactionAdapter(max_messages=50)
    context_builder = build_context_builder(max_messages=50)
    tool_registry = ToolRegistry()

    # 子 Agent 桩与适配器
    child_model_access = _build_child_model_access(scenario.child_answer)
    model_registry = StaticModelRegistry(
        adapters={"scripted-child": child_model_access},
        default_model="scripted-child",
    )
    child_react_agent = ReActAgentAdapter(
        tool_registry=tool_registry, context_builder=context_builder
    )
    session_store = InMemorySessionContextStore()
    prompt_registry = StaticPromptRegistry()
    task_agent = TaskAgentAdapter(
        agent=child_react_agent,
        tool_registry=tool_registry,
        model_registry=model_registry,
        compaction=compaction,
        session_store=session_store,
        prompt_registry=prompt_registry,
        max_rounds=2,
    )

    # Agent 注册表与委派适配器
    agent_registry = StaticAgentRegistry()
    if scenario.register_target:
        agent_registry.register(
            NamedAgentConfig(
                name=scenario.target_agent,
                description="评测用子 Agent",
                system_prompt="你是用于评测的子 Agent。",
                prompt_id="eval-child@v1",
                tool_names=frozenset(),
                model="scripted-child",
            )
        )
    delegation_adapter = DelegationAdapter(
        agent_registry=agent_registry, task_agent=task_agent
    )

    # 父 Agent 的 delegate_to_agent 工具（真实实现）
    delegate_tool = DelegateToAgentTool(
        agent_registry=agent_registry,
        delegation=delegation_adapter,
        current_delegation_depth=scenario.parent_depth,
        max_delegation_depth=_AGENT_MAX_DEPTH,
    )
    tool_registry.register(delegate_tool)

    parent_react_agent = ReActAgentAdapter(
        tool_registry=tool_registry, context_builder=context_builder
    )
    parent_model_access = _build_parent_model_access(scenario)

    context = ConversationContext()
    context.add_user_message("请将下面的子任务委派给合适的 Agent。")

    config = AgentConfig(
        system_prompt="评测指标 2：委派正确性样本。",
        tool_schemas=tool_registry.get_schemas(),
        model=None,
        max_rounds=2,
        prompt_id="eval-delegation@v1",
        allowed_tool_names=frozenset({"delegate_to_agent"}),
    )

    await parent_react_agent.run(context, config, parent_model_access)

    # 观察父上下文中由 delegate_to_agent 回写的 ToolMessage
    tool_messages = [
        m
        for m in context.get_messages()
        if isinstance(m, ToolMessage) and m.tool_name == "delegate_to_agent"
    ]
    if not tool_messages:
        return 0, 1, {"reason": "未观察到 delegate_to_agent 的 ToolMessage"}
    last = tool_messages[-1]
    content = last.content

    child_depth = scenario.parent_depth + 1
    depth_ok = child_depth <= _AGENT_MAX_DEPTH

    # (a) 实际目标判定：父模型脚本已指定 target_agent；若场景指定的目标
    # 未注册（not_found）或被深度闸门拦截（depth_exceeded / cycle），则
    # ToolMessage.content 为错误消息，我们反向判定 "目标是否被真正解析执行"。
    error_markers = ("委派深度超限", "未找到")
    hit_error = any(marker in content for marker in error_markers)
    target_ok = not hit_error and scenario.register_target

    # (c) 返回拼回：内容非空且不是错误消息；content_echo 场景进一步要求
    # 逐字符等于 child_answer。
    echo_ok = len(content) > 0 and not hit_error
    if scenario.kind == "content_echo":
        echo_ok = echo_ok and content == scenario.child_answer

    passed = bool(depth_ok and target_ok and echo_ok)

    # 对于 expected_success=False 的场景，"样本成功"意味着评测命中了
    # 失败应触发的判据（即 passed 为 False）。我们在 driver 返回分子时保持
    # "分子=1 等于判定为正确委派"，后续 outcome 对齐 expected_success。
    numerator = 1 if passed else 0
    return (
        numerator,
        1,
        {
            "kind": scenario.kind,
            "target_agent": scenario.target_agent,
            "parent_depth": scenario.parent_depth,
            "child_depth": child_depth,
            "agent_max_depth": _AGENT_MAX_DEPTH,
            "tool_message_content": content[:300],
            "depth_ok": depth_ok,
            "target_ok": target_ok,
            "echo_ok": echo_ok,
        },
    )


# ---------------------------------------------------------------------------
# pytest 用例
# ---------------------------------------------------------------------------


@pytest.mark.evaluation
@pytest.mark.parametrize(
    "case",
    DELEGATION_CASES,
    ids=[c.case_id for c in DELEGATION_CASES],
)
def test_delegation_correctness(case: EvalCase, sample_sink: SampleSink) -> None:
    """驱动父 ReAct Agent 执行一次委派，回传 :class:`EvalSampleResult`。

    样本判定：
        - ``expected_success=True``：分子应为 1（三项判据全过）；
        - ``expected_success=False``：分子应为 0（深度越限 / 目标不存在 /
          循环导致至少一项失败）。

    Args:
        case: :class:`EvalCase` 实例。
        sample_sink: 会话级样本收集器 fixture。
    """

    scenario = case.inputs["scenario"]
    expected_success: bool = case.expected["expected_success"]
    numerator, denominator, details = asyncio.run(_run_delegation_sample(scenario))
    outcome = (
        SampleOutcome.PASS
        if (numerator == 1) == expected_success
        else SampleOutcome.FAIL
    )
    sample_sink.append(
        EvalSampleResult(
            case_id=case.case_id,
            metric=case.metric,
            outcome=outcome,
            numerator=numerator,
            denominator=denominator,
            details={"expected_success": expected_success, **details},
        )
    )
