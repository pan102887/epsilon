# Spec B — llm-and-tool-resilience 交付总结

## Feature

`llm-and-tool-resilience`：为 LLM API 调用和工具执行链路增加韧性机制——
tenacity 重试退避、MCP 持久 session 复用、工具级 circuit breaker。

## 产出物

### 新文件
| 文件 | 用途 |
|------|------|
| `src/infrastructure/model_access/_retry.py` | `build_retry(attempts)` 装饰器工厂：指数退避 + jitter |
| `src/infrastructure/agent/circuit_breaker_config.py` | `CircuitBreakerConfig`（TOOL_CB\_ 前缀） |
| `src/infrastructure/agent/circuit_breaker.py` | `ToolCircuitBreaker` 三态状态机 + `guard()` 异步上下文管理器 |
| `test/infrastructure/model_access/test_retry_unit.py` | 7 用例：passthrough / 重试成功 / 不可重试 / 达上限 |
| `test/infrastructure/model_access/test_openai_adapter_retry_unit.py` | 4 用例：chat 重试 / 达上限 / stream 握手 / 中途不重试 |
| `test/infrastructure/tools/mcp/test_mcp_persistent_session_unit.py` | 5 用例：session 持久 / 多次 execute / aclose / 幂等 |
| `test/infrastructure/agent/test_circuit_breaker_unit.py` | 8 用例：全状态机路径 |
| `test/infrastructure/agent/test_tool_registry_circuit_breaker_unit.py` | 4 用例：注入 / 未注入 |

### 修改文件
| 文件 | 改动 |
|------|------|
| `config.properties` | 追加 `LLM_RETRY_ATTEMPTS` 和 `TOOL_CB_*` 配置段 |
| `src/application/container_config.py` | 注入 `retry_attempts` 到 adapter；按 config 构造 breaker 注入 registry |
| `src/domain/agent/exceptions.py` | 新增 `ToolCircuitOpenError`（code 60030） |
| `src/domain/agent/tools.py` | `ToolRegistry.__init__` 新增可选 `circuit_breaker`；`execute()` 包裹 guard |
| `src/infrastructure/tools/mcp/mcp_tool_bridge.py` | `MCPToolBridge` 持久 session + `aclose()`；`MCPTool.execute` 注释 |

## 关键设计决策

1. **tenacity 装饰器工厂**：`build_retry(attempts)` 在 `<=1` 时返回恒等装饰器（零开销），
   仅装饰"首次握手"（stream 迭代过程不重试），白名单仅含三类瞬时错误。
2. **MCP 持久 session**：`discover()` 首次调用 `__aenter__`，后续 `MCPTool.execute`
   嵌套 `async with` 走 fastmcp 引用计数 fast path；`aclose()` 幂等释放。
3. **Circuit breaker 放 ToolRegistry.execute**：domain 层 Tool ABC 不感知熔断器；
   breaker 通过 duck typing（Any）注入，避免 domain→infrastructure 反向依赖。
4. **失败认定白名单**：`ToolNotFound` / `ParamValidation` / `PermissionDenied`
   属语义错误，不计入熔断失败统计。
5. **默认禁用**：LLM 重试默认 `attempts=3`（config.properties），breaker 默认 `enabled=false`，
   满足"渐进启用"运营策略。

## 测试结果

- 新增单测 **28 用例**全部通过
- 全量回归 **1644 passed, 1 failed（web_search hypothesis 已知问题，与本次改动无关）, 3 skipped**
- 与 Spec A（mcp-protocol-adapter）完成节点基线一致

## 后续可选项

- 容器关闭钩子调用 `MCPToolBridge.aclose()`（当前进程退出由 fastmcp 协议层保护）
- 熔断器持久化（跨重启保留 OPEN 状态）
- 按工具/provider 粒度差异化配置 retry 和 breaker 参数
- OpenTelemetry 指标接入：重试次数、breaker 状态变迁
