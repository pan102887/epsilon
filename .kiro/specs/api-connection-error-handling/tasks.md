# 实现计划

- [x] 1. 编写 Bug Condition 探索性测试
  - **Property 1: Bug Condition** - APIConnectionError 未被捕获导致冒泡
  - **CRITICAL**: 此测试必须在实施修复前编写和运行——测试失败即确认 bug 存在
  - **DO NOT attempt to fix the test or the code when it fails**
  - **NOTE**: 此测试编码了期望行为——修复后测试通过即验证修复正确性
  - **GOAL**: 产出反例，证明 `APIConnectionError` 未被适配器捕获
  - **Scoped PBT Approach**: 将属性范围限定为具体失败场景——Mock `AsyncOpenAI` 客户端使其抛出 `APIConnectionError`，分别调用 `chat()` 和 `stream()`
  - 测试文件：`epsilon-boot/test/infrastructure/model_access/test_openai_connection_error_bug.py`
  - 使用 Hypothesis 生成随机连接错误消息（如 "Connection refused"、"DNS resolution failed" 等）
  - Mock `self._client.chat.completions.create` 抛出 `APIConnectionError(message=<random_msg>, request=<mock_request>)`
  - 断言：调用 `chat(request)` 应抛出 `ModelConnectionError`（而非 `APIConnectionError` 或 `AttributeError`）
  - 断言：调用 `stream(request)` 应抛出 `ModelConnectionError`
  - 断言：`ModelConnectionError.code == 50006`
  - 断言：`ModelConnectionError.message` 包含连接失败描述信息
  - Bug Condition 伪代码：`isBugCondition(input) = SDK 抛出 APIConnectionError AND 该异常不是 APITimeoutError/RateLimitError/APIError 的实例`
  - 在未修复代码上运行测试
  - **EXPECTED OUTCOME**: 测试失败（这是正确的——证明 bug 存在，`APIConnectionError` 未被捕获）
  - 记录反例（如 `chat(request)` 抛出 `APIConnectionError` 而非 `ModelConnectionError`）
  - 任务完成条件：测试已编写、已运行、失败已记录
  - _Requirements: 1.1, 1.2, 2.1, 2.2_

- [x] 2. 编写 Preservation 属性测试（在实施修复前）
  - **Property 2: Preservation** - 非 APIConnectionError 异常路径行为不变
  - **IMPORTANT**: 遵循观察优先方法论
  - 测试文件：`epsilon-boot/test/infrastructure/model_access/test_openai_preservation_properties.py`
  - 观察：在未修复代码上，正常调用 `chat()` 返回 `LLMResponse`，`stream()` 产出 `StreamingChunk`
  - 观察：在未修复代码上，`APITimeoutError` 被捕获并转换为 `ModelTimeoutError`
  - 观察：在未修复代码上，`RateLimitError` 被捕获并转换为 `ModelRateLimitError`
  - 观察：在未修复代码上，`APIError`（HTTP 4xx/5xx）被捕获并转换为 `ModelAccessError` 且包含 `status_code`
  - 使用 Hypothesis 编写 property-based 测试：
    - 属性 A：对于任意正常响应内容，`chat()` 返回的 `LLMResponse.content` 与 Mock 响应一致
    - 属性 B：对于任意超时秒数，`APITimeoutError` 被转换为 `ModelTimeoutError`
    - 属性 C：对于任意 retry-after 值，`RateLimitError` 被转换为 `ModelRateLimitError`
    - 属性 D：对于任意 HTTP 状态码和错误消息，`APIError` 被转换为 `ModelAccessError` 且 details 包含 `status_code`
  - 在未修复代码上运行测试
  - **EXPECTED OUTCOME**: 所有测试通过（确认基线行为）
  - 任务完成条件：测试已编写、已运行、在未修复代码上全部通过
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [ ] 3. 实施 APIConnectionError 异常处理修复

  - [x] 3.1 新增 `ModelConnectionError` 异常类
    - 文件：`epsilon-boot/src/domain/model_access/exceptions.py`
    - 继承自 `ModelAccessError`，错误码 `50006`
    - 接受 `reason: str` 参数描述连接失败原因
    - 接受可选 `request_info: dict` 参数携带请求上下文
    - _Bug_Condition: isBugCondition(input) = SDK 抛出 APIConnectionError_
    - _Expected_Behavior: 抛出 ModelConnectionError(code=50006, message="模型服务连接失败: {reason}")_
    - _Requirements: 2.1, 2.2_

  - [x] 3.2 在 `chat()` 方法中新增 `except APIConnectionError` 分支
    - 文件：`epsilon-boot/src/infrastructure/model_access/openai_compatible_adapter.py`
    - 在 `from openai import ...` 中添加 `APIConnectionError`
    - 在 `from domain.model_access.exceptions import ...` 中添加 `ModelConnectionError`
    - 在 `except APITimeoutError` 之后、`except APIError` 之前新增 `except APIConnectionError as exc` 分支
    - 捕获后抛出 `ModelConnectionError(reason=str(exc), request_info={"model": params.get("model")})`
    - _Bug_Condition: isBugCondition(input) = SDK 抛出 APIConnectionError AND 调用方法为 chat()_
    - _Expected_Behavior: expectedBehavior(result) = 抛出 ModelConnectionError 而非冒泡 APIConnectionError_
    - _Preservation: APITimeoutError → ModelTimeoutError, RateLimitError → ModelRateLimitError, APIError → ModelAccessError 映射不变_
    - _Requirements: 2.1, 2.3, 3.2, 3.3, 3.4_

  - [x] 3.3 在 `stream()` 方法中新增相同的 `except APIConnectionError` 分支
    - 文件：`epsilon-boot/src/infrastructure/model_access/openai_compatible_adapter.py`
    - 在 `stream()` 方法的 `try/except` 块中，`except APITimeoutError` 之后、`except APIError` 之前新增 `except APIConnectionError as exc` 分支
    - 与 3.2 保持一致的异常转换逻辑
    - _Bug_Condition: isBugCondition(input) = SDK 抛出 APIConnectionError AND 调用方法为 stream()_
    - _Expected_Behavior: expectedBehavior(result) = 抛出 ModelConnectionError 而非冒泡 APIConnectionError_
    - _Preservation: stream() 中 APITimeoutError/RateLimitError/APIError 映射不变_
    - _Requirements: 2.2, 2.3, 3.2, 3.3, 3.4_

  - [x] 3.4 验证 Bug Condition 探索性测试现在通过
    - **Property 1: Expected Behavior** - APIConnectionError 被捕获并转换为 ModelConnectionError
    - **IMPORTANT**: 重新运行任务 1 中的同一测试——不要编写新测试
    - 任务 1 的测试编码了期望行为：`chat()` 和 `stream()` 捕获 `APIConnectionError` 并抛出 `ModelConnectionError`
    - 当此测试通过时，确认期望行为已满足
    - 运行 `uv run pytest epsilon-boot/test/infrastructure/model_access/test_openai_connection_error_bug.py -v`
    - **EXPECTED OUTCOME**: 测试通过（确认 bug 已修复）
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.5 验证 Preservation 测试仍然通过
    - **Property 2: Preservation** - 非 APIConnectionError 异常路径行为不变
    - **IMPORTANT**: 重新运行任务 2 中的同一测试——不要编写新测试
    - 运行 `uv run pytest epsilon-boot/test/infrastructure/model_access/test_openai_preservation_properties.py -v`
    - **EXPECTED OUTCOME**: 测试通过（确认无回归）
    - 确认修复后所有 preservation 测试仍然通过

- [x] 4. Checkpoint - 确保所有测试通过
  - 运行完整测试套件：`uv run pytest epsilon-boot/test/ -v`
  - 确保所有测试通过，如有问题请咨询用户
