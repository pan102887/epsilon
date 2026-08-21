"""Application entrypoint.

本地开发：python main.py
容器部署：python main.py（K8S 通过增加 Pod 副本数水平扩展）
"""

import os
import sys
from typing import Any

# 将 src/ 目录加入模块搜索路径，使 domain 层的裸导入（如 from domain.xxx import ...）
# 在运行时也能正确解析，与 pyproject.toml 中 pytest 的 pythonpath = ["src"] 保持一致。
_src_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "src")
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

if sys.platform == "win32":
    # prometheus_client's process_collector uses the Unix-only `resource` module.
    # Provide a stub so the import chain doesn't crash on Windows.
    import types

    _stub = types.ModuleType("resource")
    _stub.getpagesize = lambda: 4096  # type: ignore[attr-defined]
    _stub.getrusage = lambda _: (0,) * 16  # type: ignore[attr-defined]
    _stub.RUSAGE_SELF = 0  # type: ignore[attr-defined]
    sys.modules["resource"] = _stub

import logging  # noqa: E402  # 须在 sys.path 与 win32 stub 配置后再导入

# 通过自定义 LogRecord 工厂为所有日志记录注入 OTel 链路追踪字段的默认值。
# 当 OpenTelemetry LoggingInstrumentor 未启用时，LogRecord 中不存在
# otelTraceID 和 otelSpanID 属性，导致日志格式化时抛出 ValueError。
# 此工厂在 LogRecord 创建时即补充默认值（"0"），确保无论 OTel 是否启用，
# 日志格式都能正常工作。当 LoggingInstrumentor 启用后，它会覆盖这些默认值
# 为实际的 trace_id 和 span_id。
_original_factory = logging.getLogRecordFactory()


def _otel_record_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    """创建带有 OTel 默认字段的 LogRecord。"""
    record = _original_factory(*args, **kwargs)
    if not hasattr(record, "otelTraceID"):
        record.otelTraceID = "0"  # type: ignore[attr-defined]
    if not hasattr(record, "otelSpanID"):
        record.otelSpanID = "0"  # type: ignore[attr-defined]
    return record


logging.setLogRecordFactory(_otel_record_factory)

# 配置根 logger，确保所有模块通过 getLogger(__name__) 创建的 logger 都能输出到控制台。
# uvicorn 内部会自行管理自己的 handler，这里配置的是应用代码的日志输出。
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] [trace_id=%(otelTraceID)s span_id=%(otelSpanID)s] %(message)s",  # noqa: E501  # 日志格式字符串保持单行以便阅读
    datefmt="%Y-%m-%d %H:%M:%S",
)

from application import service_config  # noqa: E402  # 须在根 logger 配置完成后再导入应用模块


def main() -> None:
    """启动 FastAPI 应用服务器。

    本函数构建自定义的 uvicorn 日志配置并启动服务器。

    为什么需要自定义 log_config：
        uvicorn 启动时默认使用内置的 ``LOGGING_CONFIG``，该配置会通过
        ``logging.config.dictConfig()`` 覆盖应用通过 ``logging.basicConfig()``
        设置的 root logger formatter。uvicorn 内置的 ``DefaultFormatter``
        在格式化多行消息（如异常栈 traceback）时，不能正确保留换行符，
        导致所有栈帧被压缩到一行，严重影响开发者排查问题。

        通过显式传入自定义 ``log_config``，我们可以：
        1. 使用标准 ``logging.Formatter``（而非 uvicorn 的 ``DefaultFormatter``），
           确保异常栈 traceback 以多行格式正确输出
        2. 保持与 ``logging.basicConfig()`` 一致的日志格式（含 OTel trace_id/span_id 字段）
        3. 确保多 worker 模式下所有子进程使用一致的日志配置
           （纯 dict 配置可被 fork 后的子进程独立应用）
    """

    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - runtime import guard
        raise SystemExit(
            "uvicorn is required to run the server. Install it via 'pip install uvicorn'."
        ) from exc

    # -----------------------------------------------------------------------
    # 自定义 uvicorn 日志配置
    #
    # 关键点：
    # - 使用 "logging.Formatter" 而非 uvicorn 的 "uvicorn.logging.DefaultFormatter"
    #   标准 Formatter 能正确处理 exc_info 中的多行 traceback，保留换行符
    # - format 字符串与 logging.basicConfig() 保持一致，包含 OTel 链路追踪字段
    # - 配置为纯 dict（可序列化），确保多 worker 模式下每个子进程都能独立应用
    # - 同时配置 uvicorn、uvicorn.error、uvicorn.access 三个 logger，
    #   确保 uvicorn 自身的访问日志和错误日志也使用统一格式
    # -----------------------------------------------------------------------
    UVICORN_LOG_CONFIG: dict[str, Any] = {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "default": {
                # 使用标准 logging.Formatter，而非 uvicorn 的 DefaultFormatter。
                # DefaultFormatter 在处理含 exc_info 的日志记录时，
                # 会将多行 traceback 压缩为单行，导致异常栈不可读。
                # 标准 Formatter 的 formatException() 能正确保留换行符。
                "class": "logging.Formatter",
                "format": "%(asctime)s %(levelname)s [%(name)s] [trace_id=%(otelTraceID)s span_id=%(otelSpanID)s] %(message)s",  # noqa: E501  # 日志格式字符串保持单行以便阅读
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

    uvicorn.run(
        "application:app",
        host=service_config.host,
        port=service_config.port,
        workers=service_config.workers,
        log_level="info",
        log_config=UVICORN_LOG_CONFIG,
    )


if __name__ == "__main__":
    main()
