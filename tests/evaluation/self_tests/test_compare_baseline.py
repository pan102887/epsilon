"""``scripts.evaluation.compare_baseline`` CLI 的自测。

覆盖三种关键场景：

1. 阈值内不触发回归（``delta_pp = -3`` 高于 ``-threshold=5`` → 退出码 0）；
2. 阈值外触发回归（``delta_pp = -6`` 低于 ``-threshold=5`` → 退出码 2）；
3. 基线文件不存在：脚本打印 warning、退出码 0（首次运行允许）。

对应需求 10.4；验证 Property 7。

该测试不标 ``@pytest.mark.evaluation``，以 ``@pytest.mark.evaluation_self``
标记，以便 ``run_eval.py`` 只收集指标样本时不把这些元测试当作样本。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.evaluation.compare_baseline import RegressionReport, compare, main


pytestmark = pytest.mark.evaluation_self


def _write_result_json(path: Path, ratios: dict[str, float]) -> None:
    """把 ``metric → ratio`` 映射落成符合评测 JSON 格式的最小文件。"""

    payload = {
        "run_id": "test",
        "generated_at": "2026-05-12T00:00:00+00:00",
        "git_commit": None,
        "metrics": [
            {
                "metric": metric,
                "sample_count": 10,
                "numerator_sum": int(round(ratio * 10)),
                "denominator_sum": 10,
                "ratio": ratio,
                "failed_samples": 0,
                "error_samples": 0,
            }
            for metric, ratio in ratios.items()
        ],
        "dimension_scores": [],
        "total_score": 0.0,
        "exit_code": 0,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def test_compare_within_threshold_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """delta=-3pp 小幅下降，不触发 5pp 阈值；退出码 0。"""

    baseline = tmp_path / "baseline.json"
    latest = tmp_path / "latest.json"
    _write_result_json(baseline, {"tool_call_success_rate": 0.90})
    _write_result_json(latest, {"tool_call_success_rate": 0.87})

    exit_code = main(
        [
            "--baseline",
            str(baseline),
            "--latest",
            str(latest),
            "--threshold",
            "5.0",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "未触发回归阈值" in captured.out
    # 纯函数接口一致性检查
    reports = compare(baseline, latest, 5.0)
    assert len(reports) == 1
    r: RegressionReport = reports[0]
    assert r.violated is False
    assert r.delta_pp == pytest.approx(-3.0, abs=1e-9)


def test_compare_outside_threshold_returns_two(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """delta=-6pp 超过 5pp 阈值；退出码 2，stdout 含中文"触发回归"。"""

    baseline = tmp_path / "baseline.json"
    latest = tmp_path / "latest.json"
    _write_result_json(baseline, {"tool_call_success_rate": 0.90})
    _write_result_json(latest, {"tool_call_success_rate": 0.84})

    exit_code = main(
        [
            "--baseline",
            str(baseline),
            "--latest",
            str(latest),
            "--threshold",
            "5.0",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "触发回归" in captured.out
    # 纯函数断言
    reports = compare(baseline, latest, 5.0)
    assert len(reports) == 1
    assert reports[0].violated is True


def test_compare_missing_baseline_returns_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """基线文件不存在 → 打印 warning，退出码 0。"""

    baseline = tmp_path / "does-not-exist.json"
    latest = tmp_path / "latest.json"
    _write_result_json(latest, {"tool_call_success_rate": 0.80})

    exit_code = main(
        [
            "--baseline",
            str(baseline),
            "--latest",
            str(latest),
            "--threshold",
            "5.0",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "基线文件不存在" in captured.out
    assert "首次运行" in captured.out


def test_compare_function_missing_baseline_returns_empty(
    tmp_path: Path,
) -> None:
    """纯函数层面：基线缺失 → 返回空列表（不抛异常）。"""

    baseline = tmp_path / "nope.json"
    latest = tmp_path / "latest.json"
    _write_result_json(latest, {"tool_call_success_rate": 0.80})

    reports = compare(baseline, latest, 5.0)
    assert reports == []
