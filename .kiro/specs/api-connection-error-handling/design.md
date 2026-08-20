# APIConnectionError 异常处理 Bugfix 设计

## 概述

当模型服务不可达时（连接被拒绝、DNS 解析失败、网络不通等），OpenAI Python SDK 抛出 `APIConnectionError`。该异常未被 `OpenAICompatibleAdapter` 的 `chat()` 和 `stream()` 方法捕获，冒泡到上层后触发 `AttributeError` 二次异常。

修复策略：在适配器层新增 `APIConnectionError` 的 `except` 分支，将其转换为新的领域层异常 `ModelConnectionError`，与现有异常处理模式保持一致。

## 术语表

- **Bug_Condition (C)**：触发 bug 的条件——模型服务不可达时 SDK 抛出 `APIConnectionError`，该异常未被适配器层捕获
- **Property (P)**：期望行为——`APIConnectionError` 应被捕获并转换为领域层 `ModelConnectionError`，包含连接失败描述信息
- **Preservation**：现有的 `APITimeoutError`、`RateLimitError`、`APIError` 异常处理行为，以及正常响应路径，均不受修复影响
- **OpenAICompatibleAdapter**：`infrastructure/model_access/openai_compatible_adapter.py` 中的模型接入适配器，负责调用 OpenAI 兼容 API 并将 SDK 异常映射为领域层异常
- **APIConnectionError**：OpenAI SDK 中的连接异常，继承自 `openai.APIConnectionError`（非 `APIError` 子类），不具有 `status_code` 属性
- **ModelConnectionError**：待新增的领域层异常，继承自 `ModelAccessError`，表示模型服务连接失败

## Bug 详情

### Bug 条件

当模型服务不可达（连接被拒绝、DNS 解析失败、网络超时等）时，OpenAI SDK 抛出 `APIConnectionError`。该异常不继承自 `APIError`（它与 `APIStatusError` 是兄弟关系，共同继承自 `openai.APIError` 的更上层基类），因此现有的 `except APIError` 分支无法捕获它。未被捕获的 `APIConnectionError` 冒泡到上层代码，在日志记录或异常处理中访问 `exc.status_code` 属性时触发 `AttributeError`，因为连接根本未建立，不存在 HTTP 响应。

**形式化规约：**
```
FUNCTION isBugCondition(input)
  INPUT: input 为对 OpenAICompatibleAdapter.chat() 或 stream() 的调用
  OUTPUT: boolean

  RETURN SDK 抛出异常类型为 APIConnectionError
         AND 该异常不是 APITimeoutError 的实例
         AND 该异常不是 RateLimitError 的实例
         AND 该异常不是 APIError（APIStatusError）的实例
END FUNCTION
```

### 示例

- 用户调用 `chat()` 时模型服务 host 不可达 → 期望：抛出 `ModelConnectionError(code=50006, message="模型服务连接失败: ...")`；实际：抛出未捕获的 `APIConnectionError`，随后触发 `AttributeError`
- 用户调用 `stream()` 时 DNS 解析失败 → 期望：抛出 `ModelConnectionError`；实际：同上
- 用户调用 `chat()` 时模型服务正常响应 → 期望且实际：正常返回 `LLMResponse`（不受影响）
- 用户调用 `chat()` 时模型服务返回 HTTP 500 → 期望且实际：抛出 `ModelAccessError`（不受影响）

## 期望行为

### 保持不变的行为

**不变行为：**
- 模型服务可达且正常响应时，`chat()` 返回 `LLMResponse`，`stream()` 产出 `StreamingChunk`，行为不变
- `APITimeoutError` 继续被捕获并转换为 `ModelTimeoutError`，行为不变
- `RateLimitError`（HTTP 429）继续被捕获并转换为 `ModelRateLimitError`，行为不变
- `APIError`（HTTP 4xx/5xx）继续被捕获并转换为 `ModelAccessError` 并包含 `status_code`，行为不变
- 流式对话中领域层异常继续通过 SSE 事件向客户端发送错误信息，行为不变

**范围：**
所有不涉及 `APIConnectionError` 的调用路径完全不受此修复影响，包括：
- 正常的模型调用和响应
- `APITimeoutError` 异常路径
- `RateLimitError` 异常路径
- `APIError` 异常路径
- 路由层的异常处理逻辑

## 假设的根因

基于 bug 描述，最可能的原因是：

1. **`except` 分支缺失**：`chat()` 和 `stream()` 方法的 `try/except` 块中没有 `except APIConnectionError` 分支。现有分支仅捕获 `APITimeoutError`、`RateLimitError` 和 `APIError`，而 `APIConnectionError` 不是 `APIError` 的子类，因此无法被捕获。

2. **SDK 异常继承层次误解**：开发时可能假设 `APIConnectionError` 继承自 `APIError`，实际上 OpenAI SDK 的异常层次为：
   - `openai.OpenAIError`（基类）
     - `APIError`（即 `APIStatusError`，有 `status_code` 属性）
       - `RateLimitError`
       - ...
     - `APIConnectionError`（无 `status_code` 属性）
     - `APITimeoutError`

3. **缺少对应的领域层异常**：`domain/model_access/exceptions.py` 中没有定义 `ModelConnectionError`，导致即使想捕获也没有合适的领域层异常可以转换。

## 正确性属性

Property 1: Bug Condition - APIConnectionError 被捕获并转换为领域层异常

_For any_ 对 `chat()` 或 `stream()` 的调用，当 OpenAI SDK 抛出 `APIConnectionError` 时，修复后的适配器 SHALL 捕获该异常并抛出 `ModelConnectionError`（继承自 `ModelAccessError`），包含连接失败的描述信息，且不访问 `status_code` 属性。

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation - 非 APIConnectionError 异常路径行为不变

_For any_ 对 `chat()` 或 `stream()` 的调用，当 SDK 抛出的异常不是 `APIConnectionError`（即 `APITimeoutError`、`RateLimitError`、`APIError`）或调用正常返回时，修复后的适配器 SHALL 产生与修复前完全相同的行为，保持现有异常映射和正常响应逻辑不变。

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

## 修复实现

### 所需变更

假设根因分析正确：

**文件**：`epsilon-boot/src/domain/model_access/exceptions.py`

**变更 1：新增 `ModelConnectionError` 异常类**
- 继承自 `ModelAccessError`
- 错误码 `50006`
- 接受 `reason` 参数描述连接失败原因
- 接受可选的 `request_info` 参数携带请求上下文

**文件**：`epsilon-boot/src/infrastructure/model_access/openai_compatible_adapter.py`

**变更 2：导入 `APIConnectionError` 和 `ModelConnectionError`**
- 在 `from openai import ...` 中添加 `APIConnectionError`
- 在 `from domain.model_access.exceptions import ...` 中添加 `ModelConnectionError`

**变更 3：在 `chat()` 方法的 `try/except` 块中新增 `except APIConnectionError` 分支**
- 位置：在 `except APITimeoutError` 之后、`except APIError` 之前
- 捕获 `APIConnectionError` 并抛出 `ModelConnectionError`
- 传入 `reason=str(exc)` 和请求上下文信息

**变更 4：在 `stream()` 方法的 `try/except` 块中新增相同的 `except APIConnectionError` 分支**
- 与变更 3 保持一致的异常转换逻辑

## 测试策略

### 验证方法

测试策略分两阶段：首先在未修复代码上运行探索性测试以确认 bug 存在并验证根因假设，然后在修复后验证正确性和行为保持。

### 探索性 Bug 条件检查

**目标**：在实施修复前，通过测试用例复现 bug，确认或否定根因分析。如果否定，需要重新假设根因。

**测试计划**：Mock `AsyncOpenAI` 客户端使其抛出 `APIConnectionError`，调用 `chat()` 和 `stream()` 方法，观察是否产生未捕获异常。在未修复代码上运行以观察失败。

**测试用例**：
1. **chat() 连接错误测试**：Mock SDK 抛出 `APIConnectionError`，调用 `chat()`，观察异常行为（未修复代码上将失败）
2. **stream() 连接错误测试**：Mock SDK 抛出 `APIConnectionError`，调用 `stream()`，观察异常行为（未修复代码上将失败）
3. **不同连接错误原因测试**：Mock 不同的错误消息（连接被拒绝、DNS 失败等），验证均未被捕获（未修复代码上将失败）

**预期反例**：
- `APIConnectionError` 未被任何 `except` 分支捕获，冒泡为未处理异常
- 可能原因：`except APIError` 无法匹配 `APIConnectionError`，因为后者不是前者的子类

### Fix 检查

**目标**：验证对所有满足 bug 条件的输入，修复后的函数产生期望行为。

**伪代码：**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := chat_fixed(input) 或 stream_fixed(input)
  ASSERT result 抛出 ModelConnectionError
  ASSERT ModelConnectionError.code == 50006
  ASSERT ModelConnectionError.message 包含连接失败描述
  ASSERT 不产生 AttributeError
END FOR
```

### Preservation 检查

**目标**：验证对所有不满足 bug 条件的输入，修复后的函数与原函数行为一致。

**伪代码：**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT chat_original(input) = chat_fixed(input)
  ASSERT stream_original(input) = stream_fixed(input)
END FOR
```

**测试方法**：推荐使用 property-based testing 进行 preservation 检查，因为：
- 可自动生成大量测试用例覆盖输入域
- 能捕获手动单元测试可能遗漏的边界情况
- 对非 bug 输入的行为不变性提供强保证

**测试计划**：先在未修复代码上观察正常调用和其他异常路径的行为，然后编写 property-based 测试捕获该行为。

**测试用例**：
1. **正常响应保持**：验证正常调用路径在修复后仍返回相同的 `LLMResponse`
2. **APITimeoutError 保持**：验证超时异常仍被转换为 `ModelTimeoutError`
3. **RateLimitError 保持**：验证速率限制异常仍被转换为 `ModelRateLimitError`
4. **APIError 保持**：验证其他 API 错误仍被转换为 `ModelAccessError` 并包含 `status_code`

### 单元测试

- 测试 `chat()` 方法捕获 `APIConnectionError` 并抛出 `ModelConnectionError`
- 测试 `stream()` 方法捕获 `APIConnectionError` 并抛出 `ModelConnectionError`
- 测试 `ModelConnectionError` 的属性（code、message、details）
- 测试不同连接错误原因的消息格式

### Property-Based 测试

- 生成随机的连接错误消息，验证 `chat()` 和 `stream()` 均正确转换为 `ModelConnectionError`
- 生成随机的正常响应，验证修复后的正常路径行为不变
- 生成随机的异常类型（`APITimeoutError`、`RateLimitError`、`APIError`），验证现有异常映射不变

### 集成测试

- 测试 `ModelConnectionError` 作为 `BizException` 子类被 `exception_handlers.py` 正确处理，返回 `{"code": 50006, "message": "..."}`
- 测试流式对话中 `ModelConnectionError` 通过 SSE 事件正确传递错误信息
- 测试从适配器到路由的完整异常传播链路
