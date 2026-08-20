"""三项核心自动化指标的评测样本包。

按 ``docs/spec/spec-ai-evaluation/design.md`` "组件 5" 规划：

- ``test_tool_call_success_rate.py``：工具调用成功率（阶段 3.1）。
- ``test_delegation_correctness.py``：委派正确性（阶段 3.3）。
- ``test_context_compaction_effectiveness.py``：上下文压缩有效性
  （阶段 3.5）。
- ``test_meta_*.py``：各指标的元测试，使用
  ``@pytest.mark.evaluation_self`` 标记，供 CI 验证指标实现的正确性。

本阶段（阶段 2）仅建立空包骨架，供 ``EvalRunner.run()`` 在无样本时
依旧能返回一个合法的 :class:`EvalResult`。
"""
