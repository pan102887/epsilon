# 交付总结：结构化工具执行结果（Structured Tool Result）

- **Feature slug**：`structured-tool-result`
- **状态**：全部 37 个任务勾选完成（6 组 + 6 个 Checkpoint），最终全量回归 **2814 passed / 3 skipped / 0 failed**。

## 1. 目标与结果

将工具执行结果从裸 `str` 升级为结构化值对象 `ToolExecutionResult`（`content` + `metadata`），使 Agent 追踪（trace）能记录工具类型特有的结构化元数据，并补齐 Agent Loop 级异常与 `max_rounds==1` 快速路径的追踪覆盖。对 LLM 可见行为保持不变（INV-1）。

## 2. 最终产物清单

### Spec 文档
- `requirement.md`、`design.md`、`tasks.md`（全勾选）、`review-log.md`（append-only 审查记录）、`summary.md`（本文件）。

### 生产代码
- `src/domain/agent/tools.py` — 新增 `ToolExecutionResult`（frozen 值对象）；`Tool.execute/run`、`ToolRegistry.execute`、`ScopedToolRegistry.execute` 返回类型改为 `ToolExecutionResult`。
- `src/domain/agent/trace_value_objects.py` — `ToolCallTrace` 新增 `metadata` 字段。
- `src/infrastructure/tools/**`（13 个工具）— 各工具 `execute()` 返回 `ToolExecutionResult` 并填充类型特有 metadata。
- `src/infrastructure/agent/react_agent_adapter.py` — 消费 `ToolExecutionResult`；新增 `_truncate_metadata`（≤2KB）、`_record_error_trace`；四入口（run/resume/run_streaming/run_events）补录 `ErrorTrace`；`max_rounds==1` 快速路径经 `response_capture` 补录 `ModelCallTrace`。
- `src/infrastructure/agent/round_stream_accumulator.py` — 抽出 `record_chunk`、`build_response` 支持 `latency_ms` 覆盖，支撑快速路径 response 捕获。
- `src/infrastructure/trace/local_file_trace_store_adapter.py` — `_dict_to_step` 对 `tool_call` 分支 `pop("metadata", {})`，JSONL 前向兼容。

### 测试（净增 27 例）
- `test/domain/agent/test_tools_unit.py`（新增）— `ToolExecutionResult` 契约与 registry 透传。
- 各工具测试补充 metadata 字段/类型断言（python_exec、read_file、list_dir、shell_exec、web_search、mcp、handoff/parallel 等）。
- `test/infrastructure/agent/test_react_agent_trace_unit.py` — metadata 透传、error_class/error_message、ErrorTrace 写入。
- `test/infrastructure/trace/test_local_file_trace_store_unit.py` — 新旧 JSONL 兼容。

### 文档同步
- `docs/steering/tool-authoring.md`、`docs/domain-model.md`、`docs/tools.md`、`docs/architecture.md` 均已同步至代码实际状态。

## 3. 值得记录的设计决策

- **`ToolExecutionResult` 置于 `domain/agent/tools.py`**：与 `Tool` 同模块，消除跨模块循环导入。
- **`metadata: dict[str, Any]`**：free-form trace 扩展字段，值类型天然异构，非 API 契约；按 `python-typing-lint` 例外在 docstring 说明，各工具逐键标注含义与类型。
- **异常路径 metadata 集中在 adapter 构造**：工具内不感知 trace，权限拒绝/超时/一般异常在 `_execute_tool_call` 各 except 分支填 `error_class`。
- **`ErrorTrace` 与 `ToolCallTrace` 职责分离**：工具失败经 `ToolCallTrace.success=False` 记录；`ErrorTrace` 仅记 Agent Loop 级非工具异常，fire-and-forget 不阻断异常传播（INV-5）。
- **快速路径 response 捕获（design §7.2 偏差）**：`_stream_final_round`/`_stream_events_final_round` 原未暴露 response，改用可选 `response_capture` 出参 + `record_chunk` 提取，`response_capture=None` 时行为字节级不变。

## 4. 测试覆盖

- 值对象：frozen 不可变、default metadata 不共享、异构值、registry 透传、权限拒绝仍抛异常。
- 13 工具 metadata 字段名/类型对齐（以源码为准，修正了 design/requirement 若干分歧，详见文档核对记录）。
- Adapter trace 集成：metadata 透传、异常 error 字段、ErrorTrace 写入、工具失败不产生 ErrorTrace。
- JSONL 前向兼容：含/不含 metadata 的新旧行均可读回（INV-4）。

## 5. 后续事项 / Follow-ups

- **评审门**：本环境 `spec-evaluator` 子代理不可用，各 slice 以生成方自校验替代并记录于 `review-log.md`；建议在具备 evaluator 的环境补一次正式审查。
- **环境提示**：内网 Nexus 源不可达，测试须离线运行（`PYTHONPATH=src UV_OFFLINE=1 uv run --frozen --no-sync pytest` 或 `.venv/bin/pytest`）；未改动 `pyproject.toml`/`uv.lock`。
- **既有 pyright 基线**：7 处残留告警为 HEAD 既有（opentelemetry env import、run_id/approval_id 等 legacy），非本次引入。
