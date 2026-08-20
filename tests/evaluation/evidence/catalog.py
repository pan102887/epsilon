"""各维度「必备证据清单」。

本模块以 ``DimensionId.value → list[EvidenceReference]`` 的形式登记
spec-ai-evaluation 每个评估维度对应的证据引用。阶段 1 建立骨架（七键
空列表），阶段 4 在此处按维度逐条回填真实证据，每个维度 ≥ 3 条，覆盖
源码锚点（``path:Lstart-Lend``）、配置键（``config.properties:<KEY>``）
或文档路径（``PATH_ONLY``）。

每条 ``EvidenceReference.raw`` 的路径与行号均以仓库根为基准，经
``verify_evidence`` 校验器确认真实存在；``description`` 为一句话中文
说明，供报告渲染器直接引用。

对外仅暴露一个公开函数 ``load_catalog()``，返回浅拷贝字典。
"""

from __future__ import annotations

from tests.evaluation.evidence.models import EvidenceReference, parse_reference
from tests.evaluation.rubric import DimensionId


def _ref(raw: str, description: str) -> EvidenceReference:
    """构造单条 ``EvidenceReference`` 的便捷封装。

    Args:
        raw: 证据原始引用串（``path:Lstart-Lend`` / ``path:Lstart`` / ``path``
            / ``config.properties:<KEY>``）。
        description: 一句话中文证据描述。

    Returns:
        解析后的 ``EvidenceReference`` 实例；格式非法会直接抛
        ``EvidenceFormatError``（阶段 1 自测覆盖）。
    """

    return parse_reference(raw, description)


# ---------------------------------------------------------------------------
# 维度 1：架构与工程化
# ---------------------------------------------------------------------------

_ARCHITECTURE_EVIDENCES: list[EvidenceReference] = [
    _ref(
        "epsilon-boot/src/application/container_config.py:1017-1042",
        "container_config.py 通过 container.register 把所有 Port（ModelAccess / "
        "SessionContextStore / ToolRegistry / ContextCompactionPort / AgentPort / "
        "AgentRegistryPort / TaskAgentPort / DelegationPort / ChatServicePort 等）"
        "显式绑定到 Adapter，组合根集中在此一文件中。",
    ),
    _ref(
        "epsilon-boot/src/common/container.py:80-98",
        "自建 DI 容器 Container 维护 _registry 与 _async_resources 两套注册表，"
        "通过 Scope（Singleton/Transient）+ 异步资源生命周期托管 Port→Adapter 装配。",
    ),
    _ref(
        "epsilon-boot/src/application/container_config.py:982-1011",
        "configure_container() 按注册顺序装配 telemetry / model_client / redis "
        "或 local_persistence / gateway / workspace 五类异步资源，关闭时逆序清理，"
        "体现统一的异步资源生命周期管理。",
    ),
    _ref(
        "docs/steering/ddd-architecture.md",
        "项目 DDD 架构 Steering 规范：明确 domain → application → infrastructure "
        "的依赖方向，以及 Port / Adapter 的归属与允许例外，是评估架构合规性的基线。",
    ),
]


# ---------------------------------------------------------------------------
# 维度 2：Agent 核心能力
# ---------------------------------------------------------------------------

_AGENT_CORE_EVIDENCES: list[EvidenceReference] = [
    _ref(
        "epsilon-boot/src/infrastructure/agent/react_agent_adapter.py:128-189",
        "ReActAgentAdapter.run 完整实现 ReAct 循环：压缩 → 序列化 → LLM 调用 → "
        "tool_calls 执行 → 权限拒绝回写 ToolMessage 与异常吸收，并以 "
        "config.max_rounds 作为最大轮次保护。",
    ),
    _ref(
        "epsilon-boot/src/domain/agent/tools.py:331-401",
        "ToolRegistry.create_scoped_view 返回 ScopedToolRegistry，"
        "按 frozenset[str] 白名单暴露工具子集；未在集合内的工具调用抛 "
        "ToolPermissionDeniedError，实现 Agent 粒度的最小权限隔离。",
    ),
    _ref(
        "epsilon-boot/src/infrastructure/agent/delegate_to_agent_tool.py:128-142",
        "DelegateToAgentTool.execute 在调用 DelegationPort 前进行 "
        "next_depth = current + 1 的深度越限校验，超出 max_delegation_depth 时"
        "抛 DelegationDepthExceededError，避免委派无限递归。",
    ),
    _ref(
        "epsilon-boot/src/infrastructure/chat/sliding_window_compaction_adapter.py:42-64",
        "SlidingWindowCompactionAdapter.compact_messages 分离 system 与非 system 消息，"
        "全量保留 SystemMessage 并截取末尾 max_messages 条非 system 消息，"
        "以窗口 N 由配置驱动的方式达成上下文压缩。",
    ),
]


# ---------------------------------------------------------------------------
# 维度 3：模型与提示工程
# ---------------------------------------------------------------------------

_MODEL_PROMPT_EVIDENCES: list[EvidenceReference] = [
    _ref(
        "epsilon-boot/src/application/container_config.py:60-65",
        "PROVIDERS 显式登记 cliproxy / zhipu / deepseek / qwen 四类 Provider，"
        "由 _init_model_client 逐一读取配置并注册到 ProviderRegistry，体现"
        "多 Provider 可插拔能力。",
    ),
    _ref(
        "epsilon-boot/src/infrastructure/model_access/provider_registry.py:146-200",
        "ProviderRegistry.get_adapter_for_model 以 itertools.cycle 维护每个模型"
        "的 Round-Robin 迭代器，实现跨 Provider 的请求均匀分布。",
    ),
    _ref(
        "epsilon-boot/src/infrastructure/model_access/router_config.py:17-43",
        "RouterConfig 通过 hot_reload = True 开启配置热更新，由 create_config 工厂"
        "决定是否返回 ConfigProxy 代理，驱动 default_provider / default_model / "
        "routing_strategy 三类路由决策参数在运行期刷新。",
    ),
    _ref(
        "epsilon-boot/config.properties:MODEL_CLIPROXY_MODELS",
        "config.properties 为每个 Provider 显式登记 MODEL_*_MODELS / API_KEY / "
        "DEFAULT_MODEL 等键，驱动注册流程和 /v1/models 路由清单，是 Prompt / "
        "模型路由的唯一权威配置源。",
    ),
]


# ---------------------------------------------------------------------------
# 维度 4：安全与合规
# ---------------------------------------------------------------------------

_SECURITY_EVIDENCES: list[EvidenceReference] = [
    _ref(
        "epsilon-boot/src/infrastructure/tools/shell_exec/shell_exec_tool.py:59-98",
        "ShellExecTool 通过 _SENSITIVE_KEYWORDS（KEY/SECRET/PASSWORD/TOKEN/"
        "CREDENTIAL）与平台保留变量白名单组合，构造 sanitize_env() 剥离敏感环境"
        "变量；子进程 env 参数来自该清洁副本。",
    ),
    _ref(
        "epsilon-boot/src/infrastructure/tools/shell_exec/shell_exec_tool.py:217-249",
        "ShellExecTool.execute 先做 local_materialization 能力守卫，再通过 "
        "Workspace.resolve_path + materialize_cwd 把工作区相对路径转成宿主 "
        "cwd；越界抛 WorkspaceConfinementViolation → ToolExecutionError。",
    ),
    _ref(
        "epsilon-boot/src/infrastructure/tools/python_exec/python_exec_tool.py:42-50",
        "PythonExecTool 复用 shell_exec_tool.sanitize_env 并在模块级声明 "
        "BLOCKED_CALLS 黑名单，覆盖 exec/eval/compile/__import__/open 等危险调用，"
        "与 AST 静态分析共同构成沙箱基线。",
    ),
    _ref(
        "epsilon-boot/src/infrastructure/workspace/local_filesystem/_guards.py:31-151",
        "SymlinkGuard 在 follow_symlinks=False 严格模式下逐段 os.lstat 校验，"
        "任一祖先为符号链接立即抛 WorkspaceConfinementViolation(SYMLINK_ESCAPE)，"
        "覆盖符号链接逃逸场景。",
    ),
    _ref(
        "epsilon-boot/src/infrastructure/workspace/local_filesystem/_guards.py:154-215",
        "IdentityGuard 在启动期缓存 root 的 st_dev，每次 I/O 前比对目标或最近"
        "存在祖先的 st_dev，不一致即抛 WorkspaceConfinementViolation(CROSS_DEVICE)，"
        "防御挂载点 / 大小写折叠逃逸。",
    ),
    _ref(
        "epsilon-boot/src/application/container_config.py:212-275",
        "_validate_exec_working_dir 在 ToolRegistry 创建阶段对 SHELL_EXEC_WORKING_DIR "
        "/ PYTHON_EXEC_WORKING_DIR 做启动期二次校验，越界翻译为 "
        "ConfigurationError 触发 fail-fast 回滚。",
    ),
    _ref(
        "epsilon-boot/config.properties:AGENT_MAX_DELEGATION_DEPTH",
        "config.properties 显式登记 AGENT_MAX_DELEGATION_DEPTH=3 与 "
        "AGENT_DELEGATE_TOOL_ENABLED=true，限制 Agent 间委派递归深度，"
        "是安全合规的配置源证据。",
    ),
]


# ---------------------------------------------------------------------------
# 维度 5：可靠性与性能
# ---------------------------------------------------------------------------

_RELIABILITY_EVIDENCES: list[EvidenceReference] = [
    _ref(
        "epsilon-boot/src/application/routers/chat.py:108-134",
        "chat 路由的 _event_generator 在 SSE 流式中捕获任意异常并回写 error 事件"
        "+ [DONE] 标记，避免异常冒泡到 sse_starlette 的 TaskGroup 导致连接断开；"
        "这是 SSE 可靠性的核心路径。",
    ),
    _ref(
        "epsilon-boot/src/infrastructure/agent/react_agent_adapter.py:155-178",
        "ReActAgentAdapter 对工具权限拒绝 / 执行异常统一以 ToolMessage 回写上下文，"
        "保证 Agent Loop 在失败路径上仍能交还模型继续决策，而不是整批中止。",
    ),
    _ref(
        "epsilon-boot/src/infrastructure/model_access/provider_registry.py:170-200",
        "ProviderRegistry 的 Round-Robin 策略在单 Provider 故障时切换到同模型的"
        "其它 Provider，并在提供商被移除后重建迭代器，提供基础的故障均摊能力。",
    ),
    _ref(
        "epsilon-boot/src/infrastructure/telemetry/otel_config.py:16-75",
        "OtelConfig 暴露 enabled / exporter_endpoint / traces_sampler / "
        "instrument_fastapi / instrument_httpx / instrument_redis / "
        "instrument_sqlalchemy 等开关，驱动 OpenTelemetry 链路追踪的延迟与 token "
        "观测入口。",
    ),
    _ref(
        "epsilon-boot/src/infrastructure/telemetry/otel_setup.py:1-44",
        "otel_setup 按配置构建 Resource / Sampler / BatchSpanProcessor，注册为"
        "异步资源在容器启动时初始化、停止时刷新 span，是可观测链路的基础设施骨架。",
    ),
]


# ---------------------------------------------------------------------------
# 维度 6：可测试性与质量
# ---------------------------------------------------------------------------

_TESTABILITY_EVIDENCES: list[EvidenceReference] = [
    _ref(
        "epsilon-boot/test",
        "epsilon-boot/test/ 按 domain / application / infrastructure / "
        "integration 分层组织测试，覆盖领域值对象、Adapter、Router、多进程并发与"
        "启动期校验等场景，作为本次回归评测的既有测试基线。",
    ),
    _ref(
        "tests/evaluation/metrics/test_tool_call_success_rate.py",
        "本特性新增的 Tool_Call_Success_Rate 指标脚本，通过桩 ScriptedModelAccess "
        "+ 真实 ReActAgentAdapter 产出 20 条样本，填补 Agent 工具调用回归缺口。",
    ),
    _ref(
        "tests/evaluation/metrics/test_delegation_correctness.py",
        "本特性新增的 Delegation_Correctness 指标脚本，覆盖 success / "
        "depth_exceeded / not_found / cycle_depth_exceeded / content_echo 五类"
        "场景共 15 条样本，回归 Agent 间委派正确性。",
    ),
    _ref(
        "tests/evaluation/metrics/test_context_compaction_effectiveness.py",
        "本特性新增的 Context_Compaction_Effectiveness 指标脚本，以 "
        "(L, S, N) 三维参数化生成 36 条样本，直接驱动 "
        "SlidingWindowCompactionAdapter 验证 SystemMessage 无损与窗口 N 语义。",
    ),
    _ref(
        "tests/evaluation/self_tests/test_rubric_consistency.py",
        "Rubric 自测覆盖 7 维度存在性、权重归一、5 级齐全、citations ≥ 2 个不同"
        "框架，作为评测脚本自身质量的门禁。",
    ),
]


# ---------------------------------------------------------------------------
# 维度 7：前端 / UX
# ---------------------------------------------------------------------------

_FRONTEND_UX_EVIDENCES: list[EvidenceReference] = [
    _ref(
        "epsilon-client/src/hooks/use-chat.ts:56-128",
        "useChat Hook 通过 AbortController + streamChat 组合，支持流式增量拼接、"
        "主动中止与错误态消息占位清理；是 ChatPanel 的状态中枢。",
    ),
    _ref(
        "epsilon-client/src/lib/chat-api.ts:86-154",
        "streamChat 以 fetch + ReadableStream 解析 SSE 行流，识别 [DONE] 标记并"
        "调用 onDone；捕获 AbortError 与其它 Error 分别静默或上报给调用方。",
    ),
    _ref(
        "epsilon-client/src/components/chat/chat-panel.tsx:35-78",
        "ChatPanel 将 ChatHeader + ModelSelector + MessageList + ChatInput 组合为"
        "聊天主界面，并把 abort / clearChat 回调直连到输入框与头部，满足"
        "流式中止与会话清除两类交互。",
    ),
    _ref(
        "epsilon-client/src/components/task/task-workspace.tsx:61-229",
        "TaskWorkspace 展示任务执行结果的 status / latency / model / trace，"
        "说明后端已把 execution_trace 暴露到前端；但无法在聊天侧实时查看 trace，"
        "缺失 chat → trace 联动与反馈通道。",
    ),
]


# ---------------------------------------------------------------------------
# 目录装配
# ---------------------------------------------------------------------------

_CATALOG: dict[str, list[EvidenceReference]] = {
    DimensionId.ARCHITECTURE.value: _ARCHITECTURE_EVIDENCES,
    DimensionId.AGENT_CORE.value: _AGENT_CORE_EVIDENCES,
    DimensionId.MODEL_PROMPT.value: _MODEL_PROMPT_EVIDENCES,
    DimensionId.SECURITY.value: _SECURITY_EVIDENCES,
    DimensionId.RELIABILITY.value: _RELIABILITY_EVIDENCES,
    DimensionId.TESTABILITY.value: _TESTABILITY_EVIDENCES,
    DimensionId.FRONTEND_UX.value: _FRONTEND_UX_EVIDENCES,
}


def load_catalog() -> dict[str, list[EvidenceReference]]:
    """加载证据目录。

    Returns:
        以维度 ID（字符串）为键、证据引用列表为值的浅拷贝字典。
        每个维度的列表长度 ≥ 3，详细证据由阶段 4 录入；`verify_evidence`
        脚本将在阶段 5 联调时逐条校验路径与行号存在性。

        返回浅拷贝以避免调用方修改模块级 ``_CATALOG``；列表内的
        ``EvidenceReference`` 本身为 frozen dataclass，天然不可变。
    """

    return {dim_id: list(refs) for dim_id, refs in _CATALOG.items()}
