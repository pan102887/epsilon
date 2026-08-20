"""评测桩依赖包。

本包提供零外部依赖的桩实现，按结构类型（Protocol）匹配
`epsilon-boot/src/domain/*/ports.py` 中的端口接口：

- :mod:`tests.evaluation.stubs.model_access`：桩 ``ModelAccessPort``
- :mod:`tests.evaluation.stubs.agent_registry`：桩 ``AgentRegistryPort``
- :mod:`tests.evaluation.stubs.session_context_store`：桩 ``SessionContextStorePort``

桩实现只允许导入业务领域层（``domain/**``）的值对象与异常类
（属于 ``docs/steering/ddd-architecture.md`` "允许的例外 — 测试/评测代码"
范围），**不得**导入 ``infrastructure/**``、``application/**`` 或
``common/**`` 内部模块，以避免评测代码反向耦合基础设施。

所有桩实现均可由 ``tests/evaluation/runner/runner.py::EvalRunner`` 在单元
测试中构造、注入真实 Adapter，完成三项核心指标的确定性评测。
"""
