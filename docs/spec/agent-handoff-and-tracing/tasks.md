# Agent Handoff & 链路追踪 — 任务清单

## 领域层（domain/agent/）

- [x] T1. `value_objects.py`：新增 `DelegationRequest`（agent_name/task_goal/input_data）和
      `HandoffResult`（target_agent/content/success/usage/model）值对象，frozen dataclass。
      对应 R1.1 / R3.1。
- [x] T2. `exceptions.py`：新增 `HandoffPerformed(Exception)` 信号异常类，承载 target_agent/
      content/usage/model；docstring 说明 "成功信号" 语义并区分 ToolExecutionError。对应 R1.2/R1.3。
- [x] T3. `ports.py::DelegationPort`：新增 `delegate_parallel(requests, delegation_depth,
      max_delegation_depth) -> list[DelegationResult]` 与 `handoff(agent_name,
      context_messages, delegation_depth, max_delegation_depth) -> HandoffResult` Protocol
      方法签名。对应 R1.1 / R3.1。
- [x] T4. `domain/agent/__init__.py`：导出 `DelegationRequest` / `HandoffResult` /
      `HandoffPerformed`。

## 领域层（domain/chat/）

- [x] T5. `context.py::ConversationContext`：新增 `append_message(msg: BaseMessage) -> int`
      方法，用于 handoff 上下文克隆。

## 基础设施层（infrastructure/agent/）

- [x] T6. `handoff_context.py`：新增 ContextVar 模块（`get_parent_context()` /
      `set_parent_context()` / `reset_parent_context(token)`）。
- [x] T7. `delegation_adapter.py::DelegationAdapter`：追加 `model_registry` /
      `agent_provider` / `tool_registry_provider` / `handoff_max_rounds` 字段；
      实现 `delegate_parallel`（错误隔离，单条吞异常返回失败 result）与 `handoff`
      （绕过 TaskAgentPort 直接调 AgentPort.run，避免 add_user_message 污染父侧消息）。
- [x] T8. `handoff_to_agent_tool.py`：新增 `HandoffToAgentTool(Tool)`，从 ContextVar 取
      父消息快照后调 `DelegationPort.handoff` → 抛 `HandoffPerformed`；深度超限 / Agent
      未注册 / ContextVar 未设置 / handoff success=False 等场景均返回错误字符串而不
      抛信号，让 LLM 自我纠正。
- [x] T9. `delegate_parallel_tool.py`：新增 `DelegateParallelTool(Tool)`，schema 含
      minItems=1/maxItems=8；扩展 validate_params 校验子项；execute 输出
      `[✓/✗] <agent>\n<content>` 聚合格式。
- [x] T10. `round_outcome.py`：`RoundOutcomeKind` 增 `"handoff"`；`RoundOutcome` 增
       `handoff_target` / `handoff_content` 字段。
- [x] T11. `react_agent_adapter.py`：模块级 `tracer = _otel_trace.get_tracer(__name__)`；
       `_iter_rounds` 每轮在 OTel span（"react_agent.round"）内执行模型调用 + 写
       attributes（round_num/tool_call_count/has_tool_calls/gen_ai.usage.\*/approval_required），
       异常时 record_exception + ERROR；终止形态用独立 "react_agent.terminated" span 记录
       terminated_reason / handoff_target；`_execute_tool_call` 增 `except HandoffPerformed`
       分支，把 metadata["handoff_target"] 写到 ToolMessage；`_iter_rounds` 每轮入口
       `_detect_handoff(context)` 命中即 yield kind="handoff" 并 return；3 个并发入口
       入口 `set_parent_context(ctx)` + finally `reset_parent_context(token)`；3 个
       执行入口（run / run_streaming / run_events）都新增 kind="handoff" 分支。
       ⚠️ OTel span 关键约束：start_as_current_span 不能跨 yield（contextvars Token
       reset 报错），所以每轮 with 块内只完成模型调用 + 属性写入，退出 with 后再 yield。
- [x] T12. `infrastructure/agent/__init__.py`：导出 `HandoffToAgentTool` /
       `DelegateParallelTool` / `DelegationAdapter` / `DelegateToAgentTool`。

## 装配层（application/）

- [x] T13. `container_config.py`：`_create_delegation_adapter` 注入 `model_registry` 与
       两个 provider 函数；`_register_delegate_tool` 在原 `DelegateToAgentTool` 之后
       追加注册 `HandoffToAgentTool` + `DelegateParallelTool`，受同一
       `AGENT_DELEGATE_TOOL_ENABLED` 开关控制。

## 测试层（test/infrastructure/agent/）

- [x] T14. `test_delegation_adapter_handoff_and_parallel_unit.py`：DelegationAdapter
       handoff（5 测试）+ delegate_parallel（5 测试）+ 1 RuntimeError 测试，共 11 测试。
- [x] T15. （并入 T14）delegate_parallel 顺序保持 / 错误隔离 / 深度超限 / 空请求。
- [x] T16. `test_handoff_and_parallel_tools_unit.py`：HandoffToAgentTool 8 测试 +
       DelegateParallelTool 7 测试，共 15 测试。
- [x] T17. （并入 T16）DelegateParallelTool schema / validate_params / 端到端 run 流水线。
- [x] T18. `test_react_agent_handoff_unit.py`：4 测试覆盖 run / run_streaming / run_events
       三入口的 handoff 终止形态 + 不写 metadata["error"]。
- [x] T19. `test_react_agent_otel_span_unit.py`：6 测试用 InMemorySpanExporter 验证每轮
       round span / 终止 span / 异常 ERROR 状态 / parent-child 嵌套 / NoOpTracer 兜底。
- [x] T20. `test_delegation_parallel_property.py`：hypothesis 生成 1-6 条混合状态请求，
       验证顺序保持 + 错误隔离不变量。

## 验证

- [x] T21. 运行 `python -m pytest test/infrastructure/agent test/infrastructure/telemetry
       test/infrastructure/task test/infrastructure/chat -q`：410 passed，无回归。
       全量 `python -m pytest test`：**1616 passed + 3 skipped + 1 pre-existing failure**
       （web_search hypothesis 边界用例，与本次改动无关）。
- [x] T22. 撰写 `summary.md`。
