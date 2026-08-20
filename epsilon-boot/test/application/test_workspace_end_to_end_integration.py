"""端到端 Workspace 接入集成测试（Phase 13.5）。

覆盖场景（对应 tasks.md 13.5）：

1. ``ScopedToolRegistry`` 场景下对 ``read_file`` 传入 ``../etc/passwd`` →
   ``ToolExecutionError`` 以 ToolMessage 形式回传（不终止 Agent Loop，
   需求 8.5）；
2. ``write_file`` 成功消息使用逻辑路径（不含宿主根）；
3. ``list_dir("/")`` 返回条目路径为 ``/``-起始逻辑路径；
4. ``ShellExecTool``（``SHELL_EXEC_ENABLED=true``）时 ``cwd`` 落在
   ``WORKSPACE_ROOT`` 之内；
5. 启动期 ``SHELL_EXEC_WORKING_DIR=/etc`` → ``container.start()`` fail-fast。

测试策略（与 ``test_workspace_container_integration.py`` 一致）：

- 用 ``importlib.util`` 直接加载 ``container_config.py`` 绕开
  ``application/__init__.py`` 的平台依赖；
- 用 ``patch.object(..., "workspace_config", <stub>)`` 等绕过 pydantic
  对环境变量的实时读取；
- 子进程相关场景用 ``unittest.mock.patch`` 拦截
  ``asyncio.create_subprocess_exec`` 验证传参，不真起子进程。
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from common.configuration import ConfigurationError
from domain.agent.exceptions import ToolExecutionError

pytestmark = [pytest.mark.asyncio]


def _load_container_config_module():
    config_path = (
        pathlib.Path(__file__).resolve().parents[2] / "src" / "application" / "container_config.py"
    )
    spec = importlib.util.spec_from_file_location(
        "test_workspace_end_to_end_integration_module", str(config_path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_config_module = _load_container_config_module()


@pytest.fixture(autouse=True)
def _isolate_container():
    """用例间隔离容器全局状态。"""
    from common.container import container

    original_registry = container._registry.copy()
    original_singletons = container._singletons.copy()
    original_resources = container._async_resources[:]
    original_initialized = container._initialized_resources[:]
    yield
    container._registry = original_registry
    container._singletons = original_singletons
    container._async_resources = original_resources
    container._initialized_resources = original_initialized


@pytest.fixture(autouse=True)
def _reset_workspace_singleton():
    original = _config_module._workspace_singleton
    yield
    _config_module._workspace_singleton = original


# ---------------------------------------------------------------------------
# 端到端 happy path：读越界路径 → ToolExecutionError
# ---------------------------------------------------------------------------


async def test_read_file_outside_workspace_raises_tool_execution_error(tmp_path, monkeypatch):
    """通过真实 Workspace（LocalFilesystemWorkspace）解析 ``../etc/passwd``
    应该抛 ``ToolExecutionError``；ScopedToolRegistry 会将其包装为
    ToolMessage 形态（需求 8.5）。"""
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSPACE_BACKEND", "local_filesystem")

    from common.configuration import create_config
    from infrastructure.workspace.workspace_config import WorkspaceConfig

    stub_cfg = create_config(WorkspaceConfig)
    with patch.object(_config_module, "workspace_config", stub_cfg):
        await _config_module._init_workspace()

        from infrastructure.tools.filesystem.read_file_tool import ReadFileTool

        ws = _config_module._workspace_singleton
        assert ws is not None
        tool = ReadFileTool(workspace=ws)

        with pytest.raises(ToolExecutionError) as exc_info:
            await tool.execute(file_path="../etc/passwd")

        assert "超出工作区边界" in exc_info.value.message
        assert str(tmp_path) not in exc_info.value.message
        assert exc_info.value.tool_name == "read_file"


async def test_write_file_success_message_uses_logical_path(tmp_path, monkeypatch):
    """``write_file`` 成功消息应使用 ``/xxx`` 逻辑路径，不含宿主根。"""
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSPACE_BACKEND", "local_filesystem")

    from common.configuration import create_config
    from infrastructure.workspace.workspace_config import WorkspaceConfig

    stub_cfg = create_config(WorkspaceConfig)
    with patch.object(_config_module, "workspace_config", stub_cfg):
        await _config_module._init_workspace()

        from infrastructure.tools.filesystem.write_file_tool import WriteFileTool

        ws = _config_module._workspace_singleton
        tool = WriteFileTool(workspace=ws)

        result = await tool.execute(file_path="notes.md", content="hello")
        msg = result.content
        assert "/notes.md" in msg
        assert str(tmp_path) not in msg
        assert result.metadata["logical_path"] == "/notes.md"


async def test_list_dir_returns_logical_paths(tmp_path, monkeypatch):
    """``list_dir("/")`` 返回的条目路径应以 ``/`` 起始且不含宿主根。"""
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSPACE_BACKEND", "local_filesystem")

    # 创建测试文件与子目录
    (tmp_path / "a.md").write_text("")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("")

    from common.configuration import create_config
    from infrastructure.workspace.workspace_config import WorkspaceConfig

    stub_cfg = create_config(WorkspaceConfig)
    with patch.object(_config_module, "workspace_config", stub_cfg):
        await _config_module._init_workspace()

        from infrastructure.tools.filesystem.list_dir_tool import ListDirTool

        ws = _config_module._workspace_singleton
        tool = ListDirTool(workspace=ws)

        result = await tool.execute(directory_path="/")
        output = result.content

        # 结果中的条目路径都应以 '/' 起始
        lines = [ln for ln in output.splitlines() if ln.strip()]
        for ln in lines:
            assert ln.startswith("/"), f"条目未以 '/' 起始：{ln}"
        # 不含宿主路径
        assert str(tmp_path) not in output
        assert result.metadata["logical_path"] == "/"


# ---------------------------------------------------------------------------
# ShellExecTool：cwd 落在 WORKSPACE_ROOT 之内
# ---------------------------------------------------------------------------


async def test_shell_exec_subprocess_cwd_inside_workspace_root(tmp_path, monkeypatch):
    """``ShellExecTool`` 通过 ``materialize_cwd`` 把子进程 cwd 锁在
    ``WORKSPACE_ROOT`` 之内。"""
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSPACE_BACKEND", "local_filesystem")

    from common.configuration import create_config
    from infrastructure.workspace.workspace_config import WorkspaceConfig

    stub_cfg = create_config(WorkspaceConfig)
    with patch.object(_config_module, "workspace_config", stub_cfg):
        await _config_module._init_workspace()

        from infrastructure.tools.shell_exec.shell_exec_tool import ShellExecTool

        ws = _config_module._workspace_singleton
        tool = ShellExecTool(workspace=ws)

        # mock subprocess
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        fake_proc.communicate = AsyncMock(return_value=(b"", b""))
        fake_proc.kill = MagicMock()
        fake_exec = AsyncMock(return_value=fake_proc)

        with patch(
            "infrastructure.tools.shell_exec.shell_exec_tool.asyncio.create_subprocess_exec",
            new=fake_exec,
        ):
            await tool.execute(command="echo hi")

        cwd = fake_exec.call_args.kwargs["cwd"]
        # cwd 必须是 tmp_path（或其下的子目录）
        cwd_abs = os.path.abspath(cwd)  # noqa: ASYNC240  # 测试内同步路径断言，非阻塞 I/O
        root_abs = os.path.abspath(str(tmp_path))  # noqa: ASYNC240  # 同上
        assert cwd_abs == root_abs or cwd_abs.startswith(root_abs + os.sep), (
            f"子进程 cwd={cwd} 未落在 WORKSPACE_ROOT={tmp_path} 之内"
        )


# ---------------------------------------------------------------------------
# 启动期 fail-fast：SHELL_EXEC_WORKING_DIR=/etc（越界）
# ---------------------------------------------------------------------------


async def test_shell_exec_working_dir_outside_workspace_fails_fast(tmp_path, monkeypatch):
    """``SHELL_EXEC_WORKING_DIR=/etc`` 与 ``WORKSPACE_ROOT=<tmp_path>`` 冲突时，
    ``_create_tool_registry`` 阶段应 fail-fast 抛 ``ConfigurationError``。"""
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSPACE_BACKEND", "local_filesystem")

    from common.configuration import create_config
    from infrastructure.workspace.workspace_config import WorkspaceConfig

    stub_cfg = create_config(WorkspaceConfig)

    # Stub shell_exec_config：enabled=True + working_dir="/etc"
    from types import SimpleNamespace

    shell_cfg_stub = SimpleNamespace(
        enabled=True,
        timeout=30,
        max_output_size=51200,
        working_dir="/etc",
    )

    with patch.object(_config_module, "workspace_config", stub_cfg):
        await _config_module._init_workspace()

        ws = _config_module._workspace_singleton
        assert ws is not None

        # 直接调 _validate_exec_working_dir 验证 fail-fast（这是 11.3 的助手，
        # 也是 _create_tool_registry 在每个 exec 工具注册前的守卫调用点）。
        with pytest.raises(ConfigurationError) as exc_info:
            _config_module._validate_exec_working_dir(
                ws=ws,
                config_name="SHELL_EXEC_WORKING_DIR",
                working_dir=shell_cfg_stub.working_dir,
            )

        msg = str(exc_info.value)
        assert "SHELL_EXEC_WORKING_DIR" in msg
        assert "工作区内" in msg or "留空" in msg


async def test_python_exec_working_dir_outside_workspace_fails_fast(tmp_path, monkeypatch):
    """对称地验证 ``PYTHON_EXEC_WORKING_DIR``。"""
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setenv("WORKSPACE_BACKEND", "local_filesystem")

    from common.configuration import create_config
    from infrastructure.workspace.workspace_config import WorkspaceConfig

    stub_cfg = create_config(WorkspaceConfig)

    with patch.object(_config_module, "workspace_config", stub_cfg):
        await _config_module._init_workspace()

        ws = _config_module._workspace_singleton

        with pytest.raises(ConfigurationError) as exc_info:
            _config_module._validate_exec_working_dir(
                ws=ws,
                config_name="PYTHON_EXEC_WORKING_DIR",
                working_dir="/etc",
            )

        msg = str(exc_info.value)
        assert "PYTHON_EXEC_WORKING_DIR" in msg
        assert "工作区内" in msg or "留空" in msg
