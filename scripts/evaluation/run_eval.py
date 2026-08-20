"""评测主入口：驱动 pytest 收集样本、聚合指标、写 JSON、可选回归对比。

用法（均需在仓库根 /workspace 下执行）：
    uv run python -m scripts.evaluation.run_eval --metric=all
    uv run python -m scripts.evaluation.run_eval --metric=tool_call_success_rate
    uv run python -m scripts.evaluation.run_eval --baseline=docs/evaluation/results/2026-05-01.json

退出码：
    0 — 运行成功且（若提供 baseline）未触发回归。
    1 — 脚本自身异常（参数非法、路径不可写、桩实现崩溃等）。
    2 — 指标相对基线回退 ≥ 阈值。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from tests.evaluation.runner.models import MetricId
from tests.evaluation.runner.runner import EvalRunner, RunnerConfig


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数。"""

    parser = argparse.ArgumentParser(
        prog="run_eval",
        description="AI Agent 评测主入口",
    )
    parser.add_argument(
        "--metric",
        default="all",
        help="选定指标（all 或具体 MetricId value）",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="结果 JSON 输出路径（默认 docs/evaluation/results/<run_id>.json）",
    )
    parser.add_argument(
        "--baseline",
        default=None,
        help="回归对比基线 JSON 路径",
    )
    parser.add_argument(
        "--regression-threshold",
        type=float,
        default=5.0,
        help="回退百分点阈值（默认 5.0）",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """解析 CLI 参数，构造 RunnerConfig，执行评测，返回退出码。

    Args:
        argv: 命令行参数列表；``None`` 时使用 ``sys.argv[1:]``。

    Returns:
        退出码：0 成功 / 1 脚本异常 / 2 回归触发。
    """

    try:
        args = _parse_args(argv)
    except SystemExit:
        return 1

    repo_root = Path(__file__).resolve().parents[2]

    selected_metrics: frozenset[MetricId] | None = None
    if args.metric != "all":
        try:
            selected_metrics = frozenset([MetricId(args.metric)])
        except ValueError:
            print(f"[run_eval] 错误：未知指标 '{args.metric}'", file=sys.stderr)
            return 1

    output_dir = repo_root / "docs" / "evaluation" / "results"
    baseline_path = Path(args.baseline) if args.baseline else None

    config = RunnerConfig(
        output_dir=output_dir,
        baseline_path=baseline_path,
        regression_threshold=args.regression_threshold,
        selected_metrics=selected_metrics,
        metrics_test_path=repo_root / "tests" / "evaluation" / "metrics",
        rootdir=repo_root,
    )

    try:
        runner = EvalRunner(config)
        result = runner.run()
    except Exception as exc:
        print(f"[run_eval] 脚本异常：{exc}", file=sys.stderr)
        return 1

    if result.exit_code != 0:
        print(f"[run_eval] Runner 内部异常（exit_code={result.exit_code}）", file=sys.stderr)
        return 1

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        import json
        payload = result.to_dict()
        out_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        json_path = out_path
    else:
        json_path = runner.write_json(result)

    print(f"[run_eval] 结果已写入：{json_path}")

    _print_summary(result)

    if baseline_path:
        from scripts.evaluation.compare_baseline import compare

        reports = compare(baseline_path, json_path, args.regression_threshold)
        if not reports:
            print("\n[run_eval] 回归对比：基线文件不存在，跳过（首次运行）。")
            return 0

        print("\n[run_eval] 回归对比：")
        header = f"{'指标':<40}{'基线':<12}{'最新':<12}{'Δpp':<12}{'状态'}"
        print(header)
        print("-" * 80)
        violated = False
        for r in reports:
            status = "VIOLATED" if r.violated else "OK"
            sign = "+" if r.delta_pp >= 0 else ""
            print(
                f"{r.metric:<40}{r.baseline_ratio:<12.4f}"
                f"{r.latest_ratio:<12.4f}{sign}{r.delta_pp:<11.4f} {status}"
            )
            if r.violated:
                violated = True

        if violated:
            return 2

    return 0


def _print_summary(result) -> None:
    """打印指标摘要表。"""

    header = (
        f"{'指标':<40}{'分子':<12}{'分母':<12}{'比率':<14}"
        f"{'样本数':<10}{'失败':<8}{'错误'}"
    )
    print(header)
    print("-" * 94)
    for m in result.metrics:
        print(
            f"{m.metric.value:<40}{m.numerator_sum:<12}"
            f"{m.denominator_sum:<12}{m.ratio:<14.4f}"
            f"{m.sample_count:<10}{m.failed_samples:<8}{m.error_samples}"
        )


if __name__ == "__main__":
    sys.exit(main())
