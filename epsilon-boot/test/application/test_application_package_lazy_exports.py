"""顶层 application 包 lazy export 的导入副作用回归测试。"""

from __future__ import annotations

import importlib
import sys

import pytest

SERVER_APP_MODULE = "application.api.server_app"


def _drop_application_modules() -> None:
    """清理 application 相关模块缓存，让每个用例重新观察导入副作用。"""
    for module_name in list(sys.modules):
        if module_name == "application" or module_name.startswith("application."):
            sys.modules.pop(module_name)


@pytest.fixture(autouse=True)
def isolated_application_modules():
    """隔离本文件的应用层导入，避免 FastAPI app 缓存影响其他测试。"""
    _drop_application_modules()
    yield
    _drop_application_modules()


def test_import_application_does_not_load_fastapi_app() -> None:
    """仅导入顶层包时，不应加载 FastAPI app 模块。"""
    importlib.import_module("application")

    assert SERVER_APP_MODULE not in sys.modules


def test_import_cli_runtime_does_not_load_fastapi_app_or_configure_container(
    monkeypatch,
) -> None:
    """CLI runtime 模块导入不应初始化 HTTP adapter 或配置容器。"""
    calls: list[str] = []
    container_config = importlib.import_module("application.container_config")

    def fake_configure_container() -> None:
        calls.append("called")

    monkeypatch.setattr(container_config, "configure_container", fake_configure_container)

    runtime_module = importlib.import_module("application.cli.runtime")

    assert runtime_module.CliRuntime.__name__ == "CliRuntime"
    assert calls == []
    assert SERVER_APP_MODULE not in sys.modules


def test_service_config_export_does_not_load_fastapi_app() -> None:
    """兼容导出 service_config 时，不应创建 FastAPI app。"""
    from application import service_config as package_service_config

    server_config = importlib.import_module("application.api.server_config")

    assert package_service_config is server_config.service_config
    assert SERVER_APP_MODULE not in sys.modules


def test_app_export_lazy_loads_fastapi_app(monkeypatch) -> None:
    """兼容导出 app 时，才按需加载 FastAPI app 模块。"""
    calls: list[str] = []
    container_config = importlib.import_module("application.container_config")

    def fake_configure_container() -> None:
        calls.append("called")

    monkeypatch.setattr(container_config, "configure_container", fake_configure_container)

    from application import app as package_app

    server_app = importlib.import_module(SERVER_APP_MODULE)

    assert package_app is server_app.app
    assert calls == ["called"]
