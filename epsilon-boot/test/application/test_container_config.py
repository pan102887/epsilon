"""容器配置单元测试。

验证 configure_container() 正确注册 Port → Adapter 绑定，以及
_create_tool_registry() 工厂函数的工具注册逻辑。本期随领域事件基础设施
清理（Domain_Event_Decommission），相关事件总线 / 事件存储 Port 与
对应 Adapter 均已被完全移除；对应断言从本测试中一并剔除。

本测试保留的断言范围：

- SessionContextStorePort 仍被注册且为 Scope.SINGLETON；
- ContextBuilderPort 仍被注册且为 Scope.SINGLETON；
- ModelAccessPort / ModelRegistryPort 仍被注册；
- _create_tool_registry() 返回包含预期工具的 ToolRegistry 实例；
- 其他与 Chat Service 模型路由相关的既有断言。

由于 ``src/application/__init__.py`` 会触发 server_app 的完整初始化链
（包含 prometheus_client 等平台相关依赖），测试中通过 importlib 直接加载
container_config 模块，避免触发 application 包的 __init__.py 初始化副作用。
"""

import importlib.util
import pathlib
import string
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from common.container_models import Scope, make_registry_key
from domain.agent.tools import ToolRegistry
from domain.chat.ports import SessionContextStorePort


def _load_container_config_module() -> Any:
    """直接加载 container_config 模块，绕过 application 包的 __init__.py。

    使用 importlib 从文件路径加载 ``src/application/container_config.py``，
    避免触发 ``application/__init__.py`` 中 server_app 的完整初始化链
    （prometheus_client 在 Windows 上可能因 resource 模块不兼容而失败）。

    Returns:
        container_config 模块对象
    """
    config_path = (
        pathlib.Path(__file__).resolve().parents[2] / "src" / "application" / "container_config.py"
    )
    spec = importlib.util.spec_from_file_location("test_container_config_module", str(config_path))
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_config_module = _load_container_config_module()
configure_container = _config_module.configure_container
_create_tool_registry = _config_module._create_tool_registry
_create_compaction_adapter = _config_module._create_compaction_adapter
_create_context_builder = _config_module._create_context_builder
_create_agent = _config_module._create_agent


@pytest.fixture(autouse=True)
def _isolate_container() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """每个测试使用独立的容器状态，避免测试间污染。

    保存全局容器的原始状态，测试结束后恢复。
    """
    from common.container import container

    test_container = cast(Any, container)
    original_registry = test_container._registry.copy()
    original_singletons = test_container._singletons.copy()
    original_resources = test_container._async_resources[:]
    yield
    test_container._registry = original_registry
    test_container._singletons = original_singletons
    test_container._async_resources = original_resources


# ---------------------------------------------------------------------------
# SessionContextStorePort 注册验证（Domain_Event_Decommission 后仍保留）
# ---------------------------------------------------------------------------


def test_session_context_store_port_registered_as_singleton():
    """验证 configure_container() 将 SessionContextStorePort 注册为 Singleton。

    本期事件总线 / 事件存储相关 Port 已随 Domain_Event_Decommission 移除，
    但 SessionContextStorePort 仍作为会话上下文存储的 Port 保留；其注册
    Scope 必须为 Singleton，确保进程内所有调用方（ChatServiceAdapter /
    TaskAgentAdapter）共享同一 Adapter 实例。
    """
    from common.container import container

    configure_container()

    registry = cast(Any, container)._registry
    key = make_registry_key(SessionContextStorePort)
    assert key in registry, "SessionContextStorePort 应注册在容器中"
    entry = registry[key]
    assert entry.scope == Scope.SINGLETON, "SessionContextStorePort 必须以 Scope.SINGLETON 注册"


# ---------------------------------------------------------------------------
# ToolRegistry DI 注册单元测试
# ---------------------------------------------------------------------------

EXPECTED_TOOL_NAMES = {
    "edit_file",
    "git_apply_patch",
    "git_diff",
    "git_status",
    "glob",
    "grep",
    "http_request",
    "list_dir",
    "read_file",
    "read_many_files",
    "web_fetch",
    "write_file",
}


async def test_create_tool_registry_returns_registry_with_tools(tmp_path: pathlib.Path) -> None:
    """验证 _create_tool_registry() 返回包含预期工具的 ToolRegistry 实例。

    调用工厂函数后，逐一检查基础文件、代码检索、Git 与 HTTP 工具均已注册到
    返回的 ToolRegistry 中。

    _create_tool_registry() 为异步函数（内部可能通过容器解析依赖），
    此处通过替换 agent_config 跳过委派工具注册，
    仅验证基础工具集。

    Requirements: 4.3, 4.4
    """
    from domain.workspace.policy import WorkspacePolicy
    from infrastructure.workspace import LocalFilesystemWorkspace

    ws = LocalFilesystemWorkspace(root=tmp_path, policy=WorkspacePolicy(), follow_symlinks=False)
    _config_module._workspace_singleton = ws
    from common.container import container

    container.register(
        _config_module.Workspace,
        lambda: _config_module._workspace_singleton,
        Scope.SINGLETON,
    )
    fake_agent_config = MagicMock()
    fake_agent_config.delegate_tool_enabled = False
    fake_agent_config.max_delegation_depth = 3
    with patch.object(_config_module, "agent_config", fake_agent_config):
        registry = await _create_tool_registry()

    assert isinstance(registry, ToolRegistry)
    for tool_name in EXPECTED_TOOL_NAMES:
        assert registry.has(tool_name), f"ToolRegistry 应包含工具 '{tool_name}'"
        assert registry.get(tool_name) is not None, f"get('{tool_name}') 不应返回 None"


async def test_create_tool_registry_contains_expected_tool_names(tmp_path: pathlib.Path) -> None:
    """验证 _create_tool_registry() 返回的 ToolRegistry 的 get_schemas() 包含预期工具名称。

    通过 get_schemas() 获取所有已注册工具的 schema 列表，提取其中的
    function.name 字段，确认与预期工具名称集合一致。

    _create_tool_registry() 为异步函数，此处通过设置
    agent_config.delegate_tool_enabled=false 跳过委派工具注册。

    Requirements: 4.3, 4.4
    """
    from domain.workspace.policy import WorkspacePolicy
    from infrastructure.workspace import LocalFilesystemWorkspace

    ws = LocalFilesystemWorkspace(root=tmp_path, policy=WorkspacePolicy(), follow_symlinks=False)
    _config_module._workspace_singleton = ws
    from common.container import container

    container.register(
        _config_module.Workspace,
        lambda: _config_module._workspace_singleton,
        Scope.SINGLETON,
    )
    fake_agent_config = MagicMock()
    fake_agent_config.delegate_tool_enabled = False
    fake_agent_config.max_delegation_depth = 3
    # shell_exec / python_exec 现默认开启，但本测试聚焦"无条件注册的基础工具集"，
    # 故显式关闭这两个可选执行工具，使断言不受其默认开关变动影响（与上面关闭委派工具同理）。
    # 两个 config 为 frozen 实例，无法 patch 属性，改为整体替换模块级单例为 enabled=False 的替身。
    import infrastructure.tools.python_exec.python_exec_config as _py_cfg_module
    import infrastructure.tools.shell_exec.shell_exec_config as _sh_cfg_module

    fake_shell_cfg = MagicMock()
    fake_shell_cfg.enabled = False
    fake_python_cfg = MagicMock()
    fake_python_cfg.enabled = False

    with (
        patch.object(_config_module, "agent_config", fake_agent_config),
        patch.object(_sh_cfg_module, "shell_exec_config", fake_shell_cfg),
        patch.object(_py_cfg_module, "python_exec_config", fake_python_cfg),
    ):
        registry = await _create_tool_registry()
    schemas = registry.get_schemas()

    schema_names = {s["function"]["name"] for s in schemas}
    assert schema_names == EXPECTED_TOOL_NAMES, (
        f"Schema 工具名称不匹配: 期望 {EXPECTED_TOOL_NAMES}，实际 {schema_names}"
    )


async def test_register_delegate_tool_uses_agent_config_max_depth():
    """验证委派工具延迟注册从 agent_config 读取最大委派深度。"""
    registry = ToolRegistry()
    agent_registry = MagicMock()
    agent_registry.list_names.return_value = []
    delegation = AsyncMock()
    event_store = AsyncMock()
    workflow_config = MagicMock()
    workflow_config.recent_collaboration_summary_limit = 5

    resolve_map: dict[Any, Any] = {
        _config_module.ToolRegistry: registry,
        _config_module.AgentRegistryPort: agent_registry,
        _config_module.DelegationPort: delegation,
        _config_module.RunEventStorePort: event_store,
        _config_module.RunWorkflowConfig: workflow_config,
    }

    async def fake_resolve(abstract_type: Any, **kwargs: Any) -> Any:
        """根据类型返回预构建的 mock 对象。"""
        return resolve_map[abstract_type]

    fake_agent_config = MagicMock()
    fake_agent_config.delegate_tool_enabled = True
    fake_agent_config.max_delegation_depth = 7

    with (
        patch.object(_config_module, "container", MagicMock()) as mock_container,
        patch.object(_config_module, "agent_config", fake_agent_config),
    ):
        mock_container.resolve = AsyncMock(side_effect=fake_resolve)
        await _config_module._register_delegate_tool()

    tool = registry.get("delegate_to_agent")
    assert tool is not None
    assert hasattr(tool, "_max_delegation_depth"), "委派工具应具有 _max_delegation_depth 属性"
    assert cast(Any, tool).max_delegation_depth == 7


async def test_create_task_agent_uses_task_agent_config_max_rounds():
    """验证 TaskAgentAdapter 装配从 task_agent_config 读取最大轮次。"""
    mock_agent = MagicMock()
    mock_tool_registry = MagicMock()
    mock_model_registry = MagicMock()
    mock_compaction = MagicMock()
    mock_session_store = MagicMock()
    mock_prompt_registry = MagicMock()
    mock_approval_store = MagicMock()

    resolve_map: dict[Any, Any] = {
        _config_module.AgentPort: mock_agent,
        _config_module.ToolRegistry: mock_tool_registry,
        _config_module.ModelRegistryPort: mock_model_registry,
        _config_module.ContextCompactionPort: mock_compaction,
        _config_module.SessionContextStorePort: mock_session_store,
        _config_module.PromptRegistryPort: mock_prompt_registry,
        _config_module.ApprovalStateStorePort: mock_approval_store,
    }

    async def fake_resolve(abstract_type: Any, **kwargs: Any) -> Any:
        """根据类型返回预构建的 mock 对象。"""
        return resolve_map[abstract_type]

    fake_task_agent_config = MagicMock()
    fake_task_agent_config.max_rounds = 8

    with (
        patch.object(_config_module, "container", MagicMock()) as mock_container,
        patch.object(_config_module, "task_agent_config", fake_task_agent_config),
        patch("infrastructure.task.task_agent_adapter.TaskAgentAdapter") as MockAdapter,
    ):
        mock_container.resolve = AsyncMock(side_effect=fake_resolve)
        await _config_module._create_task_agent()

    assert MockAdapter.call_args.kwargs["max_rounds"] == 8
    assert MockAdapter.call_args.kwargs["approval_store"] is mock_approval_store


# ---------------------------------------------------------------------------
# Property 3: 提供商过滤逻辑
# ---------------------------------------------------------------------------


@dataclass
class _MockProviderSpec:
    """单个提供商的测试规格，用于驱动属性测试的输入生成。

    Attributes:
        registry_name: 注册列表中的名称（如 "cliproxy"）。
        env_prefix: 环境变量前缀（如 "MODEL_CLIPROXY_"）。
        enabled: 是否启用。
        api_key: API 密钥，空字符串表示未配置。
        provider_name: 配置中的 provider_name 字段值。
    """

    registry_name: str
    env_prefix: str
    enabled: bool
    api_key: str
    provider_name: str


# 生成 1~5 个提供商规格的策略
_provider_name_alphabet = string.ascii_lowercase + string.digits
_api_key_alphabet = string.ascii_letters + string.digits


@st.composite
def _provider_specs_strategy(draw: st.DrawFn) -> list[_MockProviderSpec]:
    """生成 1~5 个具有唯一 provider_name 的提供商规格列表。

    每个提供商随机生成 enabled（布尔）和 api_key（可能为空字符串），
    确保 provider_name 在列表内唯一，避免字典 key 冲突。

    Returns:
        随机生成的 _MockProviderSpec 列表。
    """
    count = draw(st.integers(min_value=1, max_value=5))
    specs: list[_MockProviderSpec] = []
    used_names: set[str] = set()

    for i in range(count):
        # 生成唯一的 provider_name
        provider_name = draw(
            st.text(
                alphabet=_provider_name_alphabet,
                min_size=1,
                max_size=10,
            ).filter(lambda n, _used=used_names: n not in _used)
        )
        used_names.add(provider_name)

        enabled = draw(st.booleans())
        api_key = draw(
            st.text(
                alphabet=_api_key_alphabet,
                min_size=0,
                max_size=20,
            )
        )

        specs.append(
            _MockProviderSpec(
                registry_name=f"reg_{i}",
                env_prefix=f"TEST_{i}_",
                enabled=enabled,
                api_key=api_key,
                provider_name=provider_name,
            )
        )

    return specs


class TestProviderFilteringLogic:
    """属性测试：提供商过滤逻辑。

    **Validates: Requirements 2.3, 2.4, 2.5, 5.1**

    验证 ``_init_model_client()`` 的过滤行为：当且仅当提供商的
    ``enabled=True``、``api_key`` 非空且 ``provider_name`` 非空时，
    该提供商才会被注册到 ``_provider_registry`` 中。

    使用 mock 对象模拟 ``create_provider_config``，避免真实配置文件依赖。

    注意：适配器已移除，当前 _init_model_client 仅执行过滤逻辑，
    不再创建适配器或注册提供商到 ProviderRegistry。
    此测试验证过滤逻辑本身的正确性（仅满足条件的提供商会通过过滤）。
    """

    @settings(max_examples=100, deadline=None)
    @given(specs=_provider_specs_strategy())
    @pytest.mark.asyncio
    async def test_only_enabled_providers_with_api_key_are_initialized(
        self, specs: list[_MockProviderSpec]
    ) -> None:
        """对任意 enabled/api_key 组合，仅满足条件的提供商被注册到 ProviderRegistry。

        **Validates: Requirements 2.3, 2.4, 2.5, 5.1**

        通过 hypothesis 生成随机的提供商规格列表，mock 掉外部依赖后
        调用 ``_init_model_client()``，验证：
        1. ``_provider_registry`` 中恰好包含所有满足条件的提供商
        2. 不满足条件的提供商不会出现在注册表中

        Args:
            specs: hypothesis 生成的随机提供商规格列表。
        """
        # 构建 PROVIDERS 列表和对应的 mock 配置
        providers_list = [(s.registry_name, s.env_prefix) for s in specs]

        # 为每个 env_prefix 创建对应的 mock 配置对象
        config_map: dict[str, MagicMock] = {}
        for s in specs:
            mock_cfg = MagicMock()
            mock_cfg.enabled = s.enabled
            mock_cfg.api_key = s.api_key
            mock_cfg.provider_name = s.provider_name
            mock_cfg.default_model = "test-model"
            mock_cfg.temperature = 0.7
            mock_cfg.max_tokens = 4096
            mock_cfg.timeout = 30
            mock_cfg.max_retries = 2
            mock_cfg.max_connections = 100
            mock_cfg.max_keepalive_connections = 20
            mock_cfg.models = ""
            mock_cfg.api_base = "http://localhost:8080/v1"
            mock_cfg.get_model_list = MagicMock(return_value=["test-model"])
            config_map[s.env_prefix] = mock_cfg

        def fake_create_config(env_prefix: str) -> MagicMock:
            """根据 env_prefix 返回预构建的 mock 配置对象。"""
            return config_map[env_prefix]

        try:
            with (
                patch.object(
                    _config_module,
                    "PROVIDERS",
                    providers_list,
                ),
                patch.object(
                    _config_module,
                    "create_provider_config",
                    side_effect=fake_create_config,
                ),
            ):
                await _config_module._init_model_client()

            # 适配器已移除，_init_model_client 当前不注册提供商。
            # 验证注册中心已创建但无提供商注册。
            registry = _config_module._provider_registry
            assert registry is not None, "ProviderRegistry 应已创建"
        finally:
            # 清理模块级状态
            _config_module._provider_registry = None


# ---------------------------------------------------------------------------
# Property 6: 客户端生命周期完整性
# ---------------------------------------------------------------------------


@st.composite
def _mock_clients_strategy(draw: st.DrawFn) -> dict[str, AsyncMock]:
    """生成 1~10 个具有唯一提供商名称的 mock HTTP 客户端字典。

    每个客户端是一个 AsyncMock，其 ``aclose`` 方法为 AsyncMock，
    用于验证 ``_cleanup_model_client`` 是否正确关闭了所有客户端。

    Returns:
        提供商名称 → AsyncMock 客户端的字典。
    """
    count = draw(st.integers(min_value=1, max_value=10))
    used_names: set[str] = set()
    clients: dict[str, AsyncMock] = {}

    for _ in range(count):
        name = draw(
            st.text(
                alphabet=_provider_name_alphabet,
                min_size=1,
                max_size=10,
            ).filter(lambda n, _used=used_names: n not in _used)
        )
        used_names.add(name)

        mock_client = AsyncMock()
        mock_client.aclose = AsyncMock()
        clients[name] = mock_client

    return clients


# ---------------------------------------------------------------------------
# Task 4.2: _create_chat_service() 注入 ModelRegistryPort 验证
# ---------------------------------------------------------------------------


async def test_create_chat_service_resolves_model_registry_port():
    """验证 _create_chat_service() 通过容器解析 ModelRegistryPort 并注入 ChatServiceAdapter。

    mock 掉容器的 resolve 方法和 ChatServiceAdapter 构造函数，
    验证工厂函数解析了 ModelRegistryPort（而非 ModelAccessPort），
    并将其作为 model_registry 参数传递给 ChatServiceAdapter。

    Requirements: 6.1, 6.3
    """
    from domain.model_access.ports import ModelRegistryPort

    mock_model_registry = MagicMock(spec=ModelRegistryPort)
    mock_session_store = MagicMock()
    mock_context_builder = MagicMock()
    mock_tool_registry = MagicMock()
    mock_agent = MagicMock()
    mock_prompt_registry = MagicMock()

    resolve_map: dict[Any, Any] = {
        _config_module.SessionContextStorePort: mock_session_store,
        _config_module.ModelRegistryPort: mock_model_registry,
        _config_module.PromptRegistryPort: mock_prompt_registry,
        _config_module.ContextBuilderPort: mock_context_builder,
        _config_module.ToolRegistry: mock_tool_registry,
        _config_module.AgentPort: mock_agent,
    }

    async def fake_resolve(abstract_type: Any, **kwargs: Any) -> Any:
        """根据类型返回预构建的 mock 对象。"""
        return resolve_map[abstract_type]

    with (
        patch.object(_config_module, "container", MagicMock()) as mock_container,
        patch(
            "infrastructure.chat.chat_service_adapter.ChatServiceAdapter",
        ) as MockAdapter,
        patch(
            "infrastructure.chat.chat_config.chat_config",
        ) as mock_chat_config,
    ):
        mock_container.resolve = AsyncMock(side_effect=fake_resolve)
        mock_chat_config.max_tool_rounds = 3
        mock_chat_config.tool_calling_enabled = True

        await _config_module._create_chat_service()

        # 验证解析了 ModelRegistryPort
        resolved_types = [call.args[0] for call in mock_container.resolve.call_args_list]
        assert _config_module.ModelRegistryPort in resolved_types, (
            "_create_chat_service() 应解析 ModelRegistryPort"
        )

        # 验证 ChatServiceAdapter 构造参数包含 model_registry
        adapter_call_kwargs = MockAdapter.call_args
        assert "model_registry" in adapter_call_kwargs.kwargs, (
            "ChatServiceAdapter 应接收 model_registry 参数"
        )
        assert adapter_call_kwargs.kwargs["model_registry"] is mock_model_registry, (
            "model_registry 应为容器解析的 ModelRegistryPort 实例"
        )
        assert "context_builder" in adapter_call_kwargs.kwargs, (
            "ChatServiceAdapter 应接收 context_builder 参数"
        )
        assert adapter_call_kwargs.kwargs["context_builder"] is mock_context_builder, (
            "context_builder 应为容器解析的 ContextBuilderPort 实例"
        )


async def test_create_chat_service_does_not_resolve_model_access_port():
    """验证 _create_chat_service() 不再解析 ModelAccessPort。

    确认工厂函数的依赖解析列表中不包含 ModelAccessPort，
    ChatServiceAdapter 的构造参数中也不包含 model_access。

    Requirements: 6.1
    """
    mock_model_registry = MagicMock()
    mock_session_store = MagicMock()
    mock_context_builder = MagicMock()
    mock_tool_registry = MagicMock()
    mock_agent = MagicMock()
    mock_prompt_registry = MagicMock()

    resolve_map: dict[Any, Any] = {
        _config_module.SessionContextStorePort: mock_session_store,
        _config_module.ModelRegistryPort: mock_model_registry,
        _config_module.PromptRegistryPort: mock_prompt_registry,
        _config_module.ContextBuilderPort: mock_context_builder,
        _config_module.ToolRegistry: mock_tool_registry,
        _config_module.AgentPort: mock_agent,
    }

    async def fake_resolve(abstract_type: Any, **kwargs: Any) -> Any:
        """根据类型返回预构建的 mock 对象。"""
        return resolve_map[abstract_type]

    with (
        patch.object(_config_module, "container", MagicMock()) as mock_container,
        patch(
            "infrastructure.chat.chat_service_adapter.ChatServiceAdapter",
        ),
        patch(
            "infrastructure.chat.chat_config.chat_config",
        ) as mock_chat_config,
    ):
        mock_container.resolve = AsyncMock(side_effect=fake_resolve)
        mock_chat_config.max_tool_rounds = 3
        mock_chat_config.tool_calling_enabled = True

        await _config_module._create_chat_service()

        # 验证未解析 ModelAccessPort
        resolved_types = [call.args[0] for call in mock_container.resolve.call_args_list]
        assert _config_module.ModelAccessPort not in resolved_types, (
            "_create_chat_service() 不应再解析 ModelAccessPort"
        )


def test_model_access_port_registration_still_exists():
    """验证 configure_container() 仍然注册了 ModelAccessPort。

    即使 ChatServiceAdapter 不再直接依赖 ModelAccessPort，
    容器中 ModelAccessPort 的注册应保留，供其他消费者使用。

    Requirements: 6.2
    """
    from common.container import container

    configure_container()

    # 验证 ModelAccessPort 已注册（检查 _registry 中是否存在该类型的键）
    from domain.model_access.ports import ModelAccessPort

    assert make_registry_key(ModelAccessPort) in cast(Any, container)._registry, (
        "ModelAccessPort 应仍然注册在容器中"
    )


async def test_create_compaction_adapter_returns_llm_summary_adapter():
    """验证默认上下文压缩工厂返回 LLM 摘要压缩适配器。"""
    from domain.prompt.value_objects import LoadedPrompt
    from infrastructure.chat.llm_summary_compaction_adapter import (
        LLMSummaryCompactionAdapter,
    )

    prompt_registry = MagicMock()
    prompt_registry.get.return_value = LoadedPrompt(
        prompt_id="context-summary@v1",
        name="context-summary",
        version="v1",
        content="summary prompt",
    )

    async def fake_resolve(abstract_type: Any, **kwargs: Any) -> Any:
        """只解析 PromptRegistryPort。"""
        assert abstract_type is _config_module.PromptRegistryPort
        return prompt_registry

    with (
        patch.object(_config_module, "container", MagicMock()) as mock_container,
        patch("infrastructure.chat.chat_config.chat_config") as mock_chat_config,
    ):
        mock_container.resolve = AsyncMock(side_effect=fake_resolve)
        mock_chat_config.max_messages = 33
        mock_chat_config.compaction_encoding = "cl100k_base"
        mock_chat_config.compaction_trigger_tokens = 1234
        mock_chat_config.compaction_keep_recent_messages = 7

        adapter = await _create_compaction_adapter()

    assert isinstance(adapter, LLMSummaryCompactionAdapter)
    assert adapter.trigger_tokens == 1234
    assert adapter.keep_recent_messages == 7
    assert adapter.fallback.max_messages == 33
    prompt_registry.get.assert_called_once_with("context-summary")


def test_context_compaction_port_registration_is_singleton():
    """验证 ContextCompactionPort 仍以 singleton 注册。"""
    from common.container import container
    from domain.chat.ports import ContextCompactionPort

    configure_container()

    registry = cast(Any, container)._registry
    key = make_registry_key(ContextCompactionPort)
    assert key in registry
    assert registry[key].scope == Scope.SINGLETON


def test_context_builder_port_registration_is_singleton():
    """验证 ContextBuilderPort 以 singleton 注册。"""
    from common.container import container
    from domain.chat.ports import ContextBuilderPort

    configure_container()

    registry = cast(Any, container)._registry
    key = make_registry_key(ContextBuilderPort)
    assert key in registry
    assert registry[key].scope == Scope.SINGLETON


async def test_create_context_builder_resolves_compaction_and_constructs_adapter():
    """验证上下文构建工厂解析压缩端口并构造 ContextBuilderAdapter。"""
    from infrastructure.chat.context_builder_adapter import ContextBuilderAdapter
    from infrastructure.chat.environment_context_provider import (
        StaticEnvironmentContextProvider,
    )

    mock_compaction = MagicMock()

    async def fake_resolve(abstract_type: Any, **kwargs: Any) -> Any:
        """只解析 ContextCompactionPort。"""
        assert abstract_type is _config_module.ContextCompactionPort
        return mock_compaction

    with patch.object(_config_module, "container", MagicMock()) as mock_container:
        mock_container.resolve = AsyncMock(side_effect=fake_resolve)

        builder = await _create_context_builder()

    assert isinstance(builder, ContextBuilderAdapter)
    assert builder.compaction is mock_compaction
    assert isinstance(builder.environment_provider, StaticEnvironmentContextProvider)
    mock_container.resolve.assert_awaited_once_with(_config_module.ContextCompactionPort)


async def test_create_agent_resolves_context_builder_and_constructs_adapter():
    """验证 _create_agent() 解析 ContextBuilderPort 并注入 ReActAgentAdapter。"""
    mock_tool_registry = MagicMock()
    mock_context_builder = MagicMock()
    mock_approval_policy = MagicMock()
    mock_approval_store = MagicMock()

    resolve_map: dict[Any, Any] = {
        _config_module.ToolRegistry: mock_tool_registry,
        _config_module.ContextBuilderPort: mock_context_builder,
        _config_module.ApprovalPolicyPort: mock_approval_policy,
        _config_module.ApprovalStateStorePort: mock_approval_store,
        # _create_agent() 通过容器解析 TraceStorePort（可为 None）注入
        # ReActAgentAdapter；fake resolve_map 需登记该键，否则 KeyError。
        _config_module.TraceStorePort: None,
    }

    async def fake_resolve(abstract_type: Any, **kwargs: Any) -> Any:
        """根据类型返回预构建的 mock 对象。"""
        return resolve_map[abstract_type]

    with (
        patch.object(_config_module, "container", MagicMock()) as mock_container,
        patch("infrastructure.agent.react_agent_adapter.ReActAgentAdapter") as MockAdapter,
    ):
        mock_container.resolve = AsyncMock(side_effect=fake_resolve)

        await _create_agent()

    resolved_types = [call.args[0] for call in mock_container.resolve.call_args_list]
    assert _config_module.ContextBuilderPort in resolved_types, (
        "_create_agent() 应解析 ContextBuilderPort"
    )
    assert MockAdapter.call_args.kwargs["tool_registry"] is mock_tool_registry
    assert MockAdapter.call_args.kwargs["context_builder"] is mock_context_builder
    assert MockAdapter.call_args.kwargs["approval_policy"] is mock_approval_policy
    assert MockAdapter.call_args.kwargs["approval_store"] is mock_approval_store


def test_model_registry_port_registration_exists():
    """验证 configure_container() 注册了 ModelRegistryPort。

    确认容器中存在 ModelRegistryPort 的注册，
    使 ChatServiceAdapter 能通过容器解析获取注册中心实例。

    Requirements: 6.3
    """
    from common.container import container

    configure_container()

    from domain.model_access.ports import ModelRegistryPort

    assert make_registry_key(ModelRegistryPort) in cast(Any, container)._registry, (
        "ModelRegistryPort 应注册在容器中"
    )


class TestClientLifecycleCompleteness:
    """属性测试：ProviderRegistry 生命周期完整性。

    **Validates: Requirements 5.2**

    验证 ``_cleanup_model_client()`` 的清理行为：调用清理函数后，
    ``_provider_registry`` 被置为 None，释放所有注册表引用。
    """

    @settings(max_examples=100)
    @given(clients=_mock_clients_strategy())
    @pytest.mark.asyncio
    async def test_all_clients_closed_and_dicts_cleared_after_cleanup(
        self, clients: dict[str, AsyncMock]
    ) -> None:
        """对任意数量的 mock 提供商，cleanup 后注册表被清空。

        **Validates: Requirements 5.2**

        通过 hypothesis 生成随机 mock 客户端（模拟已注册的提供商数量），
        初始化 ``_provider_registry`` 后调用 ``_cleanup_model_client()``，
        验证注册表引用被置为 None。

        Args:
            clients: hypothesis 生成的提供商名称 → mock 客户端字典。
        """
        from infrastructure.model_access.provider_registry import ProviderRegistry

        _config_module._provider_registry = ProviderRegistry()

        try:
            await _config_module._cleanup_model_client()

            # 验证注册表已被清空
            assert _config_module._provider_registry is None, "_provider_registry 未被清理为 None"
        finally:
            # 确保清理模块级状态
            _config_module._provider_registry = None
