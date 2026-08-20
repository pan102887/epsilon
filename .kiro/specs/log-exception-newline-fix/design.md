# 日志异常栈换行修复 Bugfix Design

## Overview

当应用通过 `logger.exception()` 或 `exc_info=True` 记录异常时，终端输出的 traceback 被压缩为单行，开发者无法正常阅读栈帧信息。根因是 `uvicorn.run()` 未指定 `log_config` 参数，uvicorn 启动时使用内置的 `LOGGING_CONFIG` 覆盖了应用通过 `logging.basicConfig()` 设置的 formatter。

修复策略：在 `uvicorn.run()` 调用中传入自定义 `log_config` 字典，复用应用已有的日志格式（含 OTel trace_id/span_id 字段），同时保留 uvicorn 自身的 access/error logger 正常工作。

## Glossary

- **Bug_Condition (C)**: 触发 bug 的条件——当应用代码使用 `logger.exception()` 或 `exc_info=True` 记录带异常栈的日志时，traceback 换行符丢失
- **Property (P)**: 期望行为——异常栈信息应以多行格式输出，每个栈帧独占一行
- **Preservation**: 修复不应影响的现有行为——普通单行日志格式、OTel trace_id/span_id 注入、uvicorn 自身日志输出
- **`main.py`**: 应用入口文件，包含 `logging.basicConfig()` 配置和 `uvicorn.run()` 调用
- **`LOGGING_CONFIG`**: uvicorn 内置的默认日志配置字典（`uvicorn.config.LOGGING_CONFIG`），当 `log_config` 参数未指定时被使用
- **`DefaultFormatter`**: uvicorn 默认 formatter，在格式化多行消息时不能正确保留异常栈换行符

## Bug Details

### Bug Condition

当应用代码通过 `logger.exception()` 或 `logger.error("...", exc_info=True)` 记录异常时，由于 uvicorn 启动过程中用内置 `LOGGING_CONFIG` 覆盖了 root logger 的 handler formatter，导致异常栈的换行符在格式化输出时丢失，所有栈帧被压缩到一行。

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type LogRecord
  OUTPUT: boolean

  RETURN input.exc_info IS NOT None
         AND input.exc_info[1] IS NOT None
         AND rootLoggerFormatterIsUvicornDefault()
END FUNCTION
```

### Examples

- `logger.exception("Unhandled exception: %s", exc)` 在全局异常处理器中调用 → 期望：多行 traceback 输出；实际：单行压缩输出
- `logger.error("处理失败", exc_info=True)` 在业务代码中调用 → 期望：多行 traceback 输出；实际：单行压缩输出
- `logger.warning("连接超时", exc_info=True)` 记录带栈的警告 → 期望：多行 traceback 输出；实际：单行压缩输出
- `logger.info("普通日志消息")` 不带 exc_info → 不受影响，正常单行输出

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- 普通单行日志（`logger.info()`、`logger.warning()` 等不带 `exc_info` 的调用）必须继续按照格式 `"%(asctime)s %(levelname)s [%(name)s] [trace_id=%(otelTraceID)s span_id=%(otelSpanID)s] %(message)s"` 正常输出
- OTel LoggingInstrumentor 启用时，日志中必须继续正确输出实际的 trace_id 和 span_id 值
- OTel LoggingInstrumentor 未启用时，日志中必须继续输出默认值 "0" 作为 trace_id 和 span_id
- uvicorn 自身的 access log 和 error log 必须继续正常输出
- 多 worker 进程模式下所有进程的日志格式必须保持一致

**Scope:**
所有不涉及 `exc_info` 的日志调用不应受此修复影响。这包括：
- 所有 `logger.info()`、`logger.debug()`、`logger.warning()` 普通调用
- uvicorn 内部的 access log 和 error log
- OTel trace context 注入机制

## Hypothesized Root Cause

基于 bug 描述和代码分析，根因链路如下：

1. **uvicorn 覆盖 root logger 配置**: `main.py` 中先调用 `logging.basicConfig()` 设置了正确的 formatter，但随后 `uvicorn.run()` 在启动时调用 `logging.config.dictConfig(LOGGING_CONFIG)`，该操作会重新配置 root logger 的 handler 和 formatter，覆盖应用的设置

2. **uvicorn DefaultFormatter 的多行处理问题**: uvicorn 的 `DefaultFormatter` 在 `formatException()` 或消息拼接时，不能正确保留 traceback 中的换行符，导致多行异常栈被压缩为单行

3. **`log_config` 参数缺失**: `uvicorn.run()` 的 `log_config` 参数默认值为 `LOGGING_CONFIG`（uvicorn 内置配置）。当不显式传入时，uvicorn 总是使用自己的默认配置，无法保留应用层的日志设置

## Correctness Properties

Property 1: Bug Condition - 异常栈多行输出

_For any_ 日志记录调用，当 `exc_info` 不为 None 且包含有效异常信息时，修复后的日志系统 SHALL 输出包含换行符的多行 traceback，每个栈帧独占一行，且输出中包含 "Traceback (most recent call last):" 标识。

**Validates: Requirements 2.1, 2.3**

Property 2: Preservation - 普通日志格式不变

_For any_ 不带 `exc_info` 的日志记录调用（`logger.info()`、`logger.warning()` 等），修复后的日志系统 SHALL 产生与修复前完全相同的输出格式，保持 `"%(asctime)s %(levelname)s [%(name)s] [trace_id=%(otelTraceID)s span_id=%(otelSpanID)s] %(message)s"` 格式不变。

**Validates: Requirements 3.1, 3.2, 3.3**

## Fix Implementation

### Changes Required

假设根因分析正确：

**File**: `epsilon-boot/main.py`

**Function**: `main()`

**Specific Changes**:

1. **构建自定义 `log_config` 字典**: 在 `main()` 函数中，构建一个与应用 `logging.basicConfig()` 格式一致的日志配置字典，包含 OTel trace_id/span_id 字段的 format 字符串

2. **传入 `log_config` 参数**: 在 `uvicorn.run()` 调用中显式传入 `log_config=CUSTOM_LOG_CONFIG`，阻止 uvicorn 使用内置默认配置覆盖应用的 formatter

3. **保留 uvicorn logger 配置**: 自定义 `log_config` 中需要包含 `uvicorn` 和 `uvicorn.access` logger 的配置，确保 uvicorn 自身日志正常工作

4. **保留 OTel 字段**: formatter 的 format 字符串必须包含 `%(otelTraceID)s` 和 `%(otelSpanID)s`，与 `logging.basicConfig()` 中的格式保持一致

5. **确保多 worker 兼容**: `log_config` 字典必须是可序列化的（纯 dict），因为多 worker 模式下 uvicorn 会 fork 进程，每个子进程需要独立应用日志配置

## Testing Strategy

### Validation Approach

测试策略分两阶段：首先在未修复代码上运行探索性测试，确认 bug 存在并验证根因假设；然后在修复后验证 bug 已解决且现有行为未被破坏。

### Exploratory Bug Condition Checking

**Goal**: 在实施修复前，通过测试用例复现 bug，确认或否定根因分析。如果否定，需要重新假设根因。

**Test Plan**: 模拟 uvicorn 启动后的日志环境（应用 uvicorn 默认 LOGGING_CONFIG），然后通过 `logger.exception()` 记录异常，检查输出中 traceback 是否包含换行符。在未修复代码上运行以观察失败。

**Test Cases**:
1. **全局异常处理器测试**: 模拟 `global_exception_handler` 中 `logger.exception()` 的调用，检查 traceback 输出格式（will fail on unfixed code）
2. **exc_info=True 测试**: 使用 `logger.error("...", exc_info=True)` 记录异常，检查输出格式（will fail on unfixed code）
3. **嵌套异常测试**: 记录带有 `__cause__` 链的异常，检查完整链的换行（will fail on unfixed code）
4. **深栈异常测试**: 记录调用栈较深的异常，检查所有栈帧是否正确换行（will fail on unfixed code）

**Expected Counterexamples**:
- traceback 输出中不包含换行符 `\n`，所有栈帧被压缩到一行
- 可能原因：uvicorn `LOGGING_CONFIG` 覆盖了 root logger 的 formatter

### Fix Checking

**Goal**: 验证对所有满足 bug 条件的输入，修复后的函数产生期望行为。

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := formatLogRecord_fixed(input)
  ASSERT result CONTAINS "\n"
  ASSERT result CONTAINS "Traceback (most recent call last):"
  ASSERT eachStackFrameOnSeparateLine(result)
END FOR
```

### Preservation Checking

**Goal**: 验证对所有不满足 bug 条件的输入，修复后的函数与原函数产生相同结果。

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT formatLogRecord_original(input) = formatLogRecord_fixed(input)
END FOR
```

**Testing Approach**: 推荐使用 property-based testing 进行 preservation checking，因为：
- 可以自动生成大量不同的日志消息和级别组合
- 能捕获手动测试可能遗漏的边界情况
- 对"所有非 bug 输入行为不变"提供强保证

**Test Plan**: 先在未修复代码上观察普通日志（不带 exc_info）的输出行为，然后编写 property-based test 捕获该行为，确保修复后保持不变。

**Test Cases**:
1. **普通日志格式保持**: 验证 `logger.info()`、`logger.warning()` 等调用的输出格式在修复前后一致
2. **OTel 字段保持**: 验证日志中 trace_id 和 span_id 字段在修复前后行为一致（启用/未启用 OTel 两种场景）
3. **uvicorn 日志保持**: 验证 uvicorn access log 和 error log 在修复后仍正常输出
4. **日志级别保持**: 验证各日志级别（DEBUG、INFO、WARNING、ERROR）的输出行为在修复前后一致

### Unit Tests

- 测试自定义 `log_config` 字典的结构正确性（包含必要的 formatters、handlers、loggers）
- 测试 formatter 的 format 字符串包含 OTel 字段
- 测试 `uvicorn.run()` 调用时 `log_config` 参数被正确传入

### Property-Based Tests

- 生成随机异常类型和消息，验证修复后 traceback 始终包含换行符和正确的栈帧格式
- 生成随机普通日志消息（不带 exc_info），验证输出格式与修复前一致
- 生成随机日志级别和消息组合，验证 OTel 字段始终存在

### Integration Tests

- 测试完整的 uvicorn 启动流程，验证日志配置未被覆盖
- 测试全局异常处理器 → logger.exception() → 终端输出的完整链路
- 测试多 worker 模式下日志格式的一致性
