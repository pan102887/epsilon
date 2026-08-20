# 实现计划：Agent Adapter Refactor v2（三入口轮次复用收口与领域字段升级）

## 概述

本期是 v1 落地后的第二轮内部质量重构，按 design.md 的 4 个 PR 拆分顺序推进：

```
PR-1 (Context 字段升级 + add_* 返回 int + setattr 清理)
  ├─→ PR-2 (Final_Round_Stream_Helper + system_prompt 收口)
  │     └─→ PR-4 (AgentResult.terminated_reason 暴露 + max_rounds 命中告警)
  └─→ PR-3 (_execute_tool_call 元组 + run_events 复用 + HITL resume 测试 + assistant_delta 文档)
```

PR-2 与 PR-3 之间无强耦合，可并行 review；建议按 PR-1 → PR-2 → PR-3 → PR-4 顺序合入。整个 v2 重构完成后，需求 1-8 与 NFR-1 至 NFR-7 全部满足，4 条静态扫描 grep 均为 0 命中。

本期严格内部重构：不引入新 Port、不调整 HTTP/SSE 契约、不调整审批语义、不新增配置键、不调整模型路由、前端代码不变。

## Tasks

- [x] 1. PR-1：`ConversationContext` 字段升级与序列化（领域层 + 基础设施层 setattr 清理）

  - [x] 1.1 在 `ConversationContext` 引入 `event_timestamps` / `session_id` 正式字段
    - 修改 `epsilon-boot/src/domain/chat/context.py`
    - 在 `ConversationContext.__init__` 中初始化 `self.event_timestamps: dict[int, int] = {}` 与 `self.session_id: str | None = None`（约 5 行新增）
    - 更新类 docstring 的 `Attributes` 段，新增 `event_timestamps` 与 `session_id` 两条说明（中文，按 `docs/steering/code-documentation.md`）；含义见 design.md 第 5 节
    - 严格仅使用 Python 标准库类型（`dict[int, int]` / `str | None`），不得引入 ORM、Pydantic、Redis 类型
    - _需求: 5.1, 5.2, NFR-3, NFR-5_

  - [x] 1.2 修改 `add_assistant_message_with_tool_calls` 与 `add_tool_result` 返回 int
    - 修改 `epsilon-boot/src/domain/chat/context.py`
    - `ConversationContext.add_assistant_message_with_tool_calls(content, tool_calls)` 返回 `int`（追加后 `len(self._messages) - 1`）
    - `ConversationContext.add_tool_result(tool_name, result, tool_call_id="")` 返回 `int`（追加后 `len(self._messages) - 1`）
    - `add_assistant_message` / `add_user_message` / `add_system_message` 不强制改造，保持 `None` 返回
    - 中文 docstring 在 `Returns` 段说明索引语义（"即追加后 `len(_messages) - 1`"）
    - _需求: 4.1, 4.2, 4.3, 4.7, NFR-5_

  - [x] 1.3 实现 `to_dict` 紧凑序列化策略
    - 修改 `epsilon-boot/src/domain/chat/context.py:ConversationContext.to_dict`
    - 仅在 `self.event_timestamps` 非空时附加 `event_timestamps` 键；仅在 `self.session_id is not None` 时附加 `session_id` 键
    - `event_timestamps` 写入时拷贝一份（`dict(self.event_timestamps)`）避免外部修改污染
    - 中文 docstring 解释紧凑策略与 NFR-4 向后兼容口径
    - _需求: 5.3, NFR-4, NFR-5_

  - [x] 1.4 实现 `from_dict` 双向兼容反序列化
    - 修改 `epsilon-boot/src/domain/chat/context.py:ConversationContext.from_dict`
    - 缺失 `event_timestamps` 视为空 dict；缺失 `session_id` 或值为 null 视为 `None`
    - 含 `event_timestamps` 时显式 `int(k): int(v)` 还原 `dict[int, int]`（JSON 不支持 int 键）
    - 兼容 v1 旧格式：仅含 `messages`（可能含被忽略的 `max_messages`）
    - 中文 docstring 列出 3 类兼容形态
    - _需求: 5.4, 5.5, NFR-4, NFR-5_

  - [x] 1.5 修改 `_stamp_event` 写入 `event_timestamps` 正式字段
    - 修改 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py:ReActAgentAdapter._stamp_event`
    - 由 `setattr(context, "_event_timestamps", ...)` / `getattr(...)` 隐式挂载改为 `context.event_timestamps[message_index] = int(time.time() * 1000)`
    - 中文 docstring 说明"已通过 `__init__` 保证字段在所有实例上以空 dict 形态存在，无需懒创建"
    - _需求: 5.6, NFR-3, NFR-5_

  - [x] 1.6 删除 `react_agent_adapter.py` 中所有 `context.message_count - 1` 表达式
    - 修改 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py`
    - `_record_assistant_with_tool_calls`：改为 `msg_index = context.add_assistant_message_with_tool_calls(...)`；后续 `_stamp_event(context, msg_index)`
    - `_apply_approval_decisions` 的 `reject` 分支：改为 `msg_index = context.add_tool_result(...); self._stamp_event(context, msg_index)`
    - `_execute_tool_call`（PR-1 范围内仍是单返回值 `str`）：改为 `msg_index = context.add_tool_result(...); self._stamp_event(context, msg_index)`
    - `run_events` 内联工具执行块的 `add_tool_result + _stamp_event(... message_count - 1)`：本 PR 临时改为 `msg_index = context.add_tool_result(...); self._stamp_event(context, msg_index)`，整段内联实现的彻底删除留到 PR-3
    - 文件中不得残留任何 `context.message_count - 1` 表达式
    - _需求: 4.4, 4.5, 4.6, NFR-6_

  - [x] 1.7 `ChatServiceAdapter` 4 处 setattr 替换为正式字段赋值
    - 修改 `epsilon-boot/src/infrastructure/chat/chat_service_adapter.py`
    - 4 处 `setattr(context, "session_id", request.session_id)`（约第 197 / 294 / 362 / 423 行）替换为 `context.session_id = request.session_id`
    - `_save_interrupt` 中 `context.session_id if hasattr(context, "session_id") else ""` 简化为 `context.session_id or ""`
    - _需求: 5.8, 5.9, NFR-6_

  - [x] 1.8 `TaskAgentAdapter._extract_trace` 调用处由 getattr 改为正式字段
    - 修改 `epsilon-boot/src/infrastructure/task/task_agent_adapter.py`
    - `execute()` 第 269 行附近的 `getattr(context, "_event_timestamps", {}) or {}` 替换为 `context.event_timestamps`
    - `_extract_trace` 内部 `event_timestamps: dict[int, int] | None = None` 签名与 `stamps = event_timestamps or {}` 函数体保持不变
    - _需求: 5.7, 5.9, NFR-6_

  - [x] 1.9 单元测试：`add_*` 返回索引
    - 新增 `epsilon-boot/test/domain/chat/test_context_add_returns_index_unit.py`
    - 覆盖：(a) `add_assistant_message_with_tool_calls("", [tc])` 返回 0；(b) 连续两次返回 0、1；(c) `add_tool_result(...)` 返回 `prev_count`；(d) 断言 `returned_index == ctx.message_count - 1`
    - _需求: 4.1, 4.2, 4.8, Property 6_

  - [x] 1.10 单元测试：`event_timestamps` 序列化双向兼容
    - 新增 `epsilon-boot/test/domain/chat/test_context_event_timestamps_serialization_unit.py`
    - 覆盖：(a) 默认实例 `to_dict()` 仅含 `messages` 键；(b) 写入 `event_timestamps[k]=t` 后 `to_dict()` 含 `event_timestamps`；(c) `from_dict({"messages": [...]})` 还原后 `event_timestamps == {}` 且 `session_id is None`；(d) `from_dict(to_dict(ctx))` 往返；(e) JSON 字符串键还原为 int 键；(f) 仅含 `event_timestamps` / 仅含 `session_id` 的混合旧格式
    - _需求: 5.1, 5.2, 5.3, 5.4, 5.5, 5.10, Property 7_

  - [x] 1.11 单元测试：`session_id` 字段升级
    - 新增 `epsilon-boot/test/domain/chat/test_context_session_id_unit.py`
    - 覆盖：`ctx.session_id = "sess-1"` 直接赋值生效；默认值 `None`；通过 `to_dict` / `from_dict` 回环
    - _需求: 5.2, 5.5, 5.8_

  - [x] 1.12 单元测试：`ChatServiceAdapter.session_id` 写入回归
    - 修改 `epsilon-boot/test/infrastructure/chat/test_chat_service_adapter_session_id_unit.py`（若不存在则新增）
    - 4 处入口断言 `context.session_id == request.session_id`（直接赋值生效，不再依赖 setattr）
    - _需求: 5.8, 5.9_

  - [x] 1.13 修改测试：`TaskAgentAdapter._extract_trace` 时间戳来源
    - 修改 `epsilon-boot/test/infrastructure/task/test_task_agent_adapter_unit.py`
    - 覆盖：(a) `_extract_trace` 通过 `context.event_timestamps` 直接读取（不再 `getattr`）；(b) Trace 时间戳取自事件时刻（mock `time.time` 在事件发生时返回 1000，断言 `trace[i].timestamp_ms == 1000_000`）
    - _需求: 5.7, Property 3 部分_

  - [x] 1.14 Property 测试：`add_*` 返回索引性质
    - 新增 `epsilon-boot/test/domain/chat/test_context_add_returns_index_property.py`
    - 对 `n ∈ [0, 50]` 次随机混合调用，所有返回值 ≥ 0、严格单调递增 1、且每次返回值等于该次调用后的 `message_count - 1`
    - _需求: 4.1, 4.2, Property 6_

  - [x] 1.15 Property 测试：`ConversationContext` 序列化往返
    - 新增 `epsilon-boot/test/domain/chat/test_context_serialization_roundtrip_property.py`
    - 对随机生成的 `(messages, event_timestamps, session_id)` 三元组构造 ctx，断言 `from_dict(to_dict(ctx)) == ctx`（按 messages 列表内容 + event_timestamps + session_id 比对）
    - _需求: 5.3, 5.4, 5.5, Property 7_

  - [x] 1.16 Checkpoint：PR-1 静态扫描与测试
    - 在 `epsilon-boot/` 目录下执行：
      - `grep -rn "setattr(context," src/`（应 0 命中）
      - `grep -rn "getattr(context, \"_event_timestamps\"" src/`（应 0 命中）
      - `grep -rn "getattr(context, \"session_id\"" src/`（应 0 命中）
      - `grep -rn "context.message_count - 1" src/infrastructure/agent/`（应 0 命中）
    - 运行新增/修改的单元测试与 property 测试，全部通过
    - _需求: 4.6, 5.6, 5.7, 5.8, 5.9, NFR-6_

- [x] 2. PR-2：`Final_Round_Stream_Helper` 抽取 + `system_prompt` 注入收口

  - [x] 2.1 新增 `_stream_final_round` 私有方法
    - 修改 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py`
    - 类内新增 `async def _stream_final_round(self, context, config, model_access, base_usage) -> AsyncIterator[StreamingChunk]`，签名与 docstring 见 design.md 第 2 节
    - 内部步骤：`self._context_builder.build(...)` → `total_usage = merge_usage(base_usage 副本, builder_result.usage)` → 组装 `ChatRequest(messages=builder_result.serialized_messages, model=config.model, tools=config.tool_schemas)` → `model_access.stream(chat_request)`
    - 逐分片：`finished` 时合并 `total_usage | (chunk.usage or {})` 并产出 `StreamingChunk(delta_content, finished=True, usage=合并后, metadata=chunk.metadata)`；否则原样产出 `chunk`
    - 中文 docstring 说明 NFR-1 模型调用次数不变
    - _需求: 2.1, 2.7, 2.8, 2.9, NFR-1, NFR-5_

  - [x] 2.2 新增 `_stream_events_final_round` 私有方法
    - 修改 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py`
    - 类内新增 `async def _stream_events_final_round(self, context, config, model_access, base_usage, round_num) -> AsyncIterator[AgentStreamEvent]`，签名与 docstring 见 design.md 第 3 节
    - 内部步骤：`build` → `total_usage = merge_usage(base_usage, builder_result.usage)` → 组装 `ChatRequest` → `model_access.stream(...)`
    - 逐分片：`chunk.delta_content` 非空时 yield `AgentStreamEvent(kind="assistant_delta", content=...)`；`chunk.finished` 时 yield `AgentStreamEvent(kind="assistant_done", usage=merge_usage(total_usage, chunk.usage or {}), metadata={"round": round_num})`
    - 显式说明本方法不产出 `status` 事件，由调用方在进入最后一轮前自行 yield
    - _需求: 2.2, 2.7, 2.8, 2.9, NFR-1, NFR-5_

  - [x] 2.3 `run_streaming` 收敛 2 处复制为 `_stream_final_round` 调用
    - 修改 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py:ReActAgentAdapter.run_streaming`
    - `max_rounds == 1` 分支替换为 `async for chunk in self._stream_final_round(context, config, model_access, base_usage={}): yield chunk`（约 715-736 行 → 1 行）
    - "中间轮次耗尽"分支替换为 `async for chunk in self._stream_final_round(context, config, model_access, base_usage=last_usage): yield chunk`（约 774-794 行 → 1 行）
    - 删除入口处的 `_ensure_agent_system_prompt(context, config)` 调用
    - `max_rounds == 1` 分支显式调用 `self._ensure_agent_system_prompt(context, config)` 并加注释（按 design.md 第 9 节模板）
    - _需求: 1.1, 1.2, 1.3, 1.4, 2.3, 2.4, 2.7, 2.8_

  - [x] 2.4 `run_events` 收敛 2 处复制为 `_stream_events_final_round` 调用
    - 修改 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py:ReActAgentAdapter.run_events`
    - `max_rounds == 1` 分支：先 `yield AgentStreamEvent(kind="status", ..., metadata={"round": 1})`，再 `async for ev in self._stream_events_final_round(context, config, model_access, base_usage={}, round_num=1): yield ev`（约 808-837 行 → 数行）
    - "中间轮次耗尽"分支：先 yield 最终轮次的 `status` 事件，再 `async for ev in self._stream_events_final_round(context, config, model_access, base_usage=last_usage, round_num=config.max_rounds): yield ev`（约 942-970 行 → 数行）
    - 删除入口处的 `_ensure_agent_system_prompt(context, config)` 调用
    - `max_rounds == 1` 分支显式调用 `self._ensure_agent_system_prompt(context, config)` 并加注释
    - _需求: 1.1, 1.2, 1.3, 1.4, 2.5, 2.6, 2.7, 2.8_

  - [x] 2.5 单元测试：`Final_Round_Stream_Helper` 行为
    - 新增 `epsilon-boot/test/infrastructure/agent/test_react_agent_final_round_helper_unit.py`
    - 覆盖：(a) `run_streaming` 在 `max_rounds == 1` 时通过 `_stream_final_round` 完成产出，`model_access.stream.call_count == 1` 且 `chat.call_count == 0`；(b) `run_streaming` 在 `max_rounds == 3` 中间轮次都返回 tool_calls 时调用 `chat` 2 次 + `stream` 1 次；(c) `run_events` 同 (a)(b)；(d) 两路径产出的 `finished=True` 分片 `usage` 字段值相同（不变量回归）
    - _需求: 2.1-2.9, NFR-1, Property 2_

  - [x] 2.6 单元测试：`system_prompt` 单源注入
    - 新增 `epsilon-boot/test/infrastructure/agent/test_react_agent_system_prompt_single_site_unit.py`（或扩展现有 system_prompt 注入测试）
    - 覆盖：(a) `run_streaming` 入口处不再调用 `_ensure_agent_system_prompt`（通过 mock 计数）；(b) `max_rounds == 1` 分支下 SystemMessage 仅注入一次；(c) `max_rounds > 1` 分支下 `_iter_rounds` 内注入一次；(d) 多次连续调用 `run_streaming` 共享 context 时 SystemMessage 数量不增加
    - _需求: 1.1, 1.2, 1.3, 1.4, 1.5, Property 1_

  - [x] 2.7 Property 测试：`Final_Round_Stream_Helper` 等价性
    - 新增 `epsilon-boot/test/infrastructure/agent/test_react_agent_final_round_helper_property.py`
    - 对随机生成的 `(max_rounds: int in [1, 5], 中间轮次 tool_calls 数量, 最终 stream 分片序列)`，断言 `_stream_final_round` 在 `max_rounds == 1` 与"中间轮次耗尽"两路径产出的 `StreamingChunk` 序列在 (a) `delta_content` 拼接结果、(b) `finished=True` 分片的 `metadata` 字典、(c) `finished=True` 分片的 `usage` 在所有 key 上数值相等
    - `_stream_events_final_round` 同理覆盖 `assistant_delta` + `assistant_done` 序列
    - _需求: 2.7, Property 2_

  - [x] 2.8 Checkpoint：PR-2 静态扫描与测试
    - 在 `epsilon-boot/` 目录下执行：
      - `grep -rn "_ensure_agent_system_prompt" src/infrastructure/agent/react_agent_adapter.py | grep -v "_iter_rounds"`：除 `_iter_rounds` 入口与 `max_rounds == 1` 分支外应仅剩定义本身（共 3 处调用对应 2 类位置）
      - `grep -rn "build.*ChatRequest\|model_access\.stream" src/infrastructure/agent/react_agent_adapter.py`：应仅出现在 `_stream_final_round` / `_stream_events_final_round` 与 `_iter_rounds` 中
    - 运行 PR-2 新增的单元测试与 property 测试，全部通过；同时运行 PR-1 既有测试，确认无回归
    - _需求: 1.5, 2.7, NFR-6_

- [x] 3. PR-3：`_execute_tool_call` 元组返回 + `run_events` 复用 + HITL resume 时间戳测试 + `assistant_delta` 文档化

  - [x] 3.1 修改 `_execute_tool_call` 返回元组并写入失败标记
    - 修改 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py:ReActAgentAdapter._execute_tool_call`
    - 返回类型由 `str` 改为 `tuple[str, bool]`（二元组：`(result, is_error)`）
    - 实现按 design.md 第 4 节：`try` 中执行 `_ensure_tool_authorized` + `_tool_registry.execute`；`ToolPermissionDeniedError` 与 `Exception` 均设 `is_error=True`、`result=str(exc)`、调用 `_log_tool_failure(...)`
    - `msg_index = context.add_tool_result(...)`；`is_error=True` 时通过 `context.get_messages()[msg_index]` 取出 `ToolMessage` 并 `msg.metadata["error"] = True`
    - 末尾调用 `self._stamp_event(context, msg_index)` 后 `return result, is_error`
    - 中文 docstring 在 `Returns` 段说明二元组语义；说明成功时不写 `error` 键（与 `to_dict()` 既有"非空 metadata 才输出"语义自然对齐）
    - _需求: 3.1, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, NFR-2, NFR-5_

  - [x] 3.2 调用面适配：忽略元组返回值的 caller
    - 修改 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py`
    - `run` 主循环、`_apply_approval_decisions` 的 `approve` / `edit` 分支、`run_streaming` 的 `tool_calls` outcome：调用 `await self._execute_tool_call(...)` 不解包返回值（兼容形式即可，无需显式忽略）
    - 不在外层重复调用 `add_tool_result` / `_stamp_event`
    - _需求: 3.1, 3.2_

  - [x] 3.3 删除 `run_events` 内联工具执行块，改为复用 `_execute_tool_call`
    - 修改 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py:ReActAgentAdapter.run_events`
    - 删除原 870-912 行附近的 "鉴权 → 执行 → 异常 → add_tool_result → _stamp_event" 内联实现
    - 替换为 design.md 第 4.1 节伪代码：先 `yield AgentStreamEvent(kind="tool_start", ..., metadata={"round": outcome.round_num})`；再 `result, is_error = await self._execute_tool_call(context, tool_call, config)`；最后 `yield AgentStreamEvent(kind="tool_error" if is_error else "tool_result", content=result, ..., metadata={"round": outcome.round_num})`
    - 不在外层重复 `add_tool_result` / `_stamp_event`
    - _需求: 3.1, 3.2, 3.3, 3.4, 3.5, 3.7_

  - [x] 3.4 更新 `AgentStreamEventKind.assistant_delta` 注释
    - 修改 `epsilon-boot/src/domain/agent/value_objects.py`
    - 在 `AgentStreamEventKind` 的 `assistant_delta` 取值上方添加注释（按 design.md 第 10.1 节）："累加文本片段：可能为整段（中间轮次直接命中纯文本回复时）也可能为分块（最后一轮 stream 真分片）。客户端必须按累加方式渲染，不要假设每个 assistant_delta 都是单字符或固定长度的'分片'。"
    - 取值集合不变（NFR-2）
    - _需求: 7.1, NFR-2_

  - [x] 3.5 同步 `docs/agent.md` 累加语义说明
    - 修改 `docs/agent.md`
    - 在描述 `run_events` 输出格式的小节后追加 design.md 第 10.2 节给出的段落："`assistant_delta` 事件的 `content` 字段语义为'累加文本片段'……该行为是合规的，不需要前端改动。"
    - 若 `docs/api.md` 涉及 `assistant_delta` 描述，也同步更新
    - _需求: 7.2, 7.5_

  - [x] 3.6 单元测试：`_execute_tool_call` 元组返回与失败标记
    - 新增 `epsilon-boot/test/infrastructure/agent/test_react_agent_execute_tool_call_tuple_unit.py`
    - 覆盖：(a) 工具成功 → 返回 `(result, False)`，`ToolMessage.metadata == {}`，`to_dict()` 不含 `metadata` 键；(b) `ToolPermissionDeniedError` → 返回 `(str(exc), True)`，`ToolMessage.metadata == {"error": True}`，`_log_tool_failure` 的 `reason="permission_denied"`；(c) 运行期 `Exception` → 同 (b)，`reason="execution_error"`；(d) `is_error=True` 时 `_stamp_event` 仍写入 `event_timestamps`
    - 使用 `caplog` 验证 `_log_tool_failure` warning 字段集合不降级（NFR-7）
    - _需求: 3.1, 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, NFR-7, Property 5_

  - [x] 3.7 单元测试：`run_events` 工具失败事件 kind
    - 新增 `epsilon-boot/test/infrastructure/agent/test_react_agent_run_events_tool_failure_unit.py`
    - 覆盖：(a) 工具失败时 `run_events` 产出 `kind="tool_error"` 且 `ToolMessage.metadata == {"error": True}`；(b) 工具成功时产出 `kind="tool_result"` 且 `ToolMessage.metadata == {}`；(c) `run_events` 内不再保留独立的 authorize/execute/except 三段实现（通过 mock `_execute_tool_call` 验证调用 1 次）
    - _需求: 3.1, 3.2, 3.4, 3.5, 3.6, 3.7, Property 5_

  - [x] 3.8 单元测试：HITL resume 时间戳回环
    - 新增 `epsilon-boot/test/infrastructure/agent/test_react_agent_hitl_resume_timestamp_roundtrip_unit.py`
    - 覆盖：(a) 在中断前注入 `event_timestamps[k]=1_717_000_000_000`；(b) 触发 HITL → `ApprovalInterrupt.context_snapshot = ctx.to_dict()` → `approval_interrupt_to_dict` → 持久化（mock store）→ `approval_interrupt_from_dict` → `ConversationContext.from_dict`；(c) resume 后调用 `_extract_trace`，断言相应 `Trace_Entry.timestamp_ms == 1_717_000_000_000`（不是 resume 时刻）
    - _需求: 6.1, 6.2, 6.3, 6.4, 6.5, Property 3_

  - [x] 3.9 Checkpoint：PR-3 静态扫描与全量 grep（NFR-6）
    - 在 `epsilon-boot/` 目录下执行 design.md「验收清单」4 条 grep，全部应 0 命中（除 `_ensure_agent_system_prompt` 这条按 2.8 口径校验）：
      - `grep -rn "_ensure_agent_system_prompt" src/infrastructure/agent/react_agent_adapter.py | grep -v "_iter_rounds"`：仅 `max_rounds == 1` 分支两处显式注入与定义本身
      - `grep -rn "setattr.*event_timestamps\|getattr.*event_timestamps\|setattr.*session_id\|getattr.*session_id" src/`：0 命中
      - `grep -rn "context\.message_count - 1" src/infrastructure/agent/ src/infrastructure/task/ src/infrastructure/chat/`：0 命中
      - `grep -rn "build.*ChatRequest\|model_access\.stream" src/infrastructure/agent/react_agent_adapter.py`：应仅出现在 `_stream_*_final_round` 与 `_iter_rounds` 中
    - 同时验证 NFR-6 原始 4 条：`setattr(context,` / `context.message_count - 1` / `getattr(context, "_event_timestamps"` / `getattr(context, "session_id"` 全 0
    - 验证文档级 lint：`grep '累加' src/domain/agent/value_objects.py` 命中、`grep '累加' docs/agent.md` 命中
    - 运行 PR-3 新增测试 + 全仓回归测试
    - _需求: 3.1-3.9, 5.6-5.9, 6.1-6.5, 7.1-7.5, NFR-6_

- [x] 4. PR-4：`AgentResult.terminated_reason` 暴露 + `max_rounds` 命中告警（业内共识方案）

  > **方案要点**：业内主流 Agent 框架（OpenAI Assistants / LangGraph / CrewAI / AutoGPT）在 `max_rounds` / `recursion_limit` 命中时**不做内部 recovery chat**，而是把超限信号原样暴露给调用方，由顶层编排决策续跑或终止。本 PR 据此实现：新增 `AgentTerminationReason` 类型 + `AgentResult.terminated_reason` 字段 + `RoundOutcome.terminated_reason` 字段；`_iter_rounds` 在循环耗尽且最后一轮 tool_calls 时仅记录 warning + 产出 `terminated_reason="max_rounds"`，**不**追加任何模型调用；流式入口在检测到该信号后**跳过** `_stream_*_final_round`，不发起最后一轮 stream。

  - [x] 4.1 新增 `AgentTerminationReason` 类型别名 + `AgentResult.terminated_reason` 字段
    - 修改 `epsilon-boot/src/domain/agent/value_objects.py`
    - 在 `AgentRunStatus` 定义后追加：`AgentTerminationReason = Literal["completed", "max_rounds"]`，附中文 docstring 说明两个取值的语义（按 design.md 第 11.1 节）
    - 在 `AgentResult` 末尾追加字段 `terminated_reason: AgentTerminationReason = "completed"`（保持 `frozen=True` 与既有字段集合不变）
    - 在 `AgentResult` 类 docstring 的 `Attributes` 段追加 `terminated_reason` 一行：默认 `"completed"`；`"max_rounds"` 表示循环达到 `config.max_rounds` 上限时最后一轮仍返回 tool_calls；与 `status` 正交（`status="approval_required"` 时 `terminated_reason` 保持 `"completed"`）
    - _需求: 8.1, 8.2, 8.3, NFR-2, NFR-5_

  - [x] 4.2 `RoundOutcome` 新增 `terminated_reason` 字段
    - 修改 `epsilon-boot/src/infrastructure/agent/round_outcome.py`
    - 在 `RoundOutcome` 末尾追加字段 `terminated_reason: AgentTerminationReason = "completed"`
    - import `AgentTerminationReason`（从 `domain.agent.value_objects` 导入，保持 `infrastructure → domain` 单向依赖）
    - 类 docstring 的 `Attributes` 段追加：仅在 `kind == "final"` 时具有非默认值；其他 kind 保持默认；本字段供 `_iter_rounds` 在循环耗尽分支按 last kind 区分两种 `final` 形态
    - _需求: 8.4, NFR-2, NFR-5_

  - [x] 4.3 `_iter_rounds` 循环耗尽分支按 last kind 决策 `terminated_reason`
    - 修改 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py:ReActAgentAdapter._iter_rounds`
    - **不**新增 `final_round_recovery` 参数（签名与 v1 保持一致，不做内部补救调用）
    - 按 design.md 第 1 节伪代码实现循环耗尽分支：
      - `last_response is None`：极端边界，直接 return（不 yield 任何 outcome）
      - `last_response.tool_calls` 非空 **且** `messages[-1] isinstance ToolMessage`：
        1. `logger.warning("Agent Loop 达到 max_rounds 仍存在未消费 tool_calls", extra={"round_num": effective_terminal, "tool_call_count": len(last_response.tool_calls)})`（不记录 `tool_call.arguments`）
        2. `yield RoundOutcome(kind="final", round_num=effective_terminal, response=last_response, total_usage=dict(total_usage), terminated_reason="max_rounds")`
      - 其他情形：`yield RoundOutcome(kind="final", round_num=effective_terminal, response=last_response, total_usage=dict(total_usage), terminated_reason="completed")`
    - 边界：本分支**不**追加任何 `model_access.chat(...)` / `model_access.stream(...)` 调用
    - 边界：最后一轮 `kind == "text"` / `kind == "approval"` 已在循环体内自行 yield + return，不进入循环耗尽分支
    - 中文 docstring 在循环耗尽分支前后补充实现说明（业内共识方案、不做补救）
    - _需求: 8.5, 8.6, 8.7, 8.8, NFR-1, NFR-7_

  - [x] 4.4 `run` / `resume` 入口透传 `terminated_reason` 到 `AgentResult`
    - 修改 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py:ReActAgentAdapter.run`
    - 在消费 `outcome.kind == "final"` / `outcome.kind == "text"` 构造 `AgentResult` 时，把 `outcome.terminated_reason` 透传到 `AgentResult.terminated_reason`（按 design.md 第 12.1 节示例）
    - HITL `approval` 分支下构造 `AgentResult(status="approval_required", ..., terminated_reason="completed")`（HITL 中断由 `status` 单独表达，不属于轮数超限）
    - `resume` 入口同 `run`：消费 `kind="final"` 时透传 `outcome.terminated_reason`（design.md 第 12.4 节）
    - _需求: 8.9, 8.10, NFR-2_

  - [x] 4.5 `run_streaming` 入口在 `terminated_reason="max_rounds"` 时跳过 `_stream_final_round`
    - 修改 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py:ReActAgentAdapter.run_streaming`
    - 在消费 `outcome.kind == "final"` 时检测 `outcome.terminated_reason`：
      - `"max_rounds"`：**跳过** `_stream_final_round` 调用，直接 `yield StreamingChunk(delta_content="", finished=True, usage=outcome.total_usage, metadata={"terminated_reason": "max_rounds"})`，然后 return
      - `"completed"`：进入 `_stream_final_round` 兜底产出（既有路径）
    - 验证：`max_rounds == 1` 分支不受影响（不进入 `_iter_rounds`，仍直接调 `_stream_final_round`）
    - _需求: 8.10, NFR-1_

  - [x] 4.6 `run_events` 入口在 `terminated_reason="max_rounds"` 时跳过 `_stream_events_final_round`
    - 修改 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py:ReActAgentAdapter.run_events`
    - 在消费 `outcome.kind == "final"` 时检测 `outcome.terminated_reason`：
      - 先 `yield AgentStreamEvent(kind="status", content="round-final", metadata={"round": outcome.round_num})`
      - `"max_rounds"`：直接 `yield AgentStreamEvent(kind="assistant_done", usage=outcome.total_usage, metadata={"round": outcome.round_num, "terminated_reason": "max_rounds"})`，然后 return（**不**调用 `_stream_events_final_round`）
      - `"completed"`：进入 `_stream_events_final_round` 兜底产出（既有路径）
    - 验证：`max_rounds == 1` 分支不受影响
    - _需求: 8.10, NFR-1_

  - [x] 4.7 单元测试：`max_rounds` 命中四入口的 `terminated_reason` 透传
    - 新增 `epsilon-boot/test/infrastructure/agent/test_react_agent_max_rounds_terminated_reason_unit.py`
    - 覆盖：
      - (a) `run`：`max_rounds=2` 且第 2 轮模型仍返回 tool_calls + 工具被执行：模拟 `chat` 两次（两次都返回 tool_calls）；断言 `chat.call_count == 2`、`stream.call_count == 0`、`AgentResult.terminated_reason == "max_rounds"`、`AgentResult.content == ""`、`AgentResult.status == "completed"`
      - (b) caplog 验证 1 条 `Max_Rounds_Termination_Warning`，`extra.round_num == 2`、`tool_call_count == len(last_tool_calls)`，且日志内容**不含** `tool_call.arguments` 完整文本
      - (c) `run_streaming`：`max_rounds=2` 中间 1 轮 tool_calls + 工具执行后命中循环耗尽：断言 `chat.call_count == 1`（`terminal_round=1`）、`stream.call_count == 0`（跳过 `_stream_final_round`）、最后一个 `StreamingChunk.finished == True` 且 `metadata["terminated_reason"] == "max_rounds"`
      - (d) `run_events`：同 (c)，断言最后一个事件 `kind == "assistant_done"` 且 `metadata["terminated_reason"] == "max_rounds"`、`stream.call_count == 0`
      - (e) 边界：最后一轮 `kind == "text"` → `AgentResult.terminated_reason == "completed"`、不触发 warning
      - (f) 边界：最后一轮 `kind == "approval"` → `AgentResult.status == "approval_required"`、`terminated_reason == "completed"`、不触发 warning
      - (g) `resume`：从 `interrupt.round_num + 1` 起跑且循环耗尽 → `AgentResult.terminated_reason == "max_rounds"`、不发起额外模型调用
    - _需求: 8.1-8.11, NFR-1, NFR-2, NFR-7, Property 4_

  - [x] 4.8 单元测试：`AgentResult.terminated_reason` 默认值与字段集合
    - 新增 `epsilon-boot/test/domain/agent/test_value_objects_terminated_reason_unit.py`（或扩展现有 `test_value_objects_unit.py`）
    - 覆盖：
      - (a) `AgentResult(content="x", model="m")` 默认构造 → `terminated_reason == "completed"`（确认末尾追加可选字段不破坏既有构造）
      - (b) `AgentResult(content="", model="m", terminated_reason="max_rounds")` → 字段读取正确
      - (c) `AgentTerminationReason` 类型别名取值集合为 `{"completed", "max_rounds"}`（通过 `typing.get_args` 验证）
      - (d) `RoundOutcome` 默认 `terminated_reason == "completed"`；显式 `terminated_reason="max_rounds"` 构造可读
    - _需求: 8.1, 8.2, 8.3, 8.4, NFR-2, NFR-5_

  - [x] 4.9 Checkpoint：PR-4 全量回归
    - 在 `epsilon-boot/` 目录下重新执行 NFR-6 4 条 grep（来自 PR-1/PR-2/PR-3 累积要求），全部 0 命中：
      - `grep -rn "setattr(context," src/`
      - `grep -rn "context.message_count - 1" src/infrastructure/agent/`
      - `grep -rn "getattr(context, \"_event_timestamps\"" src/`
      - `grep -rn "getattr(context, \"session_id\"" src/`
    - 静态扫描验证业内共识落地：
      - `grep -rn "final_round_recovery\|recovery_response\|Final_Round_Recovery_Chat\|Max_Rounds_Recovery_Warning" src/`：应 0 命中（确认无回灌相关残留）
      - `grep -rn "AgentTerminationReason\|terminated_reason" src/domain/agent/value_objects.py`：命中 `AgentTerminationReason` 与 `AgentResult.terminated_reason`
      - `grep -rn "terminated_reason" src/infrastructure/agent/round_outcome.py`：命中 `RoundOutcome.terminated_reason`
      - `grep -rn "Max_Rounds_Termination_Warning\|达到 max_rounds 仍存在未消费 tool_calls" src/infrastructure/agent/react_agent_adapter.py`：命中 1 处 warning 日志
    - 不变量回归：
      - `AgentResult.status` 取值仍为 `Literal["completed", "approval_required"]`
      - `AgentResult` / `RoundOutcome` 新字段均以"末尾追加可选字段 + 带默认值"形式加入，既有构造不破坏
      - `StreamingChunk` 字段集合不变（`metadata.terminated_reason` 仅写入既有 `metadata` 字段）
      - `AgentStreamEvent.kind` 取值集合仍为 `{"status","assistant_delta","assistant_done","tool_start","tool_result","tool_error","approval_required","error"}`（`assistant_done.metadata.terminated_reason` 同上）
      - 模型调用次数严格不变（**v2 不引入任何额外模型调用**）：
        - `run`：N 轮共 N 次 chat；命中循环耗尽时仍 N 次（暴露信号、不补救）
        - `run_streaming` / `run_events`：`max_rounds == N` 时 N-1 次 chat + 1 次 stream；命中循环耗尽时 N-1 次 chat + 0 次 stream（跳过 `_stream_*_final_round`）；`max_rounds == 1` 时 1 次 stream
        - `resume`：与 v1 一致；命中循环耗尽时同 `run`
    - 运行全仓单元测试 + property 测试，全部通过
    - _需求: 8.1-8.11, NFR-1, NFR-2, NFR-6, NFR-7_

## 备注

- **PR 顺序**：建议按 PR-1 → PR-2 → PR-3 → PR-4 顺序合入；PR-2 与 PR-3 可并行 review 但避免同时合入以减少冲突。
- **静态扫描 grep（来自 design.md「验收清单」与 NFR-6）**：每个 PR 的 Checkpoint 都应执行对应范围的 grep，最终 PR-4 完成时全部 0 命中：
  1. `grep -rn "_ensure_agent_system_prompt" epsilon-boot/src/infrastructure/agent/react_agent_adapter.py | grep -v "_iter_rounds"` → 仅 `max_rounds == 1` 分支两处显式注入与定义本身（按 design.md 第 9 节口径）
  2. `grep -rn "setattr.*event_timestamps\|getattr.*event_timestamps\|setattr.*session_id\|getattr.*session_id" epsilon-boot/src/` → 0 命中
  3. `grep -rn "context\.message_count - 1" epsilon-boot/src/infrastructure/agent/ epsilon-boot/src/infrastructure/task/ epsilon-boot/src/infrastructure/chat/` → 0 命中
  4. `grep -rn "build.*ChatRequest\|model_access\.stream" epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` → 应仅出现在 `_stream_*_final_round` 与 `_iter_rounds` 中
  5. `grep -rn "final_round_recovery\|recovery_response\|Final_Round_Recovery_Chat\|Max_Rounds_Recovery_Warning" epsilon-boot/src/` → 0 命中（确认无回灌相关残留，因业内共识方案不做内部 recovery chat）
- **依赖管理**：本期不调整 `pyproject.toml`、不增删依赖；如运行测试需要 hypothesis 等已有依赖，使用 `uv` 管理（`docs/steering/uv-package-manager.md`）。
- **配置**：本期不新增 `config.properties` 配置键（`docs/steering/config-source.md`）。
- **DDD 边界**：`event_timestamps` / `session_id` 仅使用 `dict[int, int]` / `str | None` 标准库类型；`AgentTerminationReason` 类型别名定义在 `domain/agent/value_objects.py`，仅依赖 `typing.Literal`；`AgentResult.terminated_reason` 字段位于 `domain/`，`RoundOutcome.terminated_reason` 字段位于 `infrastructure/agent/round_outcome.py`（infrastructure → domain 单向 import）；`_stream_final_round` / `_stream_events_final_round` / `_iter_rounds` 仅置于 `infrastructure/agent/`，不向 `domain/` 反向暴露（`docs/steering/ddd-architecture.md`）。
- **中文 docstring**：所有新增/修改的公开符号（含返回类型变更、新增字段、新增类型别名）必须配中文 docstring（`docs/steering/code-documentation.md` + NFR-5）。
- **不变量保持（NFR-2）**：`AgentResult.status`、`AgentStreamEvent.kind`、`StreamingChunk` 字段集合在本期重构后保持不变；`AgentResult` 与 `RoundOutcome` 新字段以"末尾追加可选字段 + 带默认值"形式加入，既有构造不破坏；`ToolMessage` 字段集合不变，仅 `metadata` 失败时由空变 `{"error": True}`。
- **业内共识方案说明**：PR-4 的 `terminated_reason` 暴露方案对齐 OpenAI Assistants（`incomplete_details.reason`）、LangGraph（`GraphRecursionError`）、CrewAI（`max_iter` failed）、AutoGPT 等业内主流 Agent 框架——把"轮数超限"信号原样暴露给调用方，由顶层编排决策续跑或终止；**不**在 Agent 内部做"recovery chat"补救。前期讨论的 `Final_Round_Recovery_Chat` 方案已根据用户反馈撤销，因为它会掩盖超限信号、阻碍长跑续跑、并叠加额外推理成本。

skills_used: spec-dev
