"""评测 Runner 主体。

本模块实现 :class:`RunnerConfig` 与 :class:`EvalRunner`，负责：

1. 以编程方式调度 ``pytest.main`` 收集 ``tests/evaluation/metrics/``
   下标记 ``@pytest.mark.evaluation`` 的样本；
2. 通过进程级 :class:`tests.evaluation.runner.sample_sink.SampleSink`
   回收 :class:`EvalSampleResult`；
3. 将样本按 :class:`MetricId` 聚合为 :class:`DimensionMetric`；
4. 将聚合结果包装为 :class:`EvalResult` 并写入 JSON。

设计依据：``docs/spec/spec-ai-evaluation/design.md`` "组件 3" 与
"数据模型 — EvalResult JSON Schema"。

样本异常处理：
    单条样本抛出未预期异常时，由各指标用例捕获并包装为
    :class:`tests.evaluation.errors.SampleExecutionError`，写入
    ``EvalSampleResult(outcome=ERROR)``；Runner 本身不重复捕获，
    避免吞掉 fixture 与 pytest 框架异常。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from tests.evaluation.runner.models import (
    DimensionMetric,
    EvalResult,
    EvalSampleResult,
    MetricId,
)

if TYPE_CHECKING:  # pragma: no cover
    pass


def _repo_root() -> Path:
    """推断仓库根目录。

    以本模块路径向上回溯：``tests/evaluation/runner/runner.py`` → 仓库根。
    """

    return Path(__file__).resolve().parents[3]


@dataclass
class RunnerConfig:
    """Runner 配置。

    Attributes:
        output_dir: 评测 JSON 落盘的目录；不存在时由 :meth:`EvalRunner.write_json`
            以 ``mkdir(parents=True, exist_ok=True)`` 创建。
        baseline_path: 回归对比基线路径；``None`` 表示不做回归对比。
        regression_threshold: 允许的回退百分点，默认 5.0。
        selected_metrics: 选定跑哪些指标；``None`` 表示全部指标。
        metrics_test_path: pytest 收集 ``@pytest.mark.evaluation`` 样本
            的根目录；默认指向 ``tests/evaluation/metrics``。
        rootdir: pytest 的 ``--rootdir`` 参数；默认指向仓库根。
    """

    output_dir: Path
    baseline_path: Path | None = None
    regression_threshold: float = 5.0
    selected_metrics: frozenset[MetricId] | None = None
    metrics_test_path: Path = field(
        default_factory=lambda: _repo_root() / "tests" / "evaluation" / "metrics"
    )
    rootdir: Path = field(default_factory=_repo_root)


class EvalRunner:
    """评测编排器：调度 pytest、聚合样本、写 JSON。"""

    def __init__(self, config: RunnerConfig) -> None:
        """初始化 Runner。

        Args:
            config: :class:`RunnerConfig` 实例。
        """

        self._config = config

    @property
    def config(self) -> RunnerConfig:
        """返回 Runner 配置（只读访问）。"""

        return self._config

    def run(self) -> EvalResult:
        """执行全部已注册 metric 的评测用例。

        - 通过 :func:`pytest.main` 收集 ``tests/evaluation/metrics`` 下
          标记 ``evaluation`` 的样本，由指标用例调用 ``sample_sink.append``
          回传 :class:`EvalSampleResult`。
        - 当 ``tests/evaluation/metrics`` 目录不存在或内部暂无样本（阶段
          2 的典型状态），pytest 退出码为 ``5``（no tests collected），
          :meth:`run` 将其视为"空样本"不报错，最终产出的 :class:`EvalResult`
          中全部指标分子分母为 0、``ratio=0.0``、``exit_code=0``。
        - 若 pytest 内部错误（退出码 2、3、4），包装到 :class:`EvalResult`
          的 ``exit_code`` 字段，便于上层脚本将脚本自身错误映射为
          退出码 1。

        Returns:
            :class:`EvalResult` 实例；调用方可继续 :meth:`write_json` 落盘。
        """

        # 延迟 import pytest：避免 self_tests 在 pytest 未被调用时引入
        # 收集副作用；同时让本模块不依赖 pytest 即可被其它工具导入。
        import pytest  # noqa: WPS433 — 意图内的局部 import

        from tests.evaluation.runner.sample_sink import get_sample_sink

        sink = get_sample_sink()
        sink.clear()

        args = ["-q", str(self._config.metrics_test_path), "-m", "evaluation"]
        if self._config.rootdir is not None:
            args.extend(["--rootdir", str(self._config.rootdir)])

        # 若指标目录不存在（阶段 2 尚未建立 metrics 样本），pytest 会
        # 抛出 "file or directory not found"；本阶段容忍此情况，直接
        # 当作零样本对待。
        pytest_exit: int
        if not self._config.metrics_test_path.exists():
            pytest_exit = 5  # NO_TESTS_COLLECTED，与 pytest 原生码对齐
        else:
            pytest_exit = int(pytest.main(args))

        samples = sink.drain()

        # 过滤 selected_metrics
        if self._config.selected_metrics is not None:
            samples = [s for s in samples if s.metric in self._config.selected_metrics]

        metrics = self.aggregate(samples)

        # pytest 退出码 0（全通过）或 5（没有样本）均视为 Runner 无自身
        # 错误；其余映射为 1，供上层脚本转写退出码。
        run_exit_code = 0 if pytest_exit in (0, 5) else 1

        return EvalResult(
            run_id=self._build_run_id(),
            generated_at=datetime.now(UTC),
            git_commit=self._detect_git_commit(),
            metrics=metrics,
            dimension_scores=tuple(),
            total_score=0.0,
            exit_code=run_exit_code,
        )

    def aggregate(self, samples: list[EvalSampleResult]) -> tuple[DimensionMetric, ...]:
        """按 :class:`MetricId` 聚合样本为 :class:`DimensionMetric`。

        Args:
            samples: 由样本 sink 回传的 :class:`EvalSampleResult` 列表。

        Returns:
            固定顺序的 :class:`DimensionMetric` 元组（按
            :class:`MetricId` 枚举声明顺序），无样本时该 metric 的
            聚合结果分子分母为 0、``ratio=0.0``。
        """

        by_metric: dict[MetricId, list[EvalSampleResult]] = {m: [] for m in MetricId}
        for sample in samples:
            if sample.metric in by_metric:
                by_metric[sample.metric].append(sample)

        result: list[DimensionMetric] = []
        for metric in MetricId:
            group = by_metric[metric]
            sample_count = len(group)
            numerator_sum = sum(s.numerator for s in group)
            denominator_sum = sum(s.denominator for s in group)
            if denominator_sum > 0:
                ratio = numerator_sum / denominator_sum
            else:
                ratio = 0.0
            failed_samples = sum(
                1 for s in group if s.outcome.value != "pass"
            )
            error_samples = sum(1 for s in group if s.outcome.value == "error")
            result.append(
                DimensionMetric(
                    metric=metric,
                    sample_count=sample_count,
                    numerator_sum=numerator_sum,
                    denominator_sum=denominator_sum,
                    ratio=ratio,
                    failed_samples=failed_samples,
                    error_samples=error_samples,
                )
            )
        return tuple(result)

    def write_json(self, result: EvalResult) -> Path:
        """把 :class:`EvalResult` 序列化为 JSON 并落盘。

        输出路径：``<output_dir>/<run_id>.json``；``output_dir`` 缺失
        时自动创建（``mkdir(parents=True, exist_ok=True)``）。

        Args:
            result: 待落盘的评测结果。

        Returns:
            实际写入的 JSON 文件路径。
        """

        output_dir = self._config.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        path = output_dir / f"{result.run_id}.json"
        payload = result.to_dict()
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------

    def _build_run_id(self) -> str:
        """生成 ``<YYYY-MM-DD_HHMMSS>_<git_short>`` 形式的运行标识。

        Returns:
            无 git 环境下 ``<git_short>`` 使用 ``"nogit"`` 兜底。
        """

        stamp = datetime.now(UTC).strftime("%Y-%m-%d_%H%M%S")
        git_short = self._detect_git_short() or "nogit"
        return f"{stamp}_{git_short}"

    def _detect_git_short(self) -> str | None:
        """读取当前 HEAD 的短 commit hash（7 位）。"""

        return self._git_rev_parse(["--short", "HEAD"])

    def _detect_git_commit(self) -> str | None:
        """读取当前 HEAD 的完整 commit hash。"""

        return self._git_rev_parse(["HEAD"])

    def _git_rev_parse(self, extra_args: list[str]) -> str | None:
        """执行 ``git rev-parse`` 获取当前仓库的 commit 标识。

        Args:
            extra_args: 透传给 ``git rev-parse`` 的额外参数。

        Returns:
            成功时返回 ``stdout`` 去除首尾空白的字符串；非 git 环境或
            命令执行失败时返回 ``None``。
        """

        try:
            completed = subprocess.run(  # noqa: S603 — 参数固定，非 shell 注入
                ["git", "rev-parse", *extra_args],
                cwd=str(self._config.rootdir) if self._config.rootdir else None,
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
        except (FileNotFoundError, subprocess.SubprocessError):
            return None
        if completed.returncode != 0:
            return None
        value = completed.stdout.strip()
        return value or None
