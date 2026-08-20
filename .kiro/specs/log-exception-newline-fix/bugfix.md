# Bugfix Requirements Document

## Introduction

当应用抛出异常时，`logger.exception()` 输出的异常栈信息在终端中没有正确换行，所有栈帧信息挤在一行显示，导致开发者难以阅读和定位问题。

根因在于 `uvicorn.run()` 启动时未指定 `log_config` 参数，uvicorn 使用其内置的默认日志配置（`LOGGING_CONFIG`），该配置会覆盖应用通过 `logging.basicConfig()` 设置的 formatter。uvicorn 默认的 `DefaultFormatter` 在格式化多行消息时，不能正确保留异常栈中的换行符，导致 traceback 被压缩为单行输出。

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN 应用代码通过 `logger.exception()` 记录异常（如全局异常处理器捕获未处理异常）THEN 系统在终端输出的异常栈信息（traceback）中所有栈帧被压缩到一行显示，换行符丢失或未被正确渲染

1.2 WHEN `uvicorn.run()` 启动时未指定 `log_config` 参数 THEN uvicorn 的默认日志配置覆盖了 `logging.basicConfig()` 设置的 formatter，导致应用层 logger 的格式化行为不受控

1.3 WHEN 使用 `exc_info=True` 参数记录日志（如 `logger.error("...", exc_info=True)`）THEN 系统输出的异常栈信息同样没有正确换行

### Expected Behavior (Correct)

2.1 WHEN 应用代码通过 `logger.exception()` 记录异常 THEN 系统 SHALL 在终端输出格式正确的多行异常栈信息，每个栈帧独占一行，便于开发者阅读和定位问题

2.2 WHEN `uvicorn.run()` 启动时 THEN 系统 SHALL 使用自定义的日志配置，确保应用层 `logging.basicConfig()` 设置的日志格式（包含 OTel trace_id/span_id 字段）不被 uvicorn 默认配置覆盖

2.3 WHEN 使用 `exc_info=True` 参数记录日志 THEN 系统 SHALL 输出格式正确的多行异常栈信息，与 `logger.exception()` 行为一致

### Unchanged Behavior (Regression Prevention)

3.1 WHEN 应用代码通过 `logger.info()`、`logger.warning()` 等记录普通单行日志 THEN 系统 SHALL CONTINUE TO 按照现有格式 `"%(asctime)s %(levelname)s [%(name)s] [trace_id=%(otelTraceID)s span_id=%(otelSpanID)s] %(message)s"` 正常输出

3.2 WHEN OTel LoggingInstrumentor 启用时 THEN 系统 SHALL CONTINUE TO 在日志中正确输出实际的 trace_id 和 span_id 值

3.3 WHEN OTel LoggingInstrumentor 未启用时 THEN 系统 SHALL CONTINUE TO 在日志中输出默认值 "0" 作为 trace_id 和 span_id

3.4 WHEN uvicorn 自身输出访问日志和错误日志 THEN 系统 SHALL CONTINUE TO 正常输出 uvicorn 的日志信息，日志级别为 info

3.5 WHEN `service_config.workers > 1` 时 THEN 系统 SHALL CONTINUE TO 在所有工作进程中保持一致的日志格式和异常栈换行行为
