"""评测脚本自身的异常定义。

评测代码使用独立异常族，避免与业务域异常（`domain/*/exceptions.py`）
混用而导致"评测失败"被误读为"业务缺陷"。设计依据：
`docs/spec/spec-ai-evaluation/design.md` 「错误处理」章节。

各异常对应的建议脚本退出码（由 `scripts/evaluation/run_eval.py` 统一处理）：

- `EvidenceFormatError` → 退出码 1（脚本自身错误）
- `EvidenceNotFoundError` → 退出码 1（证据校验失败）
- `RubricConsistencyError` → 退出码 1（Rubric 自身不一致）
- `SampleExecutionError` → 被 `EvalRunner` 捕获，转写为
  `EvalSampleResult(outcome=ERROR)`，不中止整批
- `RegressionThresholdViolation` → 退出码 2（指标回退触发阈值）

所有异常消息必须为中文，与仓库既有风格保持一致
（对齐 `docs/steering/code-documentation.md`）。
"""

from __future__ import annotations


class EvaluationError(Exception):
    """评测脚本错误基类。

    所有评测代码抛出的自定义异常都应继承本类；捕获端可以借此一次性
    兜底评测链路上的异常，避免与业务域异常混淆。
    """


class EvidenceFormatError(EvaluationError):
    """证据引用字符串格式非法。

    触发场景：`tests/evaluation/evidence/models.py::parse_reference`
    收到不符合 `^[^\\s:]+(:L?\\d+(-L?\\d+)?)?$` 正则的输入。

    建议退出码：1（脚本自身错误）。
    """


class EvidenceNotFoundError(EvaluationError):
    """证据指向的路径或行号不存在。

    触发场景：`tests/evaluation/evidence/verifier.py::verify_evidence`
    遇到路径不存在、行号越界或摘录不匹配，但调用方要求以异常方式传播时。
    注意：`verify_evidence` 默认采用"不抛异常、批量返回 `EvidenceCheck`"
    策略；本异常仅用于需要 fail-fast 的 CLI 入口（例如
    `scripts/evaluation/verify_evidence.py` 检测到任一失败时包装抛出）。

    建议退出码：1（脚本自身错误）。
    """


class RubricConsistencyError(EvaluationError):
    """Rubric 自身一致性校验失败。

    触发场景：`tests/evaluation/rubric/dimensions.py::load_rubric`
    发现以下情形之一：
    - 7 个维度权重之和与 1.0 的偏差超过 1e-9；
    - 某维度的 5 级 citations 跨级去重后框架数量少于 2；
    - 维度数量不足 7 或维度标识重复。

    建议退出码：1（脚本启动即失败）。
    """


class SampleExecutionError(EvaluationError):
    """单条评测样本执行异常的包装。

    触发场景：`EvalRunner.run()` 循环体捕获到被测 Adapter / 桩
    抛出的任意异常时，包装为本异常，携带 `case_id`、原始异常类名与
    traceback，随后写入 `EvalSampleResult(outcome=ERROR)` 并继续后续样本。

    建议退出码：不直接映射；由 `EvalRunner` 聚合到
    `DimensionMetric.error_samples`，用于摘要展示。
    """

    def __init__(self, case_id: str, cause: BaseException) -> None:
        """构造样本级异常包装。

        Args:
            case_id: 触发异常的评测样本唯一标识（对应 `EvalCase.case_id`）。
            cause: 被捕获的原始异常对象，用于保留 `__cause__` 链。
        """
        message = f"评测样本 {case_id!r} 执行异常：{type(cause).__name__}: {cause}"
        super().__init__(message)
        self.case_id = case_id
        self.cause = cause
        self.__cause__ = cause


class RegressionThresholdViolation(EvaluationError):
    """回归对比结果触发阈值。

    触发场景：`scripts/evaluation/compare_baseline.py::compare` 检测到
    任一指标相对基线下降大于等于阈值（默认 5 个百分点），用于 CI
    场景下以退出码 2 提示失败。

    建议退出码：2（指标回退）。
    """
