# agent-adapter-refactor-v3 — 落地总结

## Feature

`agent-adapter-refactor-v3`：把 v2 落地后暴露的 5 类问题（中间轮次纯文本非
真流式分片、工具执行无 timeout / 取消、缺少 token 预算、`_iter_rounds`
循环耗尽不可达分支、`tool_arguments_delta` 缺失）一次性按业内主流方案对齐
解决。

## 决策清单

| 决策 | 选定方案 | 业内对齐 |
|---|---|---|
| 1 | **B**：ReAct 内部全程 ``stream`` + 内部累积聚合 | OpenAI Assistants / LangGraph / Vercel AI SDK |
| 2 | 落地 ``tool_arguments_delta`` 真分片事件 | Anthropic ``input_json_delta`` / Vercel ``tool-call-delta`` |
| 3 | **(b)**：工具 timeout 全局 + per-tool override | OpenAI Agents SDK / CrewAI |
| 4 | **(a)**：仅引入 ``max_total_tokens``（v3 起步） | OpenAI Assistants ``max_completion_tokens`` 简化版 |
| 5 | **(b)**：``_iter_rounds`` 循环耗尽分支 ``assert`` + 注释 | 项目内部强不变量 |

## 最终产物清单

### 新增（domain）

- `domain/model_access/value_objects.py::StreamingToolCallDelta`：v3 流式工具调用增量切片值对象。
- `domain/model_access/value_objects.py::StreamingChunk.tool_calls`：末尾追加可选字段，遵循"中间分片增量、末尾分片完整列表"协议。
- `domain/agent/value_objects.py::AgentStreamEventKind` 追加 ``"tool_arguments_delta"`` 取值。
- `domain/agent/value_objects.py::AgentTerminationReason` 扩展取值集合至 ``{"completed", "max_rounds", "token_budget_exceeded"}``。
- `domain/agent/value_objects.py::AgentConfig` 追加 ``tool_timeout_seconds`` / ``max_total_tokens`` 字段（含 ``__post_init__`` 校验）。
- `domain/agent/tools.py::Tool.timeout_seconds`：带 ``return None`` 默认实现的 property，支持 per-tool override 而不强制子类实现。

### 新增（infrastructure）

- `infrastructure/agent/round_stream_accumulator.py::_RoundStreamAccumulator`：单轮流式分片累积器，对外产出与 ``model_access.chat()`` 等价的 ``LLMResponse``。
- `infrastructure/model_access/openai_compatible_adapter.py::OpenAICompatibleAdapter.stream` 重写：透传 SDK ``delta.tool_calls`` 增量并在 ``finished=True`` 分片重组完整列表。

### 修改（infrastructure）

- `infrastructure/agent/react_agent_adapter.py`
  - `_iter_rounds`：删除 ``model_access.chat`` 调用，改为 ``_RoundStreamAccumulator`` 内部累积；新增 ``token_budget_exceeded`` 跨轮终止流程；循环耗尽分支由"if/else 静默回退"升级为 ``Terminal_Round_Boundary_Assert`` 强约束。
  - `_execute_tool_call`：``asyncio.wait_for`` 超时包裹；新增 ``except asyncio.TimeoutError`` 分支输出 ``reason="timeout"`` warning，``ToolMessage.metadata["error"] = True`` 持久化。
  - `_stream_events_final_round`：在最后一轮 stream 阶段，对中间分片 ``chunk.tool_calls`` 逐个产出 ``AgentStreamEvent(kind="tool_arguments_delta")``。
  - `_resolve_tool_timeout` / `_compute_total_tokens` / `_is_token_budget_exceeded` / `_log_token_budget_exceeded` 辅助方法新增。
  - `run_streaming` / `run_events`：``terminated_reason in ("max_rounds", "token_budget_exceeded")`` 时跳过最后一轮 stream，产出携带 ``terminated_reason`` 元数据的终止分片 / 事件。

### 测试（新增 9 个文件，66 个用例）

- `test/domain/model_access/test_streaming_chunk_tool_calls_field_unit.py`
- `test/infrastructure/model_access/test_openai_compatible_stream_tool_calls_unit.py`
- `test/infrastructure/model_access/test_openai_compatible_stream_tool_calls_property.py`
- `test/infrastructure/agent/test_round_stream_accumulator_unit.py`
- `test/infrastructure/agent/test_round_stream_accumulator_property.py`
- `test/infrastructure/agent/test_react_agent_iter_rounds_stream_only_unit.py`
- `test/infrastructure/agent/test_react_agent_tool_arguments_delta_unit.py`
- `test/domain/agent/test_tool_timeout_property_unit.py`
- `test/domain/agent/test_agent_config_validation_unit.py`
- `test/infrastructure/agent/test_react_agent_tool_timeout_unit.py`
- `test/infrastructure/agent/test_react_agent_token_budget_unit.py`
- `test/infrastructure/agent/test_react_agent_terminal_assert_unit.py`

### 测试（v3 NFR-3 等价改写）

- 共约 12 个 v2 既有 ``model_access.chat`` mock 测试改写为 ``model_access.stream`` 等价 mock；语义等价不调整断言（仅必要时由 ``chat_call_count`` 改为 ``stream_call_count`` 以反映"v3 ReAct 全程 stream"）。
- 共享辅助：`test/infrastructure/agent/_v3_stream_helpers.py`（``response_to_chunks`` / ``FakeStreamModel`` / ``install_stream_mock``）。

## 执行验证

| 项 | 结果 |
|---|---|
| 全量 ``uv run pytest -q`` | **1542 passed, 3 skipped, 0 failed**（含 PR-3/PR-4 共 53 条新增测试） |
| ``ReAct_Internal_Chat_Zero_Reference`` grep | `model_access.chat(` 在 `infrastructure/agent/`：**0 次命中** |
| 非 ReAct ``chat()`` 保留 grep | `chat_service_adapter.py` / `llm_summary_compaction_adapter.py`：**各 1 次命中**（保留） |
| ``slots=True`` grep | `domain/model_access/value_objects.py`：**0 次命中** |
| ``其他循环耗尽分支：保持 completed`` grep | **0 次命中**（v2 残留兜底注释已删除） |
| ``last_response.tool_calls`` 用法 | 仅出现在 ``assert`` 表达式与 warning 日志参数内，无 ``if`` 分支 |

## 业内方案对齐总览

| 主题 | 本项目方案 | 对齐参考 |
|---|---|---|
| 推进路径 | ``_iter_rounds`` 全程 stream + 内部累积 | OpenAI Assistants / LangGraph |
| 工具调用增量 | ``StreamingChunk.tool_calls`` 中间分片增量 / ``finished=True`` 完整重组 | Anthropic ``input_json_delta`` |
| 终止信号 | ``AgentTerminationReason ∈ {completed, max_rounds, token_budget_exceeded}`` | OpenAI Assistants ``incomplete_details.reason`` / LangGraph ``GraphRecursionError`` |
| 工具超时 | 全局 ``tool_timeout_seconds`` + per-tool ``Tool.timeout_seconds`` override | OpenAI Agents SDK / CrewAI |
| 预算控制 | ``max_total_tokens`` 跨轮累计 | OpenAI Assistants ``max_completion_tokens`` |
| HITL | ``status="approval_required"`` 与 ``terminated_reason`` 正交 | LangGraph checkpoint + interrupt |

## 已知遗留 / 后续演进

- 决策 12（_stream_final_round (a) 路线）：``StreamingChunk`` 通道目前**不**透传工具调用增量，前端打字机收益由 ``run_events`` 通过 ``tool_arguments_delta`` 单独承载。如需在 ``run_streaming`` 通道获得 typewriter 效果，留待后续 spec。
- 决策 4 (a)：本期仅 ``max_total_tokens``，不引入 Pydantic AI 风格的 ``request_limit`` / ``response_tokens_limit`` / 成本预算。
- ``Tool.timeout_seconds`` 在 ``ScopedToolRegistry`` 包装路径下可能不暴露 ``get(name)`` 接口；当前实现使用 ``getattr + isinstance`` 安全回退到全局值。如未来 ``ScopedToolRegistry`` 暴露 ``get``，per-tool 覆盖将在该路径下生效。
- 工具超时取消语义由各 ``Tool`` 实现内部 ``try/except CancelledError`` 处理（如已发出 SQL 的回滚），本期不引入统一补偿机制。

## 文档归档

- `requirement.md` / `design.md` / `tasks.md` / `review-log.md` 完整保留在
  `docs/spec/agent-adapter-refactor-v3/`。
- `tasks.md` 全部 51 项 checkbox 已勾选 ``[x]``。
