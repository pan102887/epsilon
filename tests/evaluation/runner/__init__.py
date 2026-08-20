"""评测 Runner 与结果容器包。

本包包含：

- :mod:`tests.evaluation.runner.models`：评测运行期数据模型
  （:class:`MetricId`、:class:`SampleOutcome`、:class:`EvalCase`、
  :class:`EvalSampleResult`、:class:`DimensionMetric`、
  :class:`DimensionScore`、:class:`EvalResult`）。
- :mod:`tests.evaluation.runner.runner`：:class:`RunnerConfig` 与
  :class:`EvalRunner`，负责调度 pytest 收集样本、聚合指标、写出 JSON。
- :mod:`tests.evaluation.runner.sample_sink`：进程级样本收集器
  :class:`SampleSink`，由 pytest fixture 注入给指标用例。
"""
