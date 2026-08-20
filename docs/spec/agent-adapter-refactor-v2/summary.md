# Summary：Agent Adapter Refactor v2（三入口轮次复用收口与领域字段升级）

## Feature

- **Slug**: `agent-adapter-refactor-v2`
- **Spec 目录**: `docs/spec/agent-adapter-refactor-v2/`
- **完成日期**: 2026-06-03
- **状态**: ✅ 已交付，最终评审 PASS

## 最终产物清单

### Spec 三件套
- `requirement.md` — 8 项功能需求 + 7 项非功能需求 + 9 项 Property
- `design.md` — 12 个组件、4 个 PR 拆分、四入口透传序列图
- `tasks.md` — 4 个 PR 共 38 个子任务（全部 `[x]`）
- `review-log.md` — 4 个 PR 的完整审计记录

### 后端代码（`epsilon-boot/`）

**领域层**
- `src/domain/chat/context.py` — `event_timestamps: dict[int, int]` / `session_id: str | None` 升级为正式字段；`add_assistant_message_with_tool_calls` / `add_tool_result` 返回 `int`；`to_dict` 紧凑序列化；`from_dict` 三类向后兼容
- `src/domain/agent/value_objects.py` — `AgentTerminationReason = Literal["completed", "max_rounds"]`；`AgentResult.terminated_reason` 末尾追加；`AgentStreamEventKind.assistant_delta` 累加语义注释

**基础设施层**
- `src/infrastructure/agent/round_outcome.py` — `RoundOutcome.terminated_reason` 末尾追加
- `src/infrastructure/agent/react_agent_adapter.py` —
  - `_stream_final_round` / `_stream_events_final_round` 抽出（4 处复制 → 2 个 helper）
  - `_execute_tool_call` 返回 `tuple[str, bool]` + 失败时 `ToolMessage.metadata["error"] = True`
  - `_iter_rounds` 循环耗尽分支按 last kind 决策 `terminated_reason`，**不**追加任何模型调用
  - `_stamp_event` 写入正式字段（不再 setattr 隐式挂载）
  - `run_streaming` / `run_events` 在 `terminated_reason == "max_rounds"` 时跳过 `_stream_*_final_round`
- `src/infrastructure/chat/chat_service_adapter.py` — 4 处 `setattr(context, "session_id", ...)` 替换为正式字段直接赋值
- `src/infrastructure/task/task_agent_adapter.py` — `getattr(context, "_event_timestamps", ...)` 替换为 `context.event_timestamps`

### 测试（`epsilon-boot/test/`）
新增/修改 14 个测试文件，覆盖 unit + property + 序列化回环 + 四入口 + HITL resume：

| PR | 文件 |
|---|---|
| PR-1 | `test/domain/chat/test_context_add_returns_index_unit.py` |
| PR-1 | `test/domain/chat/test_context_event_timestamps_serialization_unit.py` |
| PR-1 | `test/domain/chat/test_context_session_id_unit.py` |
| PR-1 | `test/domain/chat/test_context_add_returns_index_property.py` |
| PR-1 | `test/domain/chat/test_context_serialization_roundtrip_property.py` |
| PR-1 | `test/infrastructure/chat/test_chat_service_adapter_session_id_unit.py` |
| PR-1 | `test/infrastructure/task/test_task_agent_adapter_unit.py` (扩展) |
| PR-2 | `test/infrastructure/agent/test_react_agent_final_round_helper_unit.py` |
| PR-2 | `test/infrastructure/agent/test_react_agent_final_round_helper_property.py` |
| PR-2 | `test/infrastructure/agent/test_react_agent_system_prompt_single_site_unit.py` |
| PR-3 | `test/infrastructure/agent/test_react_agent_execute_tool_call_tuple_unit.py` |
| PR-3 | `test/infrastructure/agent/test_react_agent_run_events_tool_failure_unit.py` |
| PR-3 | `test/infrastructure/agent/test_react_agent_hitl_resume_timestamp_roundtrip_unit.py` |
| PR-4 | `test/infrastructure/agent/test_react_agent_max_rounds_terminated_reason_unit.py` |
| PR-4 | `test/domain/agent/test_value_objects_terminated_reason_unit.py` |

### 文档
- `docs/agent.md` — 新增 `run_events` 输出格式说明 + `assistant_delta` 累加语义段落

## 关键设计决策

### 1. PR 拆分：4 个独立 PR + 依赖图
```
PR-1 (Context 字段升级 + add_* 返回 int + setattr 清理)
  ├─→ PR-2 (Final_Round_Stream_Helper + system_prompt 收口)
  │     └─→ PR-4 (terminated_reason 暴露 + max_rounds 命中告警)
  └─→ PR-3 (_execute_tool_call 元组 + run_events 复用)
```
PR-2 与 PR-3 之间无强耦合，可并行 review；PR-4 依赖 PR-2 的 `_stream_*_final_round` helper（用于跳过判定）。

### 2. PR-4：业内共识方案（终决稿）
**早期草案** `Final_Round_Recovery_Chat`（在循环耗尽时追加一次 chat 回灌）经讨论后**撤销**，改为业内主流框架共识：

| 框架 | 命中 max_rounds 时 | 本期 v2 方案 |
|---|---|---|
| OpenAI Assistants | Run → `incomplete` + `incomplete_details.reason` | `AgentResult.terminated_reason="max_rounds"` |
| LangGraph | 抛 `GraphRecursionError` | `terminated_reason` 字段（更轻量、不抛异常） |
| CrewAI | `max_iter` failed | 同上 |
| AutoGPT | 硬停 + "cycle budget exhausted" 标记 | 同上 |

**选择理由**：
- 把"轮数超限"信号原样暴露给调用方，由顶层编排决策续跑或终止
- 不在 Agent 内部做"recovery chat"补救：避免掩盖超限信号、阻碍长跑续跑、叠加额外推理成本
- NFR-1（模型调用次数严格不变）由此自然成立

### 3. ConversationContext 字段升级（PR-1）
将之前通过 `setattr(context, "_event_timestamps", ...)` / `setattr(context, "session_id", ...)` 隐式挂载的属性，提升为 `__init__` 显式初始化的正式字段，并参与 `to_dict` / `from_dict` 序列化（紧凑策略 + 三类向后兼容）。

### 4. `_execute_tool_call` 元组返回 + 失败 metadata（PR-3）
返回类型由 `str` 改为 `tuple[str, bool]`，失败时 `ToolMessage.metadata["error"] = True`。`run_events` 内联工具执行块整段删除，复用 `_execute_tool_call` 后按 `is_error` 切换 `tool_error` / `tool_result` 事件 kind。

### 5. `Final_Round_Stream_Helper` 抽取（PR-2）
`run_streaming` / `run_events` 各 2 处复制实现（共 4 处）收敛为 `_stream_final_round` + `_stream_events_final_round` 两个 helper；`_ensure_agent_system_prompt` 收口至 `_iter_rounds` + `max_rounds == 1` 分支两类位置。

## 验证结果

### 静态扫描（NFR-6 + recovery 残留，全部 0 命中）
```
grep -rn "setattr(context," src/                                       → 0
grep -rn "context.message_count - 1" src/infrastructure/agent/         → 0
grep -rn 'getattr(context, "_event_timestamps"' src/                   → 0
grep -rn 'getattr(context, "session_id"' src/                          → 0
grep -rn "final_round_recovery|recovery_response|Final_Round_Recovery_Chat|Max_Rounds_Recovery_Warning" src/ → 0
```

### 回归测试
```
uv run pytest -q  → 1480 passed, 3 skipped in 40.57s
```

### 不变量保持（NFR-2）
- `AgentResult.status` 取值集合：`Literal["completed", "approval_required"]`（不变）
- `AgentStreamEvent.kind` 取值集合：8 种（不变）
- `StreamingChunk` 字段集合：不变（max_rounds 命中只走 `metadata.terminated_reason`）
- `AgentResult` / `RoundOutcome` 新字段：末尾追加 + 带默认值，既有构造未破坏

### 模型调用次数（NFR-1，严格不变）
| 入口 | max_rounds=N 全程未命中 | max_rounds=N 命中循环耗尽 | max_rounds=1 |
|---|---|---|---|
| `run` | N 次 chat | N 次 chat（不补救） | 1 次 chat |
| `run_streaming` | N-1 次 chat + 1 次 stream | N-1 次 chat + **0** 次 stream（跳过 helper） | 1 次 stream |
| `run_events` | 同 `run_streaming` | 同 `run_streaming` | 同 `run_streaming` |
| `resume` | 同 `run` | 同 `run` | — |

## 测试覆盖

- **单元测试**: 14 个新增/扩展文件，覆盖序列化、四入口、tool 元组、HITL resume、max_rounds 透传等
- **Property 测试**: `add_*` 返回索引、`to_dict/from_dict` 往返、`Final_Round_Stream_Helper` 等价性
- **回归测试**: 全仓 1480 passed（含 v1 既有测试）

## Follow-ups（后续可选优化，不在本期范围）

1. **自主续跑机制**：基于 `terminated_reason="max_rounds"` 信号，由顶层 orchestrator（`ChatServiceAdapter` / `TaskAgentAdapter` / 前端）实现"用户确认后追加预算续跑"等高阶策略——本期仅暴露信号，不实现策略
2. **更多 termination reason**：未来如需引入 `tool_budget_exhausted` / `time_limit` 等新取值时，扩展 `AgentTerminationReason` Literal 并按需增加对应入口分支
3. **`assistant_delta` 累加语义**：本期仅在代码注释 + `docs/agent.md` 文档化，前端未做改动；如未来需要增加"严格分块"模式，可考虑在 `AgentStreamEvent.metadata` 中增加 `is_final_chunk` 等可选标记
4. **测试套：HITL resume 期间命中 max_rounds**：当前测试覆盖 `resume` 普通耗尽，未覆盖"resume 后再次进入 HITL 中断"+"resume 跑到底命中 max_rounds"组合极端路径

## 备注

- **依赖管理**：本期未调整 `pyproject.toml`，无新增依赖
- **配置**：本期未新增 `config.properties` 配置键
- **DDD 边界**：所有 import 方向严格 `infrastructure → domain` 单向
- **中文 docstring**：所有新增/修改公开符号已配中文 docstring（NFR-5）
- **HTTP/SSE 契约**：本期未变更，前端代码未改动
- **业内共识方案**：PR-4 与 OpenAI Assistants `incomplete_details.reason` 模型对齐；早期 `Final_Round_Recovery_Chat` 草案因业内零先例 + 阻碍长跑续跑被撤销

skills_used: spec-dev
