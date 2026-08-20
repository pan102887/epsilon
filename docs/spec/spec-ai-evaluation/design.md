# 设计文档：AI Agent 工作台系统性评估（spec-ai-evaluation）

## 概述

本特性为 `epsilon-boot`（FastAPI + DDD 六边形后端）与 `epsilon-client`（Next.js 控制台）交付一次"以业界 AI-Agent 应用标准"为尺的系统性自评：一份对管理层/研发/QA 三角色可用的 Markdown `Evaluation_Report`，加一套可复测、可进入回归流水的 `Evaluation_Script`。两类产物均为"纯增量"，仅落在 `docs/evaluation/`、`tests/evaluation/`、`scripts/evaluation/` 三个目录，不触碰任何业务源码。

设计显式遵循四份强制规范：
- `docs/steering/ddd-architecture.md`：评测代码属于"工具/基础设施"视角，不新增 `domain/` → `infrastructure/` 导入、不绕过 Port 直接调用 Adapter 实现细节；对被测项通过领域公开模型与 Port 接口注入桩实现达成解耦。
- `docs/steering/config-source.md`：脚本若需要运行时配置，一律从 `epsilon-boot/config.properties` 读取（经由既有 `PropertiesBaseSettings` / `config_proxy`），不新建配置文件；本特性自身阈值等纯评测参数落在 `tests/evaluation/config/eval.toml`，与业务配置隔离。
- `docs/steering/uv-package-manager.md`：后端侧所有命令使用 `uv run …`；若需要新依赖（如 `jsonschema`、`rich`），通过 `uv add --group evaluation <pkg>` 写入 `pyproject.toml` 的 `[dependency-groups].evaluation`，更新 `uv.lock`，禁用 `pip`/`poetry`。
- `docs/steering/code-documentation.md`：评测脚本中的模块、类、公开函数/方法、复杂算法均提供中文 docstring。

## 设计决策

| 决策 | 选择方案 | 理由 |
|---|---|---|
| 报告结构 | 主报告 `docs/evaluation/report.md` + 每维度一份子报告 `docs/evaluation/dimensions/<n>-<slug>.md` | 主报告聚合"执行摘要 / 读者导览 / 评分表 / 改进清单 / 附录"，子报告承载长篇证据与 Rubric 详解；避免单文件过长，便于分维度归档。（对齐需求 2、需求 14） |
| Rubric 打分粒度 | 每维度采用 1-5 级离散评分，每级附 2 条以上业界框架条款判据 | 1-5 级是 AgentBench / τ-bench 等公开工作的常用粒度；显式列条款满足需求 4。 |
| 总分算法 | 加权平均 `Σ(dim_score × weight) / Σ(weight)`，权重固定（见 Rubric 章节） | 避免主观再加工；权重固定在 Rubric 内，出现改权重的提案时以"改进建议"形式提出而非运行时调整。 |
| 证据引用格式 | `相对路径:Lstart-Lend` / `相对路径:Lstart` / `相对路径` / `config.properties:<key>` | 对齐需求 3.2；报告生成器对格式做正则校验。 |
| 自动化指标选型 | Tool_Call_Success_Rate、Delegation_Correctness、Context_Compaction_Effectiveness 三项（需求 5.1 硬约束） | 三项映射到 ReAct_Loop、委派、滑动窗口三个关键路径；可用"注入桩 Port"方式稳定测量，不依赖真实 LLM Provider。 |
| 被测单元注入方式 | 通过 `domain/*/ports.py` 的 Protocol 接口，由评测脚本向真实 Adapter 构造函数手动传入桩依赖；默认走桩 `ModelAccessPort` 与桩 `AgentRegistryPort` | 保持"不动业务代码"约束；Port 本身是结构类型协议，无需继承；符合 DDD 依赖方向。 |
| LLM-as-judge | 本期**不引入**，全部三项指标使用确定性判定（规则 / 结构化断言 / 字段比对） | 抖动与成本均不可控，且需求 5.3 要求"不要求真实 LLM Provider 可达"；如未来引入，将在 `tests/evaluation/judges/` 下单独模块化，不影响现有指标。 |
| 回归阈值语义 | 核心指标下降 ≥ 5 个百分点 → 非零退出码；阈值可经 `--regression-threshold` 覆盖（需求 10.4） | 百分点差比率差更直观；退出码 2 表示"指标回退"、1 表示"脚本自身异常"、0 表示"运行成功且未回退"。 |
| 行号漂移应对 | 报告生成器对 Evidence 做"首次生成时锚点抓取 + 关键字摘录"；脚本 `scripts/evaluation/verify_evidence.py` 可复核证据路径存在、起止行号合法且摘录仍与源码匹配 | 源码迁移后能以非零退出码提醒"证据失效"，不依赖人工全量巡检。 |
| 脚本入口组织 | 单一入口 `scripts/evaluation/run_eval.py`；`tests/evaluation/` 下用 pytest 标记 `@pytest.mark.evaluation` 注册评测用例；入口内部用 pytest 的 `main()` 驱动收集 | 符合需求 6.1 的两种驱动方式；pytest 自带的参数化、fixture、用例收集能力天然适合评测样本；pytest 退出码能与脚本退出码合并。 |
| 报告骨架生成 | 脚本生成 `docs/evaluation/report.md` 与七份子报告的"骨架 + 分数占位符 + 证据槽位"；人工在占位符内撰写结论性段落 | 打分表与指标由脚本自动注入，减少人工 typo；分析性结论仍由人工撰写以保证深度。 |

## 架构

### 组件与数据流

```
┌─────────────────────────────────────────────────────────────────────┐
│  Stage 1. Rubric 定义       tests/evaluation/rubric/*.py            │
│                             （7 维度 × 5 级 × 业界框架条款）         │
└───────────────────────┬─────────────────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────────────────┐
│  Stage 2. 证据收集          tests/evaluation/evidence/              │
│   - 源码锚点（path:Lstart-Lend）                                    │
│   - config.properties 键                                            │
│   - 摘录片段（短字符串）                                            │
└───────────────────────┬─────────────────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────────────────┐
│  Stage 3. 自动化脚本        scripts/evaluation/run_eval.py          │
│   - pytest 收集 tests/evaluation/metrics/test_*.py                   │
│   - 桩 Port 注入 → 驱动 ReActAgentAdapter / DelegationAdapter /     │
│     SlidingWindowCompactionAdapter                                   │
│   - 产出 EvalResult JSON                                            │
└───────────────────────┬─────────────────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────────────────┐
│  Stage 4. 报告生成          scripts/evaluation/render_report.py     │
│   - 合并 Rubric + 证据 + EvalResult                                 │
│   - 生成 docs/evaluation/report.md + dimensions/*.md（骨架）         │
│   - 人工填写结论段落（禁止改打分与指标）                            │
└───────────────────────┬─────────────────────────────────────────────┘
                        │
┌───────────────────────▼─────────────────────────────────────────────┐
│  Stage 5. 回归对比          scripts/evaluation/compare_results.py   │
│   - 传入基线 JSON 与最新 JSON                                       │
│   - 逐指标计算差值，超阈值 → 退出码 2                               │
└─────────────────────────────────────────────────────────────────────┘
```

### 关键数据流（以 Tool_Call_Success_Rate 为例）

```
pytest collect → ToolCallSuccessRateCase.run()
  → 构造桩 ModelAccessPort（按脚本指定返回 tool_calls 序列，结构类型匹配 chat + stream）
  → 构造真实 ToolRegistry（注册 FakeEchoTool、可选 UnknownTool）
  → 构造真实 ReActAgentAdapter(model_access=fake, tool_registry=real)
  → AgentPort.run(ConversationContext, AgentConfig)
  → 收集每次工具调用的 (tool_name, status)
  → 返回 EvalSampleResult(numerator, denominator, success)
  → pytest 通过参数化累积多个样本
  → EvalRunner 聚合为 DimensionMetric
  → 写入 docs/evaluation/results/<timestamp>.json
```

## 组件与接口

以下组件全部位于 `tests/evaluation/` 或 `scripts/evaluation/` 下；所有类名、方法名、参数均为最终形态；均使用 Python 3.11 + 标准库 + 已有依赖（`pytest`、`hypothesis`、`pydantic`），无需引入新三方库即可实现最小可行版本。若后续需要 `jsonschema` / `rich` 等，按规则经 `uv add --group evaluation` 引入。

### 组件 1：Rubric 定义

- 位置：`tests/evaluation/rubric/__init__.py`、`tests/evaluation/rubric/dimensions.py`
- 职责：把 7 维度 × 5 级评分判据以结构化形式持有，供脚本渲染与报告生成。
- 签名：

```python
# tests/evaluation/rubric/dimensions.py
"""评估 Rubric 定义：七维度、权重、各级判据与业界框架来源。

本模块只持有纯数据结构，不依赖 FastAPI / Redis / LLM 客户端等基础设施。
"""

from dataclasses import dataclass, field
from enum import Enum

class DimensionId(str, Enum):
    """七个评估维度的唯一标识符。"""
    ARCHITECTURE = "architecture"
    AGENT_CORE = "agent_core"
    MODEL_PROMPT = "model_prompt"
    SECURITY = "security"
    RELIABILITY = "reliability"
    TESTABILITY = "testability"
    FRONTEND_UX = "frontend_ux"

@dataclass(frozen=True)
class FrameworkCitation:
    """单条业界框架引用，供 Rubric 与改进建议复用。"""
    framework: str            # 如 "Anthropic"
    section: str              # 如 "Tool use best practices — Schema clarity"
    url: str                  # 公开链接或出处说明

@dataclass(frozen=True)
class RubricLevel:
    """Rubric 某一级（1-5）的判据定义。"""
    score: int                # 1..5
    criterion: str            # 该级的自然语言判据
    citations: tuple[FrameworkCitation, ...]   # ≥ 2 条，对齐需求 4.1

@dataclass(frozen=True)
class DimensionRubric:
    """单个维度的完整 Rubric。"""
    id: DimensionId
    title: str                # 中文标题，如 "架构与工程化"
    weight: float             # 权重，Σ 为 1.0
    scope_backend: tuple[str, ...]    # 该维度扫描的后端目录（相对仓库根）
    scope_frontend: tuple[str, ...]   # 扫描的前端目录；可为空
    min_evidence: int         # 最少证据条数（需求 3.1，默认 3）
    levels: tuple[RubricLevel, ...]   # 5 级判据

def load_rubric() -> tuple[DimensionRubric, ...]:
    """加载全部 7 个维度的 Rubric，返回稳定顺序。"""
```

### 组件 2：证据模型与校验

- 位置：`tests/evaluation/evidence/models.py`、`tests/evaluation/evidence/verifier.py`
- 职责：证据引用格式解析、路径与行号存在性校验、可选的摘录匹配校验。
- 签名：

```python
# tests/evaluation/evidence/models.py
"""证据引用领域模型（评测视角，与业务 domain/ 层无关）。"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

class EvidenceKind(str, Enum):
    """证据类型：源码行号 / 配置键 / 仅路径。"""
    CODE_LINES = "code_lines"
    CONFIG_KEY = "config_key"
    PATH_ONLY = "path_only"

@dataclass(frozen=True)
class EvidenceReference:
    """单条证据引用，对齐需求 3.2 的格式约束。"""
    raw: str                  # 原始字符串，如 "epsilon-boot/src/..py:10-42"
    kind: EvidenceKind
    path: Path                # 仓库根相对路径
    line_start: int | None    # 起始行（从 1 开始）
    line_end: int | None      # 结束行；仅一行时与 line_start 相等
    description: str          # 一句话证据描述

def parse_reference(raw: str, description: str) -> EvidenceReference:
    """按需求 3.2 定义的格式解析证据引用字符串；格式非法时抛 EvidenceFormatError。"""

# tests/evaluation/evidence/verifier.py
"""证据路径与行号的存在性校验，用于报告生成前预检与回归校验。"""

from dataclasses import dataclass

@dataclass(frozen=True)
class EvidenceCheck:
    """单条证据的校验结果。"""
    reference: "EvidenceReference"
    path_exists: bool
    line_range_valid: bool    # line_start ≤ line_end ≤ 文件总行数
    excerpt_matches: bool     # 若提供 expected_excerpt 则比对；否则恒 True
    error: str | None         # 校验失败时的人类可读说明

def verify_evidence(
    references: list["EvidenceReference"],
    repo_root: Path,
    expected_excerpts: dict[str, str] | None = None,
) -> list[EvidenceCheck]:
    """批量校验证据列表，返回每条的校验结果。"""
```

### 组件 3：评测用例模型与 Runner

- 位置：`tests/evaluation/runner/models.py`、`tests/evaluation/runner/runner.py`
- 职责：统一"单条样本 → 指标 → 维度聚合"的运行契约，pytest 用例仅返回 `EvalSampleResult`，Runner 负责聚合。
- 签名：

```python
# tests/evaluation/runner/models.py
"""评测运行期的数据模型（结果容器，非领域模型）。"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

class MetricId(str, Enum):
    """三项核心自动化指标的唯一标识符。"""
    TOOL_CALL_SUCCESS_RATE = "tool_call_success_rate"
    DELEGATION_CORRECTNESS = "delegation_correctness"
    CONTEXT_COMPACTION_EFFECTIVENESS = "context_compaction_effectiveness"

class SampleOutcome(str, Enum):
    """单条样本的最终状态。"""
    PASS = "pass"         # 按期望通过
    FAIL = "fail"         # 业务断言失败（如工具调用失败）
    ERROR = "error"       # 评测脚本自身抛异常（计入失败样本，不中止）

@dataclass(frozen=True)
class EvalCase:
    """单条评测用例的定义（静态配置）。"""
    case_id: str                   # 全局唯一，建议 "<metric>-<seq>"
    metric: MetricId               # 归属指标
    description: str               # 一句话描述
    inputs: dict[str, object]      # 样本输入（桩模型返回、待压缩消息等）
    expected: dict[str, object]    # 期望（允许的工具名、正确委派目标等）

@dataclass(frozen=True)
class EvalSampleResult:
    """单条样本的运行结果。"""
    case_id: str
    metric: MetricId
    outcome: SampleOutcome
    numerator: int                 # 如"成功的工具调用数"
    denominator: int               # 如"总工具调用数"
    details: dict[str, object]     # 调试字段（工具名、错误消息等）
    error: str | None              # outcome == ERROR 时填充

@dataclass(frozen=True)
class DimensionMetric:
    """单项自动化指标的聚合结果。"""
    metric: MetricId
    sample_count: int
    numerator_sum: int
    denominator_sum: int
    ratio: float                   # numerator_sum / max(denominator_sum, 1)
    failed_samples: int            # outcome != PASS 的样本数
    error_samples: int             # outcome == ERROR 的样本数

@dataclass(frozen=True)
class DimensionScore:
    """单维度的最终评分（可由脚本写入，也可由人工审阅调整后回填）。"""
    dimension: str                 # DimensionId.value
    score: int                     # 1..5
    weight: float
    rationale: str                 # 打分理由（骨架阶段为空字符串）
    evidence_refs: tuple[str, ...] # 每条 EvidenceReference.raw

@dataclass(frozen=True)
class EvalResult:
    """单次完整评测运行的顶层结果，对应一份 JSON 文件。"""
    run_id: str                    # 时间戳 + 短 hash
    generated_at: datetime
    git_commit: str | None         # 可选，从 git rev-parse HEAD 读取
    metrics: tuple[DimensionMetric, ...]
    dimension_scores: tuple[DimensionScore, ...]   # 可能仅含 dimension + weight，其余为占位
    total_score: float             # 加权平均；dimension_scores 未填写时为 0.0
    exit_code: int                 # 脚本建议退出码
```

```python
# tests/evaluation/runner/runner.py
"""评测 Runner：收集 pytest 产出的样本、聚合为 DimensionMetric、写 JSON。"""

from dataclasses import dataclass
from pathlib import Path

@dataclass
class RunnerConfig:
    """Runner 配置，仅影响评测行为，不改动业务配置。"""
    output_dir: Path               # 默认 docs/evaluation/results/
    baseline_path: Path | None     # 回归对比基线；None 则跳过对比
    regression_threshold: float    # 允许回退百分点，默认 5.0
    selected_metrics: frozenset[MetricId] | None  # None 表示全部

class EvalRunner:
    """评测编排器，负责调度 pytest、聚合结果、写出 JSON。"""

    def __init__(self, config: RunnerConfig) -> None: ...

    def run(self) -> EvalResult:
        """执行全部已注册 metric 的评测用例；失败样本不中止整批。"""

    def aggregate(self, samples: list[EvalSampleResult]) -> tuple[DimensionMetric, ...]:
        """按 MetricId 聚合样本为 DimensionMetric。"""

    def write_json(self, result: EvalResult) -> Path:
        """把 EvalResult 序列化为 JSON，落盘并返回路径。"""
```

### 组件 4：桩 Port 实现

- 位置：`tests/evaluation/stubs/`
- 职责：为三项指标提供无外部依赖的桩 `ModelAccessPort`、`AgentRegistryPort`、`SessionContextStorePort`。桩实现通过结构类型匹配 `domain/*/ports.py` 中的 Protocol，**不**继承、**不**修改 `infrastructure/` 中任何 Adapter。
- 签名：

```python
# tests/evaluation/stubs/model_access.py
"""桩 ModelAccessPort：按脚本指定的脚本化返回 LLMResponse 序列。

结构类型匹配 domain/model_access/ports.py 的 ModelAccessPort，其真实签名为：
  - async def chat(self, request: ChatRequest) -> LLMResponse
  - def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass

@dataclass
class ScriptedModelAccess:
    """按预设脚本逐次返回响应的桩模型。

    Attributes:
        scripted_responses: 预先准备的 LLMResponse 序列（领域值对象），
            每次 chat() 调用按顺序返回一条；耗尽后返回空 LLMResponse(model="scripted-exhausted")。
    """
    scripted_responses: list["LLMResponse"]   # 从 domain.model_access.value_objects 导入

    async def chat(self, request: "ChatRequest") -> "LLMResponse":
        """返回脚本中下一条响应。"""

    def stream(self, request: "ChatRequest") -> "AsyncIterator[StreamingChunk]":
        """本期评测不使用 stream；抛 NotImplementedError 作为防御。"""

# tests/evaluation/stubs/agent_registry.py
"""桩 AgentRegistryPort：按名称返回预设 NamedAgentConfig。"""

@dataclass
class StaticAgentRegistry:
    """静态名称 → 配置映射。"""
    configs: dict[str, "NamedAgentConfig"]

    def get(self, name: str) -> "NamedAgentConfig":
        """名称不存在时抛 AgentNotFoundError（复用领域既有异常）。"""

    def list_names(self) -> tuple[str, ...]: ...
```

### 组件 5：三项指标的评测用例

- 位置：`tests/evaluation/metrics/test_tool_call_success_rate.py`、`test_delegation_correctness.py`、`test_context_compaction_effectiveness.py`
- 职责：用 pytest + 参数化收集样本，每条用例调用真实 Adapter + 桩 Port，输出 `EvalSampleResult`。
- 关键算法：

```python
# tests/evaluation/metrics/test_tool_call_success_rate.py
"""指标 1：工具调用成功率。

判定规则：
- 对每个案例，驱动 ReActAgentAdapter 完整跑完一轮直至无 tool_calls 或达到 max_rounds；
- 分母 = 本次运行中出现的 tool_calls 总数（含权限拒绝与执行异常）；
- 分子 = 执行过程中未抛 ToolExecutionError / ToolPermissionDeniedError / ToolNotFoundError
        且返回字符串长度 > 0 的 tool_call 次数；
- 桩模型按脚本给出固定 tool_calls 序列，规避 LLM 波动。
"""

import pytest
# 所需业务导入仅使用公开领域模型与 Adapter 构造函数，不反向修改源码：
#   from domain.agent.value_objects import AgentConfig
#   from domain.chat.context import ConversationContext
#   from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
#   from tests.evaluation.stubs.model_access import ScriptedModelAccess

@pytest.mark.evaluation
@pytest.mark.parametrize("case", TOOL_CALL_CASES, ids=[c.case_id for c in TOOL_CALL_CASES])
def test_tool_call_success_rate(case: "EvalCase",
                                sample_sink: "SampleSink") -> None:
    """驱动一轮 ReAct，统计工具调用成功率并回传 sample_sink。"""
```

```python
# tests/evaluation/metrics/test_delegation_correctness.py
"""指标 2：委派正确性。

判定规则（三项皆需通过才计为成功样本）：
(a) DelegateToAgentTool 实际被解析为 expected_target_agent；
(b) 子任务启动时的 delegation_depth = 父级 + 1，且 ≤ AGENT_MAX_DELEGATION_DEPTH；
(c) 子任务返回的 TaskResult.content 被正确写回父 Agent 上下文作为 ToolMessage。

桩策略：
- 桩 ModelAccess 让父 Agent 第 1 轮返回 delegate_to_agent(target=...) tool_call，
  第 2 轮返回 finish；让子 Agent 一轮内返回可识别的 answer。
- 真实 DelegationAdapter + 真实 TaskAgentAdapter，保证深度校验与循环依赖解法都被覆盖。
"""
```

```python
# tests/evaluation/metrics/test_context_compaction_effectiveness.py
"""指标 3：上下文压缩有效性。

判定规则（样本级双指标，分子分母按"关键信息条数 / 期望关键信息条数"计算）：
- 构造长度 L 的消息序列，含 S 条 SystemMessage 与 (L - S) 条非 system 消息；
- 调用 SlidingWindowCompactionAdapter.compact(messages, window_n=N)；
- 成功判据：
  (a) 压缩后 SystemMessage 数 = S（无损保留，对齐 docs/agent.md 与需求 8.4）；
  (b) 压缩后非 system 消息数 = min(L - S, N)；
  (c) 压缩后按原始顺序保留最后 N 条非 system 消息。
- 指标比例 = 满足 (a)(b)(c) 的样本占比。
"""
```

### 组件 6：脚本入口

- 位置：`scripts/evaluation/run_eval.py`、`scripts/evaluation/render_report.py`、`scripts/evaluation/compare_results.py`、`scripts/evaluation/verify_evidence.py`
- 职责：CLI 入口，组合 Runner / Reporter / Verifier。
- 签名：

```python
# scripts/evaluation/run_eval.py
"""评测主入口：驱动 pytest 收集样本、聚合指标、写 JSON、可选回归对比。

用法（均需在 epsilon-boot/ 下执行）：
    uv run python -m scripts.evaluation.run_eval --dimension=all
    uv run python -m scripts.evaluation.run_eval --metric=tool_call_success_rate
    uv run python -m scripts.evaluation.run_eval --baseline=docs/evaluation/results/2026-05-01.json
"""

def main(argv: list[str] | None = None) -> int:
    """解析 CLI 参数，构造 RunnerConfig，执行评测，返回退出码。

    退出码：
        0 — 运行成功且（若提供 baseline）未触发回归。
        1 — 脚本自身异常（参数非法、路径不可写、桩实现崩溃等）。
        2 — 指标相对基线回退 ≥ 阈值。
    """
```

```python
# scripts/evaluation/render_report.py
"""报告骨架生成器：根据 Rubric + 最新 EvalResult + 证据清单，生成报告骨架。

生成物：
    docs/evaluation/report.md               # 主报告骨架
    docs/evaluation/dimensions/<n>-*.md     # 七份子报告骨架

骨架中 "结论" / "Improvement_Recommendation 详解" 等段落以 <!-- TBD --> 注释占位，
由人工在后续阶段填充。打分表、指标数值、证据列表由脚本自动注入，人工不应手工改写。
"""

def render(
    rubric: "tuple[DimensionRubric, ...]",
    result: "EvalResult",
    evidence_catalog: "dict[str, list[EvidenceReference]]",
    output_root: "Path",
) -> None: ...
```

```python
# scripts/evaluation/compare_results.py
"""回归对比工具：对比两份 EvalResult JSON，差值越过阈值则以退出码 2 报错。"""

def compare(
    baseline_path: Path, latest_path: Path, threshold: float
) -> "RegressionReport": ...
```

```python
# scripts/evaluation/verify_evidence.py
"""证据存在性校验工具：批量校验 Evidence_Reference 对应路径与行号是否有效。"""
```

### 组件 7：前端指标探针（可选，本期仅作为骨架）

- 位置：`tests/evaluation/frontend/ux_probe.md`
- 职责：登记前端/UX 维度需要人工巡检的要点（SSE 完整性、AbortController 行为、trace 可见性等），作为 Rubric 证据来源。本期不跑 `bun`/`npm` 脚本；若后续引入，将通过 `epsilon-client/package.json` 单独声明（需求 6.2），不触碰 `dev`/`build`/`start`/`lint`。

## 数据模型

### Rubric / Evidence / Result 模型汇总

见"组件与接口"章节中对 `DimensionRubric`、`RubricLevel`、`FrameworkCitation`、`EvidenceReference`、`EvalCase`、`EvalSampleResult`、`DimensionMetric`、`DimensionScore`、`EvalResult`、`RegressionReport` 的 dataclass 定义。全部使用 `@dataclass(frozen=True)` 以获得不可变语义，符合仓库既有领域值对象风格。

### EvalResult JSON Schema

运行结果统一按以下结构写入 `docs/evaluation/results/<YYYY-MM-DD_HHMMSS>.json`：

```json
{
  "run_id": "2026-05-12_030559_ab12cd",
  "generated_at": "2026-05-12T03:05:59+00:00",
  "git_commit": "69be5dcc...",
  "metrics": [
    {
      "metric": "tool_call_success_rate",
      "sample_count": 20,
      "numerator_sum": 58,
      "denominator_sum": 60,
      "ratio": 0.9667,
      "failed_samples": 1,
      "error_samples": 0
    },
    {
      "metric": "delegation_correctness",
      "sample_count": 12,
      "numerator_sum": 12,
      "denominator_sum": 12,
      "ratio": 1.0,
      "failed_samples": 0,
      "error_samples": 0
    },
    {
      "metric": "context_compaction_effectiveness",
      "sample_count": 30,
      "numerator_sum": 30,
      "denominator_sum": 30,
      "ratio": 1.0,
      "failed_samples": 0,
      "error_samples": 0
    }
  ],
  "dimension_scores": [
    {"dimension": "architecture", "score": 4, "weight": 0.18, "rationale": "", "evidence_refs": []}
  ],
  "total_score": 0.0,
  "exit_code": 0
}
```

> `dimension_scores[*].score` 与 `rationale` 在脚本首次运行时可以为 0 / 空字符串；人工撰写评分与理由后，再由 `render_report.py --update-scores` 读取 `docs/evaluation/scores.toml`（本特性新增的评分源文件）回填计算 `total_score`。评分源文件与 JSON 分离，避免误把人工评分当作脚本自动产物。

### 评分源文件

位置：`docs/evaluation/scores.toml`（人工维护）。

```toml
[architecture]
score = 4
rationale = "…（中文）"
evidence_refs = [
  "epsilon-boot/src/application/container_config.py:1-80",
  "docs/steering/ddd-architecture.md",
]

[agent_core]
score = 4
rationale = "…"
evidence_refs = [
  "epsilon-boot/src/infrastructure/agent/react_agent_adapter.py:L1-L200",
]
```

权重来自 `load_rubric()`，不在评分源文件中出现，避免人工误改权重。

### 评测脚本自身的配置

`tests/evaluation/config/eval.toml`：

```toml
[runner]
output_dir = "docs/evaluation/results"
report_dir = "docs/evaluation"
default_window_n = 10              # 覆盖时传给滑动窗口压缩指标

[regression]
threshold_percent_points = 5.0

[metrics.tool_call_success_rate]
sample_count = 20

[metrics.delegation_correctness]
sample_count = 12

[metrics.context_compaction_effectiveness]
sample_count = 30
```

该文件不涉及凭证，且不与 `config.properties` 职责冲突（后者是业务运行时配置）。脚本若需要读取业务配置（例如 `AGENT_MAX_DELEGATION_DEPTH`），一律通过既有 `config_proxy` 从 `config.properties` 读取，不硬编码（需求 12.3）。

## 事务与并发边界

本特性不涉及数据库写入、消息队列发布或跨数据源事务。需声明的边界：

- **文件写：** 仅 `docs/evaluation/results/*.json`、`docs/evaluation/report.md`、`docs/evaluation/dimensions/*.md`、`tests/evaluation/reports/*.json`（若用户覆盖输出目录）。写操作在同一进程内完成，无并发；首次运行时由脚本 `mkdir(parents=True, exist_ok=True)`（对齐需求 14.4）。
- **文件读：** 仅只读访问 `epsilon-boot/config.properties`、`epsilon-boot/src/**/*.py`、`epsilon-client/src/**/*.{ts,tsx}`、`docs/**/*.md`、`docs/evaluation/scores.toml`、基线 JSON。禁止写入以上路径。
- **脚本并发：** 默认单进程；若未来以 pytest `-n` 并发加速，由于每个样本使用独立桩 Port 实例，不存在共享可变状态。
- **评测期间不启动** Redis / MySQL / 模型 Provider 连接；`container.start()` 在评测脚本中不被调用，桩 Port 直接注入真实 Adapter 构造函数。
- **回归比较的幂等性：** `compare_results.py` 对两份 JSON 均只读；同一输入始终产出同一 `RegressionReport`。

## 正确性属性

### Property 1：交付路径闭包

交付变更的所有文件路径必须以 `docs/evaluation/`、`tests/evaluation/`、`scripts/evaluation/` 之一开头。
验证需求：需求 7.1、需求 7.2、需求 12.2、需求 14.1、需求 14.2。

### Property 2：不对业务路径执行写操作

`Evaluation_Script` 运行期间，对 `epsilon-boot/src/**`、`epsilon-client/src/**`、`epsilon-boot/config.properties`、`docs/**`（除 `docs/evaluation/**`）不触发任何 `open(..., "w"|"a"|"x")` 或 `Path.write_*`。
验证需求：需求 7.4、需求 12.1、需求 12.2。

### Property 3：样本异常不中止整批

当单条 `EvalCase` 运行抛出异常时，`EvalRunner` 捕获并产出 `EvalSampleResult(outcome=ERROR)`，继续执行后续样本；最终 `DimensionMetric.error_samples` 计入失败数。
验证需求：需求 5.5。

### Property 4：证据格式严格匹配

所有 `EvidenceReference.raw` 在解析时必须满足 `^[^\s:]+(:L?\d+(-L?\d+)?)?$` 形式；禁止出现仅文件名、仅目录、跨文件通配符等情况。
验证需求：需求 3.2、需求 3.3、需求 3.4。

### Property 5：维度权重归一

`load_rubric()` 返回的全部 `DimensionRubric.weight` 之和等于 1.0（允许浮点误差 ≤ 1e-9）；`total_score` 即为加权平均。
验证需求：需求 1.1、需求 2.1、需求 13.2。

### Property 6：每维度业界框架条款最少 2 条

每个 `DimensionRubric` 的任一 `RubricLevel.citations` 跨 5 级去重后的 `FrameworkCitation.framework` 集合至少包含 2 个不同业界框架；判据文本中至少出现其中 2 条 `section`。
验证需求：需求 4.1、需求 4.2。

### Property 7：回归阈值语义

当任一 `DimensionMetric.ratio`（最新）相较基线下降 ≥ `regression_threshold` 个百分点时，`EvalResult.exit_code` = 2；否则 = 0；脚本自身异常则 = 1。
验证需求：需求 10.4。

### Property 8：SystemMessage 无损保留（压缩指标判据）

`Context_Compaction_Effectiveness` 评测中，压缩后消息流的 SystemMessage 数量恒等于压缩前，且顺序保持一致。
验证需求：需求 8.4、需求 10.4。

### Property 9：委派深度不超限（委派指标判据）

`Delegation_Correctness` 评测中，任一样本观察到的子任务 `delegation_depth` 满足 `child_depth = parent_depth + 1 ≤ AGENT_MAX_DELEGATION_DEPTH`。
验证需求：需求 8.3、需求 9（间接）。

## 错误处理

### 错误模型

评测脚本使用独立异常族，位于 `tests/evaluation/errors.py`；不复用 `domain/` 或 `common/` 的业务异常，避免让"评测失败"被误读为"业务缺陷"。

```python
# tests/evaluation/errors.py
"""评测脚本自身的异常定义。"""

class EvaluationError(Exception):
    """评测脚本错误基类。"""

class EvidenceFormatError(EvaluationError):
    """证据引用格式非法（对齐需求 3.2）。"""

class EvidenceNotFoundError(EvaluationError):
    """证据指向的路径或行号不存在。"""

class RubricConsistencyError(EvaluationError):
    """Rubric 自身一致性校验失败（如权重不归一、业界框架条款不足 2 条）。"""

class SampleExecutionError(EvaluationError):
    """被 EvalRunner 捕获、用于包装单条样本异常，携带 case_id 与原始 traceback。"""

class RegressionThresholdViolation(EvaluationError):
    """回归对比结果触发阈值；供 CI 以退出码 2 退出时使用。"""
```

### 错误场景与传播策略

| 场景 | 捕获位置 | 传播策略 |
|---|---|---|
| 样本抛业务异常（如桩配置错）| `EvalRunner.run()` 循环体 | 包装为 `SampleExecutionError` → `EvalSampleResult(outcome=ERROR)`，继续后续样本；汇总入 `error_samples` |
| 证据格式非法 | `parse_reference()` | 抛 `EvidenceFormatError`；`render_report.py` 作为脚本级错误退出（退出码 1） |
| 证据路径不存在 | `verify_evidence.py` | 汇总到 `EvidenceCheck.error`；命令退出码：有任一失败则 1 |
| Rubric 权重不归一 | `load_rubric()` 首次调用 | 抛 `RubricConsistencyError`；脚本启动即失败（退出码 1） |
| 回归基线文件不存在 | `compare_results.py` | 打印 warning，退出码 0（不当作回归失败，允许首次基线生成） |
| 回归阈值触发 | `compare_results.py` | 退出码 2；`run_eval.py` 合并后返回 2 |
| 评测过程中 I/O 写失败 | 出现即抛 `OSError` | 不吞错；脚本退出码 1 |

### 原则

- 错误一律带中文消息（与仓库既有风格一致），并附出现位置（`case_id` / 文件路径 / 行号）。
- 不静默吞异常；`SampleExecutionError` 是唯一例外，且会显式写入 `EvalSampleResult.error` 字段保留 traceback。
- `Evaluation_Script` 不向业务层抛任何自定义异常，避免"评测代码污染业务异常族"。

## 测试策略

### 评测脚本自身的单元测试

位于 `tests/evaluation/self_tests/`，通过 `uv run pytest tests/evaluation/self_tests` 执行（不加 `@pytest.mark.evaluation` 标记，以保证 `run_eval.py` 收集时不把它们当作评测样本）：

| 测试文件 | 覆盖点 | 对应 Property / 需求 |
|---|---|---|
| `test_rubric_consistency.py` | `load_rubric()` 权重归一、每维度 ≥ 2 个框架、5 级齐全 | Property 5、Property 6、需求 2.2、需求 4.1 |
| `test_evidence_parse.py` | `parse_reference()` 合法/非法格式、`path_only` / `code_lines` / `config_key` 分发 | Property 4、需求 3.2 |
| `test_evidence_verify.py` | 路径不存在、行号越界、摘录不匹配 | 需求 3.4 |
| `test_runner_aggregation.py` | `EvalRunner.aggregate()` 分子分母求和、失败样本计数、PASS/FAIL/ERROR 分类 | Property 3、需求 5.5 |
| `test_scripted_model_access.py` | 桩 Port 耗尽后返回空 `LLMResponse`、`stream` 防御性抛 `NotImplementedError` | 设计决策可用性 |
| `test_compare_results.py` | 阈值内不回退、阈值外触发、基线缺失 | Property 7、需求 10.4 |
| `test_delivery_path_guard.py` | 交付路径守卫：校验 `git diff --name-only HEAD` 全在三白名单目录下（作为 CI 可选项） | Property 1、需求 7.2 |

> 单元测试全部使用确定性 mock；不发起任何网络请求；无需真实 LLM Provider。

### 评测指标的"元测试"（验证三项指标本身的正确性）

每个 `tests/evaluation/metrics/test_*.py` 模块附一份 `_meta_test.py`，覆盖：

- 工具调用成功率：构造已知"3 成功 / 1 失败 / 1 权限拒绝"用例，断言 `numerator_sum=3, denominator_sum=5`，`ratio≈0.6`。
- 委派正确性：构造"目标正确但深度越限"、"目标不存在"、"正常委派"三类样本，断言命中率与失败分类。
- 压缩有效性：对 `L=30, S=3, N=10` 的固定消息序列断言压缩结果长度 `= 3 + 10`，SystemMessage 计数无损。

### 回归集成测试

位于 `tests/evaluation/self_tests/test_end_to_end.py`：

- 以固定种子跑一次 `run_eval.main(["--metric=all", "--output=<tmp>"])`；
- 对输出 JSON 按 schema 断言字段存在；
- 复跑一次使用第一次输出做基线，断言 `exit_code=0`；
- 人为篡改 numerator（直接修改 JSON）后复跑，断言 `exit_code=2`。

### LLM 调用隔离

所有评测样本 **只**使用 `ScriptedModelAccess`；`epsilon-boot/.env` / `config.properties` 中的真实 `MODEL_*_API_KEY` 不会被读取（因为脚本不调用 `configure_container().start()`）。测试守卫：`self_tests/test_no_external_calls.py` 使用 `monkeypatch` 拦截 `httpx.AsyncClient.request` 与 `openai.*`，断言评测全流程对外零网络调用。

### 需求追溯表

| 需求 | 覆盖章节 / 用例 |
|---|---|
| 需求 1 多角色交付 | 报告骨架（执行摘要/改进清单/读者导览）+ `run_eval.py` 单命令 + 退出码语义 |
| 需求 2 维度全覆盖 | Rubric 7 维 + `scope_backend` / `scope_frontend` 字段 |
| 需求 3 证据可追溯 | `EvidenceReference` + `verify_evidence.py` + Property 4 |
| 需求 4 业界框架引用 | `FrameworkCitation` + Property 6 + `test_rubric_consistency.py` |
| 需求 5 三项核心指标 | 组件 5 三个 metric 用例 + Property 3/8/9 |
| 需求 6 uv/配置合规 | 设计决策中 uv 规则说明 + `eval.toml` 与 `config.properties` 分离 |
| 需求 7 硬约束 | Property 1/2 + `test_delivery_path_guard.py` |
| 需求 8 Agent 核心条目 | 组件 5 指标覆盖 ReAct / Tool / Delegation / Compaction；子报告 `dimensions/2-agent-core.md` 骨架 |
| 需求 9 安全条目 | 子报告 `dimensions/4-security.md` 骨架 + 证据清单预填 |
| 需求 10 可靠/可测性 | 子报告 `dimensions/5-reliability.md`、`6-testability.md` + Property 7 |
| 需求 11 前端 UX | 子报告 `dimensions/7-frontend-ux.md` + `ux_probe.md` |
| 需求 12 Steering 合规 | 全文"与 Steering 规范的对齐"小结 + `test_delivery_path_guard.py` |
| 需求 13 改进建议 | `report.md` "改进清单"章节骨架 + 按 P0/P1/P2 小计 |
| 需求 14 交付清单 | 下一节"目录与文件结构" + `report.md` 附录 |

## 目录与文件结构

```
docs/
  evaluation/
    report.md                               ← 主报告（骨架由脚本生成，结论段落人工撰写）
    scores.toml                             ← 人工维护的维度评分与理由
    dimensions/
      1-architecture.md                     ← 骨架由脚本生成
      2-agent-core.md
      3-model-prompt.md
      4-security.md
      5-reliability.md
      6-testability.md
      7-frontend-ux.md
    results/
      <YYYY-MM-DD_HHMMSS>.json              ← 每次运行由脚本写入（机器可读）
    .gitkeep                                ← 确保空目录被 Git 追踪

tests/
  evaluation/
    __init__.py
    config/
      eval.toml                             ← 评测参数（sample_count、阈值等）
    rubric/
      __init__.py
      dimensions.py                         ← Rubric 定义（人工撰写，含业界框架引用）
    evidence/
      __init__.py
      models.py
      verifier.py
      catalog.py                            ← 各维度"必备证据清单"（人工维护）
    stubs/
      __init__.py
      model_access.py
      agent_registry.py
      session_context_store.py
    runner/
      __init__.py
      models.py
      runner.py
      sample_sink.py                        ← pytest fixture，收集样本到进程级列表
    metrics/
      __init__.py
      test_tool_call_success_rate.py        ← @pytest.mark.evaluation
      test_delegation_correctness.py
      test_context_compaction_effectiveness.py
      _meta_test_tool_call_success_rate.py  ← 元测试，验证指标实现正确性
      _meta_test_delegation_correctness.py
      _meta_test_context_compaction_effectiveness.py
    self_tests/
      test_rubric_consistency.py
      test_evidence_parse.py
      test_evidence_verify.py
      test_runner_aggregation.py
      test_scripted_model_access.py
      test_compare_results.py
      test_delivery_path_guard.py
      test_no_external_calls.py
      test_end_to_end.py
    frontend/
      ux_probe.md                           ← 前端 UX 人工巡检清单
    reports/                                ← 默认 JSON 输出位置（与 docs/evaluation/results/ 二选一）
      .gitkeep
    errors.py
    conftest.py                             ← 声明 sample_sink fixture、evaluation mark

scripts/
  evaluation/
    __init__.py
    run_eval.py                             ← CLI 主入口
    render_report.py                        ← 报告骨架生成器
    compare_results.py                      ← 回归对比工具
    verify_evidence.py                      ← 证据存在性校验工具
    README.md                               ← 面向 QA/平台工程师的使用说明
```

**文件撰写职责表**：

| 路径 | 撰写者 | 生成方式 |
|---|---|---|
| `tests/evaluation/rubric/dimensions.py` | 人工 | 依据业界框架手工编写 |
| `tests/evaluation/evidence/catalog.py` | 人工 | 逐维度列出必备证据 |
| `tests/evaluation/stubs/**` | 人工 | 按桩 Port 协议编写 |
| `tests/evaluation/runner/**`、`metrics/**`、`self_tests/**` | 人工 | 按本设计编写 |
| `docs/evaluation/report.md`、`dimensions/*.md` | 脚本生成骨架 + 人工填结论 | `render_report.py` 首次生成 + `<!-- TBD -->` 占位 |
| `docs/evaluation/scores.toml` | 人工 | 在报告骨架可读之后回填 |
| `docs/evaluation/results/*.json`、`tests/evaluation/reports/*.json` | 脚本 | `run_eval.py` 运行时生成 |

## 接口设计

### CLI 命令清单

所有命令均在 `epsilon-boot/` 目录下执行（对齐 `docs/steering/uv-package-manager.md`）：

```bash
# 运行全部指标（默认），写结果到 docs/evaluation/results/
uv run python -m scripts.evaluation.run_eval

# 仅跑单个指标
uv run python -m scripts.evaluation.run_eval --metric=tool_call_success_rate

# 指定输出路径
uv run python -m scripts.evaluation.run_eval --output=tests/evaluation/reports/latest.json

# 带基线回归对比
uv run python -m scripts.evaluation.run_eval \
  --baseline=docs/evaluation/results/2026-05-01_120000_abc.json \
  --regression-threshold=5.0

# 生成报告骨架（读取最新结果与 Rubric、证据目录）
uv run python -m scripts.evaluation.render_report \
  --result=docs/evaluation/results/latest.json \
  --output-root=docs/evaluation

# 单独跑回归对比（不重新执行评测）
uv run python -m scripts.evaluation.compare_results \
  --baseline=<path> --latest=<path> --threshold=5.0

# 校验证据清单
uv run python -m scripts.evaluation.verify_evidence \
  --catalog=tests/evaluation/evidence/catalog.py
```

### 脚本与 pytest 的集成方式

- `tests/evaluation/conftest.py` 声明 `@pytest.mark.evaluation` 标记并注册进程级 `sample_sink` fixture；
- `run_eval.py` 内部以编程方式调用 `pytest.main(["-q", "tests/evaluation/metrics", "-m", "evaluation", ...])`；
- 评测指标用例通过 `sample_sink.append(sample_result)` 回传结果；`EvalRunner` 运行结束后从 sink 读取并聚合；
- 元测试（`_meta_test_*.py`）位于 `tests/evaluation/metrics/` 但不携带 `evaluation` 标记，因此 `uv run pytest tests/evaluation/metrics` 会同时跑两类，而 `run_eval.py` 只收集带 evaluation 标记者；
- `uv run pytest tests/evaluation -m evaluation` 和 `uv run pytest tests/evaluation` 均能工作，满足需求 6.1 的两种驱动方式。

### 退出码汇总

| 退出码 | 含义 |
|---|---|
| 0 | 评测成功完成；若提供基线，所有指标未触发回退 |
| 1 | 脚本自身错误（参数非法、Rubric 不一致、I/O 失败、样本外的未捕获异常） |
| 2 | 运行成功但指标相对基线回退 ≥ 阈值（CI 应据此判定失败） |

## 与 Steering 规范的对齐

### DDD（`docs/steering/ddd-architecture.md`）

- 评测代码一律位于 `tests/evaluation/` 与 `scripts/evaluation/` 下，**不**在 `src/domain/` / `src/application/` / `src/infrastructure/` / `src/common/` 新增或修改任何文件（需求 12.2）。
- 桩 Port 通过结构类型（Protocol）匹配 `domain/*/ports.py`，不继承 Adapter，不新增 `domain/` → `infrastructure/` 导入。
- 驱动指标用例时直接 import `infrastructure/agent/react_agent_adapter.ReActAgentAdapter` 等具体 Adapter，并向其构造函数注入桩 Port。该导入在"测试/评测代码"范围内属于规范允许的例外（见 ddd-architecture.md "允许的例外" 第 2 条）。

### 配置源（`docs/steering/config-source.md`）

- 评测脚本若需业务配置（`AGENT_MAX_DELEGATION_DEPTH` 等），通过 `common/configuration/config_proxy.py` 读取 `config.properties`，不新建配置入口、不读写 `.env`。
- 评测自身参数落在 `tests/evaluation/config/eval.toml`，与业务配置物理隔离；不写入 `config.properties`（避免污染业务运行时）。

### uv（`docs/steering/uv-package-manager.md`）

- 所有命令文档化为 `uv run …`；`scripts/evaluation/README.md` 中禁止出现 `pip` / `poetry` / `pipenv` / `conda`。
- 评测如需新依赖（`jsonschema` 做 JSON schema 严格校验、`rich` 做终端表格美化），通过 `uv add --group evaluation <pkg>`；`pyproject.toml` 将新增：

```toml
[dependency-groups]
evaluation = [
    "jsonschema>=4.23",   # 评测 JSON schema 校验
]
```

初版尽量只用标准库，避免触碰 `pyproject.toml`。

### 代码文档（`docs/steering/code-documentation.md`）

- 所有新增 Python 模块首行写模块级中文 docstring，说明职责与用途；
- 所有公开类、函数、方法有中文 docstring，参数与返回值含义清楚；
- 复杂算法（如委派正确性判定的三项条件、回归阈值比较）在 docstring 中附"判定规则"小节。

## 风险与权衡

| 风险 | 影响 | 应对 |
|---|---|---|
| 证据行号漂移 | 业务代码改动后报告中的 `path:Lstart-Lend` 指向错位 | `verify_evidence.py` 作为 CI 前置步骤；证据目录中每条可选 `expected_excerpt`，校验时对比文件实际内容；若摘录失配则退出码 1，提示人工刷新 |
| 桩 Port 与真实 Adapter 行为偏离 | 指标数值虚高 | 每个桩实现附 `_meta_test`；桩 `ScriptedModelAccess` 只做"按脚本返回"最小实现，不模拟 Provider 细节；委派与压缩指标直接调用真实 Adapter，减少桩表面 |
| LLM-as-judge 抖动（未来风险） | 若引入将影响回归稳定性 | 本期不引入，明确在"Open Questions"中列出；引入时要求判官独立版本号、固定 temperature=0、以 N≥3 投票 |
| 报告骨架 + 人工填充的割裂 | 人工改 JSON 或打分表导致脚本失效 | `scores.toml` 与 `results/*.json` 分离；`render_report.py --update-scores` 是单向流水；打分表由脚本生成注释 "自动生成，请勿手工修改" |
| 评测 token 成本 | 本期 0 token（无真实 LLM 调用） | 若将来引入真实 Provider 作为可选指标，独立命名 `--enable-live-llm` 且默认关闭 |
| `tests/evaluation/metrics/_meta_test_*.py` 下划线前缀 | pytest 默认收集 `test_*.py`，`_meta_test_*.py` 不被收集 | 将元测试文件命名为 `test_meta_tool_call_success_rate.py` 等，并使用 `@pytest.mark.evaluation_self` 标记；`run_eval.py` 仅按 `evaluation` 标记收集，互不干扰 |
| 多人并行修改 `scores.toml` 与 `report.md` | 合并冲突 | 评分源文件按 `[dimension]` 分段；报告骨架的结论段落只以 `<!-- TBD -->` 占位，冲突粒度小 |

## 待定项（Open Questions）

本阶段未解决、建议在 tasks 阶段明确的条目：

1. **子报告拆分粒度**：当前设计按"一个维度一份子报告"拆分；如果个别维度内容较少，是否合并到主报告？建议：先全部拆分，若交付时发现 ≤ 200 行的子报告，合并回主报告并保留锚点。
2. **业界框架条款的具体引用清单**：`FrameworkCitation.url` 的稳定性依赖公开链接。建议在 Rubric 里同时留 `section` 文本作为"无链接可读"兜底，并在 `docs/evaluation/report.md` "评估方法"章节统一维护来源表。
3. **前端自动化指标是否本期落地**：需求未强制要求，但留有 `bun`/`npm` 脚本通道（需求 6.2）。本设计按"本期仅人工巡检"收敛；若后续要量化"SSE 完整性"等，将在独立 spec 中扩展。
4. **引入 `jsonschema` 与否**：初版仅用 `dataclasses.asdict` + 手工断言；若评测 JSON 被外部系统消费，需要正式 schema，届时按规则 `uv add --group evaluation jsonschema`。
5. **CI 接入细节**：是否在当前迭代就接入 CI（GitHub Actions / Jenkins）由运维侧决定；本设计仅提供可重复的 `uv run` 命令与退出码契约，CI yaml 片段留待 tasks 阶段确定。

---

## 自评（Clarification Loop）

针对本 design.md，向主 Agent 显式暴露以下需要人类决策的条目；若主 Agent 已在 requirement 阶段决策，请忽略对应条目：

1. **子报告拆分策略**：保留"7 份子报告 + 1 份主报告"的拆分，还是首版就合并为"单文件 report.md"？
   - 选项 A：按维度拆分（当前设计）。维护粒度清晰，适合长期演进。
   - 选项 B：单文件汇总。一页读完，适合一次性评估交付。
   - 推荐：A（维度章节通常超过 100 行）。

2. **是否本期就引入 `jsonschema` 新依赖**：
   - 选项 A：不引入，手工断言（当前设计）。改动最小。
   - 选项 B：引入以保证 `results/*.json` 外部消费安全。
   - 推荐：A。若无外部消费方，手工断言已足够。

3. **`ScriptedModelAccess` 是否需要支持 `stream`**：
   - 选项 A：仅支持 `chat`，`stream` 抛 `NotImplementedError`（当前设计，匹配 `ModelAccessPort` 真实签名：`chat -> LLMResponse` / `stream -> AsyncIterator[StreamingChunk]`）。
   - 选项 B：支持 stream，开辟"SSE 流式场景评测"通道。
   - 推荐：A。需求 5.1 三项核心指标均不依赖 stream。

4. **评分源文件格式**：
   - 选项 A：`docs/evaluation/scores.toml`（当前设计，带注释友好）。
   - 选项 B：`docs/evaluation/scores.json`（与 results JSON 同构）。
   - 推荐：A。

5. **回归阈值语义**：选择"百分点差"还是"相对比率差"？
   - 选项 A：百分点差（当前设计，与需求 10.4 原文一致）。
   - 选项 B：相对比率差（`(new - old) / old`）。
   - 推荐：A。与需求条文贴合。

请主 Agent 在这 5 个条目上确认采用推荐项，或明确另选方案；如全部采用推荐项，本设计可视为最终版本。
