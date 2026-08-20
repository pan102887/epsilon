# Spec 交付总结：Structured Agent Trace — 结构化 Agent 追踪

## Feature

`structured-agent-trace`：为 ReAct Agent Loop 引入领域级结构化追踪能力，补充 OTel span 的本地持久化缺口。

## 改动范围

### 新增文件

| 路径 | 用途 |
|------|------|
| `src/domain/agent/trace_value_objects.py` | `ModelCallTrace` / `ToolCallTrace` / `ApprovalTrace` / `ErrorTrace` / `SessionTrace` frozen dataclass |
| `src/infrastructure/trace/__init__.py` | 模块标记 |
| `src/infrastructure/trace/trace_config.py` | `TraceConfig`（`TRACE_` 前缀 pydantic-settings） |
| `src/infrastructure/trace/local_file_trace_store_adapter.py` | 本地 JSONL 文件 trace 存储 adapter |
| `test/domain/agent/test_trace_value_objects_unit.py` | 值对象测试（8 用例） |
| `test/infrastructure/trace/test_local_file_trace_store_unit.py` | 文件 adapter 测试（6 用例） |
| `test/infrastructure/agent/test_react_agent_trace_unit.py` | Agent 集成测试（4 用例） |

### 修改文件

| 路径 | 改动 |
|------|------|
| `config.properties` | 追加 `TRACE_ENABLED=true` 和 `TRACE_STORE_DIR=.epsilon/traces` |
| `src/domain/agent/ports.py` | 追加 `TraceStorePort` Protocol |
| `src/infrastructure/agent/react_agent_adapter.py` | `__init__` 追加 `trace_store` 参数；新增 `_record_trace` / `_truncate` / `_build_model_call_trace` / `_build_approval_trace` / `_record_tool_call_trace` 方法；四入口（run / resume / run_streaming / run_events）统一插桩 |
| `src/application/container_config.py` | 新增 `_create_trace_store` 工厂方法，注入 `_create_agent` |

## 测试结果

- 新增测试用例：**18 个**
- 全量回归：**1662 passed / 1 failed（已知 web_search hypothesis 边界问题）/ 3 skipped**
- 导入验证：`trace_value_objects`、`TraceStorePort`、`LocalFileTraceStoreAdapter`、`container_config` 全部正常

## 关键设计决策

1. **kind 判别字段**：每个 trace dataclass 携带 `kind: Literal[...]` 字段，JSON 序列化后可直接按 kind 反序列化。
2. **故障隔离**：`_record_trace` 使用 try/except + logger.warning，trace store 异常不影响主流程。
3. **asyncio.to_thread**：文件 IO 包裹在 to_thread 中避免阻塞事件循环。
4. **四入口统一覆盖**：run / resume / run_streaming / run_events 都记录 ModelCallTrace 和 ToolCallTrace。
5. **可选注入**：`trace_store=None` 时所有追踪操作静默跳过，零运行时开销。

## 交付状态

✅ 所有 10 个任务组已完成。
