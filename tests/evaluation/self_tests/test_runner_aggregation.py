"""``EvalRunner.aggregate()`` 聚合逻辑自测。

覆盖点：
- PASS / FAIL / ERROR 混合样本的分子分母求和；
- ``ratio = numerator_sum / denominator_sum`` 的精度；
- ``failed_samples`` = 非 PASS 样本数、``error_samples`` = ERROR 样本数；
- 分母为 0 时 ``ratio = 0.0`` 的防御分支；
- 无样本 metric 的零值占位（返回元组顺序稳定）。

对应 Property 3；覆盖需求 5.5。
"""

from __future__ import annotations

from pathlib import Path

from tests.evaluation.runner.models import (
    DimensionMetric,
    EvalSampleResult,
    MetricId,
    SampleOutcome,
)
from tests.evaluation.runner.runner import EvalRunner, RunnerConfig


def _runner(tmp_path: Path) -> EvalRunner:
    """构造使用临时目录的 Runner 实例。"""

    return EvalRunner(RunnerConfig(output_dir=tmp_path))


def _sample(
    case_id: str,
    metric: MetricId,
    outcome: SampleOutcome,
    numerator: int,
    denominator: int,
) -> EvalSampleResult:
    """便捷构造样本。"""

    return EvalSampleResult(
        case_id=case_id,
        metric=metric,
        outcome=outcome,
        numerator=numerator,
        denominator=denominator,
    )


def test_aggregate_mixed_outcomes(tmp_path: Path) -> None:
    """PASS/FAIL/ERROR 混合样本的分类计数与 ratio 精度。"""

    runner = _runner(tmp_path)
    samples = [
        _sample("tcsr-1", MetricId.TOOL_CALL_SUCCESS_RATE, SampleOutcome.PASS, 3, 3),
        _sample("tcsr-2", MetricId.TOOL_CALL_SUCCESS_RATE, SampleOutcome.FAIL, 1, 2),
        _sample("tcsr-3", MetricId.TOOL_CALL_SUCCESS_RATE, SampleOutcome.ERROR, 0, 0),
    ]

    metrics = runner.aggregate(samples)
    by_id = {m.metric: m for m in metrics}

    tcsr = by_id[MetricId.TOOL_CALL_SUCCESS_RATE]
    assert tcsr.sample_count == 3
    assert tcsr.numerator_sum == 4
    assert tcsr.denominator_sum == 5
    # 4/5 = 0.8，精度应严格等于 0.8（分子分母均为精确整数）。
    assert tcsr.ratio == 0.8
    assert tcsr.failed_samples == 2  # FAIL + ERROR
    assert tcsr.error_samples == 1


def test_aggregate_denominator_zero_defaults_ratio_to_zero(tmp_path: Path) -> None:
    """分母为 0 时 ratio 回退为 0.0，不触发 ZeroDivisionError。"""

    runner = _runner(tmp_path)
    samples = [
        _sample("d-1", MetricId.DELEGATION_CORRECTNESS, SampleOutcome.ERROR, 0, 0),
        _sample("d-2", MetricId.DELEGATION_CORRECTNESS, SampleOutcome.ERROR, 0, 0),
    ]

    metrics = runner.aggregate(samples)
    by_id = {m.metric: m for m in metrics}

    dc = by_id[MetricId.DELEGATION_CORRECTNESS]
    assert dc.sample_count == 2
    assert dc.numerator_sum == 0
    assert dc.denominator_sum == 0
    assert dc.ratio == 0.0
    assert dc.failed_samples == 2
    assert dc.error_samples == 2


def test_aggregate_returns_all_metrics_in_stable_order(tmp_path: Path) -> None:
    """即使某 metric 无样本，聚合结果也包含该 metric 的零值占位。"""

    runner = _runner(tmp_path)
    samples = [
        _sample(
            "c-1",
            MetricId.CONTEXT_COMPACTION_EFFECTIVENESS,
            SampleOutcome.PASS,
            1,
            1,
        ),
    ]

    metrics = runner.aggregate(samples)

    # 结果按 MetricId 枚举顺序返回。
    ordered_ids = [m.metric for m in metrics]
    assert ordered_ids == list(MetricId)

    # 未提供样本的 metric 为全零 DimensionMetric。
    non_observed = {
        m.metric: m
        for m in metrics
        if m.metric != MetricId.CONTEXT_COMPACTION_EFFECTIVENESS
    }
    for m in non_observed.values():
        assert isinstance(m, DimensionMetric)
        assert m.sample_count == 0
        assert m.numerator_sum == 0
        assert m.denominator_sum == 0
        assert m.ratio == 0.0
        assert m.failed_samples == 0
        assert m.error_samples == 0


def test_aggregate_ignores_unknown_metric(tmp_path: Path) -> None:
    """聚合时只按 MetricId 枚举成员分组，异常 metric 不污染（防御用例）。"""

    runner = _runner(tmp_path)
    # 所有 metric 都是 Enum，本用例验证空样本列表也返回 3 个 DimensionMetric。
    metrics = runner.aggregate([])
    assert len(metrics) == len(list(MetricId))
    assert all(m.sample_count == 0 for m in metrics)
