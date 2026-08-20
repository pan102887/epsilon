# 设计文档：结构化工具执行结果（Structured Tool Result）

## 1. 架构概览

### 1.1 变更层级

```
domain/agent/tools.py           ← 新增 ToolExecutionResult；改 execute()/run() 签名
domain/agent/trace_value_objects.py ← ToolCallTrace 新增 metadata 字段
infrastructure/tools/*/          ← 全部 13 个工具 execute() 返回 ToolExecutionResult
infrastructure/agent/react_agent_adapter.py ← 消费 ToolExecutionResult；补 ErrorTrace/ModelCallTrace
infrastructure/trace/local_file_trace_store_adapter.py ← _dict_to_step 兼容 metadata
```

### 1.2 数据流

```
Tool.execute(**kwargs)
  → ToolExecutionResult(content="...", metadata={...})
    → Tool.run() 透传 ToolExecutionResult
      → ToolRegistry.execute() 透传
        → ScopedToolRegistry.execute() 透传
          → ReActAgentAdapter._execute_tool_call()
            ├─ result.content → ToolMessage.content（回灌 LLM）
            ├─ result.content → checkpoint after_tool_call（JSON 序列化兼容）
            └─ result.metadata → _record_tool_call_trace → ToolCallTrace.metadata → JSONL
```

---

## 2. 接口设计

### 2.1 ToolExecutionResult 值对象

位置：`src/domain/agent/tools.py`（与 `Tool` 同模块，避免循环导入）。

```python
@dataclass(frozen=True)
class ToolExecutionResult:
    """工具执行结果值对象。

    封装工具执行完成后的返回数据，包含回灌给 LLM 的文本内容和供 trace 记录使用的
    结构化元数据。

    ``content`` 字段语义等价于原 ``Tool.execute()`` 返回的 ``str``——它是回灌给
    LLM 上下文的完整文本，可按 ``RESULT_SUMMARY_MAX_LEN`` 截断后写入 trace 的
    ``result_summary`` 字段，但原始值始终完整回灌 LLM。

    ``metadata`` 字段为工具类型特有的结构化元数据 dict，值类型为 ``Any`` 的原因：
    metadata 为 free-form trace 扩展字段，不同工具产出的键值类型天然异构（int、
    str、bool 等），非 API 契约字段，不作为公共接口校验目标。各工具须在
    docstring 中说明每个 metadata 键的含义与类型。

    Attributes:
        content: 回灌给 LLM 的文本内容，等价于原 ``execute() -> str`` 的返回值。
        metadata: 供 trace 记录的结构化元数据，默认空 dict。
    """

    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
```

**设计决策**：
- 放在 `tools.py` 而非独立文件：`Tool.execute()` 的返回类型应与 `Tool` 同模块，消除跨模块导入。
- `frozen=True`：不可变值对象，与项目领域值对象惯例一致。
- `metadata` 使用 `dict[str, Any]`：free-form 扩展字段，值类型异构。遵循 steering `python-typing-lint.md` 对裸 `Any` 的限制，在 docstring 中说明原因。

### 2.2 Tool 基类签名变更

```python
class Tool(ABC):
    @abstractmethod
    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """执行工具逻辑。返回结构化结果值对象。"""
        ...

    async def run(self, request: ToolCallRequest) -> ToolExecutionResult:
        """处理 ToolCallRequest，执行完整的工具调用流水线。"""
        # 1. JSON 解析 → 2. cast_params → 3. validate_params
        # 4. execute
        try:
            return await self.execute(**params)
        except ToolExecutionError:
            raise
        except Exception as e:
            raise ToolExecutionError(message=str(e), tool_name=self.name) from e
```

### 2.3 ToolRegistry / ScopedToolRegistry 签名变更

```python
class ToolRegistry:
    async def execute(self, request: ToolCallRequest) -> ToolExecutionResult:
        # 查找工具 → tool.run(request)
        ...

class ScopedToolRegistry:
    async def execute(self, request: ToolCallRequest) -> ToolExecutionResult:
        # 权限检查 → self._registry.execute(request)
        ...
```

---

## 3. 工具 Metadata 设计

### 3.1 通用约定

- 所有 metadata 键使用 `snake_case`。
- 字符串值截断上限：`command_summary` / `code_summary` → 128 字符；`url` → 256 字符。
- 不包含宿主绝对路径（Workspace §5 红线）。
- 敏感内容经 `SensitiveRedactionFilter` 逻辑处理或直接不记录。

### 3.2 Shell 执行工具 — ShellExecTool

```python
metadata = {
    "command_summary": str,    # 命令前 128 字符（脱敏后）
    "working_dir": str,        # workspace 相对 POSIX 路径（如 "/" 或 "/src"）
    "exit_code": int,          # 进程退出码，超时时为 -1
    "stdout_bytes": int,       # stdout 原始字节数（截断前）
    "stderr_bytes": int,       # stderr 原始字节数（截断前）
    "truncated": bool,         # 输出是否被截断
}
```

**脱敏**：`command_summary` 复用 `_reject_dangerous_command` 已有的命令可见性逻辑，不在 trace 中暴露完整命令。对命令中可能出现的环境变量引用不做特殊处理——命令本身是 LLM 生成的，敏感值已由 `sanitize_env` 在执行期剥离。

### 3.3 Python 执行工具 — PythonExecTool

```python
metadata = {
    "code_summary": str,       # 代码前 128 字符
    "exit_code": int,          # 进程退出码，超时时为 -1
    "stdout_bytes": int,
    "stderr_bytes": int,
    "memory_limited": bool,    # 是否启用了内存限制
    "truncated": bool,
}
```

### 3.4 文件读取工具 — ReadFileTool

```python
metadata = {
    "logical_path": str,       # workspace 相对 POSIX 路径
    "operation": "read",       # 固定字面值
    "line_range": [int, int],  # [offset, offset+limit-1]
    "lines_returned": int,     # 实际返回行数
}
```

### 3.5 文件写入工具 — WriteFileTool

```python
metadata = {
    "logical_path": str,
    "operation": "write",
    "bytes_written": int,      # 写入字节数
}
```

### 3.6 文件编辑工具 — EditFileTool

```python
metadata = {
    "logical_path": str,
    "operation": "edit",
    "bytes_written": int,
}
```

### 3.7 目录列举工具 — ListDirTool

```python
metadata = {
    "logical_path": str,
    "operation": "list",
    "recursive": bool,
    "entries_count": int,      # 返回的条目数量
}
```

### 3.8 Web 搜索工具 — WebSearchTool

```python
metadata = {
    "query": str,              # 搜索关键词（截断至 128 字符）
    "result_count": int,
}
```

### 3.9 HTTP 请求工具 — HttpRequestTool

```python
metadata = {
    "method": str,             # HTTP 方法
    "url": str,                # URL（截断至 256 字符，剥离敏感查询参数）
    "status_code": int | None, # HTTP 状态码，请求失败时为 None
    "response_bytes": int,     # 响应体字节数
}
```

### 3.10 Web 抓取工具 — WebFetchTool

```python
metadata = {
    "url": str,                # URL（截断至 256 字符，剥离敏感查询参数）
    "response_bytes": int,
    "content_type": str | None,
}
```

### 3.11 委派工具 — DelegateToAgentTool

```python
metadata = {
    "target_agent": str,
    "success": bool,
}
```

### 3.12 并行委派工具 — DelegateParallelTool

```python
metadata = {
    "targets": list[str],      # 目标 Agent 名称列表
    "results_count": int,
    "success_count": int,
}
```

### 3.13 Handoff 工具 — HandoffToAgentTool

```python
metadata = {
    "target_agent": str,
    "success": bool,
}
```

### 3.14 MCP 工具桥 — McpToolBridge

```python
metadata = {
    "mcp_server": str,         # MCP server 标识
    "mcp_tool_name": str,      # MCP 侧工具名
}
```

---

## 4. ReActAgentAdapter 改造

### 4.1 `_execute_tool_call` 返回值变更

当前签名：
```python
async def _execute_tool_call(self, ...) -> tuple[str, bool]:
    # result: str, is_error: bool
```

改造后：
```python
async def _execute_tool_call(self, ...) -> tuple[ToolExecutionResult, bool]:
    # result: ToolExecutionResult, is_error: bool
```

**消费点适配**：

| 消费点 | 当前用法 | 改造后 |
|---|---|---|
| `ToolMessage.content` | `context.add_tool_result(result=result)` | `context.add_tool_result(result=result.content)` |
| Checkpoint `after_tool_call` | `result=result` | `result=result.content` |
| `_record_tool_call_trace` | `result=result` | `result=result`（整个 ToolExecutionResult 传入） |
| Handoff 信号 | `result = signal.content` | `result = ToolExecutionResult(content=signal.content)` |
| 权限拒绝 | `result = str(exc)` | `result = ToolExecutionResult(content=str(exc))` |
| 超时 | `result = f"工具执行超时..."` | `result = ToolExecutionResult(content=f"工具执行超时...")` |
| 一般异常 | `result = str(exc)` | `result = ToolExecutionResult(content=str(exc), metadata={"error_class": type(exc).__name__})` |
| Guardrail 阻断 | `result = decision.message` | `result = ToolExecutionResult(content=decision.message)` |

**异常路径 metadata 构造**：在 `_execute_tool_call` 的各 except 分支中，构造 `ToolExecutionResult` 时填充 `metadata`：

```python
except ToolPermissionDeniedError as exc:
    result = ToolExecutionResult(
        content=str(exc),
        metadata={"error_class": "ToolPermissionDeniedError"},
    )
    is_error = True
except TimeoutError as exc:
    result = ToolExecutionResult(
        content=f"工具执行超时（{timeout}s)",
        metadata={"error_class": "TimeoutError"},
    )
    is_error = True
except Exception as exc:
    result = ToolExecutionResult(
        content=str(exc),
        metadata={"error_class": type(exc).__name__},
    )
    is_error = True
```

### 4.2 并发工具调度适配

三个并发调度方法的内部闭包返回类型同步变更：

**`_dispatch_concurrent_tool_calls`**：
```python
async def _run_and_trace(tc: ToolCallRequest) -> tuple[ToolExecutionResult, str, bool, float]:
    # 原: tuple[str, str, bool, float]
    result, is_error = await self._execute_tool_call(...)
    return result, result.content, is_error, elapsed
```

注意：闭包返回的第二个元素 `result.content` 用于 trace 记录之外的 `_record_tool_call_trace` 调用中构造 tool_results dict。需要 review 所有使用点。

**`_stream_concurrent_tool_progress`** 和 **`_events_concurrent_tool_calls`**：同理适配。

### 4.3 `_record_tool_call_trace` 改造

```python
async def _record_tool_call_trace(
    self,
    session_id: str | None,
    round_num: int,
    tool_call: ToolCallRequest,
    result: ToolExecutionResult,     # 改：从 str 变为 ToolExecutionResult
    is_error: bool,
    elapsed_ms: float,
) -> None:
    """记录单个工具调用追踪。"""
    from domain.agent.trace_value_objects import (
        ARGUMENTS_SUMMARY_MAX_LEN,
        RESULT_SUMMARY_MAX_LEN,
        ToolCallTrace,
    )

    # 从 ToolExecutionResult 提取 error 信息
    error_class: str | None = None
    error_message: str | None = None
    if is_error:
        error_class = result.metadata.get("error_class")
        error_message = self._truncate(result.content, ERROR_MESSAGE_MAX_LEN)

    # 截断 metadata 以控制 JSONL 行大小
    trace_metadata = self._truncate_metadata(result.metadata)

    await self._record_trace(
        session_id,
        ToolCallTrace(
            round_num=round_num,
            tool_name=tool_call.name,
            tool_call_id=tool_call.id,
            arguments_summary=self._truncate(
                tool_call.arguments, ARGUMENTS_SUMMARY_MAX_LEN
            ),
            result_summary=self._truncate(
                result.content, RESULT_SUMMARY_MAX_LEN
            ),
            success=not is_error,
            latency_ms=elapsed_ms,
            timestamp_epoch=time.time(),
            error_class=error_class,
            error_message=error_message,
            metadata=trace_metadata,
        ),
    )
```

新增辅助方法：

```python
@staticmethod
def _truncate_metadata(
    metadata: dict[str, Any],
    max_total_bytes: int = 2048,
) -> dict[str, Any]:
    """截断 metadata dict 的总序列化大小。

    逐个保留键值对，直到序列化大小接近上限。超出时丢弃剩余键并标记。
    """
    import json as _json

    if not metadata:
        return {}
    serialized = _json.dumps(metadata, ensure_ascii=False, default=str)
    if len(serialized.encode("utf-8")) <= max_total_bytes:
        return metadata
    # 逐键截断
    result: dict[str, Any] = {}
    current_size = 2  # "{}"
    for key, value in metadata.items():
        entry = _json.dumps({key: value}, ensure_ascii=False, default=str)
        entry_size = len(entry.encode("utf-8"))
        if current_size + entry_size > max_total_bytes - 50:  # 预留 _truncated 标记
            result["_truncated"] = True
            break
        result[key] = value
        current_size += entry_size
    return result
```

---

## 5. ToolCallTrace.metadata 字段

### 5.1 值对象变更

```python
@dataclass(frozen=True)
class ToolCallTrace:
    """工具调用记录。"""

    round_num: int
    tool_name: str
    tool_call_id: str
    arguments_summary: str
    result_summary: str
    success: bool
    latency_ms: float
    timestamp_epoch: float
    error_class: str | None = None
    error_message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)  # 新增
    kind: Literal["tool_call"] = field(default="tool_call", init=False)
```

**字段顺序**：`metadata` 在 `error_message` 之后、`kind` 之前。`kind` 是 `init=False` 字段，不受 default 顺序约束。`metadata` 有默认值，放在其他有默认值的字段后面。

### 5.2 JSONL 兼容

`LocalFileTraceStoreAdapter._dict_to_step()` 中：

```python
def _dict_to_step(raw: dict) -> AgentStepTrace | None:
    kind = raw.pop("kind", None)
    # ToolCallTrace 特殊处理 metadata
    if kind == "tool_call":
        metadata = raw.pop("metadata", {})  # 兼容旧数据
        return ToolCallTrace(**raw, metadata=metadata)
    # ... 其他类型不变
```

---

## 6. ErrorTrace 写入点

### 6.1 写入位置

在 `_iter_rounds` 循环的最外层异常处理中记录 `ErrorTrace`。当前四个入口方法的异常处理模式：

```python
# run() 中
async def run(self, context, config, model_access) -> AgentResult:
    session_id = ...
    try:
        async for outcome in self._iter_rounds(context, config, model_access, ...):
            # ... 处理 outcome
    except Exception as exc:
        # 新增：记录 ErrorTrace
        await self._record_error_trace(session_id, round_num, exc)
        raise
```

同理在 `resume()`、`run_streaming()`、`run_events()` 中补录。

### 6.2 辅助方法

```python
async def _record_error_trace(
    self,
    session_id: str | None,
    round_num: int,
    exc: Exception,
) -> None:
    """记录 Agent Loop 非工具异常为 ErrorTrace。"""
    from domain.agent.trace_value_objects import ERROR_MESSAGE_MAX_LEN, ErrorTrace

    await self._record_trace(
        session_id,
        ErrorTrace(
            round_num=round_num,
            error_class=type(exc).__name__,
            error_message=self._truncate(str(exc), ERROR_MESSAGE_MAX_LEN),
            timestamp_epoch=time.time(),
        ),
    )
```

**注意**：工具执行异常不走 `ErrorTrace`——工具失败已通过 `ToolCallTrace.success=False` + `error_class` / `error_message` 记录。`ErrorTrace` 仅用于 Agent Loop 级别的非工具异常（如模型调用失败、context 序列化错误、HITL 状态加载失败等）。

---

## 7. max_rounds==1 路径 ModelCallTrace 补录

### 7.1 问题定位

`run_streaming` 和 `run_events` 在 `max_rounds==1` 时走快速路径，直接调用 `_stream_final_round` / `_stream_events_final_round`，绕过 `_iter_rounds` 中的 trace 记录。

### 7.2 修复方案

在快速路径的最终响应完成后（`_RoundStreamAccumulator` 已累积完成、或 `done` 事件已产出），构造 `ModelCallTrace` 并写入：

```python
# run_streaming 的 max_rounds==1 分支
if config.max_rounds == 1:
    async for chunk in self._stream_final_round(context, config, model_access):
        yield chunk
    # 补录 ModelCallTrace
    if accumulated_response is not None:
        await self._record_trace(
            session_id,
            self._build_model_call_trace_from_response(
                round_num=1, response=accumulated_response, config=config,
            ),
        )
```

需要在快速路径中捕获最终的 `LLMResponse` 或等价数据（usage、model、latency_ms）。具体实现取决于 `_stream_final_round` / `_stream_events_final_round` 内部是否已暴露 response 信息——如已有 accumulator，从中提取；如未暴露，需要微调快速路径以 yield 后的 response 可达。

---

## 8. 正确性属性（不变量）

### INV-1：LLM 回灌内容不变
`ToolMessage.content` 始终取 `ToolExecutionResult.content`，值与重构前 `Tool.execute()` 返回的 `str` 语义等价。任何 LLM 可见的行为不应因本次重构发生变化。

### INV-2：Checkpoint 兼容
`checkpoint.after_tool_call(result=...)` 中的 `result` 仍为 `str`（取 `.content`），保持 JSON 序列化格式不变，已持久化的 checkpoint 可正常读回。

### INV-3：trace 写入幂等安全
`_record_trace` 的 fire-and-forget 语义不变。metadata 序列化失败由 `_truncate_metadata` 的 `default=str` 和 try/except 保底。

### INV-4：JSONL 前向兼容
含 `metadata` 的新 JSONL 行可被旧版 reader 读取（`_dict_to_step` 用 `pop("metadata", {})`，旧代码忽略未知字段）。

### INV-5：异常传播不变
`Tool.run()` 对 `ToolExecutionError` 仍 re-raise 不包装。`_execute_tool_call` 的异常分支只改变 `result` 的类型（从 `str` 到 `ToolExecutionResult`），不改变异常传播行为。

---

## 9. 错误处理策略

| 场景 | 处理 |
|---|---|
| `Tool.execute()` 抛 `ToolExecutionError` | `Tool.run()` re-raise，由 `_execute_tool_call` 的 `except Exception` 捕获，构造 `ToolExecutionResult(content=str(exc), metadata={"error_class": ...})` |
| `Tool.execute()` 抛其他异常 | `Tool.run()` 包装为 `ToolExecutionError` 后 re-raise |
| metadata 序列化失败 | `_truncate_metadata` 使用 `default=str` + try/except，最坏返回 `{}` |
| `_record_trace` 失败 | 既有 try/except + `logger.warning`，不阻塞主流程 |
| ErrorTrace 写入失败 | `_record_error_trace` 内部 try/except，不阻止异常向上传播 |

---

## 10. 测试策略

### 10.1 单元测试

| 测试范围 | 文件 | 覆盖点 |
|---|---|---|
| `ToolExecutionResult` 值对象 | `test/domain/agent/test_tools_unit.py` | frozen、default metadata、content 赋值 |
| `Tool.run()` 返回类型 | 同上 | 确认 `run()` 返回 `ToolExecutionResult` |
| `ToolRegistry.execute()` 返回类型 | 同上 | 确认透传 |
| `ScopedToolRegistry.execute()` 返回类型 | 同上 | 确认透传 + 权限拒绝仍抛异常 |
| 各工具 metadata | `test/infrastructure/tools/*/` | 每个工具的 metadata 字段正确性 |
| `ToolCallTrace.metadata` | `test/domain/agent/test_trace_value_objects_unit.py` | 新增字段序列化/反序列化 |
| `_truncate_metadata` | `test/infrastructure/agent/test_react_agent_trace_unit.py` | 正常/超限/空 dict |

### 10.2 集成测试

| 测试范围 | 文件 | 覆盖点 |
|---|---|---|
| trace metadata 透传 | `test/infrastructure/agent/test_react_agent_trace_unit.py` | _execute_tool_call → _record_tool_call_trace → ToolCallTrace.metadata |
| ErrorTrace 写入 | 同上 | 模拟模型调用异常 → ErrorTrace 写入 |
| error_class/error_message 填充 | 同上 | 模拟工具执行异常 → ToolCallTrace.error_class 非空 |
| JSONL 兼容 | `test/infrastructure/trace/test_local_file_trace_store_unit.py` | 含/不含 metadata 的 JSONL 行均可正确读写 |

### 10.3 回归验证

重构完成后运行全量测试确认无破坏：
```bash
PYTHONPATH=src uv run --frozen pytest
```
