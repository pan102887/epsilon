"""评分聚合与主报告生成器。

读取 ``docs/evaluation/scores.toml``（人工评分源）与最新评测 JSON，
计算加权总分，产出：
- ``docs/evaluation/scores.json``（机器可读聚合结果）
- ``docs/evaluation/report.md``（主报告：保留人工段落，刷新 AUTO 区块）
- ``docs/evaluation/dimensions/<n>-<slug>.md``（七份子报告骨架）

用法：
    uv run python -m scripts.evaluation.aggregate_scores \\
        --result=docs/evaluation/results/2026-05-12_052627_7e9c66c.json

退出码：
    0 — 成功。
    1 — 脚本自身异常。
"""

from __future__ import annotations

import argparse
import json
import sys
import tomllib
from datetime import datetime, timezone
from pathlib import Path


_DIMENSION_SLUGS = {
    "architecture": "1-architecture",
    "agent_core": "2-agent-core",
    "model_prompt": "3-model-prompt",
    "security": "4-security",
    "reliability": "5-reliability",
    "testability": "6-testability",
    "frontend_ux": "7-frontend-ux",
}

_DIMENSION_TITLES = {
    "architecture": "架构与工程化",
    "agent_core": "Agent 核心能力",
    "model_prompt": "模型与提示工程",
    "security": "安全与合规",
    "reliability": "可靠性与性能",
    "testability": "可测试性与质量",
    "frontend_ux": "前端/UX",
}


def _load_weights() -> dict[str, float]:
    """从 rubric 加载维度权重。"""

    from tests.evaluation.rubric.dimensions import load_rubric

    rubric = load_rubric()
    return {dim.id.value: dim.weight for dim in rubric}


def _load_scores(scores_path: Path) -> dict[str, dict]:
    """解析 scores.toml。"""

    with scores_path.open("rb") as fh:
        data = tomllib.load(fh)
    return data


def _compute_total_score(
    scores: dict[str, dict], weights: dict[str, float]
) -> float:
    """计算加权平均总分。"""

    numerator = 0.0
    denominator = 0.0
    for dim_id, weight in weights.items():
        dim_data = scores.get(dim_id, {})
        score = dim_data.get("score", 0)
        numerator += score * weight
        denominator += weight
    if denominator == 0:
        return 0.0
    return numerator / denominator


def _build_scores_json(
    scores: dict[str, dict],
    weights: dict[str, float],
    result_data: dict | None,
) -> dict:
    """构建 scores.json 内容。"""

    total_score = _compute_total_score(scores, weights)

    dimensions = []
    for dim_id in _DIMENSION_SLUGS:
        dim_data = scores.get(dim_id, {})
        dimensions.append({
            "id": dim_id,
            "title": _DIMENSION_TITLES.get(dim_id, dim_id),
            "score": dim_data.get("score", 0),
            "weight": weights.get(dim_id, 0),
            "rationale": dim_data.get("rationale", ""),
            "evidence_refs": dim_data.get("evidence_refs", []),
        })

    metrics = []
    if result_data and "metrics" in result_data:
        metrics = result_data["metrics"]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_score": total_score,
        "dimensions": dimensions,
        "metrics": metrics,
    }


def _auto_block(name: str, body: str) -> str:
    """包装为命名 AUTO 区块。"""

    return f"<!-- AUTO-START: {name} -->\n{body}\n<!-- AUTO-END: {name} -->"


def _replace_named_auto_block(
    original: str, name: str, new_body: str
) -> str:
    """在已有文档中替换命名 AUTO 区块内容。"""

    import re

    pattern = re.compile(
        rf"(<!-- AUTO-START: {re.escape(name)} -->)\n.*?\n(<!-- AUTO-END: {re.escape(name)} -->)",
        re.DOTALL,
    )
    replacement = f"\\1\n{new_body}\n\\2"
    result, count = pattern.subn(replacement, original)
    if count == 0:
        result += "\n" + _auto_block(name, new_body) + "\n"
    return result


def _build_report_header(result_path: str, total_score: float) -> str:
    """构建报告头部 AUTO 区块。"""

    now = datetime.now(timezone.utc).isoformat()
    return (
        f"> 生成时间：{now}\n"
        f"> 对应评测 JSON：`{result_path}`\n"
        f"> 总加权得分：**{total_score:.3f} / 5**"
    )


def _build_scores_table(scores: dict[str, dict], weights: dict[str, float]) -> str:
    """构建评分汇总表。"""

    lines = [
        "| 维度 | 评分 | 权重 | 加权贡献 |",
        "| --- | --- | --- | --- |",
    ]
    for dim_id in _DIMENSION_SLUGS:
        title = _DIMENSION_TITLES.get(dim_id, dim_id)
        dim_data = scores.get(dim_id, {})
        score = dim_data.get("score", 0)
        weight = weights.get(dim_id, 0)
        contrib = score * weight
        slug = _DIMENSION_SLUGS[dim_id]
        lines.append(
            f"| [{title}](dimensions/{slug}.md) | {score}/5 | {weight:.2f} | {contrib:.3f} |"
        )
    total = _compute_total_score(scores, weights)
    lines.append(f"| **加权总分** | **{total:.3f}/5** | 1.00 | — |")
    return "\n".join(lines)


def _build_metrics_table(result_data: dict | None) -> str:
    """构建自动化指标表。"""

    if not result_data or "metrics" not in result_data:
        return "_暂无指标数据_"

    lines = [
        "| 指标 | 比率 | 样本数 | 失败 | 错误 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for m in result_data["metrics"]:
        lines.append(
            f"| {m['metric']} | {m['ratio']:.4f} | {m['sample_count']} | "
            f"{m['failed_samples']} | {m['error_samples']} |"
        )
    return "\n".join(lines)


def _build_appendix(scores: dict[str, dict]) -> str:
    """构建附录交付物清单 AUTO 区块。"""

    items = [
        "- `docs/evaluation/report.md` — 主报告",
        "- `docs/evaluation/scores.toml` — 人工评分源",
        "- `docs/evaluation/scores.json` — 机器可读聚合结果",
        "- `docs/evaluation/results/*.json` — 评测运行结果",
    ]
    for dim_id, slug in _DIMENSION_SLUGS.items():
        title = _DIMENSION_TITLES.get(dim_id, dim_id)
        items.append(f"- `docs/evaluation/dimensions/{slug}.md` — {title}子报告")
    items.append("- `scripts/evaluation/` — 四个入口脚本（run_eval / compare_baseline / aggregate_scores / verify_evidence）")
    return "\n".join(items)


def _merge_report(
    report_path: Path,
    result_path: str,
    total_score: float,
    scores: dict[str, dict],
    weights: dict[str, float],
    result_data: dict | None,
) -> None:
    """合并报告：保留人工段落，替换 AUTO 区块。"""

    if report_path.exists():
        original = report_path.read_text(encoding="utf-8")
    else:
        original = _build_skeleton()

    updated = _replace_named_auto_block(
        original, "report_header",
        _build_report_header(result_path, total_score),
    )
    updated = _replace_named_auto_block(
        updated, "report_scores_table",
        _build_scores_table(scores, weights),
    )
    updated = _replace_named_auto_block(
        updated, "report_metrics_table",
        _build_metrics_table(result_data),
    )
    updated = _replace_named_auto_block(
        updated, "report_appendix",
        _build_appendix(scores),
    )

    report_path.write_text(updated, encoding="utf-8")


def _build_skeleton() -> str:
    """构建全新报告骨架。"""

    return """# AI Agent 工作台系统性评估报告

<!-- AUTO-START: report_header -->
<!-- AUTO-END: report_header -->

## 执行摘要

<!-- TBD: 人工撰写 -->

## 读者导览

<!-- AUTO-START: report_reader_guide -->
- **技术负责人**：优先阅读 [执行摘要](#执行摘要)、[评分汇总表](#评分汇总表)、[改进清单](#改进清单)。
- **开发工程师**：优先阅读 [改进清单](#改进清单) 与各维度子报告的「改进建议」。
- **QA / 平台工程师**：优先阅读 [评估方法](#评估方法) 与 [附录：交付物清单](#附录交付物清单)，按 `scripts/evaluation/README.md` 入口复跑。
<!-- AUTO-END: report_reader_guide -->

## 评估方法

<!-- AUTO-START: report_framework_table -->
<!-- AUTO-END: report_framework_table -->

## 评分汇总表

<!-- AUTO-START: report_scores_table -->
<!-- AUTO-END: report_scores_table -->

## 自动化指标

<!-- AUTO-START: report_metrics_table -->
<!-- AUTO-END: report_metrics_table -->

## 改进清单

<!-- TBD: 人工撰写 -->

## 附录：交付物清单

<!-- AUTO-START: report_appendix -->
<!-- AUTO-END: report_appendix -->
"""


def main(argv: list[str] | None = None) -> int:
    """CLI 入口。

    Args:
        argv: 命令行参数列表；``None`` 时使用 ``sys.argv[1:]``。

    Returns:
        退出码：0 成功 / 1 异常。
    """

    parser = argparse.ArgumentParser(
        prog="aggregate_scores",
        description="评分聚合与主报告生成",
    )
    parser.add_argument(
        "--result",
        default=None,
        help="最新评测 JSON 路径",
    )
    parser.add_argument(
        "--scores",
        default=None,
        help="评分源 TOML 路径（默认 docs/evaluation/scores.toml）",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help="输出根目录（默认 docs/evaluation）",
    )

    try:
        args = parser.parse_args(argv)
    except SystemExit:
        return 1

    repo_root = Path(__file__).resolve().parents[2]
    output_root = Path(args.output_root) if args.output_root else repo_root / "docs" / "evaluation"
    scores_path = Path(args.scores) if args.scores else output_root / "scores.toml"

    if not scores_path.exists():
        print(f"[aggregate_scores] 错误：scores.toml 不存在：{scores_path}", file=sys.stderr)
        return 1

    try:
        scores = _load_scores(scores_path)
        weights = _load_weights()
    except Exception as exc:
        print(f"[aggregate_scores] 加载配置异常：{exc}", file=sys.stderr)
        return 1

    result_data: dict | None = None
    result_path_str = args.result or ""
    if args.result:
        rp = Path(args.result)
        if rp.exists():
            result_data = json.loads(rp.read_text(encoding="utf-8"))
            result_path_str = str(rp)

    total_score = _compute_total_score(scores, weights)

    scores_json = _build_scores_json(scores, weights, result_data)
    scores_json_path = output_root / "scores.json"
    scores_json_path.write_text(
        json.dumps(scores_json, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[aggregate_scores] scores.json 已写入：{scores_json_path}")

    report_path = output_root / "report.md"
    _merge_report(report_path, result_path_str, total_score, scores, weights, result_data)
    print(f"[aggregate_scores] report.md 已写入：{report_path}")

    print(f"[aggregate_scores] 完成。加权总分 = {total_score:.3f} / 5。")

    return 0


if __name__ == "__main__":
    sys.exit(main())
