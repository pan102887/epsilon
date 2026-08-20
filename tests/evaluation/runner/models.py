"""评测运行期数据模型（结果容器）。

本模块仅持有 ``dataclass`` / ``Enum``，不依赖 FastAPI / Redis / LLM
客户端等基础设施。数据结构映射到 ``docs/spec/spec-ai-evaluation/design.md``
"组件 3：评测用例模型与 Runner" 章节的签名。

所有 ``dataclass`` 均为 ``frozen=True`` 以获得不可变语义，与仓库既有
领域值对象（``domain/*/value_objects.py``）风格保持一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class MetricId(StrEnum):
    """核心自动化指标的唯一标识符。

    - ``TOOL_CALL_SUCCESS_RATE``：工具调用成功率（指标 1）。
    - ``DELEGATION_CORRECTNESS``：委派正确性（指标 2）。
    - ``CONTEXT_COMPACTION_EFFECTIVENESS``：上下文压缩有效性（指标 3）。
    - ``REAL_TASK_GOLDEN_SUCCESS_RATE``：真实任务 golden set 成功率。
    """

    TOOL_CALL_SUCCESS_RATE = "tool_call_success_rate"
    DELEGATION_CORRECTNESS = "delegation_correctness"
    CONTEXT_COMPACTION_EFFECTIVENESS = "context_compaction_effectiveness"
    REAL_TASK_GOLDEN_SUCCESS_RATE = "real_task_golden_success_rate"


class SampleOutcome(StrEnum):
    """单条样本的最终状态。

    - ``PASS``：按期望通过；分子/分母均为正贡献。
    - ``FAIL``：业务断言失败（如工具调用失败、委派目标错误等），分子
      贡献为 0，分母仍照常累加。
    - ``ERROR``：评测脚本或被测 Adapter 抛出未预期异常，单条样本记为
      错误，不中止整批；聚合时进入 ``DimensionMetric.error_samples``。
    """

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"


@dataclass(frozen=True)
class EvalCase:
    """单条评测用例的静态配置。

    Attributes:
        case_id: 全局唯一标识，建议形如 ``"<metric>-<seq>"``。
        metric: 样本所属的 :class:`MetricId`。
        description: 一句话描述，便于在报告中直接引用。
        inputs: 样本输入（如桩模型要返回的 :class:`LLMResponse` 序列、
            待压缩的消息列表等），具体结构由各指标用例约定。
        expected: 样本期望（如允许工具名集合、正确委派目标、窗口 N
            等），具体结构由各指标用例约定。
    """

    case_id: str
    metric: MetricId
    description: str
    inputs: dict[str, Any] = field(default_factory=dict)
    expected: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class EvalSampleResult:
    """单条样本的运行结果。

    Attributes:
        case_id: 对应 :class:`EvalCase` 的唯一标识。
        metric: 样本所属的 :class:`MetricId`。
        outcome: 样本最终状态。
        numerator: 指标分子（如"成功的工具调用数"）。
        denominator: 指标分母（如"总工具调用数"）。对 ``ERROR`` 样本
            建议 ``denominator = 0``，避免污染分母。
        details: 调试字段（工具名、错误消息、命中的判据等），仅用于
            报告展示与调试。
        error: ``outcome == ERROR`` 时填充的错误描述，通常为
            :class:`tests.evaluation.errors.SampleExecutionError`
            的字符串化结果。
    """

    case_id: str
    metric: MetricId
    outcome: SampleOutcome
    numerator: int
    denominator: int
    details: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class DimensionMetric:
    """单项自动化指标的聚合结果。

    Attributes:
        metric: :class:`MetricId`。
        sample_count: 参与聚合的样本数量（含 PASS/FAIL/ERROR）。
        numerator_sum: 全部样本 ``numerator`` 求和。
        denominator_sum: 全部样本 ``denominator`` 求和。
        ratio: 指标比率，``denominator_sum > 0`` 时为
            ``numerator_sum / denominator_sum``，否则为 ``0.0``。
        failed_samples: ``outcome != PASS`` 的样本数。
        error_samples: ``outcome == ERROR`` 的样本数。
    """

    metric: MetricId
    sample_count: int
    numerator_sum: int
    denominator_sum: int
    ratio: float
    failed_samples: int
    error_samples: int


@dataclass(frozen=True)
class DimensionScore:
    """单维度评分（由 ``docs/evaluation/scores.toml`` 回填）。

    Attributes:
        dimension: :class:`tests.evaluation.rubric.DimensionId` 的值。
        score: ``1..5`` 的整数评分，首次脚本生成时允许为 ``0`` 表示
            "未评分占位"。
        weight: 权重，来自 ``load_rubric()``。
        rationale: 中文打分理由，首次生成为空字符串。
        evidence_refs: ``EvidenceReference.raw`` 序列，作为评分的证据
            锚点。
    """

    dimension: str
    score: int
    weight: float
    rationale: str
    evidence_refs: tuple[str, ...]


@dataclass(frozen=True)
class EvalResult:
    """单次完整评测运行的顶层结果。

    Attributes:
        run_id: 形如 ``"<YYYY-MM-DD_HHMMSS>_<git_short>"`` 的唯一运行
            标识，用于 JSON 文件命名。
        generated_at: 运行生成时间（建议 UTC）。
        git_commit: 当前仓库短 HEAD commit；非 git 环境下为 ``None``。
        metrics: 按 :class:`MetricId` 顺序的 :class:`DimensionMetric` 元组。
        dimension_scores: 七维度 :class:`DimensionScore` 元组；首次运行
            可以为"仅 dimension + weight、其余为占位"。
        total_score: 加权平均总分；首次运行 ``dimension_scores`` 未填写
            时为 ``0.0``。
        exit_code: 脚本建议退出码（``0`` 成功、``1`` 脚本自身异常、
            ``2`` 指标相对基线回退）。
    """

    run_id: str
    generated_at: datetime
    git_commit: str | None
    metrics: tuple[DimensionMetric, ...]
    dimension_scores: tuple[DimensionScore, ...]
    total_score: float
    exit_code: int

    def to_dict(self) -> dict[str, Any]:
        """序列化为可直接 ``json.dump`` 的 Python 原生字典。

        关键字段转换：
            - ``generated_at``：转成 ISO8601 字符串（若无 tzinfo，保留
              原始文本，由调用方决定是否补时区后再序列化）。
            - 所有枚举字段按 ``value`` 输出。

        Returns:
            满足 ``docs/spec/spec-ai-evaluation/design.md`` "EvalResult
            JSON Schema" 的嵌套字典。
        """

        return {
            "run_id": self.run_id,
            "generated_at": self.generated_at.isoformat(),
            "git_commit": self.git_commit,
            "metrics": [
                {
                    "metric": m.metric.value,
                    "sample_count": m.sample_count,
                    "numerator_sum": m.numerator_sum,
                    "denominator_sum": m.denominator_sum,
                    "ratio": m.ratio,
                    "failed_samples": m.failed_samples,
                    "error_samples": m.error_samples,
                }
                for m in self.metrics
            ],
            "dimension_scores": [
                {
                    "dimension": s.dimension,
                    "score": s.score,
                    "weight": s.weight,
                    "rationale": s.rationale,
                    "evidence_refs": list(s.evidence_refs),
                }
                for s in self.dimension_scores
            ],
            "total_score": self.total_score,
            "exit_code": self.exit_code,
        }
