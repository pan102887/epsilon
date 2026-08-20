"""指标 1：Tool_Call_Success_Rate（工具调用成功率）评测样本。

判定规则（严格对齐 ``docs/spec/spec-ai-evaluation/tasks.md`` 阶段 3.1）：

- 对每个样本，构造真实 :class:`ReActAgentAdapter` + 桩 :class:`ScriptedModelAccess`
  + 内置 :class:`FakeEchoTool` / :class:`FakeFailingTool`，通过脚本让模型在
  第 1 轮返回指定 ``tool_calls``、第 2 轮返回 finish（空 ``tool_calls``）终止。
- 分母 = 本次运行中模型第 1 轮返回的 ``tool_calls`` 总数；即每个样本恒为 1
  （单工具调用）。
- 分子 = 未抛 :class:`ToolExecutionError` / :class:`ToolPermissionDeniedError` /
  :class:`ToolNotFoundError` 且返回字符串长度 > 0 的调用次数。由于
  :meth:`ReActAgentAdapter.run` 已将工具异常转写为 :class:`ToolMessage` 的
  ``content``（错误消息），本用例通过观察该 ``ToolMessage.content`` 与错误类前缀
  匹配来判别，而非自己捕获异常。

五类场景（合计 ≥ 20 样本）：

1. 成功：模型调用 ``fake_echo(text="hi")`` → ToolMessage.content == "hi"；
2. 返回空串：模型调用 ``fake_echo(text="")`` → ToolMessage.content == ""；
3. 权限拒绝：使用 :meth:`ToolRegistry.create_scoped_view` 把允许工具集合收窄为
   空集或不含 ``fake_echo``，模型仍调用 ``fake_echo`` → AssistantMessage
   的 ``allowed_tool_names`` 校验触发 :class:`ToolPermissionDeniedError`；
4. 未知工具：模型返回 ``tool.name == "nonexistent_tool"`` → 经 allowed_tool_names
   阻挡或 ``ToolRegistry.execute`` 抛 :class:`ToolNotFoundError`；
5. 执行异常：模型调用 ``fake_fail`` → :class:`ToolExecutionError` 被转写到
   ToolMessage.content。

样本数合计：20 条（5 类 × 4 条变体，覆盖 tasks.md "≥ 20" 要求）。
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from domain.agent.exceptions import (
    ToolExecutionError,
    ToolNotFoundError,
    ToolPermissionDeniedError,
)
from domain.agent.tools import ToolRegistry
from domain.agent.value_objects import AgentConfig
from domain.chat.context import ConversationContext, ToolMessage
from domain.model_access.value_objects import LLMResponse, ToolCallRequest

from infrastructure.agent.react_agent_adapter import ReActAgentAdapter

from tests.evaluation.metrics._fakes import FakeEchoTool, FakeFailingTool, build_context_builder
from tests.evaluation.runner.models import (
    EvalCase,
    EvalSampleResult,
    MetricId,
    SampleOutcome,
)
from tests.evaluation.runner.sample_sink import SampleSink
from tests.evaluation.stubs.model_access import ScriptedModelAccess


# ---------------------------------------------------------------------------
# 样本类别与构造
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Scenario:
    """单条样本的构造参数。

    Attributes:
        case_id: 全局唯一样本标识。
        description: 一句话描述，便于报告展示。
        tool_name: 模型在第 1 轮返回的 ``tool_call.name``。
        tool_arguments: 模型在第 1 轮返回的 ``tool_call.arguments``（JSON 串）。
        allowed_tools: 传入 :class:`AgentConfig` 的 ``allowed_tool_names``；
            ``None`` 表示"允许 registry 中已注册的两件内置工具"。
        expected_success: 期望的"是否应计为成功"布尔值；用于分子判定。
    """

    case_id: str
    description: str
    tool_name: str
    tool_arguments: str
    allowed_tools: frozenset[str] | None
    expected_success: bool


def _success_prefix_of(error: type[Exception]) -> str:
    """返回某工具异常类在 ``str(err)`` 中的典型前缀，用于反向判定。

    Args:
        error: 异常类。

    Returns:
        前缀字符串；用于在 :class:`ToolMessage.content` 中匹配"是否命中该异常"。
    """

    return {
        ToolNotFoundError: "工具",
        ToolPermissionDeniedError: "工具",
        ToolExecutionError: "模拟执行失败",
    }[error]


TOOL_CALL_SCENARIOS: list[_Scenario] = []


def _add(
    *,
    case_id: str,
    description: str,
    tool_name: str,
    tool_arguments: str,
    allowed_tools: frozenset[str] | None,
    expected_success: bool,
) -> None:
    """向全局 :data:`TOOL_CALL_SCENARIOS` 追加一条样本参数。"""

    TOOL_CALL_SCENARIOS.append(
        _Scenario(
            case_id=case_id,
            description=description,
            tool_name=tool_name,
            tool_arguments=tool_arguments,
            allowed_tools=allowed_tools,
            expected_success=expected_success,
        )
    )


# --- 类 A：成功 × 4 ---
for idx, payload in enumerate(["hi", "ok", "你好", "x" * 20], start=1):
    _add(
        case_id=f"tool-success-{idx:02d}",
        description=f"fake_echo 成功调用 payload={payload!r}",
        tool_name="fake_echo",
        tool_arguments=json.dumps({"text": payload}),
        allowed_tools=None,
        expected_success=True,
    )

# --- 类 B：返回空串 × 4 ---
for idx in range(1, 5):
    _add(
        case_id=f"tool-empty-{idx:02d}",
        description="fake_echo 返回空字符串（分子判据反例）",
        tool_name="fake_echo",
        tool_arguments=json.dumps({"text": ""}),
        allowed_tools=None,
        expected_success=False,
    )

# --- 类 C：权限拒绝 × 4 ---
# 通过把 allowed_tool_names 收窄为空集或不含 fake_echo，让 ReAct Loop 在执行前
# 触发 ToolPermissionDeniedError。
for idx, allowed in enumerate(
    [frozenset(), frozenset(), frozenset({"fake_fail"}), frozenset({"fake_fail"})],
    start=1,
):
    _add(
        case_id=f"tool-permission-{idx:02d}",
        description="fake_echo 被 ScopedToolRegistry 作用域拒绝",
        tool_name="fake_echo",
        tool_arguments=json.dumps({"text": "forbidden"}),
        allowed_tools=allowed,
        expected_success=False,
    )

# --- 类 D：未知工具 × 4 ---
# 模型返回未注册的工具名；AgentConfig 允许该名（避免被 permission 提前拦截），
# 在 ToolRegistry.execute 处触发 ToolNotFoundError。
for idx in range(1, 5):
    _add(
        case_id=f"tool-unknown-{idx:02d}",
        description="nonexistent_tool 未在 ToolRegistry 注册",
        tool_name="nonexistent_tool",
        tool_arguments=json.dumps({"any": "payload"}),
        allowed_tools=frozenset({"fake_echo", "fake_fail", "nonexistent_tool"}),
        expected_success=False,
    )

# --- 类 E：执行异常 × 4 ---
for idx in range(1, 5):
    _add(
        case_id=f"tool-execerror-{idx:02d}",
        description="fake_fail 抛 ToolExecutionError",
        tool_name="fake_fail",
        tool_arguments=json.dumps({}),
        allowed_tools=None,
        expected_success=False,
    )


TOOL_CALL_CASES: list[EvalCase] = [
    EvalCase(
        case_id=s.case_id,
        metric=MetricId.TOOL_CALL_SUCCESS_RATE,
        description=s.description,
        inputs={"scenario": s},
        expected={"success": s.expected_success},
    )
    for s in TOOL_CALL_SCENARIOS
]


# ---------------------------------------------------------------------------
# 样本驱动
# ---------------------------------------------------------------------------


def _build_scripted_responses(scenario: _Scenario) -> list[LLMResponse]:
    """按场景构造桩模型的两轮响应脚本。

    - 第 1 轮：返回单个 :class:`ToolCallRequest`，其 ``name`` / ``arguments``
      来自场景参数。
    - 第 2 轮：返回无 ``tool_calls`` 的 ``LLMResponse`` 以终止 Agent Loop。

    Args:
        scenario: 样本场景。

    Returns:
        两条 :class:`LLMResponse`，按 FIFO 消费。
    """

    return [
        LLMResponse(
            content="",
            model="scripted-round-1",
            tool_calls=[
                ToolCallRequest(
                    id=f"call-{scenario.case_id}",
                    name=scenario.tool_name,
                    arguments=scenario.tool_arguments,
                )
            ],
        ),
        LLMResponse(content="done", model="scripted-round-2"),
    ]


def _build_tool_registry() -> ToolRegistry:
    """构造注册了 :class:`FakeEchoTool` 与 :class:`FakeFailingTool` 的
    :class:`ToolRegistry`。

    Returns:
        初始化完毕的 :class:`ToolRegistry` 实例。
    """

    registry = ToolRegistry()
    registry.register(FakeEchoTool())
    registry.register(FakeFailingTool())
    return registry


def _error_keywords() -> list[str]:
    """返回工具异常类消息中出现的中文关键词，供反向判定使用。

    Returns:
        关键词列表；:class:`ToolMessage.content` 中出现其中任何一个即视为命中
        工具异常路径，分子贡献为 0。
    """

    return ["未找到", "未授权", "模拟执行失败"]


async def _run_agent_once(scenario: _Scenario) -> tuple[int, int, dict[str, Any]]:
    """驱动 :class:`ReActAgentAdapter` 跑一轮 + 终止轮，返回样本的分子、分母与详情。

    Args:
        scenario: 样本场景。

    Returns:
        ``(numerator, denominator, details)``：
        - ``numerator``：0/1，按"未命中异常关键词且 content 长度 > 0"判定；
        - ``denominator``：恒为 1（本指标每样本观察一次 tool_call）；
        - ``details``：供报告展示的调试字段（工具名、ToolMessage.content 等）。
    """

    context_builder = build_context_builder(max_messages=50)
    registry = _build_tool_registry()
    model_access = ScriptedModelAccess(
        scripted_responses=_build_scripted_responses(scenario)
    )

    adapter = ReActAgentAdapter(tool_registry=registry, context_builder=context_builder)

    context = ConversationContext()
    context.add_user_message("请执行工具调用。")

    # 决定 allowed_tool_names：
    # - None → 允许 registry 全部注册工具（fake_echo / fake_fail）；
    # - 其它 frozenset → 原样透传给 AgentConfig。
    if scenario.allowed_tools is None:
        allowed = frozenset({"fake_echo", "fake_fail"})
    else:
        allowed = scenario.allowed_tools

    config = AgentConfig(
        system_prompt="评测指标 1：工具调用成功率样本。",
        tool_schemas=registry.get_schemas(),
        model=None,
        max_rounds=2,
        prompt_id="eval-tool-call@v1",
        allowed_tool_names=allowed,
    )

    await adapter.run(context, config, model_access)

    # 从上下文末尾寻找 ToolMessage（ReAct 会把工具执行结果作为 ToolMessage 追加）。
    tool_messages = [m for m in context.get_messages() if isinstance(m, ToolMessage)]
    if not tool_messages:
        # 异常路径兜底：未观察到工具结果，计为分母 1 / 分子 0。
        return 0, 1, {"reason": "没有 ToolMessage 被回写"}

    last = tool_messages[-1]
    content = last.content
    keywords = _error_keywords()
    hit_error = any(kw in content for kw in keywords)
    numerator = 0 if hit_error or len(content) == 0 else 1
    return (
        numerator,
        1,
        {
            "tool_name": last.tool_name,
            "tool_message_content": content[:200],
            "hit_error_keyword": hit_error,
        },
    )


# ---------------------------------------------------------------------------
# pytest 用例
# ---------------------------------------------------------------------------


@pytest.mark.evaluation
@pytest.mark.parametrize(
    "case",
    TOOL_CALL_CASES,
    ids=[c.case_id for c in TOOL_CALL_CASES],
)
def test_tool_call_success_rate(
    case: EvalCase, sample_sink: SampleSink
) -> None:
    """驱动一轮 ReAct Agent Loop，回传 :class:`EvalSampleResult`。

    样本失败时由 :class:`ReActAgentAdapter` 自身吸收工具异常、写入 ToolMessage；
    若本函数自身抛异常（桩配置错、真实 Adapter 崩溃），Runner 不做 try-except
    吞吐，样本会被 pytest 报为 ERROR 并最终体现为 fail 计数。
    """

    scenario = case.inputs["scenario"]
    expected_success: bool = case.expected["success"]
    numerator, denominator, details = asyncio.run(_run_agent_once(scenario))
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
