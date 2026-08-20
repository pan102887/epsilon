# 维度 5：可靠性与性能

## 评估结论

**评分：3 / 5**。SSE 流式错误恢复 + `[DONE]` 协议、ReAct 失败路径以 `ToolMessage` 回写、Provider Round-Robin 与 OpenTelemetry 可观测性骨架均已就位；但 **配置仅做 hot_reload 不含健康探测**、**无显式的 retry / 限流 / circuit breaker**、**无 SLO / 成本归因**、**评测回归守护不在 CI 中**，因此处于 3 与 4 之间，评 3 更贴合现状。

## 证据与分析

- [`epsilon-boot/src/application/routers/chat.py:L108-L134`](../../../epsilon-boot/src/application/routers/chat.py)
  `_event_generator` 在 `async for chunk in service.stream_chat(...)` 外层包 `try/except Exception`；异常路径写 `{"error": True, "message": str(exc), "finished": True}` + `[DONE]` 两条 SSE 事件后结束。这避免了异常冒泡到 `sse_starlette` 的 TaskGroup 导致 `ExceptionGroup`，保证客户端始终收到终止标记。
- [`epsilon-boot/src/infrastructure/agent/react_agent_adapter.py:L155-L178`](../../../epsilon-boot/src/infrastructure/agent/react_agent_adapter.py)
  工具执行分支 `try: result = await self._tool_registry.execute(tool_call) except Exception as e: result = str(e)`，权限拒绝也以 `str(error)` 回写 ToolMessage；上下文保持完整性，下一轮模型可以据此自愈。
- [`epsilon-boot/src/infrastructure/model_access/provider_registry.py:L170-L200`](../../../epsilon-boot/src/infrastructure/model_access/provider_registry.py)
  当单个 Provider 被移除（如密钥失效被管理侧下掉），`get_adapter_for_model` 检测到 `record is None` 即清理反向索引、重建 `cycle` 并重试；不会让模型路由在"半移除"状态下把流量打到空 Provider。
- [`epsilon-boot/src/infrastructure/telemetry/otel_config.py:L16-L75`](../../../epsilon-boot/src/infrastructure/telemetry/otel_config.py)
  `OtelConfig` 暴露 `exporter_endpoint` / `exporter_insecure` / `traces_sampler` / `traces_sampler_arg` / 四个 `instrument_*` 开关，默认 `enabled=False`（零开销）但运维侧可通过 `config.properties` 打开。
- [`epsilon-boot/src/infrastructure/telemetry/otel_setup.py:L1-L44`](../../../epsilon-boot/src/infrastructure/telemetry/otel_setup.py)
  `_build_resource` 注入 `service.name` / `service.version` / `deployment.environment`；FastAPI / httpx / Redis / SQLAlchemy 四个 instrumentation 可由开关控制；`init_telemetry` / `shutdown_telemetry` 作为异步资源在容器启动/关闭时钩入 lifespan，关停时会 flush span。

当前 **缺失**：
- 无 `retry` / `circuit breaker` / `backoff` 策略：`OpenAICompatibleAdapter` 仅把 `APITimeoutError` / `RateLimitError` 等 SDK 异常转为领域异常，未主动重试。
- 无 Provider 健康探测：Provider 故障仅靠"下次调用再拿 Round-Robin 选到"，缺少主动探活。
- 回归评测脚本 `run_eval.py --baseline=<>` 存在（本次交付），但尚未接入 CI 门禁。

## 业界框架对照

- **Google SRE — Principles / SLO book**（<https://sre.google/sre-book/service-level-objectives/>）：要求"每个面向用户的服务有 SLI / SLO、有错误预算、有告警梯度"。项目当前无 SLI 定义，仅有 OTel span；与 SRE 原则的距离较大。
- **Anthropic — Observability best practices（Claude documentation / Tool use with Claude）**：建议把 tool call latency、token 成本与失败原因按 session 维度聚合。项目目前只有 span + 日志，未做"按 session / per-tool"成本归因。
- **OpenAI — A Practical Guide to Building Agents（Reliability & cost control）**：建议为 Agent 请求设置 hard stop（最大 wall-clock、最大 token）。项目有 `max_rounds`，但没有 wall-clock 上限，长 tool 序列仍可能超时。
- **τ-bench / AgentBench**：强调按基准持续回归。本次评测脚本提供了该能力，但需要真正接入 CI 才能形成闭环。

## 改进建议

1. **P0 — Provider 级健康探测 + 退避重试**：在 `ProviderRegistry` 新增 `mark_provider_unhealthy(name, ttl)`，由 `OpenAICompatibleAdapter` 在连续 N 次 `APIConnectionError` 或 `5xx` 后标记，TTL 期内跳过选择；配合 `uv add --group evaluation tenacity`（或等效标准库实现）做有界重试。引用 Google SRE "Handling Overload" 原则。
2. **P1 — 定义核心 SLO 并落 Prometheus recording rule**：chat 成功率 ≥ 99%、p95 首 token 延迟 ≤ 2 s、token 成本 per session 预算告警；以 OTel metrics 暴露。引用 **Google SRE — SLOs** / **Anthropic — Observability best practices**。
3. **P1 — 评测回归脚本接入 CI**：在 PR 流水线跑 `run_eval.py --metric=all --baseline=<main 基线>`；任一指标回退 ≥ 5pp 以退出码 2 使 CI 失败；结果归档到 `docs/evaluation/results/*.json` 供长期跟踪。
4. **P2 — 为工具调用设置 wall-clock 预算**：在 `AgentConfig` 新增 `max_wall_clock_seconds`，由 `ReActAgentAdapter.run` 计时比较；超时抛 `AgentTimeoutError` 并通过 SSE 返回，补齐 OpenAI 建议的"hard stop"要求。

<!-- AUTO-START: aggregate_scores -->
### 自动生成区块

**评分**：3 / 5，**权重**：0.12，**加权得分**：0.360

**人工打分理由**：SSE 流式错误恢复 + `[DONE]` 协议、ReAct 失败路径以 `ToolMessage` 回写、Provider Round-Robin 自愈、OpenTelemetry 可观测性骨架均已就位，部分对齐 OpenAI "A Practical Guide to Building Agents — Reliability & cost control" 对错误恢复的要求，以及 Anthropic "Observability best practices" 对 trace 基建的要求。但项目缺少 Google SRE Book 强调的 SLI/SLO 与错误预算定义、无主动 retry / circuit breaker / Provider 健康探测、无 wall-clock 预算，τ-bench 所建议的持续回归也尚未进入 CI 门禁，因此处于 3 与 4 之间，评 3 更贴合现状。

#### 全局自动化指标（供维度交叉参考）

| 指标 | 分子 | 分母 | 比率 | 样本数 |
| --- | --- | --- | --- | --- |
| `tool_call_success_rate` | 6 | 20 | 0.3000 | 20 |
| `delegation_correctness` | 6 | 15 | 0.4000 | 15 |
| `context_compaction_effectiveness` | 36 | 36 | 1.0000 | 36 |

#### 证据清单（来自 `tests/evaluation/evidence/catalog.py`）

- `epsilon-boot/src/application/routers/chat.py:108-134`
- `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py:155-178`
- `epsilon-boot/src/infrastructure/model_access/provider_registry.py:170-200`
- `epsilon-boot/src/infrastructure/telemetry/otel_config.py:16-75`
- `epsilon-boot/src/infrastructure/telemetry/otel_setup.py:1-44`

<!-- AUTO-END: aggregate_scores -->
