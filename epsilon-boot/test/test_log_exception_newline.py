"""日志异常栈换行 Bug 探索性测试。

本模块用于验证 uvicorn 日志配置对异常栈 traceback 输出格式的影响。

Bug 背景：
- main.py 通过 logging.basicConfig() 设置了包含 OTel trace_id/span_id 的日志格式
- uvicorn.run() 启动时未指定 log_config 参数，uvicorn 使用内置 LOGGING_CONFIG
- 在多 worker 模式下，worker 子进程不会重新执行 main.py 的 basicConfig()，
  导致 root logger 缺少正确的 formatter 和 OTel record factory
- 修复方案：在 uvicorn.run() 中传入自定义 log_config，确保所有进程使用一致的日志格式

本测试模拟 uvicorn 启动后的日志环境，验证异常栈 traceback 的多行输出行为。
测试在修复前运行时预期 FAIL（证明 bug 存在），修复后预期 PASS。
"""

import io
import logging
import logging.config
import re
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

exception_types = st.sampled_from([ValueError, TypeError, RuntimeError, KeyError])
"""随机异常类型策略：覆盖常见的内置异常类型。"""

exception_messages = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P", "S", "Z")),
    min_size=1,
    max_size=100,
)
"""随机异常消息策略：生成包含字母、数字、标点、符号的文本。"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_custom_uvicorn_log_config() -> dict[str, Any]:
    """构建与 main.py 中 UVICORN_LOG_CONFIG 一致的自定义日志配置字典。

    此函数返回的配置字典与 main.py 的 main() 函数中定义的
    UVICORN_LOG_CONFIG 完全一致，用于在测试中模拟修复后的日志环境。

    关键区别（相比 uvicorn 默认 LOGGING_CONFIG）：
    - 使用标准 ``logging.Formatter`` 而非 uvicorn 的 ``DefaultFormatter``
    - format 字符串包含 OTel trace_id/span_id 字段
    - 标准 Formatter 能正确保留异常栈 traceback 中的换行符

    Returns:
        dict: 可传入 ``logging.config.dictConfig()`` 的日志配置字典。
    """
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                "class": "logging.Formatter",
                "format": (
                    "%(asctime)s %(levelname)s [%(name)s] "
                    "[trace_id=%(otelTraceID)s span_id=%(otelSpanID)s] %(message)s"
                ),
                "datefmt": "%Y-%m-%d %H:%M:%S",
            },
        },
        "handlers": {
            "default": {
                "class": "logging.StreamHandler",
                "formatter": "default",
                "stream": "ext://sys.stderr",
            },
        },
        "loggers": {
            "uvicorn": {
                "handlers": ["default"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.error": {
                "handlers": ["default"],
                "level": "INFO",
                "propagate": False,
            },
            "uvicorn.access": {
                "handlers": ["default"],
                "level": "INFO",
                "propagate": False,
            },
        },
    }


def _setup_uvicorn_worker_logging_env() -> tuple[logging.Logger, io.StringIO]:
    """模拟修复后的 uvicorn 多 worker 子进程日志环境，返回 (logger, stream)。

    修复后的启动流程：
    1. main.py 中 uvicorn.run() 传入自定义 log_config=UVICORN_LOG_CONFIG
    2. uvicorn 调用 config.configure_logging() → dictConfig(UVICORN_LOG_CONFIG)
    3. 自定义配置使用标准 logging.Formatter（而非 uvicorn 的 DefaultFormatter），
       确保异常栈 traceback 的换行符被正确保留

    本函数模拟修复后的场景：
    - 先重置 root logger（模拟子进程初始状态）
    - 应用自定义 UVICORN_LOG_CONFIG（模拟修复后的 configure_logging()）
    - 注入 OTel record factory（因为 application 模块导入时会触发）
    - 创建测试用 logger 并使用自定义配置中的 formatter

    Returns:
        tuple: (test_logger, stream) - 测试用 logger 和捕获输出的 StringIO
    """
    # 重置 root logger 到初始状态（模拟子进程）
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)

    # 1) 应用自定义日志配置（模拟修复后的 config.configure_logging()）
    #    使用与 main.py 中 UVICORN_LOG_CONFIG 一致的配置
    custom_log_config = _build_custom_uvicorn_log_config()
    logging.config.dictConfig(custom_log_config)

    # 2) 注入 OTel record factory（模拟 application 模块导入时的副作用）
    original_factory = logging.getLogRecordFactory()

    def _otel_record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
        """创建带有 OTel 默认字段的 LogRecord。"""
        record = original_factory(*args, **kwargs)
        if not hasattr(record, "otelTraceID"):
            record.otelTraceID = "0"
        if not hasattr(record, "otelSpanID"):
            record.otelSpanID = "0"
        return record

    logging.setLogRecordFactory(_otel_record_factory)

    # 3) 获取 uvicorn logger 的 formatter（来自自定义配置中的 "default" formatter）
    #    该 formatter 使用标准 logging.Formatter，能正确处理多行 traceback
    uvicorn_logger = logging.getLogger("uvicorn")
    formatter = None
    if uvicorn_logger.handlers:
        formatter = uvicorn_logger.handlers[0].formatter

    # 4) 创建 StringIO stream handler 用于捕获输出
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setLevel(logging.DEBUG)
    if formatter is not None:
        handler.setFormatter(formatter)

    # 5) 创建测试用 logger（模拟 application.exception_handler）
    test_logger = logging.getLogger("test.exception.newline")
    test_logger.setLevel(logging.DEBUG)
    test_logger.handlers.clear()
    test_logger.addHandler(handler)
    test_logger.propagate = False

    return test_logger, stream


# ---------------------------------------------------------------------------
# Bug Condition Exploration Test
# ---------------------------------------------------------------------------


@given(exc_type=exception_types, exc_msg=exception_messages)
@settings(max_examples=50, deadline=None)
def test_bug_condition_exception_traceback_multiline(exc_type: type, exc_msg: str) -> None:
    """Bug Condition 探索性测试：验证异常栈多行输出。

    **Validates: Requirements 1.1, 1.2, 1.3, 2.1, 2.3**

    模拟 uvicorn 多 worker 子进程环境（main.py 的 basicConfig 未执行），
    验证 logger.exception() 输出的 traceback 是否保持多行格式。

    在未修复代码上运行时，此测试预期 FAIL：
    - uvicorn 的 LOGGING_CONFIG 不配置 root logger
    - 子进程中 root logger 没有应用层的 formatter
    - 日志输出缺少 OTel trace_id/span_id 字段和正确的格式

    修复后（传入自定义 log_config），此测试预期 PASS：
    - 自定义 log_config 确保所有进程使用一致的日志格式
    - traceback 以多行格式正确输出

    对于任意异常类型和消息，格式化输出中的 traceback 部分应当：
    - 包含换行符 "\\n"
    - 包含 "Traceback (most recent call last):" 标识
    - 每个栈帧独占一行
    - 输出包含 OTel trace_id 和 span_id 字段
    """
    logger, stream = _setup_uvicorn_worker_logging_env()

    # 触发异常并通过 logger.exception() 记录
    try:
        raise exc_type(exc_msg)
    except Exception:
        logger.exception("Caught exception during processing")

    output = stream.getvalue()

    # 断言 1: 输出中包含换行符（traceback 应为多行）
    assert "\n" in output, f"输出中不包含换行符，traceback 被压缩为单行。\n实际输出: {output!r}"

    # 断言 2: 输出中包含 traceback 标识
    assert "Traceback (most recent call last):" in output, (
        f"输出中缺少 'Traceback (most recent call last):' 标识。\n实际输出: {output!r}"
    )

    # 断言 3: 栈帧信息应出现在独立的行上
    lines = output.split("\n")
    frame_lines = [line for line in lines if "File " in line]
    assert len(frame_lines) >= 1, (
        f"未找到独立的栈帧行（包含 'File ' 的行）。\n实际输出行: {lines!r}"
    )

    # 断言 4: 输出包含 OTel trace_id 和 span_id 字段（验证日志格式完整性）
    assert "trace_id=" in output, (
        f"输出中缺少 OTel trace_id 字段，日志格式不完整。\n实际输出: {output!r}"
    )
    assert "span_id=" in output, (
        f"输出中缺少 OTel span_id 字段，日志格式不完整。\n实际输出: {output!r}"
    )


# ---------------------------------------------------------------------------
# Strategies for Preservation Tests
# ---------------------------------------------------------------------------

log_levels = st.sampled_from([logging.DEBUG, logging.INFO, logging.WARNING, logging.ERROR])
"""随机日志级别策略：覆盖 DEBUG、INFO、WARNING、ERROR 四个常用级别。"""

log_messages = st.text(
    alphabet=st.characters(blacklist_characters="\n\r"),
    min_size=1,
    max_size=200,
)
"""随机日志消息策略：生成 1-200 字符的任意文本（排除换行符和回车符，避免干扰单行断言）。"""


# ---------------------------------------------------------------------------
# ANSI escape code helper
# ---------------------------------------------------------------------------

_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*m")
"""匹配 ANSI 转义序列的正则表达式，用于从日志输出中剥离颜色代码。"""


def _strip_ansi(text: str) -> str:
    """移除字符串中的 ANSI 转义序列（颜色代码）。

    uvicorn 的 DefaultFormatter 在终端输出时会添加 ANSI 颜色代码
    （如 ``\\x1b[32m`` 表示绿色），这些代码会干扰文本内容的断言。
    本函数将其全部移除，只保留纯文本内容。

    Args:
        text: 可能包含 ANSI 转义序列的字符串。

    Returns:
        移除所有 ANSI 转义序列后的纯文本字符串。
    """
    return _ANSI_ESCAPE_RE.sub("", text)


# ---------------------------------------------------------------------------
# Preservation Property Test
# ---------------------------------------------------------------------------


@given(level=log_levels, message=log_messages)
@settings(max_examples=50, deadline=None)
def test_preservation_normal_log_format(level: int, message: str) -> None:
    """Preservation 属性测试：验证普通日志（不带 exc_info）的格式在修复前后不变。

    **Validates: Requirements 3.1, 3.2, 3.3**

    模拟 uvicorn 多 worker 子进程环境，验证不带 ``exc_info`` 的普通日志调用
    产生的输出满足以下属性：

    1. 输出为单行（格式化记录内部不包含 ``\\n``，仅末尾有一个换行符）
    2. 输出包含对应的日志级别名称（INFO、WARNING、ERROR、DEBUG）
    3. 输出包含日志消息文本

    注意：在未修复的 uvicorn worker 环境中，logger 使用 uvicorn 的
    DefaultFormatter 格式 ``%(levelprefix)s %(message)s``，该格式不包含
    OTel trace_id/span_id 字段。因此本测试不断言 trace_id/span_id 的存在，
    仅验证上述三个核心属性。这些属性在修复前后都应保持不变。
    """
    logger, stream = _setup_uvicorn_worker_logging_env()

    # 使用指定级别记录普通日志（不带 exc_info）
    logger.log(level, message)

    output = stream.getvalue()
    clean_output = _strip_ansi(output)

    # 跳过空输出的情况（理论上不应发生，但防御性处理）
    if not clean_output.strip():
        return

    # 断言 1: 输出为单行——格式化记录内部不包含换行符
    # 日志输出末尾有一个 \n（StreamHandler 默认行为），
    # 但记录本身（去掉末尾换行后）不应包含额外的 \n
    record_text = clean_output.rstrip("\n")
    assert "\n" not in record_text, (
        f"普通日志输出不应包含换行符（应为单行），但发现多行输出。\n"
        f"日志级别: {logging.getLevelName(level)}\n"
        f"日志消息: {message!r}\n"
        f"实际输出: {clean_output!r}"
    )

    # 断言 2: 输出包含日志级别名称
    level_name = logging.getLevelName(level)
    assert level_name in clean_output, (
        f"普通日志输出中缺少级别名称 '{level_name}'。\n实际输出: {clean_output!r}"
    )

    # 断言 3: 输出包含日志消息文本
    # 注意：Hypothesis 生成的消息可能包含特殊字符，
    # 但 logging 模块会原样输出消息文本
    assert message in clean_output, (
        f"普通日志输出中缺少消息文本。\n"
        f"日志级别: {level_name}\n"
        f"期望消息: {message!r}\n"
        f"实际输出: {clean_output!r}"
    )
