"""指标 1 元测试：验证 Tool_Call_Success_Rate 聚合会计逻辑。

验证策略：
- 直接构造固定的 ``3 成功 / 1 失败 / 1 权限拒绝`` 样本列表；
- 调用 :class:`EvalRunner.aggregate` 聚合为 :class:`DimensionMetric`；
- 断言 ``numerator_sum=3``、``denominator_sum=5``、``ratio ≈ 0.6``。

本测试不依赖真实 Adapter，也不触发 pytest 收集指标样本，避免与
``@pytest.mark.evaluation`` 标记下的样本相互污染；统一使用
``@pytest.mark.evaluation_self`` 标记以便 CI 过滤。
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from tests.evaluation.runner.models import (
    EvalSampleResult,
    MetricId,
    SampleOutcome,
)
from tests.evaluation.runner.runner import EvalRunner, RunnerConfig


@pytest.mark.evaluation_self
def test_aggregate_3_pass_1_fail_1_permission_denied(tmp_path: Path) -> None:
    """构造 5 条样本，断言 ``numerator=3 / denominator=5 / ratio≈0.6``。

    样本分布：
    - 3 条 PASS（分子 1 / 分母 1）：对应"正常 fake_echo 成功"；
    - 1 条 FAIL（分子 0 / 分母 1）：对应"fake_fail 抛 ToolExecutionError"；
    - 1 条 FAIL（分子 0 / 分母 1）：对应"ScopedToolRegistry 拒绝"。

    ``DimensionMetric.failed_samples`` 应当为 2；``error_samples`` 应当为 0
    （PermissionDenied 与 ExecutionError 都被 Adapter 转写为 FAIL，而非 ERROR）。
    """

    runner = EvalRunner(RunnerConfig(output_dir=tmp_path))

    metric = MetricId.TOOL_CALL_SUCCESS_RATE
    samples = [
        EvalSampleResult(
            case_id=f"meta-success-{i}",
            metric=metric,
            outcome=SampleOutcome.PASS,
            numerator=1,
            denominator=1,
            details={"kind": "success"},
        )
        for i in range(3)
    ]
    samples.append(
        EvalSampleResult(
            case_id="meta-exec-error",
            metric=metric,
            outcome=SampleOutcome.FAIL,
            numerator=0,
            denominator=1,
            details={"kind": "execution-error"},
        )
    )
    samples.append(
        EvalSampleResult(
            case_id="meta-permission-denied",
            metric=metric,
            outcome=SampleOutcome.FAIL,
            numerator=0,
            denominator=1,
            details={"kind": "permission-denied"},
        )
    )

    metrics = runner.aggregate(samples)
    by_metric = {m.metric: m for m in metrics}
    target = by_metric[metric]

    assert target.sample_count == 5
    assert target.numerator_sum == 3
    assert target.denominator_sum == 5
    assert math.isclose(target.ratio, 0.6, rel_tol=1e-9, abs_tol=1e-9)
    assert target.failed_samples == 2
    assert target.error_samples == 0
