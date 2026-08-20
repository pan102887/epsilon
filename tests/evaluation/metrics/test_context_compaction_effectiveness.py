"""指标 3：Context_Compaction_Effectiveness（上下文压缩有效性）评测样本。

判定规则（严格对齐 ``docs/spec/spec-ai-evaluation/tasks.md`` 阶段 3.5，成功
必须三项全部通过）：

(a) 压缩后 :class:`SystemMessage` 数量 = ``S``；
(b) 压缩后非 system 消息数量 = ``min(L - S, N)``；
(c) 保留的非 system 消息为原序列非 system 子列的末尾 ``N`` 条，保持原始顺序。

被测目标：直接调用 :class:`SlidingWindowCompactionAdapter.compact_messages`（真实
Adapter），不经过 ReAct Loop。构造 :class:`BaseMessage` 序列时使用
"SystemMessage × S + 交错 UserMessage / AssistantMessage × (L - S)" 的固定
模式，便于判定 (c) 的"末尾 N 条"比对。

参数化组合（合计 ≥ 30）：
    ``L ∈ {10, 20, 50, 100}`` × ``S ∈ {0, 1, 3}`` × ``N ∈ {5, 10, 20}``
    = 36 条样本，满足 tasks.md "≥ 30" 要求。无效组合（``L < S``）在生成时
    过滤，剩余 36 条全部合法。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from domain.chat.context import (
    AssistantMessage,
    BaseMessage,
    SystemMessage,
    UserMessage,
)
from infrastructure.chat.sliding_window_compaction_adapter import (
    SlidingWindowCompactionAdapter,
)

from tests.evaluation.runner.models import (
    EvalCase,
    EvalSampleResult,
    MetricId,
    SampleOutcome,
)
from tests.evaluation.runner.sample_sink import SampleSink


# ---------------------------------------------------------------------------
# 样本参数与消息构造
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _CompactionScenario:
    """单条压缩样本的参数。

    Attributes:
        case_id: 样本唯一标识。
        total_length: 原始消息总条数 ``L``。
        system_count: SystemMessage 条数 ``S``。
        window_n: 滑动窗口保留的非 system 消息数 ``N``。
    """

    case_id: str
    total_length: int
    system_count: int
    window_n: int


def _build_messages(total_length: int, system_count: int) -> list[BaseMessage]:
    """构造长度为 ``total_length`` 的消息序列。

    - 前 ``system_count`` 条为 :class:`SystemMessage`，content 形如
      ``"system-00"``、``"system-01"``…以便比对保留；
    - 其余为交错的 :class:`UserMessage` / :class:`AssistantMessage`，content
      形如 ``"user-00"`` / ``"assistant-01"``，保证 ``content`` 唯一，可用
      于逐字符顺序校验。

    Args:
        total_length: 原始消息总条数。
        system_count: 放在最前的 SystemMessage 条数。

    Returns:
        长度为 ``total_length`` 的 :class:`BaseMessage` 列表。
    """

    messages: list[BaseMessage] = []
    for idx in range(system_count):
        messages.append(SystemMessage(content=f"system-{idx:02d}"))
    for idx in range(total_length - system_count):
        if idx % 2 == 0:
            messages.append(UserMessage(content=f"user-{idx:02d}"))
        else:
            messages.append(AssistantMessage(content=f"assistant-{idx:02d}"))
    return messages


COMPACTION_SCENARIOS: list[_CompactionScenario] = []

_L_VALUES = (10, 20, 50, 100)
_S_VALUES = (0, 1, 3)
_N_VALUES = (5, 10, 20)

for _L in _L_VALUES:
    for _S in _S_VALUES:
        for _N in _N_VALUES:
            if _L < _S:
                continue  # 非法组合：原序列不足以容纳 S 条 system
            COMPACTION_SCENARIOS.append(
                _CompactionScenario(
                    case_id=f"compaction-L{_L:03d}-S{_S}-N{_N:02d}",
                    total_length=_L,
                    system_count=_S,
                    window_n=_N,
                )
            )


COMPACTION_CASES: list[EvalCase] = [
    EvalCase(
        case_id=s.case_id,
        metric=MetricId.CONTEXT_COMPACTION_EFFECTIVENESS,
        description=(
            f"L={s.total_length}, S={s.system_count}, N={s.window_n} "
            "的滑动窗口压缩判据校验"
        ),
        inputs={"scenario": s},
        expected={"all_three_criteria_pass": True},
    )
    for s in COMPACTION_SCENARIOS
]


# ---------------------------------------------------------------------------
# 样本驱动（同步，无需 asyncio）
# ---------------------------------------------------------------------------


def _evaluate_scenario(scenario: _CompactionScenario) -> tuple[int, int, dict[str, Any]]:
    """对单条样本执行压缩并按三项判据计算分子、分母与细节。

    Args:
        scenario: 样本参数。

    Returns:
        ``(numerator, denominator, details)``；``denominator`` 恒为 1，
        ``numerator`` ∈ ``{0, 1}``。
    """

    messages = _build_messages(scenario.total_length, scenario.system_count)
    non_system_original = [m for m in messages if m.role != "system"]

    adapter = SlidingWindowCompactionAdapter(max_messages=scenario.window_n)
    compacted = adapter.compact_messages(messages)

    compacted_system = [m for m in compacted if m.role == "system"]
    compacted_non_system = [m for m in compacted if m.role != "system"]

    # (a) SystemMessage 数量保留
    system_count_ok = len(compacted_system) == scenario.system_count

    # (b) 非 system 数量 = min(L - S, N)
    expected_non_system = min(
        scenario.total_length - scenario.system_count, scenario.window_n
    )
    non_system_count_ok = len(compacted_non_system) == expected_non_system

    # (c) 保留的非 system 是原非 system 子列的末尾 N 条，按原始顺序
    expected_tail = (
        non_system_original[-scenario.window_n :]
        if scenario.window_n <= len(non_system_original)
        else list(non_system_original)
    )
    order_ok = [m.content for m in compacted_non_system] == [
        m.content for m in expected_tail
    ]

    passed = bool(system_count_ok and non_system_count_ok and order_ok)
    numerator = 1 if passed else 0
    return (
        numerator,
        1,
        {
            "L": scenario.total_length,
            "S": scenario.system_count,
            "N": scenario.window_n,
            "system_count_ok": system_count_ok,
            "non_system_count_ok": non_system_count_ok,
            "order_ok": order_ok,
            "compacted_length": len(compacted),
            "expected_non_system_count": expected_non_system,
        },
    )


# ---------------------------------------------------------------------------
# pytest 用例
# ---------------------------------------------------------------------------


@pytest.mark.evaluation
@pytest.mark.parametrize(
    "case",
    COMPACTION_CASES,
    ids=[c.case_id for c in COMPACTION_CASES],
)
def test_context_compaction_effectiveness(
    case: EvalCase, sample_sink: SampleSink
) -> None:
    """对单条 ``(L, S, N)`` 组合调用 :class:`SlidingWindowCompactionAdapter`
    并回传 :class:`EvalSampleResult`。

    - 所有样本 ``expected_success=True``：三项判据任意一项失败即记为 FAIL。
    - 不依赖 :mod:`asyncio`；:class:`SlidingWindowCompactionAdapter.compact_messages`
      为同步方法。

    Args:
        case: :class:`EvalCase` 实例。
        sample_sink: 会话级样本收集器 fixture。
    """

    scenario = case.inputs["scenario"]
    numerator, denominator, details = _evaluate_scenario(scenario)
    outcome = SampleOutcome.PASS if numerator == 1 else SampleOutcome.FAIL
    sample_sink.append(
        EvalSampleResult(
            case_id=case.case_id,
            metric=case.metric,
            outcome=outcome,
            numerator=numerator,
            denominator=denominator,
            details=details,
        )
    )
