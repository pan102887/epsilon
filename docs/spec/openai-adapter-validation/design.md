# 设计文档：OpenAICompatibleAdapter 风险项修复

## 概述

本文档针对 `requirement.md` 中识别的 3 项潜在风险，设计最小化修复方案。

---

## 风险 1：流式迭代阶段异常未映射为领域异常（中优先级）

### 问题分析

`stream()` 方法中 `async for chunk in response:` 循环没有 try-except 保护。OpenAI SDK 的 `AsyncStream.__stream__` 内部通过 `response.aiter_bytes()` 读取 SSE 数据流，迭代期间可能抛出：

| 异常类型 | 触发场景 |
|---------|---------|
| `httpx.ReadTimeout` | 服务端长时间未推送新 chunk |
| `httpx.RemoteProtocolError` | 服务端未发送完整 chunked body 即断连 |
| `httpx.ReadError` | 底层 socket 读取错误 |
| `openai.APIError` | SSE 流中推送了 error event |

这些异常会以原始形态泄漏到 application 层，违反领域异常边界封装原则。

### 修复方案

在 `stream()` 方法的 `async for chunk in response:` 循环外包裹 try-except，映射规则与 `_chat_completion_once` / `_stream_open_once` 保持一致：

```python
async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
    params = self._build_params(request, stream=True)
    response = await self._stream_open(params)

    acc: dict[int, dict[str, Any]] = {}

    try:
        async for chunk in response:
            # ... 现有迭代逻辑不变 ...
            pass
    except APITimeoutError:
        raise ModelTimeoutError(
            timeout_seconds=self._config.timeout,
            request_info={"model": params.get("model"), "phase": "stream_iteration"},
        )
    except APIError as exc:
        raise ModelAccessError(
            message=f"流式迭代中模型服务错误: {exc.message}",
            details={"model": params.get("model"), "status_code": exc.status_code, "phase": "stream_iteration"},
        )
    except httpx.ReadTimeout:
        raise ModelTimeoutError(
            timeout_seconds=self._config.timeout,
            request_info={"model": params.get("model"), "phase": "stream_iteration"},
        )
    except (httpx.RemoteProtocolError, httpx.ReadError) as exc:
        raise ModelConnectionError(
            reason=str(exc),
            request_info={"model": params.get("model"), "phase": "stream_iteration"},
        )
```

### 设计决策

- **不重试**：迭代阶段已向上游 yield 了部分 token，重试会导致重复 token 回放，与已有 docstring 中"yield 后中途断流不重试"策略一致。
- **`phase` 字段**：在 `request_info` 中增加 `"phase": "stream_iteration"`，与握手阶段异常区分，便于日志排障。
- **异常优先级**：先捕获 `APITimeoutError`（SDK 层），再捕获 `APIError`（SDK 层），最后捕获 `httpx.*`（传输层），避免 SDK 已包装的异常被传输层 catch 覆盖。

---

## 风险 2：流式 tool_call.id 空值未校验（低优先级）

### 问题分析

同步 `chat()` 方法在构造 `ToolCallRequest` 前对每个 `tool_call.id` 做了显式空值校验，空值时抛出 `InvalidToolCallIdError`。

流式 `stream()` 方法的 `_materialize_full_tool_calls()` 使用 `slot.get("id") or None` 折叠空值，但不抛出异常。当 `finished=True` 分片输出包含 id 为空的 tool_call 时，下游 `_RoundStreamAccumulator` 的 D3 容错分支通过 `not delta.id` 检测违约并回退到增量累积结果。

**功能影响评估**（2026-06-05 修订）：

经全链路追踪，D3 容错回退的**实际效果是静默吞掉 tool_call**：

1. `_materialize_full_tool_calls` 把 `''` 归一化为 `None` → D3 回退触发
2. 增量 `build_response()` 中 `slot.get("id") or ""` = `""` → `not tc_id` → skip
3. `response.tool_calls = []`（空列表）
4. `_iter_rounds` 走 text 路径 → `RoundOutcome(kind="text")`
5. 用户看到空回复或部分文本，tool-use loop **静默断裂**，无任何错误提示

对比：`tool_call.id` 为空时，无论是否抛异常，tool-use loop 都必定断裂——没有 id 无法构造后续 `ToolMessage`（`tool_call_id` 必须匹配），后续 API 调用也会 400。因此 D3 的"容错"实质上是"静默失败 vs fail-fast 给出明确错误"的区别，**fail-fast 是更好的用户体验**。

### 修复方案（采用 Option C：调用方位补校验）

保持 `_materialize_full_tool_calls()` 的"空串归一化为 None"语义不变（不修改方法签名，不破坏 5 个既有归一化测试），在 `stream()` 方法中两处调用 `_materialize_full_tool_calls(acc)` 之后，对返回列表逐项校验 id：

```python
# stream() 方法 finished 分支和 usage-only 分支中，
# _materialize_full_tool_calls 返回后补 id 校验：
tool_calls_field = self._materialize_full_tool_calls(acc)
if tool_calls_field:
    for delta in tool_calls_field:
        if not delta.id:
            raise InvalidToolCallIdError(
                source="stream_finished",
                raw_id_value=delta.id,
                provider=self._config.provider_name,
                model=params.get("model"),
                tool_name=delta.name,
                tool_call_index=delta.index,
            )
```

### 设计决策

- **不修改 `_materialize_full_tool_calls` 方法签名**：该 helper 职责为"展开 + 归一化"，校验职责上提到调用方（`stream()`）。关注点分离。
- **保留 5 个既有 `test_openai_compatible_materialize_normalize_unit.py` 测试**：`_materialize_full_tool_calls` 行为不变——空串归一化为 `None`，不抛异常。
- **保留下游 D3 容错回退作为冗余防御层**：理论上 `stream()` 层已抛异常，D3 永远不会触发，但作为防御性编程不构成死代码（未来如果 `stream()` 被绕过或校验被移除，D3 仍生效）。
- **异常 `source="stream_finished"`** 明确标识来源链路，与同步的 `"chat_sync"` 区分。
- **仅校验 `id`**（OpenAI 规范的强制字段），`name` 为空让下游自然失败（`ToolCallRequest.__post_init__` 已有兜底）。
- **异常传播路径已验证**：`InvalidToolCallIdError` → `_RoundStreamAccumulator.consume()` → `_iter_rounds()` → SSE `_event_generator()` 的 `except Exception` → 发送 `{"error": true, "message": "工具调用 id 不合法...", "finished": true}` → 用户得到明确错误提示。

---

## 风险 3：未传递 user 安全审计参数（低优先级）

### 问题分析

OpenAI 官方已将原 `user` 参数升级为 `safety_identifier` 参数（2026 年更新），用于辅助平台检测滥用行为。适配器 `_build_params()` 未显式传递此参数。

### 修复方案

**不在 `ChatRequest` 中新增字段**，通过已有的 `extra_params` 透传机制支持：

1. 在 `_build_params()` 中增加对 `ProviderConfig` 新字段 `safety_identifier` 的读取：

```python
def _build_params(self, request: ChatRequest, *, stream: bool) -> dict[str, Any]:
    # ... 现有逻辑 ...

    if self._config.safety_identifier:
        params["user"] = self._config.safety_identifier

    if request.extra_params:
        params.update(request.extra_params)  # extra_params 可覆盖

    return params
```

2. 在 `ProviderConfig` 中增加可选字段：

```python
@dataclass(frozen=True)
class ProviderConfig:
    # ... 现有字段 ...
    safety_identifier: str | None = None
```

### 设计决策

- **使用 `"user"` 参数名**：OpenAI SDK 当前版本仍通过 `user` 参数传递安全标识符（`safety_identifier` 是 OpenAI 文档层面的命名更新，SDK 参数名尚未变更）。若 SDK 后续更新参数名为 `safety_identifier`，届时修改此处即可。
- **配置级注入**：安全标识符通常由部署环境配置（如环境变量 `MODEL_QWEN_SAFETY_IDENTIFIER`），而非每次请求动态传入。放在 `ProviderConfig` 层级最合理。
- **`extra_params` 优先级更高**：若调用方在 `extra_params` 中显式设置了 `user`，会覆盖 config 默认值，保留灵活性。
- **不做 hash**：是否 hash 由调用方决定（可能已是 UUID/session ID），适配器不做额外转换。

---

## 影响范围

| 修改文件 | 变更类型 | 说明 |
|---------|---------|------|
| `src/infrastructure/model_access/openai_compatible_adapter.py` | 修改 | 风险 1/2/3 的核心修复 |
| `src/infrastructure/model_access/provider_config.py` | 修改 | 新增 `safety_identifier` 可选字段 |
| `test/infrastructure/model_access/` | 新增/修改 | 对应的单元测试 |

## 不影响范围

- `domain/model_access/value_objects.py` — `ChatRequest` 不新增字段
- `domain/model_access/exceptions.py` — 异常类不变，仅新增使用场景
- 其他 adapter（如 Claude adapter）— 不在本次修复范围
