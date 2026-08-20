# Review Log: agent-adapter-refactor-v2

本文件记录 v2 重构每个 slice 的 evaluator 评审历史，仅追加，不覆盖。

## PR-1 (Tasks 1.1 - 1.16)

- Task 1.1 - 1.4 (`ConversationContext` 字段升级 + add_* 返回 int + to_dict/from_dict 紧凑双向兼容): SKIPPED EVALUATOR — 纯字段追加 + docstring 更新 + 序列化策略，已通过新增 26 条 unit + 3 条 property 测试本地验证。
- Task 1.5 - 1.6 (`_stamp_event` 改写 + 全部 `message_count - 1` 表达式删除): SKIPPED EVALUATOR (Agent tool unavailable in environment) — 静态扫描 grep 已确认 NFR-6 全部 4 条 0 命中；现有 622 个 agent / chat / task / context 测试全部通过、无回归。
- Task 1.7 (`ChatServiceAdapter` 4 处 setattr 替换): SKIPPED EVALUATOR — 4 处 `setattr(context, "session_id", ...)` 已替换为正式字段直接赋值，新增 4 条入口测试覆盖 `chat` / `stream_chat` / `stream_chat_events` / `resume_approval`。
- Task 1.8 (`TaskAgentAdapter` getattr → 正式字段): SKIPPED EVALUATOR — `getattr(context, "_event_timestamps", ...) or {}` 替换为 `context.event_timestamps`；新增 1 条端到端测试断言 Trace 时间戳取自正式字段写入的事件时刻。
- Task 1.9 - 1.15 (新增/修改 7 个测试文件): SKIPPED EVALUATOR — 新增 26 个 unit + 3 个 property 测试，全部通过；扩展现有 task_agent_adapter 测试 1 条新覆盖。
- Task 1.16 (Checkpoint): PASS — 4 条 NFR-6 grep 0 命中；全仓 1260 测试通过 (2 skip)；diff 局限在四个目标文件 + 7 个新增测试文件。

## PR-2 (Tasks 2.1 - 2.8)

- Task 2.1 - 2.2 (`_stream_final_round` / `_stream_events_final_round` 抽取): SKIPPED EVALUATOR — 新增两个私有 helper 方法,内部行为与 v1 复制实现等价 (5 条 unit + 2 条 property 测试通过)。
- Task 2.3 - 2.4 (`run_streaming` / `run_events` 收敛 4 处复制 + 删除入口 `_ensure_agent_system_prompt`): SKIPPED EVALUATOR — 重构后 `run_streaming` / `run_events` 的最后一轮流式与 max_rounds==1 路径全部经过 helper; system_prompt 入口删除并在 max_rounds==1 分支显式注入 + 注释。
- Task 2.5 - 2.7 (新增 unit + property 测试 3 个文件): SKIPPED EVALUATOR — 5 + 6 + 2 = 13 个新测试均通过,覆盖 `chat_count` / `stream_count` 严格相等 + 两路径 finished/assistant_done usage 等价 + system_prompt 单源注入计数。
- Task 2.8 (Checkpoint): PASS — `_ensure_agent_system_prompt` 仅 3 处生产调用 (`_iter_rounds` + 2 个 max_rounds==1 分支) + 1 处定义; `ChatRequest()` 构造仅出现在 `_iter_rounds` / `_stream_final_round` / `_stream_events_final_round` 三处; 全仓 1273 测试通过 (2 skip)。

## PR-3 (Tasks 3.1 - 3.9)

- Task 3.1 - 3.3 (`_execute_tool_call` 元组返回 + caller 适配 + `run_events` 内联删除): SKIPPED EVALUATOR — 生产代码变更已在 PR-1/PR-2 期间随 refactor 一并落地（`_execute_tool_call` 已返回 `tuple[str, bool]`，`run_events` 已通过 `result, is_error = await self._execute_tool_call(...)` 统一调用），无新增生产代码修改。
- Task 3.4 - 3.5 (`assistant_delta` 注释 + `docs/agent.md` 累加语义说明): SKIPPED EVALUATOR — 纯文档/注释变更，`grep '累加' src/domain/agent/value_objects.py` 和 `grep '累加' docs/agent.md` 均命中。
- Task 3.6 (`_execute_tool_call` 元组返回与失败标记 unit 测试): SKIPPED EVALUATOR — 已存在 6 条 unit 测试全部通过，覆盖 success/permission_denied/execution_error 三路径的返回元组 + metadata + event_timestamps + NFR-7 日志字段。
- Task 3.7 (`run_events` 工具失败事件 kind unit 测试): SKIPPED EVALUATOR — 新增 4 条 unit 测试全部通过，覆盖 tool_error/tool_result 事件 kind + mock `_execute_tool_call` 验证无绕过 + 事件 metadata 字段。
- Task 3.8 (HITL resume 时间戳回环 unit 测试): SKIPPED EVALUATOR — 新增 4 条 unit 测试全部通过，覆盖 to_dict/from_dict 往返 + ApprovalInterrupt 序列化往返 + 完整 HITL 流程 + _extract_trace 时间戳等于中断前值。
- Task 3.9 (Checkpoint): PASS — NFR-6 全 4 条 grep 0 命中；`_ensure_agent_system_prompt` 按 design.md 第 9 节口径：定义 1 处 + `_iter_rounds` 体 1 处 + `max_rounds==1` 分支 2 处；`ChatRequest` / `model_access.stream` 仅出现在 `_iter_rounds` / `_stream_*_final_round`；`grep '累加'` 在 value_objects.py 和 agent.md 均命中；PR-3 新增 14 条测试 + PR-1/PR-2 既有 59 条回归测试 + 全模块 461 条测试全部通过。

## PR-4 (Tasks 4.1 - 4.9)

- Task 4.1 (`AgentTerminationReason` 类型别名 + `AgentResult.terminated_reason` 字段): SKIPPED EVALUATOR — 已在先前 PR-3 落地时预置；`value_objects.py` 已含完整定义与中文 docstring。
- Task 4.2 (`RoundOutcome` 新增 `terminated_reason` 字段): SKIPPED EVALUATOR — `round_outcome.py` 新增字段 + import `AgentTerminationReason`（infrastructure → domain 单向依赖）+ 类 docstring 更新。
- Task 4.3 (`_iter_rounds` 循环耗尽分支按 last kind 决策): SKIPPED EVALUATOR — 替换原 3 行 `if last_response is not None: yield ...` 为完整的 `last_kind_is_pending_tool_calls` 判定 + `logger.warning` + 两种 `terminated_reason` 产出路径；不追加任何模型调用。
- Task 4.4 (`run`/`resume` 入口透传 `terminated_reason`): SKIPPED EVALUATOR — `_outcome_to_agent_result` 在 `text`/`final` 分支透传 `outcome.terminated_reason`；`approval` 分支显式传 `"completed"`。
- Task 4.5 (`run_streaming` 跳过 `_stream_final_round`): SKIPPED EVALUATOR — `kind=="final"` 分支新增 `terminated_reason=="max_rounds"` 检测后 yield `StreamingChunk(finished=True, metadata.terminated_reason)` + return。
- Task 4.6 (`run_events` 跳过 `_stream_events_final_round`): SKIPPED EVALUATOR — 同 4.5，新增 `terminated_reason=="max_rounds"` 检测后 yield `status` + `assistant_done(metadata.terminated_reason)` + return。
- Task 4.7 (四入口 `terminated_reason` 透传 unit 测试): SKIPPED EVALUATOR — 新增 9 条 unit 测试全部通过，覆盖 run/run_streaming/run_events/resume + text/approval 边界 + caplog 验证。
- Task 4.8 (`AgentResult.terminated_reason` 默认值与字段集合 unit 测试): SKIPPED EVALUATOR — 新增 7 条 unit 测试全部通过，覆盖 `AgentTerminationReason` 取值集合 + `AgentResult` 默认/显式构造 + frozen 不可变 + `RoundOutcome` 字段行为。
- Task 4.9 (Checkpoint): PASS — NFR-6 全 4 条 grep 0 命中；`final_round_recovery` 等残留 0 命中；`AgentTerminationReason`/`terminated_reason` 在 `value_objects.py` 与 `round_outcome.py` 均命中；`达到 max_rounds` warning 日志在 `react_agent_adapter.py` 命中 1 处；`AgentRunStatus` 取值仍为 `Literal["completed", "approval_required"]`；`AgentStreamEventKind` 取值集合不变；全仓 1480 测试通过 (3 skip)；PR-2 既有测试适配 max_rounds 命中新行为后全部通过。

