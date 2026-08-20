# 技术设计文档：Structured Agent Trace — 结构化 Agent 追踪

## 概述

本文档描述 `structured-agent-trace` feature 的技术设计，覆盖领域层值对象定义、端口协议、基础设施层 adapter 实现和 DI 装配方案。

## 架构定位

```
domain/agent/trace_value_objects.py    ← 值对象（R1）
domain/agent/ports.py                  ← TraceStorePort 追加（R2）
infrastructure/trace/trace_config.py   ← 配置类（R6）
infrastructure/trace/local_file_trace_store_adapter.py  ← 本地文件 adapter（R4）
infrastructure/agent/react_agent_adapter.py  ← 追踪记录注入点（R3）
application/container_config.py        ← DI 装配（R5）
config.properties                      ← 配置键值（R6）
```

## 设计决策

### D1：trace 值对象使用 `kind` 判别字段而非 Python Union

每个 trace step dataclass 都携带 `kind: Literal["model_call"]` / `Literal["tool_call"]` / `Literal["approval"]` / `Literal["error"]` 类属性字段。序列化为 JSON 后可通过 `kind` 反序列化到正确类型，无需 `__class__` 或 `type` 魔法字段。

Python 类型层面定义 `AgentStepTrace = ModelCallTrace | ToolCallTrace | ApprovalTrace | ErrorTrace`（type alias）。

### D2：截断在 adapter 层执行，domain 层仅定义常量

`ARGUMENTS_SUMMARY_MAX_LEN = 128`、`RESULT_SUMMARY_MAX_LEN = 256`、`ERROR_MESSAGE_MAX_LEN = 512` 定义为 `trace_value_objects.py` 中的模块级常量。ReActAgentAdapter 内部在构造 trace 对象前调用 `_truncate(text, max_len)` 工具函数截断。

### D3：trace 记录通过 `_record_trace` 私有方法集中处理

在 `ReActAgentAdapter` 新增 `async def _record_trace(self, session_id: str, step: AgentStepTrace) -> None`：
- `self._trace_store` 为 None 时直接 return（no-op）。
- 否则 `try: await self._trace_store.append_step(session_id, step)` + `except Exception: logger.warning(...)`。
- 确保所有 trace 记录异常不冒泡。

### D4：本地文件 adapter 使用 `asyncio.to_thread` 包裹 sync IO

`append_step` 内部将 JSON 序列化 + 文件 append 操作包裹在 `asyncio.to_thread` 中。文件以 `"a"` 模式打开，每次 `write(json_line + "\n")` 后不显式 `flush`（OS 缓冲即可，性能优先）。

### D5：session trace 文件命名

路径：`{TRACE_STORE_DIR}/{session_id}.jsonl`。`session_id` 中可能含 UUID 连字符，直接用作文件名（POSIX / Windows 均合法）。

### D6：`list_traces` 仅返回轻量摘要

`list_traces` 扫描目录获取 `.jsonl` 文件列表，按 mtime 倒序，对每个文件只读首行获取 `started_at_epoch`（从第一条 step 的 `timestamp_epoch` 推断），返回 `SessionTrace(session_id=stem, started_at_epoch=..., steps=[], metadata={"step_count": line_count})`。避免全量反序列化。

### D7：get_session_trace 反序列化策略

逐行 `json.loads`，根据 `kind` 字段分发到对应 dataclass 构造。无法解析的行跳过 + `logger.warning`。

### D8：ReActAgentAdapter 构造函数新增参数

`__init__` 追加 `trace_store: "TraceStorePort | None" = None`。使用 `TYPE_CHECKING` 保护导入，运行时不强制引入 trace 模块。

### D9：trace 记录位置

| 时机 | trace 类型 | 数据来源 |
|------|-----------|---------|
| `_iter_rounds` 每轮 LLM 响应返回后 | `ModelCallTrace` | `LLMResponse.usage` + `elapsed_ms` |
| `_execute_tool_call` 返回后 | `ToolCallTrace` | `tool_call.name/id/arguments` + `result` + `elapsed_ms` + `is_error` |
| `_check_and_interrupt_approval` 产生中断时 | `ApprovalTrace` | `interrupt.approval_id` + actions |
| Agent Loop 捕获非工具异常时 | `ErrorTrace` | `exc.__class__.__name__` + `str(exc)` |

### D10：session_id 获取方式

`run` / `resume` / `run_streaming` / `run_events` 方法签名中均有 `session_id: str` 参数（从 `ConversationContext` 或调用者传入）。需确认：

查看 `run` 方法签名发现 `session_id` 不是直接参数。实际上 `AgentPort.run` 签名为 `async def run(self, context: ConversationContext, config: AgentConfig) -> AgentResult`。session_id 从 `context.session_id` 获取（`ConversationContext` 上有 `session_id` 属性）。

### D11：trace_config 配置类

```python
class TraceConfig(PropertiesBaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRACE_")
    enabled: bool = True
    store_dir: str = ".epsilon/traces"
```

模块级 `trace_config = create_config(TraceConfig)` 全局实例。

## 值对象定义

```python
# src/domain/agent/trace_value_objects.py

from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Literal

ARGUMENTS_SUMMARY_MAX_LEN = 128
RESULT_SUMMARY_MAX_LEN = 256
ERROR_MESSAGE_MAX_LEN = 512


@dataclass(frozen=True)
class ModelCallTrace:
    kind: Literal["model_call"] = field(default="model_call", init=False)
    round_num: int
    model: str
    prompt_id: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    timestamp_epoch: float


@dataclass(frozen=True)
class ToolCallTrace:
    kind: Literal["tool_call"] = field(default="tool_call", init=False)
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


@dataclass(frozen=True)
class ApprovalTrace:
    kind: Literal["approval"] = field(default="approval", init=False)
    round_num: int
    approval_id: str
    actions_summary: list[str]
    timestamp_epoch: float


@dataclass(frozen=True)
class ErrorTrace:
    kind: Literal["error"] = field(default="error", init=False)
    round_num: int
    error_class: str
    error_message: str
    timestamp_epoch: float


AgentStepTrace = ModelCallTrace | ToolCallTrace | ApprovalTrace | ErrorTrace


@dataclass(frozen=True)
class SessionTrace:
    session_id: str
    started_at_epoch: float
    steps: list[AgentStepTrace] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
```

## Port 定义

在 `src/domain/agent/ports.py` 中追加：

```python
class TraceStorePort(Protocol):
    """结构化 Agent 追踪存储端口。"""

    async def append_step(self, session_id: str, step: "AgentStepTrace") -> None: ...
    async def get_session_trace(self, session_id: str) -> "SessionTrace | None": ...
    async def list_traces(self, limit: int = 20) -> list["SessionTrace"]: ...
```

## 本地文件 Adapter 实现

```python
# src/infrastructure/trace/local_file_trace_store_adapter.py

class LocalFileTraceStoreAdapter:
    """本地 JSONL 文件 trace 存储。"""

    def __init__(self, store_dir: str) -> None:
        self._store_dir = Path(store_dir)
        self._store_dir.mkdir(parents=True, exist_ok=True)

    async def append_step(self, session_id: str, step: AgentStepTrace) -> None:
        line = json.dumps(self._step_to_dict(step), ensure_ascii=False)
        await asyncio.to_thread(self._append_line, session_id, line)

    async def get_session_trace(self, session_id: str) -> SessionTrace | None:
        path = self._store_dir / f"{session_id}.jsonl"
        if not path.exists():
            return None
        steps = await asyncio.to_thread(self._read_steps, path)
        started = steps[0].timestamp_epoch if steps else 0.0
        return SessionTrace(session_id=session_id, started_at_epoch=started, steps=steps)

    async def list_traces(self, limit: int = 20) -> list[SessionTrace]:
        files = sorted(self._store_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)[:limit]
        return [self._file_to_summary(f) for f in files]

    def _append_line(self, session_id: str, line: str) -> None:
        path = self._store_dir / f"{session_id}.jsonl"
        with path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def _read_steps(self, path: Path) -> list[AgentStepTrace]:
        steps = []
        for raw in path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                steps.append(self._dict_to_step(json.loads(raw)))
            except Exception:
                logger.warning("trace 行解析失败，跳过: %s", raw[:100])
        return steps

    def _file_to_summary(self, path: Path) -> SessionTrace:
        # 只读首行获取 started_at + 统计行数
        ...

    @staticmethod
    def _step_to_dict(step: AgentStepTrace) -> dict[str, Any]:
        from dataclasses import asdict
        return asdict(step)

    @staticmethod
    def _dict_to_step(d: dict[str, Any]) -> AgentStepTrace:
        kind = d.get("kind")
        # 按 kind 分发到对应 dataclass
        ...
```

## ReActAgentAdapter 集成

### 插桩位置

1. **ModelCallTrace**：在 `_iter_rounds` 中模型调用完成后（`LLMResponse` 返回时）：
   ```python
   await self._record_trace(session_id, ModelCallTrace(
       round_num=round_num,
       model=config.model or "default",
       prompt_id=config.prompt_id,
       input_tokens=response.usage.get("prompt_tokens", 0),
       output_tokens=response.usage.get("completion_tokens", 0),
       latency_ms=elapsed_ms,
       timestamp_epoch=time.time(),
   ))
   ```

2. **ToolCallTrace**：在 `_execute_tool_call` 返回后，通过在 `_dispatch_concurrent_tool_calls` 或单工具执行点记录：
   ```python
   start = time.time()
   result, is_error = await self._execute_tool_call(context, tool_call, config)
   elapsed = (time.time() - start) * 1000
   await self._record_trace(session_id, ToolCallTrace(
       round_num=round_num,
       tool_name=tool_call.name,
       tool_call_id=tool_call.id,
       arguments_summary=_truncate(tool_call.arguments, ARGUMENTS_SUMMARY_MAX_LEN),
       result_summary=_truncate(result, RESULT_SUMMARY_MAX_LEN),
       success=not is_error,
       latency_ms=elapsed,
       timestamp_epoch=time.time(),
   ))
   ```

3. **ApprovalTrace**：在 `_check_and_interrupt_approval` 产生中断后记录。

4. **ErrorTrace**：在 Agent Loop 顶层 try/except 中非工具异常路径记录。

### session_id 传递

`run` 系列方法通过 `context.session_id` 获取 session_id。需确认 `ConversationContext` 上有此属性。若无，则从 `metadata["session_id"]` 或由调用层显式传入。

## DI 容器装配

```python
# container_config.py 新增

async def _create_trace_store() -> "TraceStorePort | None":
    from infrastructure.trace.trace_config import trace_config
    if not trace_config.enabled:
        return None
    from infrastructure.trace.local_file_trace_store_adapter import LocalFileTraceStoreAdapter
    return LocalFileTraceStoreAdapter(store_dir=trace_config.store_dir)
```

在 `_create_agent` 中：
```python
trace_store = await container.resolve_optional(TraceStorePort)  # 或 try/except KeyError
return ReActAgentAdapter(
    tool_registry=...,
    context_builder=...,
    approval_policy=...,
    approval_store=...,
    trace_store=trace_store,
)
```

## 配置

`config.properties` 追加：
```properties
# --- Structured Agent Trace ---
TRACE_ENABLED=true
TRACE_STORE_DIR=.epsilon/traces
```

## 测试策略

| 测试文件 | 覆盖需求 |
|----------|---------|
| `test/domain/agent/test_trace_value_objects_unit.py` | R1.* 值对象构造、kind 字段、frozen 验证 |
| `test/infrastructure/trace/test_local_file_trace_store_unit.py` | R4.* append/read/list/目录创建/异常处理 |
| `test/infrastructure/agent/test_react_agent_trace_unit.py` | R3.* trace 记录集成、故障隔离、no-op 路径 |

## 风险与缓解

| 风险 | 缓解 |
|------|------|
| 高频 trace 写入拖慢 Agent Loop | D4：asyncio.to_thread 异步化；NFR-1 约束 < 5ms |
| trace 文件无限增长 | 后续 spec 补充清理策略；当前仅记录步骤级摘要，文件增长可控 |
| session_id 可能含特殊字符 | UUID 格式（连字符）合法文件名；非 UUID 场景做 sanitize |
| 并发 append 丢数据 | 单进程 + POSIX append-only 原子写保证；多进程场景后续处理 |
