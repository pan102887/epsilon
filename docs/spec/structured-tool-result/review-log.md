# Review Log — structured-tool-result

追加式审计记录，勿覆盖历史条目。

## CP2 — 全部工具重构测试适配

- 任务：CP2（Checkpoint 2，适配第 2 组工具重构导致的失败测试断言）
- 尝试 #1：本环境未启用 spec-evaluator 子代理（Task 工具不可用），改为生成方自校验：断言适配与 ToolExecutionResult 契约、design §3.12/§3.5/§3.7 metadata 字段名与语义一致（如 success_count==1 对应一 True 一 False），核对仅改测试、未触碰工具业务逻辑。
- 改动：仅测试文件断言适配（将对 `execute()`/`run()` 返回值的字符串 `in` 判断改为断言 `.content`，并补充 `.metadata` 结构化字段断言）。未改任何被测工具业务逻辑。
- 涉及文件：
  - test/infrastructure/agent/test_handoff_and_parallel_tools_unit.py
  - test/infrastructure/agent/test_workflow_collaboration_governance_unit.py
  - test/application/test_workspace_end_to_end_integration.py
  - test/application/test_long_task_phase6_recovery_collaboration_integration.py
- 验证：CP2 命令范围 242 passed；test/application 全量 420 passed，无回归。

## 第 3 组 T3.1–T3.5 + CP3 — ReActAgentAdapter 改造

- 任务：T3.1–T3.5 与 CP3（消费 ToolExecutionResult、_record_tool_call_trace 升级 + _truncate_metadata、三并发调度适配、_record_error_trace 补录、max_rounds==1 快速路径 ModelCallTrace 补录）。
- 尝试 #1：本环境未启用 spec-evaluator 子代理（Task 工具不可用），改为生成方自校验，核对以下不变量与验收标准：
  - INV-1：`ToolMessage.content` 与 `AgentStreamEvent.content` 恒取 `.content`；LLM 回灌内容语义不变。
  - INV-2：checkpoint `after_tool_call(result=...)` 仍取 `result.content`（str），序列化格式不变。
  - INV-5：异常传播不变——各 except 分支仅把 `result` 从 str 换为 ToolExecutionResult；`_GuardrailApprovalRequired` 在四入口的 error-trace 包装中显式 re-raise，不吞异常。
  - 需求 7.2/7.3/7.4：`error_class` 从 metadata 提取、`error_message` 取截断 content，成功路径二者为 None（对应异常分支 metadata 已填 `error_class`）。
  - 需求 8：ErrorTrace 仅记录 Agent Loop 级非工具异常，fire-and-forget 经 `_record_trace` 故障隔离，不阻止传播；四入口 run/resume/run_streaming/run_events 均覆盖。
  - 需求 9：快速路径经 `_RoundStreamAccumulator.record_chunk` 边产出边累积，`build_response(latency_ms=...)` 提供 model/usage/latency，`_build_model_call_trace_from_response` 复用既有字段构造逻辑；trace_store 为 None 时静默跳过。
  - design §7.2 偏差处理：快速路径原 `_stream_final_round`/`_stream_events_final_round` 未暴露 response；按实际代码新增可选 `response_capture` 出参 + 累积器 `record_chunk` 方法（从 `consume` 抽取共用累积逻辑，避免分片合并规则重复实现），实现最小侵入且不改变 response_capture=None 时的原行为。
- 改动（生产代码）：
  - src/infrastructure/agent/react_agent_adapter.py（T3.1–T3.5 全部）
  - src/infrastructure/agent/round_stream_accumulator.py（新增 `record_chunk`、`build_response` 增 `latency_ms` 可选覆盖）
- 改动（测试适配，仅断言/mock 契约升级为 ToolExecutionResult，未削弱原校验）：test/infrastructure/agent/ 下 react_agent 相关多文件、events/context_engineering/tool_arguments_delta/otel_span 等；test/infrastructure/chat/test_agent_loop_{sync,streaming}.py、test_dynamic_model_routing_properties.py；test/application/test_long_task_phase6_recovery_collaboration_integration.py；test/integration/test_long_task_runtime_convergence_p0.py。
- 验证：CP3 命令范围（离线）162 passed；全量回归 2787 passed / 3 skipped / 0 failed；ruff 通过；pyright 无新增错误（残留 7 项均为 HEAD 既存基线：opentelemetry env 未装 + run_id/approval_id/ApprovalRequiredPayload|None 旧告警）。

## 第 4 组 T4.1 + CP4 — 存储层 JSONL 兼容

- 任务：T4.1 与 CP4（`LocalFileTraceStoreAdapter._dict_to_step()` 对 `kind=="tool_call"` 分支 pop metadata 并传入构造函数，兼容旧 JSONL 数据）。
- 尝试 #1：本环境未启用 spec-evaluator 子代理（Task 工具不可用），改为生成方自校验，核对以下不变量与验收标准：
  - INV-4：JSONL 前向兼容——旧行无 metadata 字段时经 `d.pop("metadata", {})` 兜底为空 dict，含 metadata 的新行原样传入 `ToolCallTrace(**d, metadata=metadata)`；两条路径均可正确读回。
  - design §5.2：仅对 `kind=="tool_call"` 做 metadata 特殊处理，其他 kind 保持既有 `cls(**d)` 分支不变，最小改动。
  - 未改动 `_step_to_dict`/append 写入侧（asdict 已含 metadata 字段，写侧无需变更）。
- 改动（生产代码）：src/infrastructure/trace/local_file_trace_store_adapter.py（`_dict_to_step` 新增 tool_call 分支）。
- 验证：CP4 命令范围（离线）test/infrastructure/trace/ 10 passed；全量回归 2787 passed / 3 skipped / 0 failed；ruff 对改动文件通过。

## 第 5 组 T5.1–T5.4 + CP5 — 测试补充与全量回归

- 任务：T5.1–T5.4 与 CP5（ToolExecutionResult 值对象单元测试、各工具 metadata 测试缺口补齐、adapter trace 集成测试、JSONL 兼容测试、全量回归）。
- 尝试 #1：本环境未启用 spec-evaluator 子代理（Task 工具不可用），改为生成方自校验，核对以下要点：
  - T5.1（新增 test/domain/agent/test_tools_unit.py，11 用例）：frozen 双字段不可变（FrozenInstanceError）、metadata default_factory 实例间不共享、按值判等、异构值类型；Tool.run()/ToolRegistry.execute()/ScopedToolRegistry.execute() 透传 ToolExecutionResult 且权限拒绝仍抛 ToolPermissionDeniedError。断言均针对具体值，无永真占位。
  - T5.2：先检查现有覆盖，发现 python_exec/read_file 零 metadata 断言（缺口），shell 缺 stdout_bytes/stderr_bytes、list_dir 缺 recursive、web_search 缺 query、mcp 缺 mcp_server；仅补齐缺口并加 set(keys) 全字段集合断言。metadata 字段名/类型严格对齐 design §3.2–§3.14（源码为准：mcp 为 {mcp_server, mcp_tool_name}、web_search 为 {query, result_count}）。handoff 补错误返回路径 {target_agent, success=False}（§3.13）。
  - T5.3（扩展 test_react_agent_trace_unit.py，+4 用例）：成功路径 result.metadata 透传 ToolCallTrace.metadata 且 error_class/error_message 为 None（需求 7.2/7.4）；失败路径 error_class 取 metadata、error_message 取截断 content（需求 7.3）；模型 stream 抛异常 → ErrorTrace 写入且原异常向上传播（需求 8.1/8.2）；工具失败不产生 ErrorTrace（需求 8.4）。
  - T5.4（扩展 test_local_file_trace_store_unit.py，+3 用例）：含 metadata 的 tool_call 行写回 roundtrip；手工构造无 metadata 的旧行读回兜底空 dict；同文件混合新旧行均正确读回（INV-4）。
- 改动（仅测试文件，未触碰任何生产代码）：
  - 新增：test/domain/agent/test_tools_unit.py
  - 扩展：test/infrastructure/tools/python_exec/test_python_exec_tool_unit.py、test/infrastructure/tools/filesystem/test_read_file_tool_unit.py、test/infrastructure/tools/filesystem/test_list_dir_tool_unit.py、test/infrastructure/tools/shell_exec/test_shell_exec_tool_unit.py、test/infrastructure/tools/web_search/test_web_search_tool.py、test/infrastructure/agent/test_handoff_and_parallel_tools_unit.py、test/infrastructure/tools/mcp/test_mcp_tool_bridge.py、test/infrastructure/agent/test_react_agent_trace_unit.py、test/infrastructure/trace/test_local_file_trace_store_unit.py
- 验证：CP5 全量回归（离线）2814 passed / 3 skipped / 0 failed（较基线 2787 净增 27 个通过用例）；ruff 对全部改动测试文件通过。

## 第 6 组 T6.1–T6.4 + CP6 — 文档同步

- 任务：T6.1–T6.4 与 CP6（同步 tool-authoring.md / domain-model.md / tools.md / architecture.md，反映 ToolExecutionResult 契约、各工具 metadata、ToolCallTrace.metadata 与 ErrorTrace/ModelCallTrace 补录）。
- evaluator：本组为 doc-only slice（未改任何生产/测试代码），按 spec-generator 规范无需 evaluator；且本环境 spec-evaluator 子代理不可用（Task 工具不可用）。改为生成方自校验，逐条核对文档与代码实际状态一致（以源码为准，非照抄 design 伪代码）：
  - T6.1（docs/steering/tool-authoring.md §2 + 新增 §2.1 + 检查清单）：execute() 返回类型改为 ToolExecutionResult；新增 §2.1 说明 content/metadata 语义、snake_case、命令/代码摘要 128 字符与 URL 256 字符截断、_truncate_metadata ≈2KB、无宿主绝对路径、脱敏、异常路径由 adapter 统一构造。核对 src/domain/agent/tools.py（execute/run/ToolRegistry/ScopedToolRegistry 均 -> ToolExecutionResult）与 react_agent_adapter.py 的 _truncate_metadata（max_total_bytes=2048、_truncated 标记、default=str）。
  - T6.2（docs/domain-model.md「工具调用」章节）：新增 ToolExecutionResult frozen 值对象（content=回灌 LLM 文本、metadata=结构化 trace 扩展、位于 domain/agent/tools.py），并将 Tool/ToolRegistry/ScopedToolRegistry 三处 -> str 改为 -> ToolExecutionResult。与 tools.py 定义一致。
  - T6.3（docs/tools.md 顶部返回类型说明 + 13 工具 metadata 表 + 注册步骤 2）：各工具 metadata 字段严格以源码为准——web_search=result_count（非 requirement 的 results_count）、http_request 含 method、web_fetch 含 content_type（无 truncated/status_code）、delegate_to_agent/handoff={target_agent,success}（无 delegation_depth）、mcp={mcp_server,mcp_tool_name}（无 success）。逐一 grep 各工具 metadata= 字面量核对。
  - T6.4（docs/architecture.md 新增「结构化 Agent 追踪（TraceStorePort）」章节）：ToolCallTrace.metadata 透传与 _truncate_metadata 截断、error_class/error_message 填充语义、JSONL pop("metadata",{}) 兼容；ErrorTrace 仅记 Agent Loop 级非工具异常且不与 ToolCallTrace 重复、覆盖 run/resume/run_streaming/run_events；max_rounds==1 快速路径经 response_capture 补录 ModelCallTrace(round_num=1)。核对 react_agent_adapter.py 的 _record_error_trace/_record_tool_call_trace/run_events 快速路径 fast_path_response。
  - CP6：四文档均已同步且与源码一致（未发现遗留 execute() -> str 描述指向工具契约）。
- 改动（仅文档，未触碰任何代码 / pyproject.toml / uv.lock）：
  - docs/steering/tool-authoring.md、docs/domain-model.md、docs/tools.md、docs/architecture.md
- 验证：doc-only，无需跑测试；离线只读 grep 交叉核对各工具 metadata 字面量与 adapter trace 逻辑，均一致。
