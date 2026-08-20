# Spec B — llm-and-tool-resilience 技术设计

## 总览

三个独立子模块共享一个 Spec，因为它们都属于"基础设施层韧性增强"，且互不耦合。
设计采用最小侵入：domain 层零改动，全部新增逻辑落在 `infrastructure/`。

```
┌────────────────────────────────────────────────────────────────────┐
│              ReActAgentAdapter / ChatServiceAdapter                │
└────────────┬─────────────────────────────────┬─────────────────────┘
             │                                 │
   ModelAccessPort                       ToolRegistry.execute
             │                                 │
   ┌─────────▼─────────┐              ┌────────▼────────┐
   │ OpenAICompatible  │              │   Tool.run()    │
   │   Adapter         │              │                 │
   │   ┌──────────────┐│              │ ┌─────────────┐ │
   │   │ R1: tenacity ││              │ │ R3: circuit │ │
   │   │  retry wrap  ││              │ │   breaker   │ │
   │   └──────────────┘│              │ │  decorator  │ │
   └───────────────────┘              │ └─────────────┘ │
                                      └────┬────────────┘
                                           │
                                    MCPTool.execute
                                    ┌──────▼─────────┐
                                    │ R2: persistent │
                                    │   client       │
                                    │   session      │
                                    └────────────────┘
```

## 组件设计

### C1：tenacity 重试装饰器（R1）

**位置**：`src/infrastructure/model_access/_retry.py`（新文件，模块级 helper）

```python
# 伪代码示意
from tenacity import (
    AsyncRetrying, retry_if_exception_type, stop_after_attempt,
    wait_random_exponential, before_sleep_log,
)

def build_retry(attempts: int) -> Callable[[F], F]:
    if attempts <= 1:
        return lambda fn: fn  # passthrough，无开销
    return retry(
        stop=stop_after_attempt(attempts),
        wait=wait_random_exponential(min=1, max=30),
        retry=retry_if_exception_type(
            (ModelTimeoutError, ModelRateLimitError, ModelConnectionError)
        ),
        before_sleep=before_sleep_log(logger, logging.INFO),
        reraise=True,
    )
```

**集成**：`OpenAICompatibleAdapter` 在 `__init__` 接收 `retry_attempts`：

- `chat`：直接装饰 `_chat_once` 内部方法。
- `stream`：拆为 `_stream_open()`（首次握手）+ `_stream_iter()`（迭代）；
  仅装饰 `_stream_open`。`stream` 异步生成器：
  ```python
  async def stream(self, request):
      response = await self._stream_open_with_retry(request)  # ← 装饰
      async for chunk in self._stream_iter(response, request):
          yield chunk
  ```

**正确性验证**：装饰只发生在首次握手，已 yield 后任何中途异常按原始类型上抛
（不重试，避免重复 token）。

### C2：MCP 持久 session（R2）

**位置**：`src/infrastructure/tools/mcp/mcp_tool_bridge.py`（改动）

`MCPToolBridge` 加入持久 session 状态：

```python
class MCPToolBridge:
    def __init__(self, ...):
        self._client = Client(transport, timeout=timeout)
        self._session_owner = False  # 是否持有外层引用

    async def discover(self) -> list[MCPTool]:
        if not self._session_owner:
            await self._client.__aenter__()
            self._session_owner = True
        tools = await self._client.list_tools()
        return [MCPTool(self._client, t, ...) for t in tools]

    async def aclose(self) -> None:
        if self._session_owner:
            try:
                await self._client.__aexit__(None, None, None)
            except Exception as e:
                logger.warning("MCPToolBridge.aclose() 异常: %s", e)
            finally:
                self._session_owner = False
```

`MCPTool.execute`：

```python
async def execute(self, **kwargs):
    # 由 bridge 持久 session 时，外层 with 已 open；
    # 嵌套 with 在 fastmcp 2.x 通过引用计数走 fast path。
    async with self._client:
        result = await self._client.call_tool(self._mcp_name, kwargs, ...)
```

**关键点**：
- 不破坏现有"嵌套 `async with`"语义（保留作为退化路径）。
- bridge 析构责任在 `application/container_config.py` 容器关闭钩子（可后续接入；
  本期先保证 `aclose()` 可用）。

### C3：工具级 circuit breaker（R3）

**位置**：
- `src/infrastructure/agent/circuit_breaker.py`（新文件，状态机实现）
- `src/infrastructure/agent/circuit_breaker_config.py`（pydantic-settings 配置）
- `src/domain/agent/exceptions.py`（追加 `ToolCircuitOpenError`）

**状态机数据结构**：

```python
@dataclass
class _BreakerState:
    state: Literal["CLOSED", "OPEN", "HALF_OPEN"] = "CLOSED"
    failure_count: int = 0
    opened_at: float = 0.0  # OPEN 进入时间，用于计算 recovery
    half_open_in_flight: int = 0  # HALF_OPEN 探测并发计数

class ToolCircuitBreaker:
    def __init__(self, *, failure_threshold, recovery_timeout, half_open_max_calls):
        self._states: dict[str, _BreakerState] = {}
        self._lock = asyncio.Lock()  # 状态切换串行化

    async def guard(self, tool_name: str) -> AsyncContextManager[None]:
        # 进入：检查状态、决定 PASS / RAISE / 记 in-flight
        # 退出：成功 → 清理；异常（计数）→ 失败计数 + 状态切换
```

**与 Tool.run 集成**：装饰器形态，避免修改 `Tool` ABC：

```python
# 在 ToolRegistry.execute 中包裹（不动 Tool.run）
async def execute(self, request):
    tool = self._tools.get(request.name)
    if tool is None:
        raise ToolNotFoundError(...)
    if self._breaker is None:  # 未启用
        return await tool.run(request)
    async with self._breaker.guard(tool.name):
        return await tool.run(request)
```

**为什么放在 ToolRegistry 而不是 Tool.run**：
- `Tool` ABC 是领域层，不应感知熔断器（NFR-1）；
- `ToolRegistry` 已是统一执行入口，所有路径（直接 / 含 ScopedToolRegistry）均经过；
- ScopedToolRegistry 委托底层 ToolRegistry，因此熔断器对 scoped 视图同样生效；
- 装饰器外置便于 mock、单测无 Tool 依赖。

**失败认定**（R3.3）：
```python
_NON_FAILURE_EXCEPTIONS = (
    ToolNotFoundError,
    ToolParameterValidationError,
    ToolPermissionDeniedError,
)
# HandoffPerformed 不继承 BizException，自然被视为成功（不触发 except）
```

**HALF_OPEN 探测并发**：以 `half_open_in_flight < half_open_max_calls` 为放行条件；
拒绝时直接抛 `ToolCircuitOpenError`。

## 配置与装配

### 新增配置项（config.properties）

```properties
# LLM 重试（每提供商可用 MODEL_<PROVIDER>_RETRY_ATTEMPTS 覆盖；默认值在适配器侧）
LLM_RETRY_ATTEMPTS=3

# 工具熔断器
TOOL_CIRCUIT_BREAKER_ENABLED=false
TOOL_CB_FAILURE_THRESHOLD=5
TOOL_CB_RECOVERY_TIMEOUT_SECONDS=30
TOOL_CB_HALF_OPEN_MAX_CALLS=1
```

### 装配链改动

**`OpenAICompatibleAdapter.__init__`** 增 `retry_attempts: int = 1` 参数。
`_create_provider_adapter`（在 `container_config.py`）从 `ProviderConfig.max_retries`
（已存在，复用）+ 新增模块级 `LLM_RETRY_ATTEMPTS` 选最大值传入。

**`ToolRegistry.__init__`** 增 `circuit_breaker: ToolCircuitBreaker | None = None`
参数。`_create_tool_registry`（在 `container_config.py`）按
`TOOL_CIRCUIT_BREAKER_ENABLED` 决定是否注入 breaker。

## 关键决策

### D1：tenacity 装饰只覆盖 stream 首次握手

**权衡**：
- 全程重试（含 yield 后）：实现简单，但会向上游回放重复 token（消息错乱）。
- **本方案**：拆分握手 / 迭代两阶段；正确但需重构 stream 方法。

**结论**：选择正确性方案。重复 token 是不可接受的副作用，远比"实现简单"重要。

### D2：MCP 持久 session 不在 execute 内补偿

**问题**：远端可能主动断连，`call_tool` 抛 connection error。
**选项 A**：execute 内重新 `__aenter__` 重试 → 复杂度爆炸（重入、并发、错误吞）。
**选项 B（本方案）**：仅持有外层 with；断连后让 R3（熔断器）兜底；
重连恢复留 v2。
**理由**：保持 R2 范围最小，避免与 R3 职责重叠。

### D3：熔断器实现自研而非 pybreaker / circuitbreaker

| 选项 | 优 | 劣 |
|---|---|---|
| pybreaker | 成熟、文档完善 | 偏 sync、async 路径靠 wrapper、不直接对应 asyncio.Lock |
| circuitbreaker (PyPI) | 轻量装饰器 | 仅装饰器形态，状态非 per-tool / 难注入 mock 时间 |
| **自研（本方案）** | 200 行、完全 fit `Tool` 调用图、易测（注入 time fn） | 维护成本（功能边界小，可控） |

### D4：熔断器装在 ToolRegistry 而非 Tool

- Tool ABC 是 domain 层（NFR-1）；
- 装在 Registry 自动覆盖 ScopedToolRegistry（委托关系）；
- 不需修改任何具体 Tool 子类。

### D5：每 provider 独立 retry_attempts 还是全局

**本方案**：全局 `LLM_RETRY_ATTEMPTS`，不为每个 provider 单独配置（已有 `max_retries`
作为 OpenAI SDK 内置重试，与本层独立）。理由：tenacity 层重试更面向"瞬时网络抖动"，
是跨 provider 共性需求；按需未来可引入 `MODEL_<P>_TENACITY_ATTEMPTS` 覆盖。

### D6：HALF_OPEN 用并发计数而非 token bucket

**理由**：单次探测语义最简、最安全；多次探测在 LLM 工具调用语境下意义不大
（一次成功就足以判定恢复）。

## 测试策略

| 模块 | 测试类型 | 关键用例 |
|---|---|---|
| `_retry.py` | 单测 | passthrough（attempts≤1）、`ModelTimeoutError` 触发重试、`ModelAccessError` 不重试、达上限抛原始异常 |
| `OpenAICompatibleAdapter` | 单测（mock SDK） | `chat` retry 正确路径、`stream` 首次握手 retry、yield 后异常不重试 |
| `MCPToolBridge` | 单测（fastmcp in-memory） | `discover()` 后持久 session 状态、多次 `execute` 不重复握手（断言 `__aenter__` 调用次数）、`aclose()` 正确释放 |
| `ToolCircuitBreaker` | 单测（注入虚拟时钟） | CLOSED→OPEN（达阈值）、OPEN→HALF_OPEN（冷却到期）、HALF_OPEN→CLOSED（探测成功）、HALF_OPEN→OPEN（探测失败）、不计入异常类型 |
| `ToolRegistry` 集成 | 单测 | breaker 注入与未注入路径、`OPEN` 时直接抛 `ToolCircuitOpenError` 而不调 Tool.execute |

## 模块边界一览

```
src/infrastructure/model_access/_retry.py          [新] R1 装饰器工厂
src/infrastructure/model_access/openai_compatible_adapter.py [改] 接入 retry，stream 拆分
src/infrastructure/model_access/provider_config.py [改] 增 retry_attempts 字段（可选，覆盖全局）

src/infrastructure/tools/mcp/mcp_tool_bridge.py    [改] 持久 client，新增 aclose()

src/infrastructure/agent/circuit_breaker.py        [新] R3 状态机
src/infrastructure/agent/circuit_breaker_config.py [新] R3 pydantic-settings
src/domain/agent/exceptions.py                     [改] 增 ToolCircuitOpenError
src/domain/agent/tools.py                          [改] ToolRegistry 接受可选 breaker
src/application/container_config.py                [改] 装配 retry/breaker

config.properties                                  [改] 新增 R1/R3 配置项
pyproject.toml                                     [改] 新增 tenacity 依赖
```

## 已规避的非目标

- 跨提供商 fallback（属 model-routing 主题）。
- MCP 重连恢复（断连后自动 `__aexit__/__aenter__`）。
- 熔断器分布式状态（多 worker 共享）。
- Prometheus 指标暴露（可在后续 telemetry Spec 中添加 `tool_circuit_breaker_state`
  gauge）。
