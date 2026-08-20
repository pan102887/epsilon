# Spec B — llm-and-tool-resilience 任务清单

## 任务编号约定

- T0：环境与依赖
- T1*：R1 LLM API 重试
- T2*：R2 MCP 持久 session
- T3*：R3 工具熔断器
- T4*：装配与配置
- T5*：测试
- T6*：收尾

## 任务列表

- [x] **T0.1** 新增依赖 `tenacity>=8.0` 到 `pyproject.toml`（避免锁定到 8.x；
      tenacity API 8/9 兼容）
- [x] **T0.2** 验证 `.venv` 安装 tenacity 正常（`uv sync` / `pip install` 二选一）

### R1：LLM API 重试退避（tenacity）

- [x] **T1.1** 新增 `src/infrastructure/model_access/_retry.py`：
  - `build_retry(attempts: int)` 装饰器工厂（attempts ≤ 1 时返回恒等函数）；
  - 异常白名单 `_RETRYABLE_EXCEPTIONS = (ModelTimeoutError, ModelRateLimitError, ModelConnectionError)`；
  - `before_sleep_log` 输出 INFO 日志。
- [x] **T1.2** 重构 `OpenAICompatibleAdapter`：
  - `__init__` 新增 `retry_attempts: int = 1` 参数（默认 1=禁用）；
  - `chat` 拆出 `_chat_once(params)` 内部方法，外层用 `build_retry(attempts)` 包装；
  - `stream` 拆出 `_stream_open(params)` 与 `_stream_iter(response, ...)`，
    仅 `_stream_open` 应用 retry。
- [x] **T1.3** 在 `application/container_config.py` 的 `_create_provider_adapter` 中
      读取 `LLM_RETRY_ATTEMPTS`（新增 pydantic-settings 配置项或直接复用模块级常量
      工具）注入 `OpenAICompatibleAdapter(retry_attempts=...)`。

### R2：MCP 持久 session 复用

- [x] **T2.1** 修改 `MCPToolBridge`：
  - 新增 `_session_owner: bool = False` 标志；
  - `discover()` 内首次调用时 `await self._client.__aenter__()` 并置 owner=True；
  - 新增 `async def aclose(self)` 方法显式 `__aexit__`，幂等（重复调用不抛）；
  - `discover()` 失败时进入 except 分支 → 不持有 session（保留 fail-soft）。
- [x] **T2.2** `MCPTool.execute` 保持 `async with self._client:` 嵌套写法
      （由 fastmcp 引用计数自然 fast path），但新增注释说明依赖关系。
- [x] **T2.3** （可选，本期跳过）容器关闭钩子调用 `aclose()`：当前 `Container`
      无析构钩子，留 TODO 注释；进程退出时由 fastmcp 自身保护协议处理。

### R3：工具级 circuit breaker

- [x] **T3.1** 在 `src/domain/agent/exceptions.py` 追加 `ToolCircuitOpenError`
      （继承 `ToolExecutionError`，错误码 `60030`）。
- [x] **T3.2** 新增 `src/infrastructure/agent/circuit_breaker_config.py`
      （pydantic-settings，前缀 `TOOL_CB_`）：
  - `enabled: bool = False`
  - `failure_threshold: int = 5`
  - `recovery_timeout_seconds: float = 30.0`
  - `half_open_max_calls: int = 1`
- [x] **T3.3** 新增 `src/infrastructure/agent/circuit_breaker.py`：
  - `_BreakerState` dataclass；
  - `ToolCircuitBreaker` 主类，注入 `time_fn=time.monotonic`（便于测试）；
  - `guard(tool_name)` 异步上下文管理器；
  - 状态切换用 `asyncio.Lock` 保护；
  - 失败计入逻辑遵循 R3.3。
- [x] **T3.4** 修改 `src/domain/agent/tools.py` `ToolRegistry`：
  - `__init__` 新增可选 `circuit_breaker: ToolCircuitBreaker | None = None`；
  - `execute(request)` 内：breaker 非 None → `async with breaker.guard(tool.name):`
    包裹原有调用；breaker None → 走原路径（向下兼容）。

### R4：装配与配置

- [x] **T4.1** `application/container_config.py`：
  - `_create_tool_registry` 内按 `circuit_breaker_config.enabled` 决定是否
    构造 `ToolCircuitBreaker` 并注入 `ToolRegistry`。
- [x] **T4.2** `config.properties`：追加 R1 / R3 配置示例段（默认禁用，注释说明
      启用方式与各项含义）。

### R5：测试

- [x] **T5.1** 新增 `test/infrastructure/model_access/test_retry_unit.py`：
  - passthrough（attempts=1）；
  - 重试到第 N 次成功；
  - 不可重试异常立即抛；
  - 达上限抛原始异常类型。
- [x] **T5.2** 新增 `test/infrastructure/model_access/test_openai_adapter_retry_unit.py`：
  - mock `AsyncOpenAI.chat.completions.create`：
    - chat 重试 + 成功；
    - chat 重试达上限抛 `ModelTimeoutError`；
    - stream 首次握手抛异常 → 重试；
    - stream yield 后断流不重试（用 raise mid-iteration 验证）。
- [x] **T5.3** 新增 `test/infrastructure/tools/mcp/test_mcp_persistent_session_unit.py`：
  - 用 fastmcp `FastMCP` in-memory server 起 stub 工具；
  - 启动 bridge → discover → 多次 execute；
  - 断言 client `__aenter__` 调用次数 = 1（持久）；
  - `aclose()` 后 `__aexit__` 被调用；幂等：重复 close 不抛。
- [x] **T5.4** 新增 `test/infrastructure/agent/test_circuit_breaker_unit.py`：
  - CLOSED → 失败累积达阈值 → OPEN；
  - OPEN → 抛 `ToolCircuitOpenError` 不调底层 fn；
  - OPEN → 时钟推进达 recovery_timeout → HALF_OPEN；
  - HALF_OPEN 探测成功 → CLOSED + 计数清零；
  - HALF_OPEN 探测失败 → OPEN + 重新计时；
  - HALF_OPEN 并发探测：第 1 个放行、第 2 个拒绝；
  - 不计入异常类型（ToolNotFound / ParamValidation / PermissionDenied）正常通过。
- [x] **T5.5** 新增 `test/infrastructure/agent/test_tool_registry_circuit_breaker_unit.py`：
  - registry 注入 breaker → execute 调用 guard；
  - registry 未注入 → 走原有快路径（无 guard）。
- [x] **T5.6** 全量回归：`pytest test -q`，与 Spec A 完成节点对齐基线。

### R6：收尾

- [x] **T6.1** 编写 `summary.md` 收尾：交付清单、关键决策、测试结果、后续可选项。

## 完成标准

- 所有 T* 任务均勾选完成；
- 每个 R 域至少 1 项单测通过；
- 全量回归不引入新失败（与 Spec A 完成节点基线对齐）；
- spec 文档树齐全：requirement.md / design.md / tasks.md / summary.md。
