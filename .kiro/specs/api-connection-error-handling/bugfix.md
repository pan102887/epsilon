# Bugfix 需求文档

## 简介

当模型服务不可达（如目标主机拒绝连接、DNS 解析失败、网络不通等）时，OpenAI Python SDK 抛出 `APIConnectionError`。该异常未被 `openai_compatible_adapter.py` 的 `chat()` 和 `stream()` 方法捕获，冒泡到上层后，日志记录代码尝试访问 `exc.status_code` 属性导致 `AttributeError`，应用产生二次异常而非返回有意义的错误信息。

根因在于 `APIConnectionError` 不继承自 `APIError`（它继承自 `openai.APIConnectionError`，与 `APIStatusError` 是兄弟关系），因此现有的 `except APIError` 分支无法捕获它。同时，该异常没有 `status_code` 属性，因为连接根本未建立，不存在 HTTP 响应。

## Bug 分析

### 当前行为（缺陷）

1.1 WHEN 模型服务不可达（连接被拒绝、DNS 解析失败等）且调用 `chat()` 方法时 THEN 系统抛出未捕获的 `openai.APIConnectionError`，该异常冒泡到上层代码，最终在日志记录时因访问 `exc.status_code` 属性触发 `AttributeError: 'APIConnectionError' object has no attribute 'status_code'`

1.2 WHEN 模型服务不可达且调用 `stream()` 方法时 THEN 系统同样抛出未捕获的 `openai.APIConnectionError`，导致与 1.1 相同的 `AttributeError` 二次异常

1.3 WHEN `APIConnectionError` 冒泡到 `chat.py` 路由的 `_event_generator` 时 THEN 系统在异常处理中假设异常具有 `status_code` 属性，产生 `AttributeError` 而非向客户端返回有意义的连接错误信息

### 期望行为（正确）

2.1 WHEN 模型服务不可达且调用 `chat()` 方法时 THEN 系统 SHALL 捕获 `APIConnectionError` 并将其转换为领域层异常（如 `ModelConnectionError` 或 `ModelAccessError`），包含连接失败的描述信息，不包含 `status_code`（因为不存在 HTTP 响应）

2.2 WHEN 模型服务不可达且调用 `stream()` 方法时 THEN 系统 SHALL 捕获 `APIConnectionError` 并将其转换为与 2.1 相同类型的领域层异常，行为一致

2.3 WHEN 连接错误的领域层异常传播到上层路由时 THEN 系统 SHALL 正常记录错误日志并向客户端返回包含错误信息的响应（同步模式返回 JSON 错误响应，流式模式通过 SSE 事件发送错误信息），不产生二次异常

### 不变行为（回归防护）

3.1 WHEN 模型服务可达且正常响应时 THEN 系统 SHALL 继续正常返回 `LLMResponse` 或 `StreamingChunk`，行为不变

3.2 WHEN 模型调用超时（`APITimeoutError`）时 THEN 系统 SHALL 继续抛出 `ModelTimeoutError`，行为不变

3.3 WHEN 触发速率限制（`RateLimitError`，HTTP 429）时 THEN 系统 SHALL 继续抛出 `ModelRateLimitError`，行为不变

3.4 WHEN 模型返回其他 API 错误（`APIError`，如 HTTP 400/500 等）时 THEN 系统 SHALL 继续抛出 `ModelAccessError` 并包含 `status_code`，行为不变

3.5 WHEN 流式对话过程中发生领域层异常时 THEN `chat.py` 路由的 `_event_generator` SHALL 继续通过 SSE 事件向客户端发送错误信息并正常结束流，行为不变
