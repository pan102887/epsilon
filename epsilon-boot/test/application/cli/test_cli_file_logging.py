"""CLI 入口本地文件日志装配测试。

验证 TUI / exec 入口在 ``CliRuntime`` 启动后经 ``_configure_cli_file_logging``
把 ``RotatingFileHandler`` 挂到 root logger（落 USER tier
``~/.epsilon/<project-hash>/logs/``），``serve`` 路径不装配文件日志，以及
``EPSILON_LOG_TO_FILE=false`` 时不装配（需求 4.1/4.2，Property 9）。

测试通过直接调用可测切片 ``_configure_cli_file_logging`` 与对 ``serve`` 分支
打桩，避免真正跑起全屏 TUI；并在每个用例结束时移除并关闭挂到 root logger 的
``RotatingFileHandler``，防止污染其他测试。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

import application.cli.main as cli_main


def _rotating_handlers() -> list[RotatingFileHandler]:
    """返回当前挂在 root logger 上的所有 RotatingFileHandler。"""
    return [
        h
        for h in logging.getLogger().handlers
        if isinstance(h, RotatingFileHandler)
    ]


@pytest.fixture(autouse=True)
def _cleanup_root_handlers() -> Iterator[None]:
    """用例结束时移除并关闭本用例挂到 root logger 的 RotatingFileHandler。"""
    before = set(_rotating_handlers())
    yield
    root = logging.getLogger()
    for handler in _rotating_handlers():
        if handler not in before:
            root.removeHandler(handler)
            handler.close()


@pytest.fixture
def _reset_tier_resolver(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """以临时 HOME / CWD 重置全仓库唯一的 tier 解析器缓存单例。

    Yields:
        作为 HOME（USER tier 基点）的临时目录。
    """
    import application.container_config as container_config

    home_dir = tmp_path / "home"
    project_dir = tmp_path / "project"
    home_dir.mkdir()
    project_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.chdir(project_dir)
    monkeypatch.setattr(container_config, "_tier_resolver", None)
    yield home_dir
    monkeypatch.setattr(container_config, "_tier_resolver", None)


def test_configure_cli_file_logging_attaches_rotating_handler(
    _reset_tier_resolver: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """装配后 root logger 挂上落 USER tier logs 的 RotatingFileHandler。"""
    monkeypatch.setenv("EPSILON_LOG_TO_FILE", "true")

    cli_main._configure_cli_file_logging()

    handlers = _rotating_handlers()
    assert len(handlers) == 1
    log_path = Path(handlers[0].baseFilename)
    assert log_path.name == "epsilon.log"
    # USER tier：~/.epsilon/<project-hash>/logs/epsilon.log，落临时 HOME 下不入项目工作区
    assert _reset_tier_resolver in log_path.parents
    assert log_path.parent.name == "logs"


def test_configure_cli_file_logging_disabled_by_env(
    _reset_tier_resolver: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """EPSILON_LOG_TO_FILE=false 时不装配任何 RotatingFileHandler。"""
    monkeypatch.setenv("EPSILON_LOG_TO_FILE", "false")

    cli_main._configure_cli_file_logging()

    assert _rotating_handlers() == []


def test_configure_cli_file_logging_isolated_on_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """装配内部异常被兜底，不向调用方抛出（故障隔离）。"""

    def _boom() -> object:
        raise RuntimeError("resolver 故障")

    monkeypatch.setattr(
        "application.container_config._create_tier_resolver", _boom
    )
    monkeypatch.setenv("EPSILON_LOG_TO_FILE", "true")

    # 不抛出即为通过；不应挂上 handler
    cli_main._configure_cli_file_logging()
    assert _rotating_handlers() == []


def test_serve_path_does_not_configure_file_logging(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """serve 分支不调用文件日志装配（既有 FastAPI 日志链路不变）。"""
    called = {"configured": False, "served": False}

    def _fake_configure() -> None:
        called["configured"] = True

    def _fake_serve(*, host: str, port: int, reload: bool) -> int:
        called["served"] = True
        return 0

    monkeypatch.setattr(cli_main, "_configure_cli_file_logging", _fake_configure)
    monkeypatch.setattr(cli_main, "_run_serve", _fake_serve)

    exit_code = cli_main.main(["serve", "--host", "127.0.0.1", "--port", "9999"])

    assert exit_code == 0
    assert called["served"] is True
    assert called["configured"] is False
