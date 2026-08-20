# Tasks — agent-adapter-refactor-v3

> 本文件由 `design.md` 第 1524-1613 行 PR 拆分章节展开为可执行的实现任务清单。
> 每条任务关联：需求 ID（来自 `requirement.md`）+ design.md 章节锚点。
> 依赖路径：**PR-1 → PR-2 → (PR-3 ‖ PR-4)**，建议按 PR-1 → PR-2 → PR-3 → PR-4 顺序合入以避免 `react_agent_adapter.py` 冲突。
> v2 已落地内容（`_iter_rounds` 单一推进器、`_execute_tool_call` 元组返回、`AgentResult.terminated_reason` 字段、`_stream_final_round` / `_stream_events_final_round` 抽取等）不再重复，v3 在此基础上扩展。
>
> **任务粒度约定**：实现任务与单元测试任务分开列出；每个 PR 末尾给出新增 / 修改测试文件清单 + Checkpoint（静态扫描 + 全仓回归）。
> **变更兼容性**：`AgentConfig` 新增字段、`StreamingChunk` 新增字段、`Tool` 新增可选 `@property` 均为**非破坏性变更（默认值兼容）**，不需要调用方同步更新。
>
> **dataclass 形态硬约束**：本期所有新增 / 改动的 dataclass 一律使用 `@dataclass(frozen=True)`（与 `domain/model_access/value_objects.py` 中 6 个既有 dataclass 的形态保持一致），**不**附加 `slots=True`；`AgentConfig` 维持 `@dataclass(frozen=True, kw_only=True)`。
> **`tool_calls` 字段类型硬约束**：`StreamingChunk.tool_calls` 字段类型恒为 `list[StreamingToolCallDelta] | None`，默认值恒为 `None`；任务描述、测试断言、序列化示例必须统一使用此口径，**禁用** `tuple[...] = ()` / "默认空元组" 等表达。

---

## PR-1：`StreamingChunk.tool_calls` 协议扩展 + `OpenAICompatibleAdapter.stream` 工具调用分片透传

> 范围：domain 协议层 + infrastructure adapter 层。覆盖需求 2.1 / 2.2 / 2.3 / 2.7 / 2.8 / 2.9（决策 2 底层）。
> 依赖：无。

### 实现任务

- [x] **1.1** 新增 `domain/model_access/value_objects.py::StreamingToolCallDelta` 值对象（`@dataclass(frozen=True)`，**不带 `slots=True`**，与同文件 `ChatRequest` / `LLMResponse` / `StreamingChunk` 等 6 个既有 dataclass 形态严格一致）。字段：`index: int`、`id: str | None = None`、`name: str | None = None`、`arguments_delta: str | None = None`。中文 docstring 说明各字段语义、`finished=True` 分片重组完整列表的契约（`id` / `name` / `arguments_delta` 在该分片中均保证非 `None`）。【需求 2.1；design.md §190-240】**非破坏性变更（新值对象）**
- [x] **1.2** 在 `domain/model_access/value_objects.py::StreamingChunk` 末尾追加 `tool_calls: list[StreamingToolCallDelta] | None = None` 字段（与 design.md §244 / §273-274 / §1185 + requirement §115 / §190 一致；**禁用** `tuple[...] = ()` 形态）。更新类 docstring 的 `Attributes` 段说明：(a) `None` 表示该分片不携带工具调用相关数据；(b) 中间分片（`finished=False`）非 `None` 时携带本分片观察到的增量切片；(c) `finished=True` 分片若包含完整工具调用，本字段为按 `StreamingToolCallDelta.index` 重组后的完整列表（每个元素的 `arguments_delta` 为完整 arguments JSON 而非增量）；(d) `frozen=True` 不变。【需求 2.1；design.md §241-277】**非破坏性变更（默认值 None 兼容纯文本流）**
- [x] **1.3** `infrastructure/model_access/openai_compatible_adapter.py::OpenAICompatibleAdapter.stream` 内部新增工具调用累积状态字典 `acc: dict[int, dict[str, Any]]`（按 SDK `index` 累积 `id` / `name` / `arguments`）。每个 SDK 分片若 `delta.tool_calls` 非空，则按 `tc.index` 更新累积态、产出"本分片新观察到的增量切片"为 `list[StreamingToolCallDelta]`（每个元素**仅**携带本分片的 `tc.id` / `tc.function.name` / `tc.function.arguments`，**不**携带累积值），写入 `StreamingChunk.tool_calls`。`delta.tool_calls is None` 时 `StreamingChunk.tool_calls` 保持 `None`。【需求 2.2；design.md §381-526】
- [x] **1.4** `OpenAICompatibleAdapter.stream` 在 `finish_reason` 非 `None` 的分片以及 SDK 末尾仅携带 `usage` 的分片中，把累积态 `acc` 通过 `_materialize_full_tool_calls` 展开为"携带完整 arguments"的 `list[StreamingToolCallDelta]`（按 `index` 升序，每个元素的 `id` / `name` / `arguments_delta` 全部非 `None`），写入产出的 `StreamingChunk.tool_calls`，与 `finished=True` 一同输出。需要保证按 `(id, name, arguments_delta)` 三元组与"等价 chat() 一次返回的 `LLMResponse.tool_calls`"按 `(id, name, arguments)` 三元组逐一相等且顺序一致。【需求 2.3；design.md §381-526；Property 3】
- [x] **1.5** 纯文本响应回归保护：当 SDK 全程 `delta.tool_calls is None` 时，`StreamingChunk.tool_calls` 始终为 `None`（**不**写空列表），既有 `delta_content` / `finished` / `usage` 行为保持 v2 一致。【需求 2.8（设计选定 (a) 路线 — `_stream_final_round` 完全忽略 `chunk.tool_calls`）；需求 2.9 纯文本流回归基线；design.md §381-526；决策 12】

### 单元测试任务

- [x] **1.6** 新增 `tests/domain/model_access/test_streaming_chunk_tool_calls_field_unit.py`：覆盖 (a) `StreamingToolCallDelta` 字段约束（`index` 必填、其余三字段默认 `None`）；(b) `StreamingChunk` 默认 `tool_calls=None`（**非空元组**）；(c) `frozen=True` 不可变；(d) 末尾追加可选字段不破坏既有构造（既有位置参数构造仍合法）。【需求 2.1, NFR-2；design.md §1463】
- [x] **1.7** 新增 `tests/infrastructure/model_access/test_openai_compatible_stream_tool_calls_unit.py`：mock OpenAI SDK 输出多分片 delta 序列。验证 (a) 中间分片 `StreamingChunk.tool_calls` 仅携带本片增量（`id` / `name` 仅首片有值、`arguments_delta` 为本片字符串切片，**不**携带累积值）；(b) 多 tool_calls 并行（不同 `index`）分别累积；(c) `finished=True` 分片 `tool_calls` 为按 `index` 升序的完整列表，每个元素 `id` / `name` / `arguments_delta` 非 `None`，且按 `(id, name, arguments_delta)` 与等价 chat 一次返回的 `LLMResponse.tool_calls` 按 `(id, name, arguments)` 三元组相等；(d) 纯文本流的 `StreamingChunk.tool_calls` 全程为 `None`。【需求 2.2 / 2.3 / 2.7 / 2.9；design.md §1453】
- [x] **1.8** 新增 `tests/infrastructure/model_access/test_openai_compatible_stream_tool_calls_property.py`：Property-based — hypothesis 生成"工具调用列表 + 任意分片切分方案"，断言 `StreamingChunk.tool_calls` 中间增量 `arguments_delta` 顺序拼接 = 完整 `arguments`；`finished=True` 分片携带的累积完整列表与原始工具调用列表按 `(id, name, arguments)` 三元组逐一相等且顺序一致。【需求 2.3；Property 3；design.md §1470】

### Checkpoint

- [x] **1.9** 静态 grep：
  - `grep -rn "StreamingToolCallDelta" epsilon-boot/src/domain/model_access/value_objects.py` → 命中 1+
  - `grep -rn "tool_calls:\s*list\[StreamingToolCallDelta\]\s*|\s*None\s*=\s*None" epsilon-boot/src/domain/model_access/value_objects.py` → 命中 `StreamingChunk.tool_calls` 字段定义（确认类型为 `list[StreamingToolCallDelta] | None = None`，**非** `tuple[...]`）
  - `grep -rn "slots=True" epsilon-boot/src/domain/model_access/value_objects.py` → **零命中**（确认未引入 `slots=True`，覆盖 NFR-2 不变量"末尾追加可选字段且形态与既有 dataclass 一致"）
- [x] **1.10** 全量回归：在 `epsilon-boot/` 目录运行 `uv run pytest -q`；新增测试通过；既有 1480+ 测试无回归。

### 测试文件清单

- 新增：`test_streaming_chunk_tool_calls_field_unit.py`、`test_openai_compatible_stream_tool_calls_unit.py`、`test_openai_compatible_stream_tool_calls_property.py`
- 修改：无（既有 `OpenAICompatibleAdapter.stream` 测试在 `tool_calls is None` 路径下保持一致）

---

## PR-2：ReAct 全程 stream + 内部累积器 + `tool_arguments_delta` 事件

> 范围：infrastructure 累积器新增 + ReAct adapter 推进路径切换 + 事件 kind 扩展。覆盖需求 1.1-1.10、2.4、2.5、2.6、2.7、2.10（决策 1 + 决策 2 上层）。
> 依赖：**PR-1**（消费 `StreamingChunk.tool_calls`）。

### 实现任务

- [x] **2.1** 新增 `infrastructure/agent/round_stream_accumulator.py::_RoundStreamAccumulator` 类（`@dataclass` **不**适用，使用普通类 + `__init__`；与 design.md §527-683 完整签名严格对齐）。职责：消费 `model_access.stream(...)` 产出的 `AsyncIterator[StreamingChunk]`、`delta_content` 顺序拼接为完整 `content`、`StreamingChunk.tool_calls`（类型 `list[StreamingToolCallDelta] | None`）按 `index` 合并去重为 `list[ToolCallRequest]`（`finished=True` 分片携带的"完整 arguments"优先覆盖增量拼接结果，与决策 11 对齐）、`usage` 取 `finished=True` 分片的 `usage`（缺失视为 `{}`）、`model` 在 `__init__` 注入、`latency_ms` 取 `time.monotonic` 毫秒差。`build_response()` 产出与 v2 `model_access.chat()` 等价的 `LLMResponse`。中文 docstring 注明：累积期间不对外发任何 `StreamingChunk` / `AgentStreamEvent`（决策 7、需求 1.3）。【需求 1.1, 1.2, 1.3, 1.7, 1.9, 1.10；design.md §527-683】
- [x] **2.2** `domain/agent/value_objects.py::AgentStreamEventKind` 末尾追加 `"tool_arguments_delta"` 取值；既有取值（`status` / `assistant_delta` / `assistant_done` / `tool_start` / `tool_result` / `tool_error` / `approval_required` / `error`）保持顺序与字面不变。更新 `Literal[...]` 上方注释说明该 kind 用于 `_stream_events_final_round` 阶段产出工具调用参数 JSON 增量、`content` 为空字符串、`usage` 为 `None`、中间轮次累积期间不产出。【需求 2.4；design.md §282-301】**非破坏性变更（取值追加）**
- [x] **2.3** `infrastructure/agent/react_agent_adapter.py::_iter_rounds` 中间轮次推进逻辑改写：删除 `model_access.chat(...)` 调用 → 改为 `accumulator = _RoundStreamAccumulator(model=config.model or "")` + `await accumulator.consume(model_access.stream(chat_request))` + `response = accumulator.build_response()`。`response` 接入既有 `merge_usage` / `_record_assistant_with_tool_calls` / `_collect_pending_actions` 等 v2 分支判断，**外部 `RoundOutcome` 形态不变**（NFR-2 不变量保持）。`model_access.stream(...)` 抛出的异常透传给 `_iter_rounds` 调用者（`_RoundStreamAccumulator.consume` 不捕获）。【需求 1.1, 1.2, 1.6, 1.7, 1.8, 1.9；design.md §716-953】
- [x] **2.4** `_iter_rounds` 中间轮次累积期间**不对外产出任何事件**（决策 7）：累积期间所有 `StreamingChunk` 由累积器静默消费；`run_streaming` / `run_events` 在中间轮次的对外事件时序（heartbeat、tool_progress、status、tool_start、tool_result/tool_error 等）SHALL 与 v2 字面一致。累积完成后按既有 v2 路径 yield `RoundOutcome`，由 `run_streaming` / `run_events` 自行决定如何对外暴露文本。【需求 1.3, 1.4；design.md §76-124】
- [x] **2.5** `infrastructure/agent/react_agent_adapter.py::_stream_events_final_round` 增加 `tool_arguments_delta` 事件产出：在最后一轮 `model_access.stream(...)` 内 `async for chunk in stream`；当 `chunk.tool_calls is not None and not chunk.finished` 时遍历 `chunk.tool_calls`（类型 `list[StreamingToolCallDelta]`），逐个产出 `AgentStreamEvent(kind="tool_arguments_delta", content="", tool_name=delta.name, tool_call_id=delta.id, arguments=delta.arguments_delta or "", usage=None, metadata={"round": round_num})`。同一 `tool_call_id` 的多个 delta 严格按 SDK 产出顺序；`tool_call_id` / `tool_name` 仅首个 delta 携带非 `None`，后续 delta 可能为 `None`。`finished=True` 分片仍按 v2 产出 `assistant_done`，**不**额外补产 `tool_start`（与决策 8 对齐）。【需求 2.5, 2.6, 2.7, 2.10；design.md §1059-1141】
- [x] **2.6** `_stream_final_round`（用于 `run_streaming`）按 design.md §1143 / 决策 12 选定 (a) 路线：**完全忽略** `chunk.tool_calls`，仅按 v2 既有形态产出 `delta_content` / `finished` / `usage` 形态的 `StreamingChunk`，**不**透传 `chunk.tool_calls` 到产出的 `StreamingChunk.tool_calls`；本期 `_stream_final_round` 主体**不修改**。【需求 2.8 (a) 路线；design.md §1143 决策 12；NFR-2】
- [x] **2.7** 既有所有 `model_access.chat` mock 测试改写为 `model_access.stream` 等价 mock：在 `tests/` 目录下识别所有依赖 `model_access.chat` 的 ReAct 测试（v2 `test_react_agent_max_rounds_terminated_reason_unit.py` 等），用 mock `stream(...)` async iterator 替代——产出等价分片序列（`delta_content` 拼接结果 = 原 `content`、`tool_calls` 按本期协议在分片中产出、`usage` 在 `finished=True` 分片携带）。语义等价不调整断言。【NFR-3；design.md §1454-1460】

### 单元测试任务

- [x] **2.8** 新增 `tests/infrastructure/agent/test_round_stream_accumulator_unit.py`：覆盖 (a) 纯文本累积：`delta_content` 顺序拼接 → `LLMResponse.content`；(b) 单 tool_call 累积：多分片 `arguments_delta` 拼接 → `LLMResponse.tool_calls[0].arguments`；(c) 多 tool_calls 并行（不同 `index`）累积；(d) `usage` 取 `finished=True` 分片，缺失视为 `{}`；(e) `latency_ms` 为非负 float；(f) `finished=True` 分片携带的"完整 arguments"优先覆盖增量拼接结果（决策 11）；(g) `build_response()` 产出与等价 chat 一次返回的 `LLMResponse` 按 `(content, tool_calls.id, tool_calls.name, tool_calls.arguments, usage)` 全等。【需求 1.1, 1.2, 1.9, 1.10；Property 1；design.md §527-683 / §1454】
- [x] **2.9** 新增 `tests/infrastructure/agent/test_round_stream_accumulator_property.py`：Property 1 — hypothesis 生成 `(content_str, tool_calls_seq, usage_dict)` 三元组，构造任意分片切分方案的 `StreamingChunk` 序列；断言 `_RoundStreamAccumulator.consume(...) → build_response()` 产出的 `LLMResponse` 满足 `content` 等于所有 `delta_content` 顺序拼接、`usage` 等于 `finished=True` 分片携带的 `usage`、`tool_calls` 与"等价 chat 一次返回"按 `(id, name, arguments)` 三元组逐一相等且顺序一致。【需求 1.2, 1.10；Property 1；design.md §1469】
- [x] **2.10** 新增 `tests/infrastructure/agent/test_react_agent_iter_rounds_stream_only_unit.py`：覆盖 (a1) **第 1 轮即返回 text 终止**（NFR-3 术语精确性约束 — 不存在"中间轮次纯文本"组合，纯文本响应在任一轮次出现都会立即触发 `text` 终止）：`model_access.chat` mock 调用 0 次、`model_access.stream` 恰好被调用 **1** 次，`AgentResult.terminated_reason == "completed"`；(a2) **`max_rounds=3` 中间轮次 tool_calls 累积，第 3 轮 text 终止**：每轮均返回 tool_calls 直至最后一轮文本终止，`model_access.chat` mock 调用 0 次、`model_access.stream` 被调用 **3** 次；每轮 `_RoundStreamAccumulator` 累积出的 `LLMResponse.tool_calls` 与"等价 chat 一次返回"按 `(id, name, arguments)` 三元组逐一相等且顺序一致；`LLMResponse.content` 等于所有 `delta_content` 顺序拼接；`LLMResponse.usage` 等于 `finished=True` 分片的 `usage`；(b) 累积期间不向上层产出对外 `StreamingChunk` / `AgentStreamEvent`（用 `run_streaming` 与 `run_events` 验证中间轮次 chunk/event 数量与 v2 一致）。【需求 1.1, 1.2, 1.3, 1.4, 1.5, 1.8, 1.9, 1.10；NFR-3；Property 1, Property 7；design.md §1455】
- [x] **2.11** 新增 `tests/infrastructure/agent/test_react_agent_tool_arguments_delta_unit.py`：mock `run_events` 最后一轮 stream 阶段产出多分片 `delta.tool_calls`，断言 (a) 收到 ≥1 条 `tool_arguments_delta` 事件；(b) 各 `tool_arguments_delta.arguments` 顺序拼接 = 完整 `arguments` JSON；(c) 末尾仍产出 `assistant_done` 事件；(d) `tool_call_id` / `tool_name` 仅首个 delta 携带非 `None`，后续可能为 `None`；(e) `tool_arguments_delta` 事件 `usage == None` 且 `content == ""`；(f) 中间轮次累积期间不产出 `tool_arguments_delta`。【需求 2.4, 2.5, 2.6, 2.7, 2.10；Property 2；design.md §1456】
- [x] **2.12** v2 既有测试 mock 改写：`test_react_agent_max_rounds_terminated_reason_unit.py` 及其他依赖 `model_access.chat` 的测试改为 `model_access.stream` 等价 mock。语义等价不调整断言。【NFR-3；design.md §1460】

### Checkpoint

- [x] **2.13** 静态 grep（核心约束 `ReAct_Internal_Chat_Zero_Reference`，**覆盖 NFR-6**）：
  - `grep -rn 'model_access\.chat(' epsilon-boot/src/infrastructure/agent/` → **零命中**【需求 1.5；NFR-6 第 1 条；Property 7】
  - `grep -rn 'await\s\+model_access\.chat(' epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` → **零命中**【NFR-6 第 2 条；Property 7】
  - `grep -rn 'model_access\.chat(' epsilon-boot/src/infrastructure/chat/chat_service_adapter.py` → 仍 ≥ 1 命中（`chat_service_adapter` 保留，**覆盖 NFR-6 第 3 条**）
  - `grep -rn 'model_access\.chat(' epsilon-boot/src/infrastructure/chat/llm_summary_compaction_adapter.py` → 仍 ≥ 1 命中（`compaction adapter` 保留，**覆盖 NFR-6 第 3 条**）
- [x] **2.14** 全量回归：在 `epsilon-boot/` 目录运行 `uv run pytest -q` 全仓通过；所有原依赖 `chat()` mock 的测试已等价改写。

### 测试文件清单

- 新增：`test_round_stream_accumulator_unit.py`、`test_round_stream_accumulator_property.py`、`test_react_agent_iter_rounds_stream_only_unit.py`、`test_react_agent_tool_arguments_delta_unit.py`
- 修改：v2 既有 `test_react_agent_max_rounds_terminated_reason_unit.py` 及其他依赖 `model_access.chat` mock 的测试（语义等价改写）

---

## PR-3：工具 timeout 全局 + per-tool

> 范围：domain `Tool` 抽象基类 + `AgentConfig` 字段 + ReAct adapter 超时分支。覆盖需求 3.1-3.10（决策 3 = b）。
> 依赖：无强依赖；建议在 PR-2 之后合入避免 `react_agent_adapter.py` 冲突解决成本。

### 实现任务

- [x] **3.1** `domain/agent/tools.py::Tool` 抽象基类追加 `@property timeout_seconds(self) -> float | None`，默认实现 `return None`。中文 docstring 说明：(a) `None` 表示沿用 `AgentConfig.tool_timeout_seconds` 全局默认；(b) `> 0` 优先于全局；(c) per-tool override 优先于全局值；(d) 既有抽象方法 `name` / `description` / `parameters` / `execute` 签名不变，新增属性不破坏既有具体工具子类（NFR-2 不变量保持）。【需求 3.2, 3.10；design.md §684-715】**非破坏性变更（默认实现 + 既有签名不变）**
- [x] **3.2** `domain/agent/value_objects.py::AgentConfig` 末尾追加 `tool_timeout_seconds: float | None = None` 字段（`@dataclass(frozen=True, kw_only=True)` 不变，与 design.md §330 / §340-347 严格对齐）。`__post_init__` 中追加校验 `if self.tool_timeout_seconds is not None and self.tool_timeout_seconds <= 0: raise ValueError("tool_timeout_seconds 必须大于 0")`，既有 `max_rounds` / `prompt_id` 校验保持不变。【需求 3.1；design.md §327-380】**非破坏性变更（默认值 None 兼容）**
- [x] **3.3** `infrastructure/agent/react_agent_adapter.py::_resolve_tool_timeout(self, tool_name: str, config: AgentConfig) -> float | None` 辅助方法新增：`tool = self._tool_registry.get(tool_name)`；`if tool is not None and tool.timeout_seconds is not None: return tool.timeout_seconds`；`return config.tool_timeout_seconds`。优先级 per-tool > 全局 > None（不超时）。【需求 3.3；Property 4；design.md §1042-1056】
- [x] **3.4** `_execute_tool_call` 加入 `asyncio.wait_for` 包裹：`timeout = self._resolve_tool_timeout(tool_call.name, config)`；当 `timeout is None` 时直接 `result = await self._tool_registry.execute(tool_call)`（与 v2 一致）；当 `timeout is not None` 时 `result = await asyncio.wait_for(self._tool_registry.execute(tool_call), timeout=timeout)`。【需求 3.3, 3.4；design.md §1001-1058】
- [x] **3.5** `_execute_tool_call` 超时分支：捕获 `asyncio.TimeoutError as exc` → `self._log_tool_failure(tool_call, exc, "timeout")` 输出 warning（`reason="timeout"`，字段集合与 v2 工具失败一致，含 `tool_name` / `tool_call_id` / `reason` / `exc_type=TimeoutError` / `exc_msg=str(exc)`，**不**记录 `tool_call.arguments` 完整文本，沿用 v2 NFR-4 / NFR-7 安全口径）→ `result = f"工具执行超时（{timeout}s)"`（中文）→ `is_error = True`。`ToolMessage.metadata["error"] = True` 持久化（与 v2 工具失败 `Tool_Failure_Metadata_Flag` 一致）。返回 `(result, True)`。【需求 3.5, 3.6, 3.7；NFR-4；design.md §1001-1058】
- [x] **3.6** 异常捕获顺序保护：内层 `try/except asyncio.TimeoutError` 已就地处理 timeout（赋值 `result/is_error` 后控制流自然脱离），外层 `except Exception` 不会"二次捕获"已处理过的 `TimeoutError`；`ToolPermissionDeniedError` / 运行期 `Exception` 仍走 v2 既有分支语义（`_log_tool_failure(reason=...)` + `metadata["error"] = True`）。超时**不**触发 `ApprovalInterrupt`（与 NFR-5 + 决策一致）。【需求 3.6, 3.7；NFR-5；design.md §1001-1058】
- [x] **3.7** 超时取消时由 OpenAI SDK / 工具自身处理 `CancelledError`（不引入补偿机制，开放问题 2 已锁定）；`asyncio.wait_for` 触发取消传播到底层 SDK 协程，由 SDK 内部释放 HTTP 连接，本期不另引入资源清理钩子。【design.md §1289-1295；§1635 开放问题 2】

### 单元测试任务

- [x] **3.8** 新增 `tests/domain/agent/test_tool_timeout_property_unit.py`：覆盖 (a) `Tool` 默认子类 `timeout_seconds` 返回 `None`；(b) 子类 override `> 0` 后返回值生效；(c) 既有抽象方法 `name` / `description` / `parameters` / `execute` 签名不变（reflection 检查）。【需求 3.2, 3.10；design.md §1462】
- [x] **3.9** 新增 `tests/infrastructure/agent/test_react_agent_tool_timeout_unit.py`：覆盖 (a) `tool_timeout_seconds=None` + `tool.timeout_seconds=None`：不引入 `wait_for`，慢工具正常完成；(b) 全局 `0.1` + 慢工具 `await asyncio.sleep(1.0)`：触发 `TimeoutError` → `is_error=True` + `ToolMessage.metadata["error"] == True` + `ToolMessage.content == "工具执行超时（0.1s)"` + `_log_tool_failure` warning `reason="timeout"`；(c) per-tool override：全局 `5.0` / 工具 `0.1` / sleep `1.0` → 用工具级值触发超时（content 携带 `0.1s`）；(d) per-tool override：全局 `0.1` / 工具 `5.0` / sleep `1.0` → 不超时（工具级值优先）；(e) 超时**不**触发 `ApprovalInterrupt`（用 `tool_use=interrupt` 工具构造）；(f) 超时日志不记录 `tool_call.arguments` 完整文本（NFR-4）；(g) `run_events` 中间轮次工具超时产出 `kind="tool_error"` 事件（不产出独立 `tool_timeout` kind，**覆盖需求 3.8**）。【需求 3.3-3.9；NFR-4, NFR-5；Property 4；design.md §1457】
- [x] **3.10** 扩展现有 `test_agent_config_validation_unit.py`（如不存在则新增 `tests/domain/agent/test_agent_config_validation_unit.py`）：覆盖 `tool_timeout_seconds=0` / `<0` 触发 `ValueError("tool_timeout_seconds 必须大于 0")`；`None` 与 `>0` 通过校验；既有字段（`max_rounds` / `prompt_id` / `allowed_tool_names`）校验行为不变。【需求 3.1；design.md §1461】

### Checkpoint

- [x] **3.11** 静态 grep：
  - `grep -rn 'asyncio\.wait_for' epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` → 命中 `_execute_tool_call` 内 1 处
  - `grep -rn 'tool_timeout_seconds' epsilon-boot/src/domain/agent/value_objects.py` → 命中 1+（字段定义 + docstring）
  - `grep -rn 'timeout_seconds' epsilon-boot/src/domain/agent/tools.py` → 命中 1+（`@property` 定义 + docstring）
- [x] **3.12** 全量回归：在 `epsilon-boot/` 目录运行 `uv run pytest -q` 全仓通过；新增测试通过；既有测试（不传 `tool_timeout_seconds`，默认 `None`）行为与 v2 一致。

### 测试文件清单

- 新增：`test_tool_timeout_property_unit.py`、`test_react_agent_tool_timeout_unit.py`
- 修改 / 扩展：`test_agent_config_validation_unit.py`

---

## PR-4：`max_total_tokens` 预算 + `terminated_reason` 扩展 + 不可达分支 assert 收口

> 范围：domain `AgentTerminationReason` / `AgentConfig` 扩展 + ReAct adapter 预算检查 + 循环耗尽分支 assert + 三入口透传。覆盖需求 4.1-4.11、5.1-5.8（决策 4 = a + 决策 5 = b）。
> 依赖：**PR-2**（共用 `_iter_rounds` 主体）。

### 实现任务

- [x] **4.1** `domain/agent/value_objects.py::AgentTerminationReason` 扩展：`Literal["completed", "max_rounds"]` → `Literal["completed", "max_rounds", "token_budget_exceeded"]`。中文 docstring 说明 `token_budget_exceeded` 语义：循环达到 `config.max_total_tokens` 上限时本轮结束后立即终止，不再发起更多模型调用；调用方应据此决策是否升档预算续跑或告知用户；具体判定规则见 `Token_Budget_Computation_Rule`。既有取值（`completed` / `max_rounds`）不变。【需求 4.2；design.md §303-325】**非破坏性变更（取值追加）**
- [x] **4.2** `domain/agent/value_objects.py::AgentConfig` 末尾追加 `max_total_tokens: int | None = None` 字段（`@dataclass(frozen=True, kw_only=True)` 不变，与 design.md §348-357 严格对齐）。`__post_init__` 中追加校验 `if self.max_total_tokens is not None and self.max_total_tokens <= 0: raise ValueError("max_total_tokens 必须大于 0")`，既有校验不变。【需求 4.1；design.md §327-380】**非破坏性变更（默认值 None 兼容）**
- [x] **4.3** `infrastructure/agent/round_outcome.py::RoundOutcome.terminated_reason` 字段类型同步随 `AgentTerminationReason` 扩展为 3 取值（无新字段，仅 type alias 跟随）；`AgentResult.terminated_reason` 字段类型亦同步扩展。**末尾追加可选字段形式不变**（NFR-2 不变量保持）。【需求 4.9；design.md §1192】
- [x] **4.4** `infrastructure/agent/react_agent_adapter.py::_compute_total_tokens(total_usage: dict[str, int]) -> int` `@staticmethod` 辅助方法新增：按 `Token_Budget_Computation_Rule` 计算 — 优先取 `total_usage["total_tokens"]`，当该键不存在或为 0 时回退到 `total_usage.get("prompt_tokens", 0) + total_usage.get("completion_tokens", 0)`。【需求 4.3；design.md §911-929】
- [x] **4.5** `_is_token_budget_exceeded(config: AgentConfig, total_usage: dict[str, int]) -> bool` `@staticmethod` 辅助方法新增：`return False if config.max_total_tokens is None else _compute_total_tokens(total_usage) > config.max_total_tokens`。【需求 4.3；design.md §931-948】
- [x] **4.6** `_iter_rounds` 每轮 `merge_usage` 后预算检查：当 `_is_token_budget_exceeded(config, total_usage)` 为 True 时，按本轮 `outcome.kind` 分支处理：
  - **text 路径**（`response.tool_calls` 为空）：仍按 `terminated_reason="completed"` 自然终止（决策 9、需求 4.7）；
  - **approval 路径**（`pending` 非空）：按 v2 `approval` 路径产出 `RoundOutcome(kind="approval", ...)`，**不**改写为 `token_budget_exceeded`（决策 10、需求 NFR-5）；
  - **tool_calls 路径**：先 yield `tool_calls` outcome 让 caller 执行工具回写 ToolMessage；通过 `budget_exceeded_pending_after_tools = True` 标记跨轮终止；下一次 `__anext__` 进入循环开头检测到该标记后，直接 yield `RoundOutcome(kind="final", terminated_reason="token_budget_exceeded", ...)` 并 `return`，**不**进入新一轮 stream。【需求 4.4, 4.6, 4.7, 4.8；NFR-5；design.md §786-869】
- [x] **4.7** `_iter_rounds` 命中预算时（即将产出 `terminated_reason="token_budget_exceeded"` 的 `RoundOutcome` 之前）输出 `Token_Budget_Exceeded_Warning` 日志：`logger.warning("Agent Loop 累计 token 超过 max_total_tokens 预算", extra={"round_num": round_num, "accumulated_total_tokens": _compute_total_tokens(total_usage), "max_total_tokens": config.max_total_tokens})`。**不**记录 `tool_call.arguments` / `delta_content` 全文。该 warning 与 `Max_Rounds_Termination_Warning` 在同一执行内**互斥**（需求 4.8）。【需求 4.5, 4.8；NFR-4；design.md §849-857】
- [x] **4.8** `_iter_rounds` 循环耗尽分支按 `Terminal_Round_Boundary_Assert` 收口：
  - 删除 v2 残留的"non-pending tool_calls 静默回退到 `terminated_reason='completed'`"分支（即 `react_agent_adapter.py` 第 545-552 行 `# 其他循环耗尽分支：保持 completed` 那段产出）；
  - `last_response is None` 极端边界（`terminal_round=0`）直接 `return`，不产出 outcome，附中文注释说明该分支仅在数学边界可达；
  - `last_response is not None` 时新增 `assert bool(last_response.tool_calls) and bool(messages) and isinstance(messages[-1], ToolMessage), "<中文断言失败信息>"` 强制约束最后一轮必为 tool_calls 且工具已被外层执行回写；
  - 配套中文注释说明：自然终止路径（text / approval）已在循环体内 `yield ... return`，唯一可达本分支的情形是循环跑完所有 N 轮且最后一轮 tool_calls；其他组合仅在 `terminal_round=0` 等数学边界可达；
  - assert 通过后产出 `RoundOutcome(kind="final", round_num=effective_terminal, response=last_response, total_usage=dict(total_usage), terminated_reason="max_rounds")` 并记录 `Max_Rounds_Termination_Warning`（与 v2 行为一致）。【需求 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.8；Property 6；design.md §870-907】
- [x] **4.9** `run_streaming` 在消费 `kind="final"` 时新增 `terminated_reason="token_budget_exceeded"` 透传分支：当 `outcome.terminated_reason == "token_budget_exceeded"` 时跳过 `_stream_final_round`，产出 `StreamingChunk(delta_content="", finished=True, usage=outcome.total_usage, metadata={"terminated_reason": "token_budget_exceeded"})` 并 return（与 v2 `max_rounds` 命中分支字面对称）；`run_events` 同构对称（命中后跳过 `_stream_events_final_round`，产出 `kind="status" content="round-final"` + `kind="assistant_done"` 携带 `metadata={"round": ..., "terminated_reason": "token_budget_exceeded"}` 后 return）。【需求 4.10；NFR-1（命中预算时跳过最后一轮 stream）；design.md §1149-1177】
- [x] **4.10** `run` / `resume` 通过既有 `_outcome_to_agent_result` 自然透传 `terminated_reason`：`AgentResult.terminated_reason == outcome.terminated_reason`，无需新增分支。`resume` 后续轮次也走 v3 全程 stream 主路径，与 v2 一致透传 `terminated_reason`（NFR-5）。【需求 4.10；NFR-5；design.md §1147】

### 单元测试任务

- [x] **4.11** 新增 `tests/infrastructure/agent/test_react_agent_token_budget_unit.py`：覆盖 (a) `run`：`max_total_tokens=B`，第 1 轮返回 tool_calls + usage 已超出 → 工具被执行（`_execute_tool_call` 调用次数 = 第 1 轮 tool_calls 数）+ `AgentResult.terminated_reason == "token_budget_exceeded"` + `Token_Budget_Exceeded_Warning` warning 仅 1 条 + `model_access.stream` 调用次数 = **1**（无第 2 轮）；(b) `run_streaming`：超限分支跳过 `_stream_final_round`，最后一个 `StreamingChunk.metadata["terminated_reason"] == "token_budget_exceeded"`；(c) `run_events`：超限分支最后一个事件为 `kind="assistant_done"` 且 `metadata["terminated_reason"] == "token_budget_exceeded"`；(d) text 路径下即使最后一轮 usage 把累计推过预算，仍 `terminated_reason == "completed"`（决策 9）；(e) approval 路径下不改写为 `token_budget_exceeded`（决策 10、NFR-5）；(f) `max_total_tokens=None`：行为与 v2 一致；(g) `max_total_tokens` 与 `max_rounds` 共存：命中预算优先（`Token_Budget_Exceeded_Warning` 与 `Max_Rounds_Termination_Warning` 在同一执行内不同时出现）；(h) `Token_Budget_Computation_Rule`：`total_tokens` 缺失时回退到 `prompt_tokens + completion_tokens`；(i) `_outcome_to_agent_result` 自然透传（无需新增分支）。【需求 4.1-4.11；Property 5；NFR-1, NFR-4, NFR-5；design.md §1458】
- [x] **4.12** 新增 `tests/infrastructure/agent/test_react_agent_terminal_assert_unit.py`：覆盖 (a) `terminal_round=0` 边界（`run_streaming` / `run_events` 设置 `terminal_round=config.max_rounds - 1` 且 `max_rounds=1` 实际不进入 `_iter_rounds` 主循环）→ `last_response is None` 分支直接 return，不抛 `AssertionError`；(b) 正常 `max_rounds` 命中（最后一轮 tool_calls + caller 已执行工具回写 ToolMessage）→ assert 通过 + 产出 `terminated_reason="max_rounds"` + `Max_Rounds_Termination_Warning` 仅 1 条；(c) 故意构造"最后一轮 tool_calls 但 caller 不执行工具回写"的人工测试场景（直接绕过 `_execute_tool_call`，使用自定义 caller 驱动 generator） → assert 抛 `AssertionError`，验证 v2 残留兜底分支确实被删除、不变量被强制表达。【需求 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8；Property 6；design.md §1459】
- [x] **4.13** 扩展现有 `test_agent_config_validation_unit.py`：覆盖 `max_total_tokens=0` / `<0` 触发 `ValueError("max_total_tokens 必须大于 0")`；`None` 与 `>0` 通过校验；既有 `max_rounds` / `prompt_id` / `tool_timeout_seconds` 校验行为不变。【需求 4.1；design.md §1461】

### Checkpoint

- [x] **4.14** 静态 grep（**覆盖 NFR-6 第 4 条**）：
  - `grep -rn 'last_response\.tool_calls' epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` → 仅出现在 `assert` 表达式内（NFR-6 第 4 条：循环耗尽分支不再有 if/else 分支判断用法）
  - `grep -rn '其他循环耗尽分支：保持 completed' epsilon-boot/src/` → **零命中**（v2 残留兜底注释已删除，需求 5.1）
  - `grep -rn 'max_total_tokens' epsilon-boot/src/domain/agent/value_objects.py` → 命中 1+（字段定义 + docstring）
  - `grep -rn 'token_budget_exceeded' epsilon-boot/src/domain/agent/value_objects.py` → 命中 `AgentTerminationReason` Literal 取值
- [x] **4.15** 全量回归：在 `epsilon-boot/` 目录运行 `uv run pytest -q` 全仓通过；既有 v2 `max_rounds` 测试无回归（行为对称延续）；`Token_Budget_Exceeded_Warning` 与 `Max_Rounds_Termination_Warning` 在同一执行内不同时出现（互斥校验）。

### 测试文件清单

- 新增：`test_react_agent_token_budget_unit.py`、`test_react_agent_terminal_assert_unit.py`
- 修改 / 扩展：`test_agent_config_validation_unit.py`

---

## PR 合入顺序

```
PR-1 (StreamingChunk.tool_calls 协议 + OpenAICompatibleAdapter SDK 透传)
  └─→ PR-2 (ReAct 全程 stream + 内部累积器 + tool_arguments_delta)
        ├─→ PR-3 (工具 timeout 全局 + per-tool)        # 可并行 review
        └─→ PR-4 (token 预算 + terminated_reason 扩展 + assert 收口)
```

**强制顺序**：PR-1 → PR-2（PR-2 消费 PR-1 的 `StreamingChunk.tool_calls`）；PR-4 在 PR-2 之后（共用 `_iter_rounds` 主体）。
**建议顺序**：PR-1 → PR-2 → PR-3 → PR-4（PR-3 与 PR-4 可并行 review，但顺序合入避免 `react_agent_adapter.py` 冲突解决成本）。

---

## 备注

- **dataclass 形态硬约束**：所有新增 / 改动的 dataclass 一律使用 `@dataclass(frozen=True)`（与 `domain/model_access/value_objects.py` 中 6 个既有 dataclass 形态严格一致），**不**附加 `slots=True`；`AgentConfig` 维持 `@dataclass(frozen=True, kw_only=True)`。本期任务中已落地的 `StreamingToolCallDelta`（任务 1.1，已勾选）即按此口径实现，可作为参考样板。
- **`tool_calls` 字段类型硬约束**：`StreamingChunk.tool_calls` 字段类型恒为 `list[StreamingToolCallDelta] | None`，默认值恒为 `None`；任务描述、测试断言、序列化示例必须统一使用此口径，**禁用** `tuple[...] = ()` / "默认空元组" 等表达。
- **NFR-3 兼容性**：所有 v2 `model_access.chat` mock 测试在 PR-2 中等价改写为 `model_access.stream` mock，断言保持不变；`chat_service_adapter.py` / `llm_summary_compaction_adapter.py` 的 `chat()` 调用保留（它们不属于 ReAct 推进路径，不在 `ReAct_Internal_Chat_Zero_Reference` 范围内）。
- **NFR-3 测试场景命名硬约束**：测试场景命名**禁止**使用"`max_rounds=N（N≥2)` + 中间轮次纯文本"等自相矛盾的术语（语义上不可达 — 纯文本响应在任一轮次出现都会立即触发 `text` 终止）；多轮场景必须用 (a1) "第 1 轮即返回 text 终止"（最短路径，stream 调用 1 次）+ (a2) "max_rounds=3 中间轮次 tool_calls 累积，第 3 轮 text 终止"（stream 调用 3 次）二选一精确表达（任务 2.10 已严格遵守）。
- **NFR-4 / NFR-7 安全口径**：超时日志、预算超限日志均**不**记录 `tool_call.arguments` 全文 / `delta_content` 全文，沿用 v2 既有约束。
- **NFR-1 模型调用次数语义**：`run` 由 v2 的 N 次 `chat()` 改为 N 次 `stream()`；`run_streaming` / `run_events` 由 N-1 次 `chat()` + 1 次 `stream()` 改为 N 次 `stream()`；命中 `max_rounds` 或 `token_budget_exceeded` 时**不**追加额外 stream（任务 2.10 / 2.13 / 4.9 / 4.11 共同验证）。
- **NFR-5 HITL 兼容**：超时**不**触发 `ApprovalInterrupt`（任务 3.6 / 3.9）；预算超限在 approval 路径下**不**改写（任务 4.6 / 4.11）；`resume` 走 v3 全程 stream 主路径（任务 4.10）。
- **NFR-6 静态扫描清单**：分散在 PR-2 任务 2.13（第 1-3 条）+ PR-4 任务 4.14（第 4 条）共同覆盖。
- **决策 12（_stream_final_round 选 (a) 路线）**：本期 `_stream_final_round` 主体不修改（任务 2.6），不透传 `chunk.tool_calls` 到产出的 `StreamingChunk.tool_calls`，前端 `StreamingChunk` 通道不获得工具调用增量；`tool_arguments_delta` typewriter 收益由 `run_events` 单独承载（任务 2.5）。
- **开放问题（不在本期范围）**：(1) `_stream_final_round`（`run_streaming` 路径）启用 (b) 路线把 `chunk.tool_calls` 透传到 `StreamingChunk.tool_calls`，留待后续 spec；(2) 工具超时取消时的副作用补偿（如已发出的 SQL）由各 Tool 实现内部 try/except `CancelledError` 处理，不在本期统一引入补偿机制。
- **业内对齐**：决策 1=B（OpenAI Assistants / LangGraph / Vercel AI SDK 模式）、决策 3=b（OpenAI Agents SDK / CrewAI 模式）、决策 4=a（OpenAI Assistants `max_completion_tokens` / Pydantic AI `UsageLimits` 简化版）、决策 5=b（assert 强约束 + 注释，本项目内部代码维护）。

---

## 任务总数

| PR | 实现任务 | 测试任务 | Checkpoint | 小计 |
|---|---|---|---|---|
| PR-1 | 5 | 3 | 2 | **10** |
| PR-2 | 7 | 5 | 2 | **14** |
| PR-3 | 7 | 3 | 2 | **12** |
| PR-4 | 10 | 3 | 2 | **15** |
| **合计** | **29** | **14** | **8** | **51** |
