"""评测端到端回归集成测试。

三步流程：

1. ``run_eval.main(["--metric=all", "--output=<tmp>/first.json"])`` →
   断言 ``exit_code=0``、JSON schema 字段齐全；
2. 以 ``first.json`` 为基线复跑 ``run_eval`` →断言 ``exit_code=0``；
3. 篡改 ``first.json`` 的 ``numerator_sum`` 使 ratio 低 6pp，再调用
   :func:`scripts.evaluation.compare_baseline.main` → 断言 ``exit_code=2``。

对应 Property 3、Property 7；需求 5.5、10.4。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.evaluation.compare_baseline import main as compare_main
from scripts.evaluation.run_eval import main as run_eval_main

from tests.evaluation.runner.models import MetricId

pytestmark = pytest.mark.evaluation_self


_REQUIRED_TOP_FIELDS = {
    "run_id",
    "generated_at",
    "git_commit",
    "metrics",
    "dimension_scores",
    "total_score",
    "exit_code",
}

_REQUIRED_METRIC_FIELDS = {
    "metric",
    "sample_count",
    "numerator_sum",
    "denominator_sum",
    "ratio",
    "failed_samples",
    "error_samples",
}


def test_end_to_end_run_then_baseline_then_regression(tmp_path: Path) -> None:
    """跑一次 → 作基线复跑 → 篡改触发回归。"""

    first_path = tmp_path / "first.json"
    tampered_path = tmp_path / "first_tampered.json"
    second_path = tmp_path / "second.json"

    # 1. 首次运行
    exit_code = run_eval_main(
        ["--metric=all", "--output", str(first_path)]
    )
    assert exit_code == 0
    assert first_path.exists()

    payload = json.loads(first_path.read_text(encoding="utf-8"))
    assert _REQUIRED_TOP_FIELDS.issubset(payload.keys()), (
        f"JSON 顶层缺字段：需 {_REQUIRED_TOP_FIELDS}，实有 {set(payload.keys())}"
    )
    for metric in payload["metrics"]:
        assert _REQUIRED_METRIC_FIELDS.issubset(metric.keys()), (
            f"指标缺字段：{set(metric.keys())}"
        )

    # 2. 以 first 为基线复跑，应 exit_code=0（无回退）
    exit_code_second = run_eval_main(
        [
            "--metric=all",
            "--output",
            str(second_path),
            "--baseline",
            str(first_path),
            "--regression-threshold",
            "5.0",
        ]
    )
    assert exit_code_second == 0

    # 3. 篡改 first.json 使某指标 ratio 低 6pp（构造"最新 - 基线 = -6pp"）
    # 做法：把基线版本中某指标的 ratio 人为提高到 (当前 + 0.06)，
    # 这样再次以此为基线做回归时，最新 (current) 相对它下降 6pp。
    tampered_payload = json.loads(json.dumps(payload))  # 深拷贝
    target_metric = tampered_payload["metrics"][0]
    # 将基线 ratio 人为拔高 +0.06（对应分子+0.6 * denom，取整重新计算）
    new_ratio = min(1.0, target_metric["ratio"] + 0.06)
    target_metric["ratio"] = new_ratio
    # numerator_sum 同步（虽然回归脚本只读 ratio 字段，但保持一致更真实）
    target_metric["numerator_sum"] = int(
        round(new_ratio * max(target_metric["denominator_sum"], 1))
    )
    tampered_path.write_text(
        json.dumps(tampered_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    exit_code_compare = compare_main(
        [
            "--baseline",
            str(tampered_path),
            "--latest",
            str(first_path),
            "--threshold",
            "5.0",
        ]
    )
    assert exit_code_compare == 2


def test_run_eval_single_metric_writes_json(tmp_path: Path) -> None:
    """单指标运行也应产出合法 JSON，指标列表含所有 MetricId 占位。"""

    out = tmp_path / "single.json"
    exit_code = run_eval_main(
        [
            "--metric=tool_call_success_rate",
            "--output",
            str(out),
        ]
    )
    assert exit_code == 0
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    # Runner.aggregate 按 MetricId 顺序返回所有指标。
    assert len(payload["metrics"]) == len(list(MetricId))
    # 未选中的指标应为零值占位
    others = [
        m for m in payload["metrics"]
        if m["metric"] != "tool_call_success_rate"
    ]
    for m in others:
        assert m["sample_count"] == 0
        assert m["ratio"] == 0.0
