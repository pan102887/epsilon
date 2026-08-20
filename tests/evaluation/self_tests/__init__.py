"""评测脚本自身的单元测试包。

本包下的测试覆盖 Rubric 一致性、证据解析、证据校验、Runner 聚合等
"评测代码的代码单元"，与 `tests/evaluation/metrics/` 下的"评测样本"
是两类测试：

- `self_tests/`：不标 `@pytest.mark.evaluation`，通过
  `uv run pytest tests/evaluation/self_tests` 或
  `python -m pytest tests/evaluation/self_tests` 执行，用于保证评测
  代码本身正确；
- `metrics/`：携带 `evaluation` 标记，由 `scripts/evaluation/run_eval.py`
  收集，产出指标 JSON。

两类测试互不干扰。
"""
