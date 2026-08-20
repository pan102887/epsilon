"""容器配置模块。

负责将所有 Port → Adapter 绑定和异步资源生命周期注册到全局容器中。
在 FastAPI lifespan 启动前调用 ``configure_container()`` 完成注册。

此模块属于 Application 层，负责依赖的组装编排。

模型接入初始化流程：
    1. 遍历 PROVIDERS 列表，为每个启用的提供商读取配置
    2. 创建 OpenAICompatibleAdapter 适配器实例
    3. 通过 ProviderRegistry 注册提供商，使用配置文件中的模型列表完成注册
    4. 将 ProviderRegistry 注册为 ModelRegistryPort，供 /v1/models API 使用
"""

import logging
import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from infrastructure.storage.local_file_tier_resolver import LocalFileTierResolver

from application.container.agent import register_agent_components
from application.container.chat import register_chat_components
from application.container.run import register_run_components
from application.container.storage import register_storage_components
from application.container.task import register_task_components
from application.container.tools import register_tool_components
from application.run.run_application_service import RunApplicationService
from application.run.run_approval_resumer import RunApprovalResumer
from application.run.run_checkpoint_recovery_service import RunRecoveryService
from application.run.run_execution_coordinator import RunExecutionCoordinator
from application.run.run_guardrail_recorder import RunGuardrailRecorder
from application.run.workflow_orchestrator import WorkflowRunOrchestrator
from common.configuration import ConfigurationError
from common.configuration.id_validation_config import id_validation_config
from common.container import container
from common.container_models import Scope
from domain.agent.ports import (
    AgentGuardrailPolicyPort,
    AgentPort,
    AgentRegistryPort,
    ApprovalPolicyPort,
    ApprovalStateStorePort,
    ArtifactStorePort,
    DelegationPort,
    RunGuardrailRecorderPort,
    TraceStorePort,
)
from domain.agent.tools import ToolRegistry
from domain.chat.context import configure_history_restore_strategy
from domain.chat.ports import (
    ChatServicePort,
    ContextBuilderPort,
    ContextCompactionPort,
    SessionContextStorePort,
    SessionIndexPort,
)
from domain.health.aggregator import ReadinessAggregator
from domain.model_access.ports import ModelAccessPort, ModelRegistryPort
from domain.prompt.ports import PromptRegistryPort
from domain.run.ports import (
    RunCheckpointStorePort,
    RunEventStorePort,
    RunObservationStorePort,
    RunStorePort,
    WorkflowRegistryPort,
    WorkflowSelectorPort,
)
from domain.task.ports import TaskAgentPort
from domain.workspace.policy import WorkspacePolicy
from domain.workspace.ports import Workspace
from domain.workspace.value_objects import WorkspaceBackendKind
from infrastructure.agent.agent_config import agent_config
from infrastructure.gateway.gateway_client import GatewayClient
from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter
from infrastructure.model_access.provider_config import create_provider_config
from infrastructure.model_access.provider_registry import ProviderRegistry
from infrastructure.model_access.router_config import router_config
from infrastructure.persistence.local_file.atomic_writer import TempFileAtomicWriter
from infrastructure.persistence.local_file.config import (
    SessionStoreBackendKind,
    local_persistence_config,
    session_store_config,
)
from infrastructure.persistence.local_file.file_lock import LockFactory
from infrastructure.persistence.local_file.path_policy import (
    CrossPlatformPathPolicy,
    PathPolicyViolation,
)
from infrastructure.persistence.local_file.tmp_file_sweeper import TmpFileSweeper
from infrastructure.prompt.exceptions import ConflictingLegacyPromptConfigError
from infrastructure.redis.redis_config import redis_config
from infrastructure.run.run_config import run_runtime_config
from infrastructure.run.run_serialization_adapters import (
    GuardrailSerializerAdapter,
    SegmentSerializerAdapter,
    WorkflowSerializerAdapter,
)
from infrastructure.run.run_worker_manager import RunWorkerManager
from infrastructure.run.static_workflow_registry_adapter import (
    StaticWorkflowRegistryAdapter,
)
from infrastructure.run.static_workflow_selector import StaticWorkflowSelector
from infrastructure.run.workflow_config import RunWorkflowConfig, run_workflow_config
from infrastructure.task.task_config import task_agent_config
from infrastructure.telemetry.otel_setup import init_telemetry, shutdown_telemetry
from infrastructure.workspace import LocalFilesystemWorkspace, WorkspaceConfig
from infrastructure.workspace.workspace_config import workspace_config

logger = logging.getLogger(__name__)

# 提供商注册列表：(日志标识名, env_prefix)
# 启用状态、provider_name、models 等均由对应 env_prefix 的配置决定
PROVIDERS: list[tuple[str, str]] = [
    ("cliproxy", "MODEL_CLIPROXY_"),
    ("zhipu", "MODEL_ZHIPU_"),
    ("deepseek", "MODEL_DEEPSEEK_"),
    ("qwen", "MODEL_QWEN_"),
    ("openai", "MODEL_OPENAI_"),
]

# 模块级变量，在 async resource init 中赋值
_redis_client: Any = None
_gateway_client: GatewayClient | None = None
_provider_registry: ProviderRegistry | None = None
_workspace_singleton: Workspace | None = None
_run_store_adapter: Any = None
_run_checkpoint_store_adapter: Any = None
_run_worker_manager: RunWorkerManager | None = None

# 本地文件持久化相关模块级单例（由 _init_local_persistence 赋值）
#
# 需求 2.补.1：**禁止**新增 _ttl_reaper / _ttl_reaper 任务；本期会话无 TTL，
# 无任何后台回收组件。
_local_persistence_root: Path | None = None
_atomic_writer: TempFileAtomicWriter | None = None
_path_policy: CrossPlatformPathPolicy | None = None
_lock_factory: LockFactory | None = None

# StorageTier → 本地目录解析器（惰性缓存单例）。
#
# trace store / artifact store 及会话主状态默认路径迁移共享同一 resolver 实例，
# 保证 project-hash 分区键一致。测试可通过
# monkeypatch.setattr("application.container_config._tier_resolver", None) 重置。
_tier_resolver: "LocalFileTierResolver | None" = None

# ── Prompt Version Registry ──
# parents[2] = epsilon-boot/（container_config.py 位于 src/application/）
# 测试通过 monkeypatch.setattr("application.container_config._PROMPT_ASSET_ROOT", ...) 覆盖
_PROMPT_ASSET_ROOT: Path = Path(__file__).resolve().parents[2] / "prompts"


def _check_legacy_prompt_conflict() -> None:
    """检测遗留 CHAT_SYSTEM_PROMPT 配置冲突，存在则 fail-fast。

    迁移期间，若环境变量或 config.properties 中仍存在 ``CHAT_SYSTEM_PROMPT``
    键，说明运维尚未完成迁移，抛出 ``ConflictingLegacyPromptConfigError``
    阻止启动，并给出三步迁移说明。

    需求 8.2 / 8.5 / 8.6。
    """
    from common.configuration.configuration_utils import _PROPERTIES_FILE, _parse_properties_file

    legacy_keys: list[str] = []

    # 检查环境变量
    if os.getenv("CHAT_SYSTEM_PROMPT"):
        legacy_keys.append("CHAT_SYSTEM_PROMPT(env)")

    # 检查 config.properties
    props = _parse_properties_file(_PROPERTIES_FILE)
    if "CHAT_SYSTEM_PROMPT" in props or "chat.system.prompt" in props:
        legacy_keys.append("CHAT_SYSTEM_PROMPT(config.properties)")

    if legacy_keys:
        raise ConflictingLegacyPromptConfigError(
            f"检测到遗留配置冲突（{', '.join(legacy_keys)}），请完成以下三步迁移：\n"
            "1. 删除 config.properties 中的 CHAT_SYSTEM_PROMPT 键\n"
            "2. 删除环境变量 CHAT_SYSTEM_PROMPT\n"
            "3. 将自定义 system prompt 内容写入 prompts/chat-default/v<N>.md，"
            "并更新 PROMPT_CHAT_DEFAULT_VERSION=v<N>"
        )


def _create_prompt_registry() -> PromptRegistryPort:
    """创建 Prompt 注册表适配器实例。

    启动期从文件系统加载所有配置引用的 Prompt 资产，构造
    ``FilesystemPromptRegistryAdapter``。任何加载失败均抛出
    ``ConfigurationError`` 子类，触发容器 fail-fast。

    可能抛出的异常：
    - ``PromptAssetDirectoryMissingError``
    - ``PromptAssetFileMissingError``
    - ``PromptAssetEncodingError``
    - ``EmptyPromptAssetError``
    - ``PromptNotConfiguredError``

    Returns:
        实现 ``PromptRegistryPort`` 的适配器实例。
    """
    from infrastructure.prompt.filesystem_prompt_registry_adapter import (
        FilesystemPromptRegistryAdapter,
    )
    from infrastructure.prompt.prompt_version_config import prompt_version_config

    return FilesystemPromptRegistryAdapter(
        root=_PROMPT_ASSET_ROOT,
        version_config=prompt_version_config,
    )


def _create_local_filesystem_workspace(cfg: WorkspaceConfig) -> Workspace:
    """构造 ``LocalFilesystemWorkspace`` 并完成启动期 7 步防御校验。

    本函数在容器启动阶段被 ``_init_workspace`` 调用，任何一步失败都通过抛出
    ``ConfigurationError`` 触发 fail-fast；调用方不得静默降级。

    校验链（与 tasks.md 8.2 / 需求 5.4-5.9 一一对应）：

    1. ``cfg.root`` 为空 → 默认使用进程当前工作目录；
    2. 相对路径（非 ``Path.is_absolute``）→ ``ConfigurationError``；
    3. 路径不存在且 ``create_if_missing=False`` → ``ConfigurationError``；
    4. 路径不存在且 ``create_if_missing=True`` → ``Path.mkdir(parents=True, exist_ok=True)``；
    5. 存在但不是目录 → ``ConfigurationError``；
    6. ``os.access(root, os.R_OK | os.W_OK)`` 失败 → ``ConfigurationError`` 中指明缺失位；
    7. 成功：构造 ``WorkspacePolicy()`` 与 ``LocalFilesystemWorkspace``。

    Args:
        cfg: 已由 pydantic 校验通过的 ``WorkspaceConfig`` 实例。

    Returns:
        已就绪的 ``LocalFilesystemWorkspace`` 实例。

    Raises:
        ConfigurationError: 任一启动期校验失败；错误消息为中文可读文案，
            不得包含凭证、token 等敏感信息。
    """
    # 1. 空路径 → 默认使用进程当前工作目录
    root_str = (cfg.root or "").strip()
    if not root_str:
        root = Path.cwd().resolve()
        logger.info(
            "WORKSPACE_ROOT 未配置，默认使用当前工作目录作为 Workspace 根：%s",
            root,
        )
    else:
        root = Path(root_str)

    # 2. 显式相对路径拒绝；空路径已在上一步转换为 cwd 绝对路径
    if not root.is_absolute():
        raise ConfigurationError(f"WORKSPACE_ROOT 必须为宿主绝对路径，实际值：{cfg.root}")

    # 3 / 4. 不存在处理
    if not root.exists():
        if not cfg.create_if_missing:
            raise ConfigurationError(
                f"WORKSPACE_ROOT 指向的目录不存在且 WORKSPACE_CREATE_IF_MISSING=false，"
                f"服务拒绝启动：{cfg.root}"
            )
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigurationError(
                f"WORKSPACE_ROOT 自动创建失败（{type(exc).__name__}）：{cfg.root}"
            ) from exc

    # 5. 存在但不是目录
    if not root.is_dir():
        raise ConfigurationError(
            f"WORKSPACE_ROOT 已存在但不是目录（文件、socket 或设备等），服务拒绝启动：{cfg.root}"
        )

    # 6. 可读写权限校验
    can_read = os.access(root, os.R_OK)
    can_write = os.access(root, os.W_OK)
    if not (can_read and can_write):
        missing: list[str] = []
        if not can_read:
            missing.append("R")
        if not can_write:
            missing.append("W")
        raise ConfigurationError(f"WORKSPACE_ROOT 缺失 {'/'.join(missing)} 权限，服务拒绝启动")

    # 7. 构造领域对象 + 适配器
    policy = WorkspacePolicy()
    return LocalFilesystemWorkspace(
        root=root.resolve(),
        follow_symlinks=cfg.follow_symlinks,
        policy=policy,
    )


# ── Workspace 后端分发表（需求 5.2 / 5.4） ──
#
# 本期仅注册 LOCAL_FILESYSTEM 一条；未来新增 OSS 后端只需在此追加键值对。
# WorkspaceConfig 的 pydantic @model_validator 已在配置加载阶段拒绝非本期
# 支持的 backend；此处的 KeyError 分支仅作启动期的 defense-in-depth，
# 覆盖有人绕过 validator（如测试 monkeypatch）的异常路径。
_WORKSPACE_BACKEND_FACTORIES: dict[WorkspaceBackendKind, Callable[[WorkspaceConfig], Workspace]] = {
    WorkspaceBackendKind.LOCAL_FILESYSTEM: _create_local_filesystem_workspace,
}


async def _init_workspace() -> None:
    """启动期初始化 Workspace 单例。

    读取 ``workspace_config`` 全局配置，按 ``backend`` 分发到对应工厂；
    工厂返回的实例被赋值给模块级 ``_workspace_singleton``，后续
    ``container.register(Workspace, lambda: _workspace_singleton, ...)``
    的工厂闭包会返回此引用。

    需求 5.2 / 5.4：``backend`` 不在分发表中时 fail-fast。

    Raises:
        ConfigurationError: ``backend`` 不在 ``_WORKSPACE_BACKEND_FACTORIES``
            中（防御性分支），或工厂校验失败。
    """
    global _workspace_singleton

    factory = _WORKSPACE_BACKEND_FACTORIES.get(workspace_config.backend)
    if factory is None:
        raise ConfigurationError(f"不支持的 WORKSPACE_BACKEND 值：{workspace_config.backend.value}")

    _workspace_singleton = factory(workspace_config)
    capabilities = _workspace_singleton.capabilities()
    logger.info(
        "Workspace 初始化完成：backend=%s，local_materialization=%s",
        workspace_config.backend.value,
        capabilities.local_materialization,
    )


async def _cleanup_workspace() -> None:
    """Workspace 无需异步清理；保留空清理钩子以对齐 ``register_async_resource`` 的约定。"""


def _validate_exec_working_dir(
    *,
    ws: Workspace,
    config_name: str,
    working_dir: str | None,
) -> None:
    """对 exec 类工具的 ``working_dir`` 做启动期二次校验（需求 10.3）。

    当 ``SHELL_EXEC_WORKING_DIR`` / ``PYTHON_EXEC_WORKING_DIR`` 非空时，
    调用 ``ws.resolve_path(working_dir)`` 做一次纯归一化校验；失败翻译为
    :class:`ConfigurationError`，触发容器 ``start()`` 的 fail-fast 回滚。

    Args:
        ws: 启动期已就绪的 :class:`Workspace` 实例。
        config_name: 配置项名称（``SHELL_EXEC_WORKING_DIR`` 或
            ``PYTHON_EXEC_WORKING_DIR``），用于错误消息。
        working_dir: 配置值；空串 / ``None`` 表示使用默认（跳过校验）。

    Raises:
        ConfigurationError: ``working_dir`` 非空且指向工作区外时抛出，
            错误消息中文可读，含具体配置项名 + "设置到工作区内" + "留空使用默认"
            三要素。
    """
    # 导入在函数内：避免模块加载期的循环依赖风险
    from domain.workspace.exceptions import WorkspaceConfinementViolation

    # 空 / None 视为"使用默认"，跳过二次校验
    if working_dir is None or not working_dir.strip():
        return

    stripped = working_dir.strip()

    # 宿主机绝对路径判定（需求 10.3）：``SHELL_EXEC_WORKING_DIR`` /
    # ``PYTHON_EXEC_WORKING_DIR`` 在运维侧通常配的是宿主机绝对路径（如
    # ``/etc``、``/opt/data``、``C:\\Windows``）。若 ``ws`` 暴露
    # ``display_root_hint()``（本期仅 ``LocalFilesystemWorkspace`` 有），
    # 则将 ``working_dir`` 当作宿主绝对路径做前缀比对——若不落在
    # WORKSPACE_ROOT 目录树下，直接 fail-fast；若落在其内，继续走
    # ``resolve_path`` 做字符级校验。这一步补齐了 "``/etc`` 被
    # ``WorkspacePolicy.resolve`` 当作工作区内 ``/etc`` 子目录" 的漏判
    # （2026-05-11 pytest 回归缺陷 B）。
    #
    # Windows 兼容：``os.path.isabs`` 在 Windows 上对形如 ``/etc`` 的
    # POSIX 风格根路径返回 ``False``（Windows 绝对路径须带盘符或为 UNC），
    # 会遗漏这类跨平台测试用例。此处额外识别以 ``/`` 起始的路径为"宿主
    # 绝对路径候选"，保证在 Windows CI 上也能走前缀比对分支。
    is_host_absolute = os.path.isabs(stripped) or stripped.startswith("/")
    if is_host_absolute and hasattr(ws, "display_root_hint"):
        try:
            root_hint = str(ws.display_root_hint())
        except Exception:  # pragma: no cover - 防御性：hint 异常时退化到旧逻辑
            root_hint = ""
        if root_hint:
            abs_wd = os.path.abspath(stripped)
            abs_root = os.path.abspath(root_hint)
            if abs_wd != abs_root and not abs_wd.startswith(abs_root + os.sep):
                raise ConfigurationError(
                    f"{config_name}={working_dir} 位于工作区外，"
                    "请将 SHELL_EXEC_WORKING_DIR / PYTHON_EXEC_WORKING_DIR 设置到工作区内，"
                    "或留空使用默认"
                )

    try:
        ws.resolve_path(stripped)
    except WorkspaceConfinementViolation as exc:
        raise ConfigurationError(
            f"{config_name}={working_dir} 位于工作区外，"
            "请将 SHELL_EXEC_WORKING_DIR / PYTHON_EXEC_WORKING_DIR 设置到工作区内，"
            "或留空使用默认"
        ) from exc


async def _init_model_client() -> None:
    """初始化模型提供商注册中心。

    流程：
    1. 创建 ProviderRegistry（统一供应商注册中心）
    2. 遍历 PROVIDERS，为每个启用的提供商读取配置
    3. 创建 OpenAICompatibleAdapter 适配器实例
    4. 通过 ProviderRegistry 注册提供商及其支持的模型列表
    """
    global _provider_registry

    _provider_registry = ProviderRegistry(
        default_model=router_config.default_model,
    )

    for registry_name, env_prefix in PROVIDERS:
        config = create_provider_config(env_prefix)

        if not config.enabled:
            logger.debug("提供商 %s 未启用，跳过", registry_name)
            continue

        if not config.api_key:
            logger.warning(
                "提供商 %s 已启用但 api_key 为空，跳过初始化",
                registry_name,
            )
            continue

        if not config.provider_name:
            logger.warning(
                "提供商 %s 的 provider_name 未配置，跳过初始化",
                registry_name,
            )
            continue

        retry_attempts = int(os.environ.get("LLM_RETRY_ATTEMPTS", "1"))
        from infrastructure.chat.chat_config import chat_config as _chat_cfg

        adapter = OpenAICompatibleAdapter(
            config,
            retry_attempts=retry_attempts,
            tokenizer_encoding=_chat_cfg.compaction_encoding,
        )
        models = config.get_model_list()
        registered = _provider_registry.register_provider(
            provider_name=config.provider_name,
            adapter=adapter,
            models=models,
        )
        if registered:
            logger.info(
                "提供商 %s 注册成功，模型列表: %s",
                registry_name,
                models,
            )
        else:
            logger.warning(
                "提供商 %s 注册失败（模型列表为空）",
                registry_name,
            )


async def _cleanup_model_client() -> None:
    """清理模型注册中心，释放资源。"""
    global _provider_registry
    _provider_registry = None
    logger.info("模型注册中心已清理")


async def _init_redis() -> None:
    """初始化 Redis 连接并验证连通性。"""
    import redis.asyncio as aioredis

    global _redis_client
    if redis_config.password:
        url = (
            f"redis://:{redis_config.password}"
            f"@{redis_config.host}:{redis_config.port}/{redis_config.db}"
        )
    else:
        url = f"redis://{redis_config.host}:{redis_config.port}/{redis_config.db}"
    _redis_client = aioredis.from_url(url, decode_responses=True)
    await _redis_client.ping()
    logger.info("Redis client initialized")


async def _cleanup_redis() -> None:
    """关闭 Redis 连接。"""
    if _redis_client:
        await _redis_client.aclose()
        logger.info("Redis client closed")


async def _init_gateway() -> None:
    """初始化网关 HTTP 客户端并启动连接池。"""
    global _gateway_client
    _gateway_client = GatewayClient()
    await _gateway_client.start()


async def _cleanup_gateway() -> None:
    """关闭网关 HTTP 客户端连接池。"""
    if _gateway_client:
        await _gateway_client.stop()


def _validate_local_persistence_root(cfg) -> Path:  # type: ignore[no-untyped-def]
    """启动期校验 ``LOCAL_PERSISTENCE_ROOT``（需求 5.4-5.10、4.4）。

    模仿 ``_create_local_filesystem_workspace`` 的 7 步校验风格，任一步骤
    失败抛出 ``ConfigurationError``，由容器 ``start()`` 的 fail-fast 语义
    触发回滚。

    步骤：

    1. ``cfg.root`` 显式置空（含空白串）→ ``ConfigurationError``；
    2. ``Path(cfg.root).resolve()`` 规范化为绝对路径；
    3. 与有效 ``WORKSPACE_ROOT`` 的冲突检测（需求 5.10）：规范化后不得
       共用或存在父子包含关系；``WORKSPACE_ROOT`` 为空时按进程 cwd 处理。
    4. 路径不存在且 ``create_if_missing=False`` → ``ConfigurationError``；
    5. 路径不存在且 ``create_if_missing=True`` → ``mkdir(parents=True, exist_ok=True)``；
    6. 存在但不是目录 → ``ConfigurationError``；
    7. ``os.access(root, R_OK|W_OK)`` 校验，缺失位列入消息；
    8. ``CrossPlatformPathPolicy.check_absolute_path_length`` 提前拦截
       Windows 260 字符上限；

    Args:
        cfg: ``LocalPersistenceConfig`` 实例（或 ``ConfigProxy`` 对其代理）。

    Returns:
        规范化后的绝对路径。

    Raises:
        ConfigurationError: 任一校验失败；错误消息中文可读、不含凭证。
    """
    # 1. 空路径 → fail-fast
    if not cfg.root or not cfg.root.strip():
        raise ConfigurationError("LOCAL_PERSISTENCE_ROOT 为空，服务拒绝启动")

    # 2. 规范化为绝对路径
    lp_root = Path(cfg.root).resolve()

    # 3. workspace 冲突检测（需求 5.10）。WORKSPACE_ROOT 为空时，Workspace
    # 会默认落到进程 cwd，因此这里也用 cwd 做等价冲突检测，避免本地会话
    # 文件被暴露给受控工具。该校验必须在 mkdir 前执行，避免失败配置留下
    # workspace 内的本地持久化目录。
    ws_root_str = (workspace_config.root or "").strip()
    if ws_root_str:
        ws_root = Path(ws_root_str)
        ws_root_resolved = ws_root.resolve() if ws_root.is_absolute() else None
    else:
        ws_root_resolved = Path.cwd().resolve()

    if ws_root_resolved is not None and (
        lp_root == ws_root_resolved
        or lp_root.is_relative_to(ws_root_resolved)
        or ws_root_resolved.is_relative_to(lp_root)
    ):
        raise ConfigurationError(
            "LOCAL_PERSISTENCE_ROOT 不得与 WORKSPACE_ROOT 共用或相互包含："
            f"lp_root={lp_root}, workspace_root={ws_root_resolved}"
        )

    # 4 / 5. 不存在处理
    if not lp_root.exists():
        if not cfg.create_if_missing:
            raise ConfigurationError(
                f"LOCAL_PERSISTENCE_ROOT 指向的目录不存在且 "
                f"LOCAL_PERSISTENCE_CREATE_IF_MISSING=false，"
                f"服务拒绝启动：{lp_root}"
            )
        try:
            lp_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ConfigurationError(
                f"LOCAL_PERSISTENCE_ROOT 自动创建失败（{type(exc).__name__}）：{lp_root}"
            ) from exc

    # 6. 存在但不是目录
    if not lp_root.is_dir():
        raise ConfigurationError(
            f"LOCAL_PERSISTENCE_ROOT 已存在但不是目录（文件、socket 或设备等），"
            f"服务拒绝启动：{lp_root}"
        )

    # 7. 可读写权限校验
    can_read = os.access(lp_root, os.R_OK)
    can_write = os.access(lp_root, os.W_OK)
    if not (can_read and can_write):
        missing: list[str] = []
        if not can_read:
            missing.append("R")
        if not can_write:
            missing.append("W")
        raise ConfigurationError(
            f"LOCAL_PERSISTENCE_ROOT 缺失 {'/'.join(missing)} 权限，服务拒绝启动：{lp_root}"
        )

    # 8. Windows 长路径预拦截
    try:
        CrossPlatformPathPolicy().check_absolute_path_length(lp_root)
    except PathPolicyViolation as exc:
        raise ConfigurationError(str(exc)) from exc

    return lp_root


# 会话主状态 USER tier 默认迁移的旧默认相对路径（决策 1a）。
#
# 迁移前 ``LocalPersistenceConfig.root`` 默认 ``"../.local_persistence/epsilon-boot"``；
# 空串启用 USER tier 默认迁移后，据此旧相对路径规范化定位旧数据目录，
# 用于首次启动一次性提示（仅提示，不自动搬运）。
_LEGACY_LOCAL_PERSISTENCE_ROOT = "../.local_persistence/epsilon-boot"


class _ResolvedLocalPersistenceConfig:
    """``LocalPersistenceConfig`` 的生效视图，仅覆盖 ``root`` 字段。

    当 ``LOCAL_PERSISTENCE_ROOT`` 未显式配置（空串）且会话后端非 redis 时，
    ``_resolve_local_persistence_config`` 用 USER tier 默认路径填充 ``root``，
    其余字段（``create_if_missing`` 等）透传底层配置。这样
    ``_validate_local_persistence_root`` 的全部启动校验与安全禁令对迁移后的
    默认路径原样生效、不弱化（ADR-0006 决策 4、Property 8）。

    Attributes:
        root: 生效的本地持久化根目录（可能为迁移后的 USER tier 默认路径）。
    """

    def __init__(self, base: Any, root: str) -> None:
        self._base = base
        self.root = root

    def __getattr__(self, name: str) -> Any:
        """未覆盖的字段透传底层配置对象。"""
        return getattr(self._base, name)


def _resolve_local_persistence_config() -> Any:
    """解析会话主状态生效配置，按需迁移到 USER tier 默认路径。

    分流规则（需求 2.2、2A.1、2A.3、8.5、8.6；ADR-0006 决策 1a/5）：

    - ``LOCAL_PERSISTENCE_ROOT`` 显式配置（strip 后非空）→ 尊重原值，不迁移；
    - ``SESSION_STORE_BACKEND=redis`` → 会话主状态走 redis，不迁移；
    - 其余（root 为空且后端非 redis）→ 迁移到
      ``_create_tier_resolver().user_persistence_root()``（USER tier 默认，
      ``~/.epsilon/persistence/<project-hash>/``），并触发首次启动一次性提示。

    Returns:
        生效配置对象：迁移场景返回 ``_ResolvedLocalPersistenceConfig``（覆盖
        ``root``），否则原样返回 ``local_persistence_config``。
    """
    if session_store_config.backend == SessionStoreBackendKind.REDIS:
        return local_persistence_config

    raw_root = (local_persistence_config.root or "").strip()
    if raw_root:
        # 显式配置优先：尊重原值，不迁移（Property 8）。
        return local_persistence_config

    user_default_root = _create_tier_resolver().user_persistence_root()
    _emit_default_migration_hint(user_default_root)
    return _ResolvedLocalPersistenceConfig(
        base=local_persistence_config,
        root=str(user_default_root),
    )


def _emit_default_migration_hint(user_default_root: Path) -> None:
    """检测旧默认目录并输出首次启动一次性迁移提示（不自动搬运数据）。

    仅当**旧默认目录 ``../.local_persistence/epsilon-boot`` 存在且非空**、
    **而新默认目录不存在或为空**时，``logger.info`` 输出中文提示（含旧路径、
    新路径、手动迁移 / 显式设置 ``LOCAL_PERSISTENCE_ROOT`` 保留旧路径两个选项）。
    **不自动搬运数据**以免误操作；检测过程任何异常静默跳过、不影响启动
    （ADR-0006 决策 5、Property 8）。

    Args:
        user_default_root: 已解析出的 USER tier 会话主状态默认根目录。
    """
    try:
        legacy_root = Path(_LEGACY_LOCAL_PERSISTENCE_ROOT).resolve()
        legacy_nonempty = legacy_root.is_dir() and any(legacy_root.iterdir())
        new_empty = (not user_default_root.exists()) or not any(user_default_root.iterdir())
        if legacy_nonempty and new_empty:
            logger.info(
                "检测到旧会话数据目录 %s（非空），本期默认已迁移至 USER tier %s。"
                "旧数据不会自动搬运，请二选一："
                "(1) 手动将旧目录内容拷贝到新默认目录；"
                "(2) 显式设置 LOCAL_PERSISTENCE_ROOT=%s 以继续使用旧路径。",
                legacy_root,
                user_default_root,
                legacy_root,
            )
    except Exception:
        # 迁移提示为尽力而为，检测失败（如权限）静默跳过，不影响启动。
        logger.debug("会话数据默认迁移提示检测失败，已跳过", exc_info=True)


async def _init_local_persistence() -> None:
    """启动期初始化本地文件持久化资源。

    流程：

    1. ``_resolve_local_persistence_config`` 解析生效配置——``LOCAL_PERSISTENCE_ROOT``
       为空时（且会话后端非 redis）迁移到 USER tier 默认路径
       ``~/.epsilon/persistence/<project-hash>/``（ADR-0006、需求 8.5/8.6）；
    2. ``_validate_local_persistence_root`` 校验 + 规范化路径（安全禁令与
       与 ``WORKSPACE_ROOT`` 的相互包含检测不弱化）；
    3. 构造 ``CrossPlatformPathPolicy`` / ``LockFactory`` /
       ``TempFileAtomicWriter`` 三个共享工具；
    4. **同步**构造一次性 ``TmpFileSweeper`` 并调用 ``sweep_once()`` 清理
       ``*.tmp-*`` 残留（需求 3.2、2.补.8；跑完即丢弃实例，不保存到模块级
       变量，不创建任何后台任务）；
    5. ``logger.info`` 输出最终绝对路径，便于运维排查 cwd 规范化结果。

    需求 2.补.1：**禁止**新增 ``_init_ttl_reaper`` / ``_cleanup_ttl_reaper``
    协程；**禁止**为 TTL 回收注册任何异步资源。
    """
    global _local_persistence_root, _atomic_writer, _path_policy, _lock_factory

    effective_config = _resolve_local_persistence_config()
    _local_persistence_root = _validate_local_persistence_root(effective_config)
    _path_policy = CrossPlatformPathPolicy()
    _lock_factory = LockFactory(acquire_timeout_ms=local_persistence_config.lock_acquire_timeout_ms)
    _atomic_writer = TempFileAtomicWriter(fsync_on_write=local_persistence_config.fsync_on_write)

    # 启动期一次性清理半写 tmp 残留；实例不保留。
    sweeper = TmpFileSweeper(
        sessions_root=_local_persistence_root / "sessions",
        max_age_seconds=local_persistence_config.tmp_sweep_max_age_seconds,
    )
    sweeper.sweep_once()

    # 本地文件后端就绪后，向 PROJECT tier home 写入一次 schema 版本元数据
    # （需求 6.3）。函数内部幂等且故障隔离，此处无需额外 try（Property 7）。
    from domain.storage.storage_tier import StorageTier
    from infrastructure.storage.schema_meta import write_schema_meta

    write_schema_meta(_create_tier_resolver().resolve(StorageTier.PROJECT).home)

    logger.info(
        "LocalPersistence 初始化完成：root=%s fsync=%s tmp_sweep_max_age_seconds=%d",
        _local_persistence_root,
        local_persistence_config.fsync_on_write,
        local_persistence_config.tmp_sweep_max_age_seconds,
    )


async def _cleanup_local_persistence() -> None:
    """本地文件持久化的清理钩子。

    目录与共享工具均不需要主动释放；本期无后台任务。保留空实现以对齐
    ``register_async_resource`` 的约定语义。
    """
    # 清理模块级引用，便于测试隔离与 GC
    global _local_persistence_root, _atomic_writer, _path_policy, _lock_factory
    _local_persistence_root = None
    _atomic_writer = None
    _path_policy = None
    _lock_factory = None


def _create_session_store() -> "SessionContextStorePort":
    """按 ``SESSION_STORE_BACKEND`` 分发创建会话上下文存储适配器。

    - ``REDIS``：沿用 ``RedisSessionContextAdapter``（依赖 ``_redis_client``
      已由 ``_init_redis`` 就绪）；
    - ``FILE``（含默认）：构造 ``LocalFileSessionContextAdapter``，注入
      启动期就绪的 ``_local_persistence_root`` / ``_lock_factory`` /
      ``_path_policy`` / ``_atomic_writer``。**不**传入 ``ttl_seconds``
      / ``reaper`` 等 TTL 参数（Adapter 构造签名已禁止）。

    Returns:
        实现 ``SessionContextStorePort`` 的适配器实例。

    Raises:
        RuntimeError: 当 ``FILE`` 后端选用但 ``_init_local_persistence``
            未就绪时抛出（通常意味着异步资源顺序配置错误）。
    """
    backend = session_store_config.backend
    if backend == SessionStoreBackendKind.REDIS:
        from infrastructure.session.redis_session_context_adapter import (
            RedisSessionContextAdapter,
        )
        from infrastructure.session.session_lock_config import session_lock_config
        from infrastructure.session.session_ttl_config import session_redis_ttl_config

        return RedisSessionContextAdapter(
            redis_client=_redis_client,  # type: ignore[arg-type]
            ttl_seconds=session_redis_ttl_config.ttl_seconds,
            conflict_retry_max=session_lock_config.conflict_retry_max,
        )

    # FILE 后端（含默认）
    if (
        _local_persistence_root is None
        or _lock_factory is None
        or _path_policy is None
        or _atomic_writer is None
    ):
        raise RuntimeError("本地文件会话后端未就绪，请确保 _init_local_persistence 已执行")
    from infrastructure.session.local_file_session_context_adapter import (
        LocalFileSessionContextAdapter,
    )

    return LocalFileSessionContextAdapter(
        root=_local_persistence_root,
        lock_factory=_lock_factory,
        path_policy=_path_policy,
        atomic_writer=_atomic_writer,
    )


def _create_session_index() -> "SessionIndexPort":
    """按 ``SESSION_STORE_BACKEND`` 分发创建会话索引适配器。"""
    backend = session_store_config.backend
    if backend == SessionStoreBackendKind.REDIS:
        from infrastructure.session.redis_session_index_adapter import (
            RedisSessionIndexAdapter,
        )
        from infrastructure.session.session_ttl_config import session_redis_ttl_config

        return RedisSessionIndexAdapter(
            redis_client=_redis_client,  # type: ignore[arg-type]
            ttl_seconds=session_redis_ttl_config.ttl_seconds,
        )

    if (
        _local_persistence_root is None
        or _lock_factory is None
        or _path_policy is None
        or _atomic_writer is None
    ):
        raise RuntimeError("本地文件会话索引后端未就绪，请确保 _init_local_persistence 已执行")

    from infrastructure.session.local_file_session_index_adapter import (
        LocalFileSessionIndexAdapter,
    )

    return LocalFileSessionIndexAdapter(
        root=_local_persistence_root,
        lock_factory=_lock_factory,
        path_policy=_path_policy,
        atomic_writer=_atomic_writer,
    )


def _create_run_store_adapter() -> RunStorePort:
    """按会话存储后端创建并共享 Run Store/Event Store 适配器。

    Run 快照与事件 cursor 必须落在同一个 adapter 实例上；因此
    ``RunStorePort`` 与 ``RunEventStorePort`` 均通过本工厂返回模块级共享实例。
    """
    global _run_store_adapter

    if _run_store_adapter is not None:
        return _run_store_adapter

    if session_store_config.backend == SessionStoreBackendKind.REDIS:
        from infrastructure.run.redis_run_store_adapter import RedisRunStoreAdapter

        _run_store_adapter = RedisRunStoreAdapter(redis_client=_redis_client)
        return _run_store_adapter

    if (
        _local_persistence_root is None
        or _lock_factory is None
        or _path_policy is None
        or _atomic_writer is None
    ):
        raise RuntimeError("本地文件 Run 后端未就绪，请确保 _init_local_persistence 已执行")

    from infrastructure.run.local_file_run_store_adapter import LocalFileRunStoreAdapter

    _run_store_adapter = LocalFileRunStoreAdapter(
        root=_local_persistence_root,
        lock_factory=_lock_factory,
        path_policy=_path_policy,
        atomic_writer=_atomic_writer,
    )
    return _run_store_adapter


def _create_run_workflow_config() -> RunWorkflowConfig:
    """返回已由配置工厂校验过的 Run workflow 配置。

    配置对象同时承载 role capability 默认关闭开关，后续 workflow registry
    会将其转换为纯领域 ``WorkflowExecutionPolicy``。
    """

    return run_workflow_config


async def _create_run_workflow_registry() -> WorkflowRegistryPort:
    """创建静态 workflow 注册表，构造期校验定义与配置一致性。"""

    config = await container.resolve(RunWorkflowConfig)
    return StaticWorkflowRegistryAdapter(config=config)


async def _create_run_workflow_selector() -> WorkflowSelectorPort:
    """创建确定性 workflow 选择器。"""

    config = await container.resolve(RunWorkflowConfig)
    registry = await container.resolve(WorkflowRegistryPort)
    return StaticWorkflowSelector(registry=registry, config=config)


async def _create_workflow_run_orchestrator() -> WorkflowRunOrchestrator:
    """创建 workflow phase 编排器。"""

    event_store = await container.resolve(RunEventStorePort)
    registry = await container.resolve(WorkflowRegistryPort)
    run_store = await container.resolve(RunStorePort)
    try:
        approval_store = await container.resolve(ApprovalStateStorePort)
    except KeyError:
        approval_store = None
    return WorkflowRunOrchestrator(
        event_store=event_store,
        workflow_registry=registry,
        workflow_serializer=WorkflowSerializerAdapter(),
        approval_store=approval_store,
        run_store=run_store,
    )


def _create_run_checkpoint_store_adapter() -> RunCheckpointStorePort:
    """按会话存储后端创建并共享 Run checkpoint store 适配器。"""
    global _run_checkpoint_store_adapter

    if _run_checkpoint_store_adapter is not None:
        return _run_checkpoint_store_adapter

    if session_store_config.backend == SessionStoreBackendKind.REDIS:
        from infrastructure.run.redis_run_checkpoint_store_adapter import (
            RedisRunCheckpointStoreAdapter,
        )

        _run_checkpoint_store_adapter = RedisRunCheckpointStoreAdapter(redis_client=_redis_client)
        return _run_checkpoint_store_adapter

    if (
        _local_persistence_root is None
        or _lock_factory is None
        or _path_policy is None
        or _atomic_writer is None
    ):
        raise RuntimeError(
            "本地文件 Run checkpoint 后端未就绪，请确保 _init_local_persistence 已执行"
        )

    from infrastructure.run.local_file_run_checkpoint_store_adapter import (
        LocalFileRunCheckpointStoreAdapter,
    )

    _run_checkpoint_store_adapter = LocalFileRunCheckpointStoreAdapter(
        root=_local_persistence_root,
        lock_factory=_lock_factory,
        path_policy=_path_policy,
        atomic_writer=_atomic_writer,
    )
    return _run_checkpoint_store_adapter


async def _create_run_execution_coordinator() -> RunExecutionCoordinator:
    """创建后台 Run 执行协调器，桥接到既有 Chat/Task 领域端口。"""
    chat_service = await container.resolve(ChatServicePort)
    task_agent = await container.resolve(TaskAgentPort)
    workflow_registry = await container.resolve(WorkflowRegistryPort)
    workflow_orchestrator = await container.resolve(WorkflowRunOrchestrator)
    checkpoint_store = None
    event_store = None
    retention_policy = None
    if run_runtime_config.checkpoint_enabled:
        checkpoint_store = await container.resolve(RunCheckpointStorePort)
        event_store = await container.resolve(RunEventStorePort)
        retention_policy = run_runtime_config.to_checkpoint_retention_policy()
    return RunExecutionCoordinator(
        chat_service=chat_service,
        task_agent=task_agent,
        segment_serializer=SegmentSerializerAdapter(),
        checkpoint_store=checkpoint_store,
        event_store=event_store,
        retention_policy=retention_policy,
        checkpoint_enabled=run_runtime_config.checkpoint_enabled,
        workflow_orchestrator=workflow_orchestrator,
        workflow_registry=workflow_registry,
    )


async def _create_run_recovery_service() -> RunRecoveryService:
    """创建 checkpoint 自动恢复服务。"""
    run_store = await container.resolve(RunStorePort)
    checkpoint_store = await container.resolve(RunCheckpointStorePort)
    event_store = await container.resolve(RunEventStorePort)
    return RunRecoveryService(
        run_store=run_store,
        checkpoint_store=checkpoint_store,
        event_store=event_store,
        retention_policy=run_runtime_config.to_checkpoint_retention_policy(),
        max_recovery_attempts=run_runtime_config.checkpoint_max_recovery_attempts,
        auto_recovery_enabled=run_runtime_config.checkpoint_auto_recovery_enabled,
        guardrail_serializer=GuardrailSerializerAdapter(),
    )


async def _create_run_worker_manager() -> RunWorkerManager:
    """创建并缓存 RunWorkerManager，供生命周期资源和唤醒回调共享。"""
    global _run_worker_manager

    if _run_worker_manager is not None:
        return _run_worker_manager

    run_store = await container.resolve(RunStorePort)
    event_store = await container.resolve(RunEventStorePort)
    executor = await container.resolve(RunExecutionCoordinator)
    recovery_sweep = None
    if (
        run_runtime_config.checkpoint_enabled
        and run_runtime_config.checkpoint_auto_recovery_enabled
    ):
        recovery_sweep = await container.resolve(RunRecoveryService)
    _run_worker_manager = RunWorkerManager(
        run_store=run_store,
        event_store=event_store,
        executor=executor,
        config=run_runtime_config,
        recovery_sweep=recovery_sweep,
    )
    return _run_worker_manager


def _wake_run_worker_if_ready() -> None:
    """同步唤醒已启动的 Run worker；未启动时保持 noop。"""
    if _run_worker_manager is not None:
        _run_worker_manager.wake_up()


async def _create_run_approval_resumer() -> RunApprovalResumer:
    """创建按 RunKind 分派的审批恢复器。"""

    chat_service = await container.resolve(ChatServicePort)
    task_agent = await container.resolve(TaskAgentPort)
    return RunApprovalResumer(
        chat_service=chat_service,
        task_agent=task_agent,
    )


async def _create_run_application_service() -> RunApplicationService:
    """创建 adapter-neutral Run 应用服务。"""
    run_store = await container.resolve(RunStorePort)
    event_store = await container.resolve(RunEventStorePort)
    guardrail_policy = await container.resolve(AgentGuardrailPolicyPort)
    workflow_selector = await container.resolve(WorkflowSelectorPort)
    approval_resumer = await container.resolve(RunApprovalResumer)
    return RunApplicationService(
        run_store=run_store,
        event_store=event_store,
        capacity_policy=run_runtime_config.to_capacity_policy(),
        event_retention_policy=run_runtime_config.to_event_retention_policy(),
        workflow_serializer=WorkflowSerializerAdapter(),
        worker_wakeup=_wake_run_worker_if_ready,
        approval_resumer=approval_resumer,
        event_stream_wait_seconds=run_runtime_config.event_stream_wait_seconds,
        guardrail_policy=guardrail_policy,
        workflow_selector=workflow_selector,
    )


async def _create_run_guardrail_recorder() -> RunGuardrailRecorderPort | None:
    """按收敛开关创建 Run guardrail recorder。"""

    if not run_runtime_config.guardrail_runtime_convergence_enabled:
        return None
    run_store = await container.resolve(RunStorePort)
    observation_store = await container.resolve(RunObservationStorePort)
    return RunGuardrailRecorder(
        run_store=run_store,
        observation_store=observation_store,
        guardrail_serializer=GuardrailSerializerAdapter(),
    )


def _run_guardrail_runtime_convergence_enabled() -> bool:
    """返回 Run guardrail 运行时收敛开关状态。"""

    return bool(run_runtime_config.guardrail_runtime_convergence_enabled)


async def _init_run_worker_manager() -> None:
    """启动后台 Run worker manager。"""
    manager = await container.resolve(RunWorkerManager)
    await manager.start()


async def _cleanup_run_worker_manager() -> None:
    """停止后台 Run worker manager 并清理模块级引用。"""
    global _run_worker_manager
    if _run_worker_manager is not None:
        await _run_worker_manager.stop()
        _run_worker_manager = None


def _create_approval_policy() -> "ApprovalPolicyPort":
    """创建审批策略提供器。"""
    from infrastructure.agent.approval_policy_provider import StaticApprovalPolicyProvider
    from infrastructure.agent.hitl_config import hitl_config

    return StaticApprovalPolicyProvider(
        enabled=hitl_config.enabled,
        interrupt_on=hitl_config.interrupt_on,
    )


def _create_guardrail_policy() -> "AgentGuardrailPolicyPort":
    """创建 Agent 智能调度与护栏策略。"""

    from domain.agent.guardrail_policy import StaticAgentGuardrailPolicy
    from infrastructure.agent.guardrail_config import agent_guardrail_config

    return StaticAgentGuardrailPolicy(agent_guardrail_config.to_policy())


def _create_approval_state_store() -> "ApprovalStateStorePort":
    """按会话后端创建审批状态存储。"""
    from infrastructure.agent.hitl_config import hitl_config

    if session_store_config.backend == SessionStoreBackendKind.REDIS:
        from infrastructure.agent.approval_state_store import RedisApprovalStateStore

        return RedisApprovalStateStore(
            redis_client=_redis_client,  # type: ignore[arg-type]
            ttl_seconds=hitl_config.state_ttl_seconds,
        )

    if (
        _local_persistence_root is None
        or _lock_factory is None
        or _path_policy is None
        or _atomic_writer is None
    ):
        raise RuntimeError("本地文件审批状态后端未就绪，请确保 _init_local_persistence 已执行")
    from infrastructure.agent.approval_state_store import LocalFileApprovalStateStore

    return LocalFileApprovalStateStore(
        root=_local_persistence_root,
        lock_factory=_lock_factory,
        path_policy=_path_policy,
        atomic_writer=_atomic_writer,
        ttl_seconds=hitl_config.state_ttl_seconds,
    )


def _create_model_access_adapter() -> "ModelAccessPort":
    """创建模型接入适配器实例。

    通过 ProviderRegistry 的负载均衡机制获取默认模型对应的适配器。
    ProviderRegistry 内部维护 model → adapter 的映射，支持 Round-Robin 负载均衡。

    Returns:
        默认模型对应的 ``OpenAICompatibleAdapter`` 实例。

    Raises:
        RuntimeError: 供应商注册中心未初始化。
        ModelAccessError: 无可用模型或默认模型未注册。
    """
    if _provider_registry is None:
        raise RuntimeError("供应商注册中心未初始化，请确保 _init_model_client 已执行")

    default_model = _provider_registry.get_default_model()
    return _provider_registry.get_adapter_for_model(default_model)


def _create_model_registry() -> "ModelRegistryPort":
    """创建模型注册中心实例（供 /v1/models API 使用）。

    直接返回已初始化的 ProviderRegistry 实例。
    """
    if _provider_registry is None:
        raise RuntimeError("供应商注册中心未初始化，请确保 _init_model_client 已执行")

    return _provider_registry  # type: ignore[return-value]


def _create_readiness_aggregator() -> ReadinessAggregator:
    """按实际装配的异步资源动态组装 ``ReadinessAggregator`` 的检查列表。

    需求 6.3 / 7.4：

    - ``redis`` 异步资源注册过 → 追加 ``RedisHealthCheckAdapter``；
    - ``database`` 异步资源注册过 → 追加 ``MysqlHealthCheckAdapter``
      （本期默认不装配，留给未来新增 MySQL 消费者时恢复）；
    - ``local_persistence`` 异步资源注册过 → 追加
      ``LocalPersistenceHealthCheckAdapter``（本期默认装配）。

    未装配的中间件健康检查**必须完全缺席**，避免 ``/health/ready`` 出现
    "未启用占位"干扰运维判断（需求 6.3.4）。

    Returns:
        ``ReadinessAggregator`` 实例，``checks`` 与当前容器状态精确对应。
    """
    checks: list = []

    if container.has_async_resource("redis"):
        from infrastructure.health.redis_health_check_adapter import (
            RedisHealthCheckAdapter,
        )

        if _redis_client is not None:
            checks.append(RedisHealthCheckAdapter(redis_client=_redis_client))

    if container.has_async_resource("database"):
        # 仅当未来恢复 MySQL 默认装配时才会走到这里（本期不默认注册）。
        from infrastructure.database.engine import get_session_factory
        from infrastructure.health.mysql_health_check_adapter import (
            MysqlHealthCheckAdapter,
        )

        checks.append(MysqlHealthCheckAdapter(session_factory=get_session_factory()))

    if container.has_async_resource("local_persistence"):
        from infrastructure.health.local_persistence_health_check_adapter import (
            LocalPersistenceHealthCheckAdapter,
        )

        if _local_persistence_root is not None:
            checks.append(LocalPersistenceHealthCheckAdapter(root=_local_persistence_root))

    return ReadinessAggregator(checks=checks)


async def _create_compaction_adapter() -> "ContextCompactionPort":
    """创建上下文压缩适配器实例。

    从 ChatConfig 读取摘要压缩配置，默认创建 LLM 语义摘要压缩适配器；
    滑动窗口适配器保留为摘要失败时的降级策略。

    Returns:
        LLMSummaryCompactionAdapter 实例，实现 ContextCompactionPort 协议。
    """
    from infrastructure.chat.chat_config import chat_config
    from infrastructure.chat.llm_summary_compaction_adapter import (
        LLMSummaryCompactionAdapter,
    )
    from infrastructure.chat.sliding_window_compaction_adapter import SlidingWindowCompactionAdapter

    prompt_registry = await container.resolve(PromptRegistryPort)
    fallback = SlidingWindowCompactionAdapter(max_messages=chat_config.max_messages)
    return LLMSummaryCompactionAdapter(
        prompt_registry=prompt_registry,
        trigger_tokens=chat_config.compaction_trigger_tokens,
        keep_recent_messages=chat_config.compaction_keep_recent_messages,
        fallback=fallback,
    )


async def _create_context_builder() -> "ContextBuilderPort":
    """创建上下文构建适配器实例。

    通过容器解析已注册的 ``ContextCompactionPort``，并注入默认启用的
    静态环境上下文提供器。压缩端口保持独立 singleton 注册，作为 builder
    的内部依赖复用。

    Returns:
        ContextBuilderAdapter 实例，实现 ContextBuilderPort 协议。
    """
    from infrastructure.chat.context_builder_adapter import ContextBuilderAdapter
    from infrastructure.chat.environment_context_provider import (
        StaticEnvironmentContextProvider,
    )

    compaction = await container.resolve(ContextCompactionPort)
    return ContextBuilderAdapter(
        compaction=compaction,
        environment_provider=StaticEnvironmentContextProvider(),
    )


def _create_agent_registry() -> "AgentRegistryPort":
    """创建 Agent 注册表实例。

    返回 AgentRegistryAdapter 作为 AgentRegistryPort 的实现，
    用于管理命名 Agent 配置的注册和查找。

    Returns:
        AgentRegistryAdapter 实例
    """
    from infrastructure.agent.agent_registry_adapter import AgentRegistryAdapter

    return AgentRegistryAdapter()


async def _create_delegation_adapter() -> "DelegationPort":
    """创建委派适配器实例。

    通过容器解析 AgentRegistryPort、TaskAgentPort 与 ModelRegistryPort，构造
    DelegationAdapter。DelegationAdapter 桥接 DelegationPort 到 TaskAgentPort，
    使 DelegateToAgentTool 无需直接依赖 TaskAgentPort，从而在架构层面打断
    循环依赖链。

    Spec A 扩展：``DelegationAdapter.handoff`` 需要在运行期访问
    ``AgentPort`` / ``ToolRegistry``，而它们又通过 ``DelegateToAgentTool`` /
    ``DelegateParallelTool`` 反向引用 ``DelegationPort``，形成循环。解法
    沿用既有"延迟注册"模式：构造期不解析 AgentPort/ToolRegistry，改在
    ``handoff`` 调用时通过容器懒解析，由两个异步 provider 函数承载。

    Returns:
        DelegationAdapter 实例，实现 DelegationPort 协议。
    """
    from infrastructure.agent.delegation_adapter import DelegationAdapter

    agent_registry = await container.resolve(AgentRegistryPort)
    task_agent = await container.resolve(TaskAgentPort)
    model_registry = await container.resolve(ModelRegistryPort)
    event_store = await container.resolve(RunEventStorePort)

    async def _agent_provider() -> "AgentPort":
        """懒解析 AgentPort，规避 DelegationPort → AgentPort 循环依赖。"""
        return await container.resolve(AgentPort)

    async def _tool_registry_provider() -> "ToolRegistry":
        """懒解析 ToolRegistry，规避 DelegationPort → ToolRegistry 循环依赖。"""
        return await container.resolve(ToolRegistry)

    return DelegationAdapter(
        agent_registry=agent_registry,
        task_agent=task_agent,
        model_registry=model_registry,
        agent_provider=_agent_provider,
        tool_registry_provider=_tool_registry_provider,
        handoff_max_rounds=agent_config.handoff_max_rounds,
        event_store=event_store,
    )


async def _register_delegate_tool() -> None:
    """将 DelegateToAgentTool / HandoffToAgentTool / DelegateParallelTool
    追加注册到已创建的 ToolRegistry 中。

    此函数在 ToolRegistry 和 DelegationPort 均已创建后调用，
    打破 ToolRegistry → DelegationPort → TaskAgentPort → AgentPort → ToolRegistry
    的运行时解析环。ToolRegistry 先创建（不含三个 delegate 系工具），
    DelegationPort 随后正常解析，最后将三个工具一并追加注册。

    Spec A 扩展：HandoffToAgentTool（控制转移）与 DelegateParallelTool（并行扇出）
    与 DelegateToAgentTool 共享 ``AGENT_DELEGATE_TOOL_ENABLED`` 开关；
    禁用时三者一同跳过，保持配置语义向后兼容。
    """
    if not agent_config.delegate_tool_enabled:
        return

    from infrastructure.agent.delegate_parallel_tool import DelegateParallelTool
    from infrastructure.agent.delegate_to_agent_tool import DelegateToAgentTool
    from infrastructure.agent.handoff_to_agent_tool import HandoffToAgentTool

    tool_registry = await container.resolve(ToolRegistry)
    agent_registry = await container.resolve(AgentRegistryPort)
    delegation = await container.resolve(DelegationPort)
    event_store = await container.resolve(RunEventStorePort)
    workflow_config = await container.resolve(RunWorkflowConfig)

    tool_registry.register(
        DelegateToAgentTool(
            agent_registry=agent_registry,
            delegation=delegation,
            max_delegation_depth=agent_config.max_delegation_depth,
        )
    )
    tool_registry.register(
        HandoffToAgentTool(
            agent_registry=agent_registry,
            delegation=delegation,
            max_delegation_depth=agent_config.max_delegation_depth,
            event_store=event_store,
            recent_collaboration_summary_limit=workflow_config.recent_collaboration_summary_limit,
        )
    )
    tool_registry.register(
        DelegateParallelTool(
            agent_registry=agent_registry,
            delegation=delegation,
            max_delegation_depth=agent_config.max_delegation_depth,
        )
    )
    logger.info(
        "DelegateToAgentTool / HandoffToAgentTool / DelegateParallelTool 已追加注册到 ToolRegistry",
    )


async def _create_tool_registry() -> ToolRegistry:
    """创建工具注册表实例并注册所有可用工具。

    实例化 ToolRegistry，导入并注册项目中已有的具体 Tool 实现：
    - filesystem 包导出的 ReadFileTool、WriteFileTool、EditFileTool（注入 Workspace）
    - ListDirTool（直接从子模块导入，注入 Workspace）
    - ShellExecTool / PythonExecTool（注入 Workspace；对 working_dir 二次校验）
    - WebSearchTool（条件注册，受 TAVILY_API_KEY 控制）
    - DelegateToAgentTool（条件注册，受 AGENT_DELEGATE_TOOL_ENABLED 控制）

    **Workspace 注入（需求 9.3）**：函数开头通过 ``container.resolve(Workspace)``
    拿到启动期已就绪的 ``Workspace`` 单例，作为构造参数注入 6 个受控工具。

    **exec 工具 working_dir 二次校验（需求 10.3 / tasks 11.3）**：对
    ``SHELL_EXEC_WORKING_DIR`` / ``PYTHON_EXEC_WORKING_DIR`` 非空的情况，
    在构造工具前调用 ``ws.resolve_path(cfg.working_dir)`` 做一次启动期守卫；
    若越界抛出 ``WorkspaceConfinementViolation``，翻译为 ``ConfigurationError``
    并给出"请将 SHELL_EXEC_WORKING_DIR / PYTHON_EXEC_WORKING_DIR 设置到
    工作区内，或留空使用默认"的中文提示。

    注册完成后记录日志，输出已注册工具的数量和名称列表。

    Returns:
        已注册所有可用工具的 ToolRegistry 实例。

    Raises:
        ConfigurationError: ``SHELL_EXEC_WORKING_DIR`` / ``PYTHON_EXEC_WORKING_DIR``
            配置值指向工作区外时抛出，触发 fail-fast。
    """
    from infrastructure.tools.filesystem import EditFileTool, ReadFileTool, WriteFileTool

    # 解析已就绪的 Workspace 单例（启动期由 _init_workspace 赋值）
    ws = await container.resolve(Workspace)

    # 按配置构造工具熔断器
    breaker = None
    try:
        from infrastructure.agent.circuit_breaker_config import circuit_breaker_config

        if circuit_breaker_config.enabled:
            from infrastructure.agent.circuit_breaker import ToolCircuitBreaker

            breaker = ToolCircuitBreaker(
                failure_threshold=circuit_breaker_config.failure_threshold,
                recovery_timeout=circuit_breaker_config.recovery_timeout_seconds,
                half_open_max_calls=circuit_breaker_config.half_open_max_calls,
            )
            logger.info(
                "工具熔断器已启用: threshold=%d, recovery=%.1fs",
                circuit_breaker_config.failure_threshold,
                circuit_breaker_config.recovery_timeout_seconds,
            )
    except Exception as e:
        logger.warning("工具熔断器配置加载失败，跳过: %s", e)

    registry = ToolRegistry(circuit_breaker=breaker)

    # 注册 filesystem 包导出的工具（注入 Workspace）
    for tool_cls in (ReadFileTool, WriteFileTool, EditFileTool):
        registry.register(tool_cls(workspace=ws))

    # 尝试注册 ListDirTool（未在 __init__.py 中导出，直接从子模块导入）
    try:
        from infrastructure.tools.filesystem.list_dir_tool import ListDirTool

        registry.register(ListDirTool(workspace=ws))
    except ImportError:
        logger.debug("ListDirTool 不可用，跳过注册")

    # 注册低风险 Workspace 代码检索工具；模块不可用时按可选工具模式跳过。
    try:
        from infrastructure.tools.glob import GlobTool
        from infrastructure.tools.grep import GrepTool
        from infrastructure.tools.read_many_files import ReadManyFilesTool

        for tool_cls in (GlobTool, GrepTool, ReadManyFilesTool):
            registry.register(tool_cls(workspace=ws))
    except ImportError:
        logger.debug("Workspace 代码检索工具不可用，跳过注册")

    # 注册受控 Git 工具；固定 git 子命令，减少对 shell_exec 的依赖。
    try:
        from infrastructure.tools.git_apply_patch import GitApplyPatchTool
        from infrastructure.tools.git_diff import GitDiffTool
        from infrastructure.tools.git_status import GitStatusTool

        for tool_cls in (GitStatusTool, GitDiffTool, GitApplyPatchTool):
            registry.register(tool_cls(workspace=ws))
    except ImportError:
        logger.debug("Git 工具不可用，跳过注册")

    # 条件注册 WebSearchTool（Web 搜索工具）
    try:
        from infrastructure.tools.web_search.tavily_config import tavily_config

        if tavily_config.api_key:
            from infrastructure.tools.web_search import WebSearchTool

            registry.register(
                WebSearchTool(
                    api_key=tavily_config.api_key,
                    default_max_results=tavily_config.search_max_results,
                )
            )
        else:
            logger.warning("TAVILY_API_KEY 未配置，跳过 WebSearchTool 注册")
    except ImportError:
        logger.debug("WebSearchTool 不可用，跳过注册")

    # 条件注册 HttpRequestTool（HTTP 请求工具）
    try:
        from infrastructure.tools.http_request.http_request_config import http_request_config

        if http_request_config.enabled:
            from infrastructure.tools.http_request import HttpRequestTool

            registry.register(
                HttpRequestTool(
                    timeout=http_request_config.timeout,
                    max_response_size=http_request_config.max_response_size,
                )
            )
        else:
            logger.info("HTTP_REQUEST_ENABLED=false，跳过 HttpRequestTool 注册")
    except ImportError:
        logger.debug("HttpRequestTool 不可用，跳过注册")

    # 条件注册 WebFetchTool（网页抓取工具）
    try:
        from infrastructure.tools.web_fetch.web_fetch_config import web_fetch_config

        if web_fetch_config.enabled:
            from infrastructure.tools.web_fetch import WebFetchTool

            registry.register(
                WebFetchTool(
                    timeout=web_fetch_config.timeout,
                    max_response_size=web_fetch_config.max_response_size,
                )
            )
        else:
            logger.info("WEB_FETCH_ENABLED=false，跳过 WebFetchTool 注册")
    except ImportError:
        logger.debug("WebFetchTool 不可用，跳过注册")

    # 条件注册 ShellExecTool（Shell 命令执行工具）
    try:
        from infrastructure.tools.shell_exec.shell_exec_config import shell_exec_config

        if shell_exec_config.enabled:
            from infrastructure.tools.shell_exec import ShellExecTool

            # 启动期二次校验：SHELL_EXEC_WORKING_DIR 非空时必须位于工作区内
            # （需求 10.3）；失败翻译为 ConfigurationError 触发 fail-fast。
            _validate_exec_working_dir(
                ws=ws,
                config_name="SHELL_EXEC_WORKING_DIR",
                working_dir=shell_exec_config.working_dir,
            )
            registry.register(
                ShellExecTool(
                    workspace=ws,
                    timeout=shell_exec_config.timeout,
                    max_output_size=shell_exec_config.max_output_size,
                    default_working_dir=shell_exec_config.working_dir or "",
                )
            )
        else:
            logger.info("SHELL_EXEC_ENABLED=false，跳过 ShellExecTool 注册")
    except ImportError:
        logger.debug("ShellExecTool 不可用，跳过注册")

    # 条件注册 PythonExecTool（Python 脚本安全执行工具）
    try:
        from infrastructure.tools.python_exec.python_exec_config import python_exec_config

        if python_exec_config.enabled:
            from infrastructure.tools.python_exec import PythonExecTool

            # 启动期二次校验：PYTHON_EXEC_WORKING_DIR 非空时必须位于工作区内
            # （需求 10.3）；失败翻译为 ConfigurationError 触发 fail-fast。
            _validate_exec_working_dir(
                ws=ws,
                config_name="PYTHON_EXEC_WORKING_DIR",
                working_dir=python_exec_config.working_dir,
            )
            registry.register(
                PythonExecTool(
                    workspace=ws,
                    timeout=python_exec_config.timeout,
                    max_output_size=python_exec_config.max_output_size,
                    max_memory_mb=python_exec_config.max_memory_mb,
                    allowed_modules=python_exec_config.get_allowed_modules(),
                )
            )
        else:
            logger.info("PYTHON_EXEC_ENABLED=false，跳过 PythonExecTool 注册")
    except ImportError:
        logger.debug("PythonExecTool 不可用，跳过注册")

    # 条件注册 MCP 远端工具（通过 fastmcp 桥接为内部 Tool）
    # fail-soft：MCP 为可选增强能力，逐 server 隔离——单 server 故障不影响其余。
    try:
        from infrastructure.tools.mcp.mcp_config import mcp_config

        servers = mcp_config.get_servers()
        if mcp_config.enabled and servers:
            from infrastructure.tools.mcp import MCPToolBridge

            total_registered = 0
            for server_name, server_spec in servers.items():
                try:
                    bridge = MCPToolBridge(
                        transport={"mcpServers": {server_name: server_spec}},
                        tool_prefix=mcp_config.tool_prefix,
                        timeout=mcp_config.timeout,
                        max_retries=mcp_config.max_retries,
                        retry_base_delay=mcp_config.retry_base_delay,
                        server_name=server_name,
                    )
                    mcp_tools = await bridge.discover()
                    for mcp_tool in mcp_tools:
                        tool_name = mcp_tool.name
                        if registry.has(tool_name):
                            suffixed = f"{tool_name}_{server_name}"
                            logger.warning(
                                "MCP 工具名 '%s' 冲突，自动重命名为 '%s'",
                                tool_name,
                                suffixed,
                            )
                            mcp_tool._name = suffixed
                        registry.register(mcp_tool)
                        total_registered += 1
                except Exception as e:
                    logger.warning("MCP server '%s' 工具发现失败，跳过: %s", server_name, e)
            logger.info("MCP 桥接注册 %d 个远端工具", total_registered)
        else:
            logger.info("MCP_ENABLED=false 或未配置 MCP_SERVERS，跳过 MCP 工具注册")
    except Exception as e:
        logger.warning("MCP 工具发现失败，跳过注册: %s", e)

    # 条件注册 DelegateToAgentTool（Agent 间委派工具）
    # 注意：DelegateToAgentTool 的注册延迟到 ToolRegistry 创建之后，
    # 因为 DelegationPort → TaskAgentPort → AgentPort → ToolRegistry 形成运行时解析环。
    # ToolRegistry 先创建（不含 DelegateToAgentTool），然后 DelegationPort 可正常解析，
    # 最后将 DelegateToAgentTool 追加注册到已创建的 ToolRegistry 中。
    if agent_config.delegate_tool_enabled:
        logger.debug("DelegateToAgentTool 将在 ToolRegistry 创建后延迟注册")
    else:
        logger.info("AGENT_DELEGATE_TOOL_ENABLED=false，跳过 DelegateToAgentTool 注册")

    tool_names = [s["function"]["name"] for s in registry.get_schemas()]
    logger.info("ToolRegistry 初始化完成，共注册 %d 个工具: %s", len(tool_names), tool_names)

    return registry


def _create_tier_resolver() -> "LocalFileTierResolver":
    """创建（并惰性缓存）本地文件存储等级解析器。

    PROJECT tier 基点取 ``WORKSPACE_ROOT``（``workspace_config.root``）；为空时
    退化为进程当前工作目录（``Path.cwd()``），使 PROJECT tier 的 traces 目录与
    既有 ``.epsilon/traces`` 等价（需求 1.6、Property 2）。USER tier 基点采用默认
    ``Path.home()``。

    解析器是全仓库唯一的 project-hash 生成点，trace store / artifact store 与
    会话主状态默认路径迁移均复用同一实例，保证分区键一致（ADR-0002/0006）。

    Returns:
        缓存的 ``LocalFileTierResolver`` 单例。
    """
    global _tier_resolver
    if _tier_resolver is not None:
        return _tier_resolver
    from infrastructure.storage.local_file_tier_resolver import LocalFileTierResolver

    ws = (workspace_config.root or "").strip()
    project_base = Path(ws) if ws else Path.cwd()
    _tier_resolver = LocalFileTierResolver(project_base=project_base)
    return _tier_resolver


def _create_trace_store() -> "TraceStorePort | None":
    """创建 trace store 实例。

    根据配置决定是否启用结构化 Agent 追踪。禁用时返回 None，
    表示 Agent 不记录 trace 且查询 API 返回空结果。

    启用时注入 ``_create_tier_resolver()`` 解析器（替代旧的 ``store_dir``），
    由适配器按 tier 解析 traces 目录；不传 tier 时默认 PROJECT，落点与既有
    ``.epsilon/traces`` 等价（需求 8.1、Property 2）。
    """
    from infrastructure.trace.trace_config import trace_config

    if not trace_config.enabled:
        return None
    from infrastructure.trace.local_file_trace_store_adapter import LocalFileTraceStoreAdapter

    return LocalFileTraceStoreAdapter(tier_resolver=_create_tier_resolver())


def _create_artifact_store() -> "ArtifactStorePort | None":
    """创建 artifact store 实例。

    根据 ``ARTIFACT_ENABLED`` 决定是否启用任务产物持久化。禁用时返回 None，
    写入方静默跳过（Property 6）。启用时注入 ``_create_tier_resolver()`` 解析器，
    与 trace store 共享同一 resolver 单例。

    Returns:
        ``LocalFileArtifactStoreAdapter`` 实例；禁用时为 None。
    """
    from infrastructure.artifact.artifact_config import artifact_config

    if not artifact_config.enabled:
        return None
    from infrastructure.artifact.local_file_artifact_store_adapter import (
        LocalFileArtifactStoreAdapter,
    )

    return LocalFileArtifactStoreAdapter(tier_resolver=_create_tier_resolver())


async def _create_agent() -> "AgentPort":
    """创建 Agent 适配器实例。

    通过容器异步解析 ToolRegistry 和 ContextBuilderPort，
    创建 ReActAgentAdapter 实例。ReActAgentAdapter 持有这两个依赖
    作为长期基础设施组件，在 Agent Loop 执行中使用。

    Returns:
        ReActAgentAdapter 实例，实现 AgentPort 协议。
    """
    from infrastructure.agent.react_agent_adapter import ReActAgentAdapter

    tool_registry = await container.resolve(ToolRegistry)
    context_builder = await container.resolve(ContextBuilderPort)
    try:
        approval_policy = await container.resolve(ApprovalPolicyPort)
        approval_store = await container.resolve(ApprovalStateStorePort)
    except KeyError:
        approval_policy = None
        approval_store = None
    try:
        guardrail_policy = await container.resolve(AgentGuardrailPolicyPort)
    except KeyError:
        guardrail_policy = None
    if _run_guardrail_runtime_convergence_enabled():
        try:
            run_guardrail_recorder = await container.resolve(RunGuardrailRecorderPort)
        except KeyError:
            run_guardrail_recorder = None
    else:
        run_guardrail_recorder = None

    trace_store = await container.resolve(TraceStorePort)
    try:
        run_event_store = await container.resolve(RunEventStorePort)
    except KeyError:
        run_event_store = None

    return ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=context_builder,
        approval_policy=approval_policy,
        approval_store=approval_store,
        trace_store=trace_store,
        guardrail_policy=guardrail_policy,
        run_guardrail_recorder=run_guardrail_recorder,
        run_event_store=run_event_store,
    )


async def _create_task_agent() -> "TaskAgentPort":
    """创建面向任务的 Agent 适配器实例。

    通过容器异步解析 AgentPort、ToolRegistry、ModelRegistryPort、
    ContextCompactionPort、SessionContextStorePort 和 PromptRegistryPort，
    从 ``task_agent_config`` 读取 ``TASK_AGENT_MAX_ROUNDS``（默认 10），
    创建 TaskAgentAdapter 实例。

    Returns:
        TaskAgentAdapter 实例，实现 TaskAgentPort 协议。
    """
    from application.task import TaskApplicationService, TaskTraceWorkflow
    from infrastructure.task.task_agent_adapter import TaskAgentAdapter

    agent = await container.resolve(AgentPort)
    tool_registry = await container.resolve(ToolRegistry)
    model_registry = await container.resolve(ModelRegistryPort)
    compaction = await container.resolve(ContextCompactionPort)
    session_store = await container.resolve(SessionContextStorePort)
    prompt_registry = await container.resolve(PromptRegistryPort)
    try:
        approval_store = await container.resolve(ApprovalStateStorePort)
    except KeyError:
        approval_store = None
    task_policy = task_agent_config.to_segment_policy()
    task_prompt_id = prompt_registry.get("task-template").prompt_id
    task_application_service = TaskApplicationService(
        session_store=session_store,
        approval_store=approval_store,
        trace_workflow=TaskTraceWorkflow(),
        segment_policy=task_policy,
        prompt_id=task_prompt_id,
    )
    return TaskAgentAdapter(
        agent=agent,
        tool_registry=tool_registry,
        model_registry=model_registry,
        compaction=compaction,
        session_store=session_store,
        prompt_registry=prompt_registry,
        approval_store=approval_store,
        max_rounds=task_agent_config.max_rounds,
        segment_policy=task_policy,
        task_application_service=cast(Any, task_application_service),
        task_template_prompt_id=task_prompt_id,
    )


async def _create_chat_service() -> "ChatServicePort":
    """创建聊天服务适配器实例。

    通过容器异步解析 SessionContextStorePort、ModelRegistryPort、PromptRegistryPort、
    ContextBuilderPort、ToolRegistry 和 AgentPort，读取 ChatConfig 的
    max_tool_rounds 和 tool_calling_enabled，组装 ChatServiceAdapter。

    ChatServiceAdapter 持有 ModelRegistryPort 而非固定的 ModelAccessPort，
    在每次对话请求时根据 ChatRequestVO.model 动态路由到对应的模型适配器。
    AgentPort 实例用于 tool_calling_enabled 时委托 Agent Loop 执行。

    Returns:
        ChatServiceAdapter 实例，实现 ChatServicePort 协议。
    """
    from application.chat import ChatApplicationService, ChatSessionContextWorkflow
    from domain.agent.value_objects import AgentConfig
    from infrastructure.chat.chat_config import chat_config
    from infrastructure.chat.chat_default_prompt import resolve_chat_default_system_prompt
    from infrastructure.chat.chat_service_adapter import ChatServiceAdapter

    session_store = await container.resolve(SessionContextStorePort)
    model_registry = await container.resolve(ModelRegistryPort)
    prompt_registry = await container.resolve(PromptRegistryPort)
    context_builder = await container.resolve(ContextBuilderPort)
    tool_registry = await container.resolve(ToolRegistry)
    agent = await container.resolve(AgentPort)
    try:
        approval_store = await container.resolve(ApprovalStateStorePort)
    except KeyError:
        approval_store = None
    try:
        session_index = await container.resolve(SessionIndexPort)
    except KeyError:
        session_index = None

    tool_schemas = tool_registry.get_schemas()
    segment_policy = chat_config.to_segment_policy()
    resolved_prompt = resolve_chat_default_system_prompt(prompt_registry)
    system_prompt = resolved_prompt.system_prompt
    prompt_id = resolved_prompt.prompt_id
    session_workflow = ChatSessionContextWorkflow(
        session_store=session_store,
        session_index=session_index,
        system_prompt=system_prompt,
        prompt_id=prompt_id,
    )

    def _resolve_model_access(model: str | None) -> tuple[ModelAccessPort, str]:
        """按聊天请求模型解析 ModelAccessPort。"""

        if model is not None:
            return model_registry.get_adapter_for_model(model), model
        default_model = model_registry.get_default_model()
        return model_registry.get_adapter_for_model(default_model), default_model

    def _make_agent_config(model: str | None) -> AgentConfig:
        """构造聊天 Agent 单段执行配置。"""

        return AgentConfig(
            system_prompt=system_prompt,
            tool_schemas=tool_schemas,
            model=model,
            max_rounds=chat_config.max_tool_rounds,
            prompt_id=prompt_id,
        )

    chat_application_service = ChatApplicationService(
        session_workflow=session_workflow,
        agent=agent,
        approval_store=approval_store,
        segment_policy=segment_policy,
        resolve_model_access=_resolve_model_access,
        make_agent_config=_make_agent_config,
    )

    return ChatServiceAdapter(
        session_store=session_store,
        model_registry=model_registry,
        prompt_registry=prompt_registry,
        context_builder=context_builder,
        agent=agent,
        tool_calling_enabled=chat_config.tool_calling_enabled,
        max_tool_rounds=chat_config.max_tool_rounds,
        tool_schemas=tool_schemas,
        approval_store=approval_store,
        session_index=session_index,
        segment_policy=segment_policy,
        session_workflow=session_workflow,
        chat_application_service=cast(Any, chat_application_service),
    )


def configure_container() -> None:
    """注册所有异步资源和 Port → Adapter 映射。

    在 FastAPI 应用创建前调用，确保容器 lifespan 启动时
    能按正确顺序初始化资源并解析依赖。

    异步资源按注册顺序初始化，关闭时逆序清理。
    GatewayClient 注册为 Singleton，供各业务 Adapter 注入使用。

    Port 绑定包括：ModelAccessPort、ModelRegistryPort、SessionContextStorePort、
    ToolRegistry、ContextCompactionPort、AgentPort、AgentRegistryPort、
    TaskAgentPort、DelegationPort、ChatServicePort、PromptRegistryPort、
    ReadinessAggregator。
    """
    # ── 启动期 fail-fast：遗留 CHAT_SYSTEM_PROMPT 冲突检测 ──
    _check_legacy_prompt_conflict()
    configure_history_restore_strategy(id_validation_config.history_restore_strategy)

    global _run_store_adapter, _run_checkpoint_store_adapter, _run_worker_manager
    global _tier_resolver
    _run_store_adapter = None
    _run_checkpoint_store_adapter = None
    _run_worker_manager = None
    _tier_resolver = None

    managed_resource_names = {
        "telemetry",
        "model_client",
        "redis",
        "local_persistence",
        "gateway",
        "workspace",
        "delegate_tool_registration",
        "run_worker_manager",
    }
    container._async_resources = [
        entry for entry in container._async_resources if entry.name not in managed_resource_names
    ]

    # ── 异步资源 ──
    # Telemetry 最先初始化，确保后续资源的初始化过程也能被追踪
    container.register_async_resource("telemetry", init_telemetry, shutdown_telemetry)
    container.register_async_resource("model_client", _init_model_client, _cleanup_model_client)

    # ── 按 SESSION_STORE_BACKEND 动态注册会话后端依赖的中间件 ──
    # 需求 6.1-6.2、7.4.1-7.4.3、2.补.1：
    # - 默认 FILE：不注册 redis，不注册任何 TTL Reaper；注册
    #   local_persistence（内部会在启动期同步跑一次 TmpFileSweeper）。
    # - REDIS：保留既有 redis 异步资源注册路径，不注册 local_persistence。
    if session_store_config.backend == SessionStoreBackendKind.REDIS:
        container.register_async_resource("redis", _init_redis, _cleanup_redis)
    else:
        # FILE 后端（含默认）
        container.register_async_resource(
            "local_persistence",
            _init_local_persistence,
            _cleanup_local_persistence,
        )

    container.register_async_resource("gateway", _init_gateway, _cleanup_gateway)
    # 注意：本期（Domain_Event_Decommission）移除默认 MySQL 装配——无任何生产
    # 消费者在使用 MySQL；`infrastructure/database/` 模块保留为死代码备用
    # （未来新增 MySQL 消费者时可恢复注册）。

    # ── Workspace 启动期校验（必须在 ToolRegistry 创建之前就绪） ──
    # 受控工具（ReadFile / WriteFile / ShellExec / PythonExec 等）会在
    # _create_tool_registry 阶段通过 container.resolve(Workspace) 注入；
    # 因此 Workspace 的异步资源初始化必须排在 database 之后、ToolRegistry 之前。
    # 需求 9.1 / 9.2 / 9.3。
    container.register_async_resource("workspace", _init_workspace, _cleanup_workspace)

    # ── 基础设施组件 ──
    container.register(GatewayClient, lambda: _gateway_client, Scope.SINGLETON)
    container.register(Workspace, lambda: _workspace_singleton, Scope.SINGLETON)

    # ── Port → Adapter 绑定 ──
    container.register(ModelAccessPort, _create_model_access_adapter, Scope.SINGLETON)
    container.register(ModelRegistryPort, _create_model_registry, Scope.SINGLETON)
    register_storage_components(
        container,
        create_session_store=_create_session_store,
        create_session_index=_create_session_index,
        create_approval_state_store=_create_approval_state_store,
        create_trace_store=_create_trace_store,
        create_artifact_store=_create_artifact_store,
        create_readiness_aggregator=_create_readiness_aggregator,
    )
    register_tool_components(container, create_tool_registry=_create_tool_registry)
    register_chat_components(
        container,
        create_compaction_adapter=_create_compaction_adapter,
        create_context_builder=_create_context_builder,
        create_chat_service=_create_chat_service,
    )
    register_task_components(container, create_task_agent=_create_task_agent)

    # ── Prompt Registry ──
    container.register(PromptRegistryPort, _create_prompt_registry, Scope.SINGLETON)

    # ── DelegateToAgentTool 延迟注册（必须在所有 Singleton 注册之后）──
    # DelegateToAgentTool 依赖 DelegationPort，而 DelegationPort → TaskAgentPort
    # → AgentPort → ToolRegistry 形成运行时解析环。通过将 DelegateToAgentTool
    # 的注册作为异步资源在启动阶段执行，确保 ToolRegistry 已创建后再追加注册。
    async def _noop_cleanup() -> None:
        """空清理函数，DelegateToAgentTool 注册无需清理。"""

    register_agent_components(
        container,
        create_approval_policy=_create_approval_policy,
        create_guardrail_policy=_create_guardrail_policy,
        create_agent=_create_agent,
        create_agent_registry=_create_agent_registry,
        create_delegation_adapter=_create_delegation_adapter,
        register_delegate_tool=_register_delegate_tool,
        noop_cleanup=_noop_cleanup,
    )
    register_run_components(
        container,
        create_run_workflow_config=_create_run_workflow_config,
        create_run_workflow_registry=_create_run_workflow_registry,
        create_run_workflow_selector=_create_run_workflow_selector,
        create_workflow_run_orchestrator=_create_workflow_run_orchestrator,
        create_run_store_adapter=_create_run_store_adapter,
        create_run_guardrail_recorder=_create_run_guardrail_recorder,
        create_run_approval_resumer=_create_run_approval_resumer,
        create_run_checkpoint_store_adapter=_create_run_checkpoint_store_adapter,
        create_run_execution_coordinator=_create_run_execution_coordinator,
        create_run_recovery_service=_create_run_recovery_service,
        create_run_application_service=_create_run_application_service,
        create_run_worker_manager=_create_run_worker_manager,
        run_worker_manager_type=RunWorkerManager,
    )

    if run_runtime_config.worker_enabled:
        container.register_async_resource(
            "run_worker_manager",
            _init_run_worker_manager,
            _cleanup_run_worker_manager,
        )
