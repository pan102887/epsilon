"""评估 Rubric 定义：七维度、权重、各级判据与业界框架来源。

本模块是 spec-ai-evaluation 的"尺子"：
- 以 `DimensionRubric` 表达单个评估维度的完整结构（ID、中文标题、权重、
  扫描范围、1-5 级判据以及每级的业界框架引用）；
- 以 `load_rubric()` 一次性返回全部 7 个维度的稳定顺序集合。

设计依据：`docs/spec/spec-ai-evaluation/design.md` 「组件 1：Rubric 定义」。
权重硬约束：`architecture=0.18, agent_core=0.22, model_prompt=0.14,
security=0.16, reliability=0.12, testability=0.10, frontend_ux=0.08`，
Σ=1.0（允许浮点误差 ≤ 1e-9）。

业界框架来源覆盖以下 7 个候选框架：OpenAI、Anthropic、LangChain、
Google ADK、AgentBench、τ-bench、Berkeley FCL；每个维度跨 5 级去重后
至少涉及 2 个不同框架，以满足需求 4.1。

本模块仅持有纯数据结构，不依赖 FastAPI / Redis / LLM 客户端等基础设施。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from tests.evaluation.errors import RubricConsistencyError


class DimensionId(str, Enum):
    """七个评估维度的唯一标识符。

    成员取值对应 `docs/spec/spec-ai-evaluation/requirement.md`
    「需求 2」中枚举的七个评估维度。
    """

    ARCHITECTURE = "architecture"
    AGENT_CORE = "agent_core"
    MODEL_PROMPT = "model_prompt"
    SECURITY = "security"
    RELIABILITY = "reliability"
    TESTABILITY = "testability"
    FRONTEND_UX = "frontend_ux"


@dataclass(frozen=True)
class FrameworkCitation:
    """单条业界框架引用，供 Rubric 与改进建议复用。

    Attributes:
        framework: 框架名称，建议使用公开可识别的简称（如 "Anthropic"、
            "OpenAI"、"LangChain"、"Google ADK"、"AgentBench"、
            "τ-bench"、"Berkeley FCL"）。
        section: 所引用的具体章节或条款标题，使用英文原文以便精确匹配外链。
        url: 公开链接或出处说明；无稳定链接时可填描述性占位字符串，但须
            保持可检索性。
    """

    framework: str
    section: str
    url: str


@dataclass(frozen=True)
class RubricLevel:
    """Rubric 某一级（1-5）的判据定义。

    Attributes:
        score: 1..5 的整数等级。
        criterion: 该级的自然语言判据（中文），用于报告生成与评分对照。
        citations: 本级引用的业界框架条款序列，长度须 ≥ 2（需求 4.1）。
    """

    score: int
    criterion: str
    citations: tuple[FrameworkCitation, ...]


@dataclass(frozen=True)
class DimensionRubric:
    """单个维度的完整 Rubric。

    Attributes:
        id: 维度枚举标识。
        title: 中文标题，对应报告章节名（如 "架构与工程化"）。
        weight: 该维度在总分中的权重；全部维度 Σweight = 1.0。
        scope_backend: 该维度扫描的后端目录（相对仓库根路径），可为空元组。
        scope_frontend: 该维度扫描的前端目录（相对仓库根路径），可为空元组。
        min_evidence: 该维度最少应提供的 Evidence_Reference 条数（默认 3）。
        levels: 1-5 级判据，长度恰为 5，按 score 升序。
    """

    id: DimensionId
    title: str
    weight: float
    scope_backend: tuple[str, ...]
    scope_frontend: tuple[str, ...]
    min_evidence: int
    levels: tuple[RubricLevel, ...]


# ---------------------------------------------------------------------------
# 通用业界框架引用条目（复用给多个维度的 levels）
# ---------------------------------------------------------------------------

_OPENAI_AGENT_DESIGN = FrameworkCitation(
    framework="OpenAI",
    section="A Practical Guide to Building Agents — Agent design patterns",
    url="https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf",
)
_OPENAI_TOOL_USE = FrameworkCitation(
    framework="OpenAI",
    section="OpenAI Platform — Function calling best practices",
    url="https://platform.openai.com/docs/guides/function-calling",
)
_ANTHROPIC_TOOL_USE = FrameworkCitation(
    framework="Anthropic",
    section="Tool use with Claude — Tool definition best practices",
    url="https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/overview",
)
_ANTHROPIC_BUILDING_AGENTS = FrameworkCitation(
    framework="Anthropic",
    section="Building effective agents — Workflow & Agent patterns",
    url="https://www.anthropic.com/research/building-effective-agents",
)
_ANTHROPIC_PROMPT_CACHING = FrameworkCitation(
    framework="Anthropic",
    section="Prompt caching — Efficient long-context usage",
    url="https://docs.anthropic.com/en/docs/build-with-claude/prompt-caching",
)
_ANTHROPIC_CONTEXT_WINDOW = FrameworkCitation(
    framework="Anthropic",
    section="Long context prompting — Context window management",
    url="https://docs.anthropic.com/en/docs/build-with-claude/long-context",
)
_LANGCHAIN_AGENT = FrameworkCitation(
    framework="LangChain",
    section="LangGraph — Agent patterns & ReAct architecture",
    url="https://langchain-ai.github.io/langgraph/tutorials/introduction/",
)
_LANGCHAIN_TESTING = FrameworkCitation(
    framework="LangChain",
    section="LangSmith — Evaluation & regression testing",
    url="https://docs.smith.langchain.com/evaluation",
)
_GOOGLE_ADK = FrameworkCitation(
    framework="Google ADK",
    section="Agent Development Kit — Multi-agent architecture patterns",
    url="https://google.github.io/adk-docs/",
)
_GOOGLE_ADK_SAFETY = FrameworkCitation(
    framework="Google ADK",
    section="Agent Development Kit — Safety & guardrails",
    url="https://google.github.io/adk-docs/safety/",
)
_AGENTBENCH = FrameworkCitation(
    framework="AgentBench",
    section="AgentBench — Evaluating LLMs as Agents (task success metrics)",
    url="https://arxiv.org/abs/2308.03688",
)
_TAU_BENCH = FrameworkCitation(
    framework="τ-bench",
    section="τ-bench — Tool-use reliability & task completion metrics",
    url="https://arxiv.org/abs/2406.12045",
)
_BERKELEY_FCL = FrameworkCitation(
    framework="Berkeley FCL",
    section="Berkeley Function-Calling Leaderboard — Tool selection accuracy",
    url="https://gorilla.cs.berkeley.edu/leaderboard.html",
)


# ---------------------------------------------------------------------------
# 各维度 Rubric 定义
# ---------------------------------------------------------------------------


def _architecture_rubric() -> DimensionRubric:
    """构造「架构与工程化」维度 Rubric。"""

    return DimensionRubric(
        id=DimensionId.ARCHITECTURE,
        title="架构与工程化",
        weight=0.18,
        scope_backend=(
            "epsilon-boot/src/application/",
            "epsilon-boot/src/domain/",
            "epsilon-boot/src/infrastructure/",
            "epsilon-boot/src/common/",
        ),
        scope_frontend=("epsilon-client/src/",),
        min_evidence=3,
        levels=(
            RubricLevel(
                score=1,
                criterion="分层混乱，domain 直接依赖基础设施或 HTTP 框架；无 DI 容器或容器绕过严重，新增能力需改动核心组合根之外的多处代码。",
                citations=(_OPENAI_AGENT_DESIGN, _GOOGLE_ADK),
            ),
            RubricLevel(
                score=2,
                criterion="分层存在但边界不清，Port/Adapter 命名与职责错位；DI 仅对部分依赖生效，组合根与业务代码耦合。",
                citations=(_GOOGLE_ADK, _LANGCHAIN_AGENT),
            ),
            RubricLevel(
                score=3,
                criterion="DDD 分层基本合规，Port 与 Adapter 一一对应但仍有 application → infrastructure 直连的少量例外；依赖注入覆盖主链路但缺统一生命周期管理。",
                citations=(_OPENAI_AGENT_DESIGN, _GOOGLE_ADK),
            ),
            RubricLevel(
                score=4,
                criterion="Port/Adapter 全量映射、依赖方向单向、DI 容器托管全部异步资源生命周期；组合根集中在 container_config.py，业务代码无 Adapter 直引用。",
                citations=(_GOOGLE_ADK, _ANTHROPIC_BUILDING_AGENTS),
            ),
            RubricLevel(
                score=5,
                criterion="在 4 分基础上具备可插拔扩展策略（多 Provider / 工具开关 / 委派 DAG 可热重载），并有机器可读的架构守卫（如 import-linter 或等价自定义脚本）作为 CI 防线。",
                citations=(_GOOGLE_ADK, _OPENAI_AGENT_DESIGN, _ANTHROPIC_BUILDING_AGENTS),
            ),
        ),
    )


def _agent_core_rubric() -> DimensionRubric:
    """构造「Agent 核心能力」维度 Rubric。"""

    return DimensionRubric(
        id=DimensionId.AGENT_CORE,
        title="Agent 核心能力",
        weight=0.22,
        scope_backend=(
            "epsilon-boot/src/domain/agent/",
            "epsilon-boot/src/domain/chat/",
            "epsilon-boot/src/infrastructure/agent/",
            "epsilon-boot/src/infrastructure/tools/",
        ),
        scope_frontend=(),
        min_evidence=3,
        levels=(
            RubricLevel(
                score=1,
                criterion="仅有单轮 LLM 调用，无 ReAct 循环或 tool_calls 处理；无工具权限隔离，无委派概念。",
                citations=(_ANTHROPIC_TOOL_USE, _LANGCHAIN_AGENT),
            ),
            RubricLevel(
                score=2,
                criterion="实现 ReAct 雏形但缺最大轮次保护或异常路径；工具注册为扁平列表，无 scoped 视图；无委派。",
                citations=(_ANTHROPIC_TOOL_USE, _BERKELEY_FCL),
            ),
            RubricLevel(
                score=3,
                criterion="ReAct 循环具备最大轮次与异常回写 ToolMessage；工具注册支持白名单视图；具备基础委派但深度未受控。",
                citations=(_ANTHROPIC_TOOL_USE, _LANGCHAIN_AGENT, _BERKELEY_FCL),
            ),
            RubricLevel(
                score=4,
                criterion="ReAct 循环覆盖权限拒绝回写、tool_calls 序列化与异常路径；ScopedToolRegistry 按 allowed_tool_names 暴露；委派有 delegation_depth 上限与循环依赖解法；上下文压缩保留全部 SystemMessage。",
                citations=(_ANTHROPIC_TOOL_USE, _ANTHROPIC_CONTEXT_WINDOW, _LANGCHAIN_AGENT),
            ),
            RubricLevel(
                score=5,
                criterion="在 4 分基础上具备工具调用与委派的端到端可观测性（span / trace），以及上下文压缩的可插拔策略与回归评测脚本，指标覆盖 Tool_Call_Success_Rate / Delegation_Correctness / Context_Compaction_Effectiveness。",
                citations=(_ANTHROPIC_BUILDING_AGENTS, _TAU_BENCH, _BERKELEY_FCL),
            ),
        ),
    )


def _model_prompt_rubric() -> DimensionRubric:
    """构造「模型与提示工程」维度 Rubric。"""

    return DimensionRubric(
        id=DimensionId.MODEL_PROMPT,
        title="模型与提示工程",
        weight=0.14,
        scope_backend=(
            "epsilon-boot/src/domain/model_access/",
            "epsilon-boot/src/infrastructure/model_access/",
            "epsilon-boot/config.properties",
        ),
        scope_frontend=(),
        min_evidence=3,
        levels=(
            RubricLevel(
                score=1,
                criterion="硬编码单一模型与 API Key；Prompt 直接嵌在业务代码中；无模板化与版本管理。",
                citations=(_OPENAI_TOOL_USE, _ANTHROPIC_PROMPT_CACHING),
            ),
            RubricLevel(
                score=2,
                criterion="支持从配置读取模型，但切换需重启；Prompt 有初步模板但版本不可追踪；无路由策略。",
                citations=(_OPENAI_AGENT_DESIGN, _ANTHROPIC_PROMPT_CACHING),
            ),
            RubricLevel(
                score=3,
                criterion="多 Provider 注册、按模型名路由；Prompt 以模板文件组织；具备基础 token 观测但无成本归因。",
                citations=(_OPENAI_AGENT_DESIGN, _ANTHROPIC_CONTEXT_WINDOW),
            ),
            RubricLevel(
                score=4,
                criterion="多 Provider 注册 + Round-Robin + 配置热重载；Prompt 支持 prompt caching 与变量注入；token 观测按会话聚合。",
                citations=(_OPENAI_AGENT_DESIGN, _ANTHROPIC_PROMPT_CACHING, _ANTHROPIC_CONTEXT_WINDOW),
            ),
            RubricLevel(
                score=5,
                criterion="在 4 分基础上具备按任务类型/成本的智能路由、Prompt 评估集（eval 套件）与 A/B 对照；Prompt 与模型变更有回归评测守卫。",
                citations=(_OPENAI_AGENT_DESIGN, _ANTHROPIC_PROMPT_CACHING, _LANGCHAIN_TESTING),
            ),
        ),
    )


def _security_rubric() -> DimensionRubric:
    """构造「安全与合规」维度 Rubric。"""

    return DimensionRubric(
        id=DimensionId.SECURITY,
        title="安全与合规",
        weight=0.16,
        scope_backend=(
            "epsilon-boot/src/infrastructure/tools/",
            "epsilon-boot/src/domain/workspace/",
            "epsilon-boot/config.properties",
        ),
        scope_frontend=(),
        min_evidence=3,
        levels=(
            RubricLevel(
                score=1,
                criterion="Shell/Python 执行工具不做环境变量脱敏、无超时与输出截断、cwd 未限定；凭证硬编码在源码中。",
                citations=(_ANTHROPIC_BUILDING_AGENTS, _GOOGLE_ADK_SAFETY),
            ),
            RubricLevel(
                score=2,
                criterion="凭证移出源码到 .env；工具有超时但无输出截断或环境变量脱敏；Workspace 存在但未覆盖 symlink 逃逸。",
                citations=(_ANTHROPIC_BUILDING_AGENTS, _GOOGLE_ADK_SAFETY),
            ),
            RubricLevel(
                score=3,
                criterion="环境变量按 API_KEY / PASSWORD / SECRET / TOKEN / CREDENTIAL 前缀脱敏；工具具备超时与输出截断；Workspace 对文件工具生效但未形成闭包。",
                citations=(_OPENAI_TOOL_USE, _GOOGLE_ADK_SAFETY),
            ),
            RubricLevel(
                score=4,
                criterion="Shell/Python 工具 cwd 锁定 WORKSPACE_ROOT；SymlinkGuard / IdentityGuard 覆盖逃逸场景；凭证优先 config.properties，.env 仅作覆盖；启动期路径校验（SHELL_EXEC_WORKING_DIR / PYTHON_EXEC_WORKING_DIR）落地。",
                citations=(_OPENAI_TOOL_USE, _GOOGLE_ADK_SAFETY, _ANTHROPIC_BUILDING_AGENTS),
            ),
            RubricLevel(
                score=5,
                criterion="在 4 分基础上具备 Prompt Injection 防御分层（系统提示隔离 + 工具输入白名单）、工具滥用检测与告警、凭证轮转手册，并有红蓝对抗演练证据。",
                citations=(_GOOGLE_ADK_SAFETY, _ANTHROPIC_BUILDING_AGENTS, _OPENAI_AGENT_DESIGN),
            ),
        ),
    )


def _reliability_rubric() -> DimensionRubric:
    """构造「可靠性与性能」维度 Rubric。"""

    return DimensionRubric(
        id=DimensionId.RELIABILITY,
        title="可靠性与性能",
        weight=0.12,
        scope_backend=(
            "epsilon-boot/src/application/",
            "epsilon-boot/src/infrastructure/observability/",
            "epsilon-boot/src/infrastructure/model_access/",
        ),
        scope_frontend=(),
        min_evidence=3,
        levels=(
            RubricLevel(
                score=1,
                criterion="SSE 流式无错误恢复；Provider 故障无退避；无延迟 / token 观测手段。",
                citations=(_OPENAI_AGENT_DESIGN, _LANGCHAIN_TESTING),
            ),
            RubricLevel(
                score=2,
                criterion="SSE 出错回写一次性错误事件；Provider 仅支持静态主备切换；仅有日志观测。",
                citations=(_OPENAI_AGENT_DESIGN, _ANTHROPIC_BUILDING_AGENTS),
            ),
            RubricLevel(
                score=3,
                criterion="SSE 错误恢复 + [DONE] 协议；Provider Round-Robin；基础 metrics（延迟、token）暴露到 Prometheus。",
                citations=(_OPENAI_AGENT_DESIGN, _ANTHROPIC_BUILDING_AGENTS),
            ),
            RubricLevel(
                score=4,
                criterion="ReAct 失败路径全量 ToolMessage 回写；Provider 热重载与健康探测；OpenTelemetry trace 贯穿 FastAPI / httpx / Redis / SQLAlchemy；成本归因可用。",
                citations=(_OPENAI_AGENT_DESIGN, _ANTHROPIC_BUILDING_AGENTS, _TAU_BENCH),
            ),
            RubricLevel(
                score=5,
                criterion="在 4 分基础上具备 SLO/SLI 定义、预算告警、限流 / 重试策略、混沌演练回归，评测脚本对核心指标做回归守护（退出码 2 作为 CI 失败信号）。",
                citations=(_OPENAI_AGENT_DESIGN, _TAU_BENCH, _LANGCHAIN_TESTING),
            ),
        ),
    )


def _testability_rubric() -> DimensionRubric:
    """构造「可测试性与质量」维度 Rubric。"""

    return DimensionRubric(
        id=DimensionId.TESTABILITY,
        title="可测试性与质量",
        weight=0.10,
        scope_backend=(
            "epsilon-boot/test/",
            "epsilon-boot/src/domain/",
            "epsilon-boot/src/infrastructure/",
        ),
        scope_frontend=(
            "epsilon-client/src/",
        ),
        min_evidence=3,
        levels=(
            RubricLevel(
                score=1,
                criterion="无单元测试或仅有少量快照；Port/Adapter 无桩实现；无回归评测脚本。",
                citations=(_LANGCHAIN_TESTING, _AGENTBENCH),
            ),
            RubricLevel(
                score=2,
                criterion="存在 unit 测试但覆盖主链路不全；集成测试依赖真实模型/数据库；无确定性评测。",
                citations=(_AGENTBENCH, _BERKELEY_FCL),
            ),
            RubricLevel(
                score=3,
                criterion="unit / property / integration 三层有基础分布；桩 Port 可用但未覆盖三项核心指标；有但非自动化的巡检。",
                citations=(_LANGCHAIN_TESTING, _BERKELEY_FCL),
            ),
            RubricLevel(
                score=4,
                criterion="三层测试分布清晰，评测脚本覆盖 Tool_Call_Success_Rate / Delegation_Correctness / Context_Compaction_Effectiveness，均走桩 Port；支持回归基线对比与阈值退出码。",
                citations=(_AGENTBENCH, _TAU_BENCH, _BERKELEY_FCL),
            ),
            RubricLevel(
                score=5,
                criterion="在 4 分基础上具备 LLM-as-judge（可选、默认关闭）、Prompt 评估集、前端 UX 自动化巡检；评测进入 CI 作为 PR 门禁。",
                citations=(_TAU_BENCH, _BERKELEY_FCL, _LANGCHAIN_TESTING),
            ),
        ),
    )


def _frontend_ux_rubric() -> DimensionRubric:
    """构造「前端/UX」维度 Rubric。"""

    return DimensionRubric(
        id=DimensionId.FRONTEND_UX,
        title="前端/UX",
        weight=0.08,
        scope_backend=(),
        scope_frontend=(
            "epsilon-client/src/",
        ),
        min_evidence=3,
        levels=(
            RubricLevel(
                score=1,
                criterion="仅支持整段返回，无流式渲染；无错误提示与中止能力；无任务工作区。",
                citations=(_OPENAI_AGENT_DESIGN, _ANTHROPIC_BUILDING_AGENTS),
            ),
            RubricLevel(
                score=2,
                criterion="支持 SSE 流式渲染但无 [DONE] 协议；错误以全局提示展示；无 AbortController。",
                citations=(_OPENAI_AGENT_DESIGN, _LANGCHAIN_AGENT),
            ),
            RubricLevel(
                score=3,
                criterion="ChatPanel 增量渲染 + SSE [DONE] 协议处理；TaskWorkspace 结果展示；错误可重试但无中止；无 trace 可见性。",
                citations=(_OPENAI_AGENT_DESIGN, _LANGCHAIN_AGENT),
            ),
            RubricLevel(
                score=4,
                criterion="流式渲染 + AbortController 中止 + 模型选择与会话管理；task 执行轨迹（execution_trace）在前端可见；错误分类展示。",
                citations=(_OPENAI_AGENT_DESIGN, _ANTHROPIC_BUILDING_AGENTS, _LANGCHAIN_AGENT),
            ),
            RubricLevel(
                score=5,
                criterion="在 4 分基础上具备用户反馈通道（点赞/点踩/复制/重试）与反馈数据回流；trace 视图含工具调用与委派可展开；满足无障碍与键盘可达性基线。",
                citations=(_ANTHROPIC_BUILDING_AGENTS, _OPENAI_AGENT_DESIGN, _LANGCHAIN_AGENT),
            ),
        ),
    )


# Rubric 构造器清单；`load_rubric` 按此顺序返回，顺序稳定性由本列表保证。
_RUBRIC_BUILDERS: tuple = (
    _architecture_rubric,
    _agent_core_rubric,
    _model_prompt_rubric,
    _security_rubric,
    _reliability_rubric,
    _testability_rubric,
    _frontend_ux_rubric,
)


def _validate_rubric(rubrics: tuple[DimensionRubric, ...]) -> None:
    """校验 Rubric 一致性。

    校验点：
    1. 维度数量恰为 7；
    2. DimensionId 不重复；
    3. 权重之和与 1.0 的偏差 ≤ 1e-9；
    4. 每个维度 5 级齐全（score 为 1..5）；
    5. 每级 citations 至少 2 条；
    6. 每个维度跨 5 级去重后的 framework 数 ≥ 2。

    任一条件不满足则抛 `RubricConsistencyError`。
    """

    if len(rubrics) != 7:
        raise RubricConsistencyError(
            f"Rubric 维度数量应为 7，实际为 {len(rubrics)}"
        )

    ids = [r.id for r in rubrics]
    if len(set(ids)) != 7:
        raise RubricConsistencyError(f"Rubric 维度 ID 存在重复：{ids!r}")

    weight_sum = sum(r.weight for r in rubrics)
    if abs(weight_sum - 1.0) > 1e-9:
        raise RubricConsistencyError(
            f"Rubric 权重之和应为 1.0，实际为 {weight_sum!r}"
        )

    for r in rubrics:
        scores = [lvl.score for lvl in r.levels]
        if scores != [1, 2, 3, 4, 5]:
            raise RubricConsistencyError(
                f"维度 {r.id.value} 的 levels 必须恰为 1..5，实际为 {scores!r}"
            )
        for lvl in r.levels:
            if len(lvl.citations) < 2:
                raise RubricConsistencyError(
                    f"维度 {r.id.value} score={lvl.score} 引用框架数 "
                    f"{len(lvl.citations)} < 2，不满足需求 4.1"
                )
        frameworks = {c.framework for lvl in r.levels for c in lvl.citations}
        if len(frameworks) < 2:
            raise RubricConsistencyError(
                f"维度 {r.id.value} 跨级去重后框架数 {len(frameworks)} < 2，"
                f"不满足需求 4.1"
            )


def load_rubric() -> tuple[DimensionRubric, ...]:
    """加载全部 7 个维度的 Rubric，返回稳定顺序。

    顺序由模块级 `_RUBRIC_BUILDERS` 决定：
    architecture → agent_core → model_prompt → security →
    reliability → testability → frontend_ux。

    Returns:
        长度为 7 的 `DimensionRubric` 元组，已通过一致性校验。

    Raises:
        RubricConsistencyError: Rubric 自身一致性校验失败（权重不归一、
            维度数量错误、citations 不足等）。
    """

    rubrics = tuple(builder() for builder in _RUBRIC_BUILDERS)
    _validate_rubric(rubrics)
    return rubrics
