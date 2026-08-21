"""Exec 工具 ``working_dir`` 与 Workspace 的二次校验集成测试（任务 8.6）。

断言目标：``SHELL_EXEC_WORKING_DIR`` / ``PYTHON_EXEC_WORKING_DIR`` 若被设置
到 Workspace 根之外，``configure_container()`` → ``container.start()``
必须 fail-fast，并给出中文错误消息提示"请将 SHELL_EXEC_WORKING_DIR /
PYTHON_EXEC_WORKING_DIR 设置到工作区内，或留空使用默认"。

Phase 11.3 已在 ``_create_tool_registry`` 中落地
``_validate_exec_working_dir(ws, config_name, working_dir)`` 调用；
原 ``xfail`` 标记已移除，本测试作为正常用例参与 CI。
"""

from __future__ import annotations

import importlib.util
import pathlib
from types import ModuleType, SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from common.configuration import ConfigurationError
from domain.workspace.value_objects import WorkspaceBackendKind

pytestmark = [pytest.mark.asyncio]


def _make_workspace_config_stub(
    *,
    backend: WorkspaceBackendKind = WorkspaceBackendKind.LOCAL_FILESYSTEM,
    root: str = "",
    follow_symlinks: bool = False,
    create_if_missing: bool = False,
) -> SimpleNamespace:
    """与 ``test_workspace_container_integration`` 保持一致的轻量 stub。

    ``workspace_config`` 是 ``create_config(WorkspaceConfig)`` 在模块导入期创建
    的单例；``monkeypatch.setenv`` 无法改写已固化的字段值，必须通过
    ``patch.object(<module>, "workspace_config", stub)`` 在用例内替换。
    """
    return SimpleNamespace(
        backend=backend,
        root=root,
        follow_symlinks=follow_symlinks,
        create_if_missing=create_if_missing,
    )


def _load_container_config_module() -> ModuleType:
    """同 ``test_workspace_container_integration``，直接加载源文件。"""
    config_path = (
        pathlib.Path(__file__).resolve().parents[2] / "src" / "application" / "container_config.py"
    )
    spec = importlib.util.spec_from_file_location(
        "test_workspace_exec_working_dir_validation_module", str(config_path)
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_config_module: Any = _load_container_config_module()


@pytest.fixture(autouse=True)
def isolate_container():
    """用例间隔离容器全局状态。"""
    from common.container import container

    original_state = container.capture_state()
    yield
    container.restore_state(original_state)


@pytest.fixture(autouse=True)
def reset_workspace_singleton():
    """重置 ``_workspace_singleton`` 以避免跨用例泄漏。"""
    original = _config_module._workspace_singleton
    yield
    _config_module._workspace_singleton = original


async def test_shell_exec_working_dir_outside_workspace_fails_fast(
    tmp_path: pathlib.Path,
) -> None:
    """``SHELL_EXEC_WORKING_DIR=/etc`` 超出 ``WORKSPACE_ROOT=<tmp_path>`` 时，
    ``_validate_exec_working_dir`` 必须 fail-fast 并抛出 ``ConfigurationError``。

    断言：
    - 抛出 ``ConfigurationError``；
    - 错误消息中包含中文提示（关键字 "SHELL_EXEC_WORKING_DIR" 或
      "工作区" 出现，且包含 "留空" 或 "工作区内" 之类的修复提示）。

    注：``workspace_config`` 为 ``create_config(WorkspaceConfig)`` 在模块导入
    期创建的单例，``monkeypatch.setenv`` 无法改写已固化的字段值（见 2026-05-11
    pytest 回归缺陷修复批次 B），这里改用 ``patch.object`` 注入 stub 配置。
    """
    stub_cfg = _make_workspace_config_stub(root=str(tmp_path))

    with patch.object(_config_module, "workspace_config", stub_cfg):
        await _config_module._init_workspace()
        ws = _config_module._workspace_singleton
        assert ws is not None

        with pytest.raises(ConfigurationError) as exc_info:
            _config_module._validate_exec_working_dir(
                ws=ws,
                config_name="SHELL_EXEC_WORKING_DIR",
                working_dir="/etc",
            )

    msg = str(exc_info.value)
    assert "SHELL_EXEC_WORKING_DIR" in msg or "工作区" in msg, (
        f"错误消息必须提示 SHELL_EXEC_WORKING_DIR 或工作区关键字，实际：{msg}"
    )
    assert "留空" in msg or "工作区内" in msg, (
        f"错误消息必须给出修复指引（'留空' / '工作区内'），实际：{msg}"
    )


async def test_python_exec_working_dir_outside_workspace_fails_fast(
    tmp_path: pathlib.Path,
) -> None:
    """``PYTHON_EXEC_WORKING_DIR=/etc`` 超出 workspace 时同样 fail-fast。

    断言与 ``shell_exec`` 用例对称。
    """
    stub_cfg = _make_workspace_config_stub(root=str(tmp_path))

    with patch.object(_config_module, "workspace_config", stub_cfg):
        await _config_module._init_workspace()
        ws = _config_module._workspace_singleton
        assert ws is not None

        with pytest.raises(ConfigurationError) as exc_info:
            _config_module._validate_exec_working_dir(
                ws=ws,
                config_name="PYTHON_EXEC_WORKING_DIR",
                working_dir="/etc",
            )

    msg = str(exc_info.value)
    assert "PYTHON_EXEC_WORKING_DIR" in msg or "工作区" in msg, (
        f"错误消息必须提示 PYTHON_EXEC_WORKING_DIR 或工作区关键字，实际：{msg}"
    )
    assert "留空" in msg or "工作区内" in msg, (
        f"错误消息必须给出修复指引（'留空' / '工作区内'），实际：{msg}"
    )


async def test_empty_working_dir_uses_default_no_error(tmp_path: pathlib.Path) -> None:
    """``SHELL_EXEC_WORKING_DIR`` / ``PYTHON_EXEC_WORKING_DIR`` 为空时
    （默认行为）不触发二次校验错误。
    """
    stub_cfg = _make_workspace_config_stub(root=str(tmp_path))

    with patch.object(_config_module, "workspace_config", stub_cfg):
        await _config_module._init_workspace()
        ws = _config_module._workspace_singleton
        assert ws is not None

        # 空串 / None 均视为"使用默认"，不应抛 ConfigurationError
        _config_module._validate_exec_working_dir(
            ws=ws, config_name="SHELL_EXEC_WORKING_DIR", working_dir=""
        )
        _config_module._validate_exec_working_dir(
            ws=ws, config_name="PYTHON_EXEC_WORKING_DIR", working_dir=None
        )

    assert _config_module._workspace_singleton is not None
