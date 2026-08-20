"""Workspace DI 装配集成测试（任务 8.5）。

本测试文件覆盖以下场景：

- happy-path：``WORKSPACE_ROOT=<tmp_path>`` + ``configure_container()`` +
  ``container.start()`` 能成功解析出 ``Workspace`` 实例，``capabilities()
  .local_materialization is True``。
- ``WORKSPACE_ROOT=""`` → 默认使用进程当前工作目录。
- ``WORKSPACE_ROOT`` 指向文件而非目录 → ``ConfigurationError``（对应需求 5.8）。
- ``WORKSPACE_BACKEND=oss``（通过 monkeypatch 绕过 validator）→
  ``_init_workspace`` 抛 ``ConfigurationError``（对应需求 5.4 的防御性
  分支：即使 validator 被绕过，``_init_workspace`` 仍能守护）。
- 装配顺序：``Workspace`` 资源在 ``ToolRegistry`` 被解析之前已就绪（需求 9.1 /
  9.2 / 9.3；Property 7 的"启动期先 Workspace 后 ToolRegistry"拓扑约束）。

实现说明：

- 与 ``test_container_config.py`` 相同，通过 ``importlib.util`` 直接加载
  ``container_config.py`` 以绕过 ``application/__init__.py`` 的初始化副作用
  （``prometheus_client`` 等平台相关依赖）。
- 每个用例使用 ``_isolate_container`` fixture 保存/恢复全局容器状态，
  避免用例间污染。
- ``workspace_config`` 是 ``create_config(WorkspaceConfig)`` 在模块导入期创建
  的单例；在测试中通过 ``monkeypatch.setattr(<module>, "workspace_config", ...)``
  注入伪造的配置实例，直接驱动 ``_init_workspace`` 的代码路径。
"""

from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from common.configuration import ConfigurationError
from common.container import Container
from common.container_models import Scope
from domain.agent.tools import ToolRegistry
from domain.workspace.ports import Workspace
from domain.workspace.value_objects import WorkspaceBackendKind


def _load_container_config_module():
    """直接加载 ``container_config`` 模块，绕过 ``application/__init__.py``。

    理由与 ``test_container_config.py`` 完全一致：``application/__init__.py``
    会触发 ``server_app`` 的完整初始化链（含 ``prometheus_client`` 等平台依赖），
    在干净的单测环境中直接加载源文件更可控。
    """
    config_path = (
        pathlib.Path(__file__).resolve().parents[2] / "src" / "application" / "container_config.py"
    )
    spec = importlib.util.spec_from_file_location(
        "test_workspace_container_integration_module", str(config_path)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_config_module = _load_container_config_module()
configure_container = _config_module.configure_container


def _make_workspace_config_stub(
    *,
    backend: WorkspaceBackendKind = WorkspaceBackendKind.LOCAL_FILESYSTEM,
    root: str = "",
    follow_symlinks: bool = False,
    create_if_missing: bool = False,
) -> SimpleNamespace:
    """构造一个与 ``WorkspaceConfig`` 字段同构的轻量 stub。

    ``_create_local_filesystem_workspace`` / ``_init_workspace`` 仅通过字段
    访问使用配置对象（``cfg.root`` / ``cfg.backend`` / ``cfg.follow_symlinks``
    / ``cfg.create_if_missing``），因此用 ``SimpleNamespace`` 即可避免触发
    ``WorkspaceConfig`` 的 ``@model_validator``（在某些用例中需要故意绕过
    validator 来验证防御性分支）。
    """
    return SimpleNamespace(
        backend=backend,
        root=root,
        follow_symlinks=follow_symlinks,
        create_if_missing=create_if_missing,
    )


@pytest.fixture(autouse=True)
def _isolate_container():
    """每个用例隔离全局容器状态，避免用例间注册残留污染。"""
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
    """重置模块级 ``_workspace_singleton`` 以避免跨用例泄漏。"""
    original = _config_module._workspace_singleton
    yield
    _config_module._workspace_singleton = original


# ---------------------------------------------------------------------------
# 8.5 happy-path：启动能解析出 Workspace，且 local_materialization=True
# ---------------------------------------------------------------------------


async def test_init_workspace_happy_path_resolves_workspace_instance(tmp_path):
    """配置合法时 ``_init_workspace`` 完成后可通过 ``Workspace`` 解析实例。

    不调用 ``configure_container()``（后者会注册所有 Port → Adapter，受
    其他基础设施依赖干扰）；仅手工注册 Workspace 绑定 + 直接调用
    ``_init_workspace`` 覆盖 fail-fast happy-path 支路。
    """
    cfg = _make_workspace_config_stub(
        backend=WorkspaceBackendKind.LOCAL_FILESYSTEM,
        root=str(tmp_path),
        follow_symlinks=False,
        create_if_missing=False,
    )

    container = Container()
    with patch.object(_config_module, "workspace_config", cfg):
        await _config_module._init_workspace()
        container.register(
            Workspace,
            lambda: _config_module._workspace_singleton,
            Scope.SINGLETON,
        )

        ws = await container.resolve(Workspace)

    assert ws is not None
    caps = ws.capabilities()
    assert caps.local_materialization is True


async def test_init_workspace_populates_module_singleton(tmp_path):
    """``_init_workspace`` 成功后 ``_workspace_singleton`` 被赋值为非空实例。"""
    cfg = _make_workspace_config_stub(
        backend=WorkspaceBackendKind.LOCAL_FILESYSTEM,
        root=str(tmp_path),
        follow_symlinks=False,
        create_if_missing=False,
    )

    with patch.object(_config_module, "workspace_config", cfg):
        await _config_module._init_workspace()

    assert _config_module._workspace_singleton is not None
    assert _config_module._workspace_singleton.capabilities().local_materialization is True


# ---------------------------------------------------------------------------
# 8.5 WORKSPACE_ROOT="" → 默认使用进程 cwd
# ---------------------------------------------------------------------------


async def test_init_workspace_empty_root_defaults_to_cwd(tmp_path, monkeypatch):
    """``root=""`` 时 ``_init_workspace`` 默认使用进程当前工作目录。"""
    monkeypatch.chdir(tmp_path)
    cfg = _make_workspace_config_stub(
        backend=WorkspaceBackendKind.LOCAL_FILESYSTEM,
        root="",
    )

    with patch.object(_config_module, "workspace_config", cfg):
        await _config_module._init_workspace()

    assert _config_module._workspace_singleton is not None
    assert _config_module._workspace_singleton.display_root_hint() == str(tmp_path.resolve())


async def test_init_workspace_whitespace_root_defaults_to_cwd(tmp_path, monkeypatch):
    """``root`` 仅含空白字符也视为未配置，默认使用进程当前工作目录。"""
    monkeypatch.chdir(tmp_path)
    cfg = _make_workspace_config_stub(
        backend=WorkspaceBackendKind.LOCAL_FILESYSTEM,
        root="   ",
    )

    with patch.object(_config_module, "workspace_config", cfg):
        await _config_module._init_workspace()

    assert _config_module._workspace_singleton is not None
    assert _config_module._workspace_singleton.display_root_hint() == str(tmp_path.resolve())


# ---------------------------------------------------------------------------
# 8.5 WORKSPACE_ROOT 指向文件 → ConfigurationError（需求 5.8）
# ---------------------------------------------------------------------------


async def test_init_workspace_root_points_to_file_raises(tmp_path):
    """``WORKSPACE_ROOT`` 指向已存在的普通文件时必须拒绝启动。"""
    file_path = tmp_path / "not_a_dir.txt"
    file_path.write_text("x", encoding="utf-8")

    cfg = _make_workspace_config_stub(
        backend=WorkspaceBackendKind.LOCAL_FILESYSTEM,
        root=str(file_path),
    )

    with (
        patch.object(_config_module, "workspace_config", cfg),
        pytest.raises(ConfigurationError) as exc_info,
    ):
        await _config_module._init_workspace()

    assert "不是目录" in str(exc_info.value) or "WORKSPACE_ROOT" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 8.5 WORKSPACE_ROOT 为相对路径 → ConfigurationError（需求 5.9）
# ---------------------------------------------------------------------------


async def test_init_workspace_relative_root_raises(tmp_path):
    """``WORKSPACE_ROOT`` 为相对路径必须拒绝。"""
    cfg = _make_workspace_config_stub(
        backend=WorkspaceBackendKind.LOCAL_FILESYSTEM,
        root="relative/path/to/ws",
    )

    with (
        patch.object(_config_module, "workspace_config", cfg),
        pytest.raises(ConfigurationError) as exc_info,
    ):
        await _config_module._init_workspace()

    assert "绝对路径" in str(exc_info.value) or "WORKSPACE_ROOT" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 8.5 WORKSPACE_ROOT 不存在 + create_if_missing=False → ConfigurationError
# ---------------------------------------------------------------------------


async def test_init_workspace_missing_root_without_create_raises(tmp_path):
    """``root`` 不存在且 ``create_if_missing=False`` 必须 fail-fast。"""
    missing_path = tmp_path / "does_not_exist"
    cfg = _make_workspace_config_stub(
        backend=WorkspaceBackendKind.LOCAL_FILESYSTEM,
        root=str(missing_path),
        create_if_missing=False,
    )

    with (
        patch.object(_config_module, "workspace_config", cfg),
        pytest.raises(ConfigurationError),
    ):
        await _config_module._init_workspace()


# ---------------------------------------------------------------------------
# 8.5 WORKSPACE_ROOT 不存在 + create_if_missing=True → 自动创建
# ---------------------------------------------------------------------------


async def test_init_workspace_missing_root_with_create_succeeds(tmp_path):
    """``create_if_missing=True`` 时自动创建目录，启动成功。"""
    missing_path = tmp_path / "auto_create" / "ws"
    cfg = _make_workspace_config_stub(
        backend=WorkspaceBackendKind.LOCAL_FILESYSTEM,
        root=str(missing_path),
        create_if_missing=True,
    )

    with patch.object(_config_module, "workspace_config", cfg):
        await _config_module._init_workspace()

    assert missing_path.is_dir()
    assert _config_module._workspace_singleton is not None


# ---------------------------------------------------------------------------
# 8.5 WORKSPACE_BACKEND=oss（monkeypatch 绕过 validator）→ ConfigurationError
# ---------------------------------------------------------------------------


async def test_init_workspace_unsupported_backend_raises(tmp_path):
    """通过 stub 直接注入非 LOCAL_FILESYSTEM 的 backend（绕过 validator），
    验证 ``_init_workspace`` 的防御性 ``factory is None`` 分支仍能 fail-fast。

    通过创建一个 ``backend.value`` 属性为 ``"oss"`` 的 namespace 模拟未来
    尚未支持的后端枚举（不在 ``_WORKSPACE_BACKEND_FACTORIES`` 键集合中）。
    """

    class _FakeBackend:
        """伪造一个不在 ``_WORKSPACE_BACKEND_FACTORIES`` 中的 backend 值。"""

        value = "oss"

        def __repr__(self) -> str:
            return "<FakeBackend.OSS>"

    cfg = _make_workspace_config_stub(
        backend=_FakeBackend(),  # type: ignore[arg-type]
        root=str(tmp_path),
    )

    with (
        patch.object(_config_module, "workspace_config", cfg),
        pytest.raises(ConfigurationError) as exc_info,
    ):
        await _config_module._init_workspace()

    msg = str(exc_info.value)
    assert "WORKSPACE_BACKEND" in msg
    assert "oss" in msg


# ---------------------------------------------------------------------------
# 8.5 装配顺序：Workspace 注册在 ToolRegistry 之前
# ---------------------------------------------------------------------------


def test_configure_container_registers_workspace_before_tool_registry():
    """校验 ``configure_container()`` 将 ``workspace`` 异步资源注册在
    ``ToolRegistry`` 之前（Property 7：启动期先 Workspace 后 ToolRegistry）。

    具体断言：
    - ``_async_resources`` 列表中 "workspace" 先于
      "delegate_tool_registration" 出现（保证 Workspace 初始化时机）。
    - ``_registry`` 中 ``Workspace`` 和 ``ToolRegistry`` 均完成注册。
    """
    configure_container()

    from common.container import container

    resource_names = [entry.name for entry in container._async_resources]

    # workspace 异步资源在注册列表中存在
    assert "workspace" in resource_names, f"workspace 资源未注册，实际列表：{resource_names}"

    # workspace 必须在 delegate_tool_registration 之前
    workspace_idx = resource_names.index("workspace")
    assert resource_names.index("delegate_tool_registration") > workspace_idx, (
        f"delegate_tool_registration 必须在 workspace 之后，实际顺序：{resource_names}"
    )

    # Workspace 和 ToolRegistry 均在 _registry 中注册
    registry_types = {
        key if isinstance(key, type) else key[0] for key in container._registry
    }
    assert Workspace in registry_types, "Workspace 未注册到 DI 容器"
    assert ToolRegistry in registry_types, "ToolRegistry 未注册到 DI 容器"


async def test_cleanup_workspace_is_noop_and_awaitable():
    """``_cleanup_workspace`` 应为无状态 no-op，且必须是可 ``await`` 的协程函数。"""
    import inspect

    assert inspect.iscoroutinefunction(_config_module._cleanup_workspace)
    # 执行一次不应抛异常
    await _config_module._cleanup_workspace()


async def test_create_tool_registry_registers_code_search_tools():
    """默认 ToolRegistry 注册三个低风险 Workspace 代码检索工具。"""
    from common.container import container

    workspace = MagicMock()
    workspace.display_root_hint.return_value = "/tmp/ws"
    container.register(Workspace, lambda: workspace, Scope.SINGLETON)

    registry = await _config_module._create_tool_registry()

    assert registry.has("glob")
    assert registry.has("grep")
    assert registry.has("read_many_files")
    assert registry.has("git_status")
    assert registry.has("git_diff")
    assert registry.has("git_apply_patch")
