"""回归对比工具：对比两份 EvalResult JSON，差值越过阈值则以退出码 2 报错。

用法：
    uv run python -m scripts.evaluation.compare_baseline \\
        --baseline=docs/evaluation/results/2026-05-01.json \\
        --latest=docs/evaluation/results/2026-05-12.json \\
        --threshold=5.0

退出码：
    0 — 成功（未触发回归或基线文件不存在）。
    1 — 脚本自身异常。
    2 — 指标回退超阈值。
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RegressionReport:
    """单项指标的回归对比结果。

    Attributes:
        metric: 指标名称（``MetricId.value``）。
        baseline_ratio: 基线 ratio。
        latest_ratio: 最新 ratio。
        delta_pp: ``(latest - baseline) * 100``，单位百分点。
        violated: ``delta_pp <= -threshold`` 时为 ``True``。
    """

    metric: str
    baseline_ratio: float
    latest_ratio: float
    delta_pp: float
    violated: bool


def compare(
    baseline_path: Path, latest_path: Path, threshold: float
) -> list[RegressionReport]:
    """对比基线与最新 JSON，返回逐指标回归报告。

    Args:
        baseline_path: 基线 JSON 文件路径。
        latest_path: 最新 JSON 文件路径。
        threshold: 回退百分点阈值。

    Returns:
        若基线文件不存在，返回空列表（首次运行允许）。
        否则返回按指标名排序的 :class:`RegressionReport` 列表。
    """

    if not baseline_path.exists():
        return []

    baseline_data = json.loads(baseline_path.read_text(encoding="utf-8"))
    latest_data = json.loads(latest_path.read_text(encoding="utf-8"))

    baseline_metrics = {m["metric"]: m["ratio"] for m in baseline_data["metrics"]}
    latest_metrics = {m["metric"]: m["ratio"] for m in latest_data["metrics"]}

    reports: list[RegressionReport] = []
    for metric_name in sorted(set(baseline_metrics) | set(latest_metrics)):
        b_ratio = baseline_metrics.get(metric_name, 0.0)
        l_ratio = latest_metrics.get(metric_name, 0.0)
        delta_pp = (l_ratio - b_ratio) * 100
        violated = delta_pp <= -threshold
        reports.append(
            RegressionReport(
                metric=metric_name,
                baseline_ratio=b_ratio,
                latest_ratio=l_ratio,
                delta_pp=delta_pp,
                violated=violated,
            )
        )

    return reports


def main(argv: list[str] | None = None) -> int:
    """CLI 入口：解析参数、执行回归对比、打印结果并返回退出码。

    Args:
        argv: 命令行参数列表；``None`` 时使用 ``sys.argv[1:]``。

    Returns:
        退出码：0 成功 / 1 异常 / 2 触发回归。
    """

    parser = argparse.ArgumentParser(
        prog="compare_baseline",
        description="评测回归对比工具",
    )
    parser.add_argument("--baseline", required=True, help="基线 JSON 路径")
    parser.add_argument("--latest", required=True, help="最新 JSON 路径")
    parser.add_argument(
        "--threshold", type=float, default=5.0, help="回退百分点阈值（默认 5.0）"
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 1

    baseline_path = Path(args.baseline)
    latest_path = Path(args.latest)

    if not baseline_path.exists():
        print(
            f"[compare_baseline] 基线文件不存在：{baseline_path}。"
            "首次运行，跳过回归对比。"
        )
        return 0

    if not latest_path.exists():
        print(
            f"[compare_baseline] 最新文件不存在：{latest_path}",
            file=sys.stderr,
        )
        return 1

    try:
        reports = compare(baseline_path, latest_path, args.threshold)
    except Exception as exc:
        print(f"[compare_baseline] 脚本异常：{exc}", file=sys.stderr)
        return 1

    violated_any = any(r.violated for r in reports)

    header = f"{'指标':<40}{'基线':<12}{'最新':<12}{'Δpp':<12}{'状态'}"
    print(header)
    print("-" * 80)
    for r in reports:
        status = "VIOLATED" if r.violated else "OK"
        sign = "+" if r.delta_pp >= 0 else ""
        print(
            f"{r.metric:<40}{r.baseline_ratio:<12.4f}"
            f"{r.latest_ratio:<12.4f}{sign}{r.delta_pp:<11.4f} {status}"
        )

    if violated_any:
        print("\n[compare_baseline] 触发回归：存在指标回退超阈值。")
        return 2
    else:
        print("\n[compare_baseline] 未触发回归阈值，全部指标在安全范围内。")
        return 0


if __name__ == "__main__":
    sys.exit(main())
