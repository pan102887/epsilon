# 需求：OpenAICompatibleAdapter 是否符合 OpenAI Chat Completions API 规范

## 背景

`OpenAICompatibleAdapter`（位于 `src/infrastructure/model_access/openai_compatible_adapter.py`）是本项目的 OpenAI 兼容协议模型接入适配器，基于 `openai` Python SDK（`AsyncOpenAI`）实现，负责将领域层 `ChatRequest` 转换为 OpenAI Chat Completions API 调用，并将响应转换回领域层值对象。

本需求旨在核对该适配器是否正确遵循 OpenAI Chat Completions API 的接入规范，识别已符合和尚未覆盖的规范点。

## 审查范围

- 请求参数构建（`_build_params`）
- 消息格式转换（`_to_openai_messages`）
- 同步响应解析（`chat`）
- 流式响应处理（`stream`）
- 错误映射与重试机制
- 工具调用（tool_calls）处理

## 规范对照结论

### ✅ 已符合规范的部分

| # | 规范要求 | 适配器实现 |
|---|---------|-----------|
| 1 | `model` 为必填字段 | `_build_params` 从 `request.model` 或 `config.default_model` 取值，始终传递 |
| 2 | `messages` 为必填字段，每条消息包含 `role` 和 `content` | `_to_openai_messages` 正确转换领域消息为 `{role, content}` 结构 |
| 3 | `AssistantMessage` 携带 `tool_calls` 时，输出 `{id, type:"function", function:{name, arguments}}` | 实现与规范严格一致 |
| 4 | `ToolMessage` 包含 `role`、`content`、`tool_call_id` | 已正确实现 |
| 5 | `temperature` 参数范围 0.0-2.0 | `ChatRequest.__post_init__` 有范围校验 |
| 6 | `max_tokens` 作为正整数传递 | 已实现，`ChatRequest` 有 `> 0` 校验 |
| 7 | `stream` 布尔参数 | `_build_params` 正确传入 `True`/`False` |
| 8 | 流式模式传递 `stream_options: {include_usage: True}` | 已实现，确保末尾 chunk 返回 usage |
| 9 | `tools` 参数——非空时传递，`None`/空列表不传递 | 已实现，避免部分模型对空数组报错 |
| 10 | 流式 tool_calls 累积——使用 `delta.tool_calls[i].index` 跨分片合并 | `acc` 字典以 index 为键，正确累积 id/name/arguments |
| 11 | 流式首分片携带 `id`+`name`，后续分片携带 `arguments` 增量 | `stream` 方法的累积逻辑正确处理 |
| 12 | `finish_reason` 非 `None` 时标记流结束 | `finished = finish_reason is not None` 正确实现 |
| 13 | 末尾 chunk 可能仅含 usage（`choices` 为空） | 已处理空 `choices` 的情况 |
| 14 | 同步响应解析 `usage` 对象 | 正确提取 `prompt_tokens`/`completion_tokens`/`total_tokens` |
| 15 | SDK 异常→领域异常映射 | `APITimeoutError`→`ModelTimeoutError`、`RateLimitError`→`ModelRateLimitError`、`APIConnectionError`→`ModelConnectionError`、`APIError`→`ModelAccessError` |
| 16 | `RateLimitError` 解析 `retry-after` header | 已实现 |
| 17 | `extra_params` 透传机制 | 通过 `params.update(request.extra_params)` 支持任意扩展参数 |

### ⚠️ 存在差异但设计合理的部分

| # | 规范参数 | 适配器行为 | 评估 |
|---|---------|-----------|------|
| 1 | `max_completion_tokens`（新版推荐替代 `max_tokens`） | 仍使用 `max_tokens` 参数名 | **可接受**。OpenAI SDK 仍支持 `max_tokens`（向后兼容），且大量兼容 Provider（百炼/GLM/DeepSeek）尚未统一迁移至新参数名。可通过 `extra_params` 覆盖。 |
| 2 | `tool_choice` 可控制工具调用策略 | 未显式传递 `tool_choice` | **可接受**。默认值为 `"auto"`，与 SDK 默认行为一致。如需指定可通过 `extra_params` 传递。 |
| 3 | `parallel_tool_calls` 控制并行工具调用 | 未显式传递 | **可接受**。可通过 `extra_params` 传递。 |
| 4 | `response_format` 结构化输出 | 未显式支持 | **可接受**。可通过 `extra_params` 传递 `{"response_format": {"type": "json_object"}}` 等。 |
| 5 | `top_p`/`frequency_penalty`/`presence_penalty`/`stop` 采样控制 | 未在 `_build_params` 中显式处理 | **可接受**。可通过 `extra_params` 透传。 |

### ❌ 潜在合规风险

| # | 风险点 | 详情 | 严重度 | 建议 |
|---|-------|------|--------|------|
| 1 | 流式迭代阶段异常未映射 | `stream` 方法在 `async for chunk in response` 迭代过程中如发生网络中断（如 `httpx.ReadTimeout`），异常不经过 `_stream_open_once` 的映射逻辑，会以原始 SDK/httpx 异常抛出 | 中 | 在 `stream` 方法的 `async for` 循环外添加 try-except，将迭代期间的异常统一映射为领域异常 |
| 2 | tool_call.id 空值在流式中未校验 | 同步 `chat` 方法对 `tool_call.id` 做了空值前置校验并抛出 `InvalidToolCallIdError`；但 `stream` 方法在 `finished=True` 输出完整 tool_calls 时，仅通过 `or None` 折叠空值，未抛出等价异常 | 低 | 在 `_materialize_full_tool_calls` 或 finished 分支中增加对 id 为空的显式校验 |
| 3 | 未设置 `user` 参数 | OpenAI 规范中 `user` 参数用于终端用户标识以辅助安全审计。适配器未传递此参数 | 低 | 可选改进：在 `ChatRequest.extra_params` 或配置中提供 user 标识传递能力 |

## 总结

**适配器整体符合 OpenAI Chat Completions API 规范**。核心请求构建、消息格式、工具调用、流式处理、错误映射等关键路径均正确实现。通过 `extra_params` 透传机制，已覆盖未显式建模的可选参数（`tool_choice`、`response_format`、`top_p` 等）。

主要改进建议：
1. **【中优先级】** 在流式迭代阶段增加异常映射，确保领域异常边界完整
2. **【低优先级】** 流式 tool_call id 空值校验与同步模式对齐
3. **【低优先级】** 考虑 `max_completion_tokens` 参数名适配（未来兼容）
