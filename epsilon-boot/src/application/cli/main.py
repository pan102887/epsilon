"""Console script entry point for epsilon."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections.abc import Sequence

from .runtime import CliRuntime
from .tui import TuiApp

logger = logging.getLogger(__name__)


def build_parser() -> argparse.ArgumentParser:
    """Create the epsilon argument parser."""
    parser = argparse.ArgumentParser(prog="epsilon")
    parser.add_argument("--version", action="version", version="epsilon 0.1.0")

    subparsers = parser.add_subparsers(dest="command")

    exec_parser = subparsers.add_parser("exec", help="执行一次性任务")
    exec_parser.add_argument("goal", help="任务目标")
    exec_parser.add_argument("--model", help="指定模型", default=None)
    exec_parser.add_argument("--json", action="store_true", help="输出结构化 JSON")

    serve_parser = subparsers.add_parser("serve", help="启动 FastAPI 服务")
    serve_parser.add_argument("--host", default="0.0.0.0")
    serve_parser.add_argument("--port", type=int, default=8000)
    serve_parser.add_argument("--reload", action="store_true")

    return parser


def _configure_cli_file_logging() -> None:
    """为 TUI / exec 入口装配本地文件日志（落 USER tier，故障隔离）。

    经容器侧 ``_create_tier_resolver()`` 获取全仓库唯一的 tier 解析器，将日志写入
    ``~/.epsilon/<project-hash>/logs/epsilon.log``（USER tier，ADR-0005 决策 2b，不污染
    项目工作区）；敏感字段词表复用 ``RequestLoggingConfig`` 的脱敏配置。

    ``serve`` 路径不调用本函数，既有 FastAPI 日志链路不受影响。装配全过程被完整
    兜底：任何失败仅记录 warning，不阻断 CLI 启动（design「错误处理」，需求 4.1/4.2）。
    """
    try:
        from application.api.middlewares.logging_config import RequestLoggingConfig
        from application.container_config import _create_tier_resolver
        from infrastructure.storage.local_file_log_sink import (
            configure_local_file_logging,
        )
        from infrastructure.storage.log_sink_config import LogSinkConfig

        sensitive_keys = frozenset(
            RequestLoggingConfig().get_sensitive_body_fields_set()
        )
        configure_local_file_logging(
            _create_tier_resolver(),
            LogSinkConfig(),
            sensitive_keys,
        )
    except Exception:  # 文件日志属非关键路径，装配失败降级为跳过，不阻断 CLI 启动
        logger.warning("装配本地文件日志失败，已跳过文件日志", exc_info=True)


async def _run_tui() -> int:
    async with CliRuntime() as runtime:
        _configure_cli_file_logging()
        return await TuiApp(runtime).run()


async def _run_exec(goal: str, *, model: str | None, json_output: bool = False) -> int:
    """执行一次性任务，并按需输出结构化 JSON。"""
    async with CliRuntime() as runtime:
        _configure_cli_file_logging()
        if json_output:
            structured = await runtime.execute_once_json(goal, model=model)
            print(json.dumps(structured.to_dict(), ensure_ascii=False))
            return 0 if structured.status == "success" else 1
        result = await runtime.execute_once(goal, model=model)
        print(result.content)
        return 0 if result.status.value == "success" else 1


def _run_serve(*, host: str, port: int, reload: bool) -> int:
    import uvicorn

    uvicorn.run(
        "application.api.server_app:app",
        host=host,
        port=port,
        reload=reload,
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """epsilon CLI entry point."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.command == "exec":
            return asyncio.run(
                _run_exec(args.goal, model=args.model, json_output=args.json)
            )
        if args.command == "serve":
            return _run_serve(host=args.host, port=args.port, reload=args.reload)
        return asyncio.run(_run_tui())
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        print(f"epsilon error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
