"""指标 2 元测试：验证 Delegation_Correctness 的样本分类会计逻辑。

验证策略（不依赖真实 Adapter，直接喂给 :class:`EvalRunner.aggregate`）：

- 构造三类样本：
  1. **正常委派**：PASS（分子 1 / 分母 1）；
  2. **目标正确但深度越限**：FAIL（分子 0 / 分母 1）；
  3. **目标不存在**：ERROR（分子 0 / 分母 0）—— 对应评测脚本把样本 driver
     自身抛出的异常包装为 :class:`SampleExecutionError` 并写入
     ``EvalSampleResult(outcome=ERROR)``；
- 断言 :class:`DimensionMetric.failed_samples` 与 ``error_samples`` 分类正确。

对应 Property 9（委派深度不超限判据），以及需求 10.2"脚本对样本异常的处理"。
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
def test_aggregate_delegation_mixed_outcomes(tmp_path: Path) -> None:
    """构造 3 类混合样本，断言聚合字段分类正确。

    样本分布：
        - 1 条 PASS（分子 1 / 分母 1）：目标正确、深度未越限、内容拼回成功；
        - 1 条 FAIL（分子 0 / 分母 1）：目标正确但深度越限，被深度闸门拒绝；
        - 1 条 ERROR（分子 0 / 分母 0）：目标不存在，driver 抛出异常被包装。

    断言：
        - ``sample_count == 3``；
        - ``numerator_sum == 1``；
        - ``denominator_sum == 2``（ERROR 样本 denominator=0 不污染分母）；
        - ``ratio ≈ 0.5``；
        - ``failed_samples == 2``（FAIL + ERROR 均计入失败）；
        - ``error_samples == 1``。
    """

    runner = EvalRunner(RunnerConfig(output_dir=tmp_path))
    metric = MetricId.DELEGATION_CORRECTNESS

    samples = [
        EvalSampleResult(
            case_id="meta-delegation-pass",
            metric=metric,
            outcome=SampleOutcome.PASS,
            numerator=1,
            denominator=1,
            details={"kind": "success"},
        ),
        EvalSampleResult(
            case_id="meta-delegation-depth-exceeded",
            metric=metric,
            outcome=SampleOutcome.FAIL,
            numerator=0,
            denominator=1,
            details={"kind": "depth_exceeded"},
        ),
        EvalSampleResult(
            case_id="meta-delegation-not-found",
            metric=metric,
            outcome=SampleOutcome.ERROR,
            numerator=0,
            denominator=0,
            details={"kind": "not_found"},
            error="评测样本 'meta-delegation-not-found' 执行异常：AgentNotFoundError",
        ),
    ]

    metrics = runner.aggregate(samples)
    by_metric = {m.metric: m for m in metrics}
    target = by_metric[metric]

    assert target.sample_count == 3
    assert target.numerator_sum == 1
    assert target.denominator_sum == 2
    assert math.isclose(target.ratio, 0.5, rel_tol=1e-9, abs_tol=1e-9)
    assert target.failed_samples == 2
    assert target.error_samples == 1
