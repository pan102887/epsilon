# 实现计划：P2 落地首片——Agent Loop 纯编排叶子逻辑与 RoundOutcome 值对象上提领域层

> 本文件由已定稿的 `design.md` 展开为可执行、可勾选的任务清单。全程为 **`Behavior_Equivalent_Refactor`（行为等价纯重构）**：新建领域模块 `src/domain/agent/agent_loop_policy.py` 承载 4 个模块级纯编排函数（`compute_total_tokens` / `is_token_budget_exceeded` / `detect_handoff` / `outcome_to_agent_result`）+ `RoundOutcome` / `RoundOutcomeKind` 值对象真身；`infrastructure/agent/round_outcome.py` 降为 re-export 兼容垫片；`react_agent_adapter.py` 去薄封装（删 4 个 `@staticmethod`、import 领域函数、调用点直调，`_log_token_budget_exceeded` 留基础设施但内部改调领域计算）；两处既有测试只改 import / 调用形式、不改断言。
> 每条任务标注：动作、目标文件、对应 requirement AC 与 design 组件 / Property 编号、可执行验证命令。所有测试 / lint / grep 命令均在 `epsilon-boot/` 下执行（测试命令统一带 `cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest`）。
> **全程硬约束**：领域新模块零 `application` / `infrastructure` / 框架 / Pydantic 依赖、不引领域事件（`P2_Invariants` 第 5 条）；ADR-0010 疑点 2（`outcome_to_agent_result` 的 `handoff` 分支 `model` 取 `outcome.response.model`）**不修正**（AC1.6）；不搬 `_iter_rounds` 循环主体 / `_execute_tool_call` / `_collect_pending_actions` / 流式累加 / guardrail / trace / 序列化 / `_log_token_budget_exceeded`（AC1.8、需求 6）；不改 `AgentPort` 四方法签名（AC2.1）、不改前端、不改依赖管理（仍仅 `uv`）；`RoundOutcome` 字段 / 类型 / 默认值 / `Literal` 逐一等价（AC1.2）；中文 docstring（`code-documentation.md`）；全量类型标注、禁裸 `Any`、`ruff` / `pyright` 零新增错误（`python-typing-lint.md`）；`Existing_Test_Suite_Green` 每波结束保持通过。

## 概述

执行采用 **波次（Wave）+ Checkpoint 门禁** 结构，遵循 design「组件依赖图」的依赖方向（`infrastructure → domain`）自底向上推进。首片只搬零 I/O、给定输入即定输出的纯叶子构件 + 值对象，`_iter_rounds` 主体深度解耦留后续片。

- **Wave 1（建领域模块 + 单测，含真身迁移+垫片原子化）**：新建 `src/domain/agent/agent_loop_policy.py`（迁 `RoundOutcome` / `RoundOutcomeKind` 真身 + 4 个模块级纯函数，逐字段 / 逐分支等价，含疑点 2 照搬），**同一波内**把 `infrastructure/agent/round_outcome.py` 降为 re-export 垫片以消除「真身重复定义」中间态；新增 `test/domain/agent/test_agent_loop_policy_unit.py` 覆盖 design 正确性属性要求的全部分支。此波**不改 `react_agent_adapter.py`**（其 `line 88` 仍从 `infrastructure.agent.round_outcome` import `RoundOutcome`，经垫片可解析）。→ **Checkpoint 1**：领域新模块 + 垫片可 import、领域单测全绿 + `ruff` / `pyright` 零错 + grep 领域模块零 `infrastructure` / `application` / 框架 / Pydantic 依赖。
- **Wave 2（基础设施委托 + 既有测试 import/调用）**：`react_agent_adapter.py` 删 4 个 `@staticmethod`、改 import 引入领域函数、5 处调用点直调（`_iter_rounds` 1970/2186、执行入口 2569/2791、`_log_token_budget_exceeded` 1014），`RoundOutcome` import 改指领域模块；两处既有测试（`test_value_objects_terminated_reason_unit.py` 改 import、`test_react_agent_token_budget_unit.py` 改 import + 调用形式）只改引用不改断言。→ **Checkpoint 2**：全量 `PYTHONPATH=src uv run --frozen pytest` 全绿 + 特征化测试全绿 + 相关文件 lint 零新增。
- **Wave 3（ADR-0011 + 文档同步）**：新建 `docs/adr/0011-relocate-agent-loop-leaf-orchestration-to-domain.md`（四段式、`Accepted`、不 supersede 0001/0010）+ `docs/adr/README.md` 索引追加 0011；按 design 结论同步 `docs/architecture.md` / `docs/domain-model.md`。→ **Checkpoint 3（最终门禁）**：全量 pytest 全绿 + grep 领域模块零反向依赖 + `ruff` / `pyright` 零新增 + git diff 范围核对（未动 `_iter_rounds` 主体 / `_execute_tool_call` / `_collect_pending_actions` / 流式 / 前端）+ `AgentPort` 四签名未变。

> **⚠️ 真身迁移 + 垫片的原子性（Wave 1 内部硬顺序，最关键）**：`RoundOutcome` / `RoundOutcomeKind` 的真身从 `infrastructure/agent/round_outcome.py` **迁入** `domain/agent/agent_loop_policy.py`。一旦真身迁走，`round_outcome.py` 若仍保留原 `@dataclass` 定义即造成 **重复定义 / 两处不同类**，破坏 `isinstance` 与 `==` 等价。因此 **T-1.1（建领域真身）与 T-1.2（round_outcome.py 改垫片）必须在同一波内、且 T-1.2 紧随 T-1.1 之后落地**——中间不得让 `round_outcome.py` 仍持有真身而领域模块已定义同名类。落地顺序：先 T-1.1 写领域模块 → 立即 T-1.2 把 `round_outcome.py` 替换为 `from domain.agent.agent_loop_policy import RoundOutcome, RoundOutcomeKind` 垫片 → 再 T-1.3 单测。此二任务不可并发、不可拆到不同波次。
> **反断裂顺序**：Wave 1 只新增领域模块并把 `round_outcome.py` 改垫片（`react_agent_adapter.py` line 88 经垫片仍可解析，既有测试不断裂）→ Wave 2 才改 `react_agent_adapter.py` 去薄封装与既有测试 import，保证每个 Checkpoint 处测试可绿。
> **行号说明**：本文 `@行号`（如 `_iter_rounds` 1970/2186、执行入口 2569/2791、`_log_token_budget_exceeded` 1014、`round_outcome.py:18/35`）引自 design.md 定位，落地前以 grep 逐点核对实际位置（现网核验：4 个 `@staticmethod` 分别在 980/994/1838/2255，调用点 1970/2186/2569/2791，`_log_token_budget_exceeded` 内 `_compute_total_tokens` 引用在 1014，`round_outcome.py` 真身在 18/35，均与 design ±1 行内一致），防止上游微调导致偏移。

---

## Wave 1：建领域模块（新增 `agent_loop_policy.py` + `round_outcome.py` 改垫片 + 领域单测）

> **原子性证据**：T-1.1 与 T-1.2 是「真身迁移 + 垫片」的一体两面，**必须一起落地、T-1.2 紧随 T-1.1**（见概述硬顺序说明），不得中间态。T-1.3 单测在领域模块就绪后落地。此波不触碰 `react_agent_adapter.py`。

- [x] 1. 领域层 Agent Loop 编排模块与单测（含真身迁移+垫片原子化）
  - [x] 1.1 新建领域模块 `src/domain/agent/agent_loop_policy.py`（迁 `RoundOutcome` / `RoundOutcomeKind` 真身 + 4 个模块级纯函数）
    - 在 `src/domain/agent/agent_loop_policy.py` 新建（当前不存在），含模块中文 docstring（照 design 组件 0 文字：说明承载纯编排叶子判定与轮次终止形态值对象、零基础设施 / 框架 / Pydantic 依赖、列 4 函数 + 值对象、声明不承载循环主体 / 工具执行 / 审批中断 / 流式 / guardrail / trace / 序列化 / 日志，回链 ADR-0010 / 0011）；顶部 `from __future__ import annotations`；全量类型标注、禁裸 `Any`。
    - **import（对齐 design 组件 0，仅领域层 + 标准库）**：`from dataclasses import dataclass`、`from typing import Literal`；`from domain.agent.value_objects import AgentConfig, AgentResult, AgentTerminationReason, ApprovalRequiredPayload`；`from domain.chat.context import ConversationContext, ToolMessage`；`from domain.model_access.value_objects import LLMResponse, ToolCallRequest`。**不引** `application` / `infrastructure` / `fastapi` / `pydantic`。
    - **值对象真身（对齐 design 组件 1，与源 `infrastructure/agent/round_outcome.py:18/35` 逐一等价）**：`RoundOutcomeKind = Literal["text", "tool_calls", "approval", "final", "handoff"]`（含类型别名 docstring）；`@dataclass(frozen=True) class RoundOutcome`，字段 `kind: RoundOutcomeKind`、`round_num: int`、`response: LLMResponse`、`total_usage: dict[str, int]`、`tool_calls: tuple[ToolCallRequest, ...] = ()`、`approval: ApprovalRequiredPayload | None = None`、`assistant_message_index: int | None = None`、`terminated_reason: AgentTerminationReason = "completed"`、`handoff_target: str | None = None`、`handoff_content: str = ""`——名称 / 类型 / 默认值 / `Literal` 取值 / frozen 语义 / **各字段中文 docstring** 全部照搬源 `round_outcome.py`，不改文字语义。
    - **`compute_total_tokens`（design 组件 2，源 `react_agent_adapter.py:980-991` 照搬去 `_`/去 `@staticmethod`）**：`def compute_total_tokens(total_usage: dict[str, int]) -> int`，函数体 `total = int(total_usage.get("total_tokens", 0) or 0); if total > 0: return total; return int(total_usage.get("prompt_tokens", 0) or 0) + int(total_usage.get("completion_tokens", 0) or 0)`，字面等价。
    - **`is_token_budget_exceeded`（design 组件 3，源 `:994-998`）**：`def is_token_budget_exceeded(config: AgentConfig, total_usage: dict[str, int]) -> bool`，`config.max_total_tokens is None` 返回 `False`，否则 `return compute_total_tokens(total_usage) > config.max_total_tokens`；源第 998 行对 `ReActAgentAdapter._compute_total_tokens(...)` 的自引用改为对模块级 `compute_total_tokens(...)` 直调，结果等价。
    - **`detect_handoff`（design 组件 4，源 `:1838-1865`）**：`def detect_handoff(context: ConversationContext) -> tuple[str, str] | None`，`messages = context.get_messages()`；`for msg in reversed(messages):` 遇非 `ToolMessage` `break`，`target = msg.metadata.get("handoff_target")`，命中 `return str(target), msg.content`，循环结束 `return None`；docstring 保留源「同轮多工具并发，handoff 可能出现在任意位置」要点。
    - **`outcome_to_agent_result`（design 组件 5，源 `:2255-2303`，含疑点 2 不修正）**：`def outcome_to_agent_result(outcome: RoundOutcome) -> AgentResult`，三分支照搬——`handoff`→`AgentResult(content=outcome.handoff_content, model=outcome.response.model if outcome.response else "", usage=outcome.total_usage, latency_ms=0.0, terminated_reason="completed")`（**`model` 取 `outcome.response.model`，AC1.6 明令不修正 ADR-0010 疑点 2**）；`kind in ("text", "final")`→`AgentResult(content=outcome.response.content, model=outcome.response.model, usage=outcome.total_usage, latency_ms=outcome.response.latency_ms, terminated_reason=outcome.terminated_reason)`；`approval`（else 分支）→`AgentResult(content="", model=outcome.response.model, usage=outcome.total_usage, latency_ms=outcome.response.latency_ms, status="approval_required", approval=outcome.approval, terminated_reason="completed")`；字段取值逐一等价。
    - _需求: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 3.1, 3.3, 3.4, 3.5_ ; _design 组件 0/1/2/3/4/5 / 数据模型 / 反向依赖复核 / Property 1、2、5、8_
    - 验证：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run python -c "from domain.agent.agent_loop_policy import RoundOutcome, RoundOutcomeKind, compute_total_tokens, is_token_budget_exceeded, detect_handoff, outcome_to_agent_result; print('ok')"`（import 正常）。

  - [x] 1.2 `src/infrastructure/agent/round_outcome.py` 降为 re-export 兼容垫片（**紧随 1.1，消除真身重复定义**）
    - 修改 `src/infrastructure/agent/round_outcome.py`（design 组件 6）：**删除**原 `@dataclass class RoundOutcome`（`:35`）与 `RoundOutcomeKind = Literal[...]`（`:18`）真身定义及其原 import（`domain.agent.value_objects` / `domain.model_access.value_objects`），**替换为** re-export 垫片：模块 docstring 标注「`RoundOutcome` / `RoundOutcomeKind` 真身已上提领域层 `domain.agent.agent_loop_policy`（P2 首片，ADR-0011）；本模块仅重导出保持既有 `from infrastructure.agent.round_outcome import RoundOutcome` 引用可解析；新代码应直接从 `domain.agent.agent_loop_policy` 导入」；`from __future__ import annotations`；`from domain.agent.agent_loop_policy import RoundOutcome, RoundOutcomeKind`；`__all__ = ["RoundOutcome", "RoundOutcomeKind"]`。
    - **原子性硬约束**：本任务必须紧随 T-1.1 落地——真身既已迁入领域模块，`round_outcome.py` 不得再保留同名 `@dataclass` 定义（否则两处不同类，破坏 `isinstance` / `==` 等价）；对外符号 `RoundOutcome` / `RoundOutcomeKind` 名称不变，`react_agent_adapter.py:88` 与既有测试的 `from infrastructure.agent.round_outcome import RoundOutcome` 经此垫片继续可解析（Property 4）。
    - _需求: 1.2, 2.6, 4.5_ ; _design 组件 6 / 调用点全表（src 内引用末行）/ Property 2、4_
    - 验证：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run python -c "from infrastructure.agent.round_outcome import RoundOutcome, RoundOutcomeKind; from domain.agent.agent_loop_policy import RoundOutcome as R2; assert RoundOutcome is R2; print('shim ok, same class')"`（垫片 re-export 同一类）。

  - [x] 1.3 新增领域构件单测 `test/domain/agent/test_agent_loop_policy_unit.py`
    - 在 `test/domain/agent/test_agent_loop_policy_unit.py` 新建（含模块中文 docstring 说明其锁定 `agent_loop_policy` 的纯函数与 `RoundOutcome`、性质为脱离运行时领域单测），**仅 import `domain.*`**（`from domain.agent.agent_loop_policy import ...`，及构造入参所需的 `domain.agent.value_objects` / `domain.chat.context` / `domain.model_access.value_objects` 领域值对象），不 import `application` / `infrastructure` / 框架；全量类型标注、禁裸 `Any`。落地前实读 `test/infrastructure/agent/test_react_agent_token_budget_unit.py` 与 `test_value_objects_terminated_reason_unit.py`，复用其 `LLMResponse` / `RoundOutcome` 构造同构写法，**不与已充分覆盖处添加等价重复断言**（AC4.4）。
    - **`compute_total_tokens` 覆盖（AC4.2，Property 1）**：`total_tokens` 命中（`{"total_tokens": 100}` → 100）、`total_tokens` 为 0 回退（`{"total_tokens": 0, "prompt_tokens": 3, "completion_tokens": 5}` → 8）、`total_tokens` 缺失回退（`{"prompt_tokens": 3, "completion_tokens": 5}` → 8）、空 dict（`{}` → 0）。
    - **`is_token_budget_exceeded` 覆盖（AC4.2，Property 1）**：`config.max_total_tokens is None` → 恒 `False`；恰好等于上限（`compute_total_tokens == max_total_tokens` → `False`）；超限（`> max_total_tokens` → `True`）。构造 `AgentConfig` 时 `max_total_tokens` 取 `None` / 具体上限。
    - **`detect_handoff` 覆盖（AC4.2，Property 1）**：命中（尾部 `ToolMessage.metadata["handoff_target"]` 非空 → 返回 `(str(target), content)`）、未命中（尾部 `ToolMessage` 无 `handoff_target` → `None`）、尾部非 `ToolMessage` 立即停止（尾部为非 `ToolMessage` 消息 → 不扫描、`None`）、同轮多 `ToolMessage` handoff 在非末尾位置命中。构造 `ConversationContext` 与 `ToolMessage`（含 `metadata`）作输入。
    - **`outcome_to_agent_result` 覆盖（AC4.2，Property 1、5）**：`handoff` 分支（`content == handoff_content`、**`model == outcome.response.model`（疑点 2 锁定）**、`latency_ms == 0.0`、`terminated_reason == "completed"`）；`text` / `final` 分支（`content == response.content`、透传 `terminated_reason`，含 `terminated_reason="max_rounds"` 与 `"token_budget_exceeded"` 透传各一）；`approval` 分支（`content == ""`、`status == "approval_required"`、`approval is not None`、`terminated_reason == "completed"`）。
    - **`RoundOutcome` 覆盖（AC4.2、AC4.4，Property 2）**：仅补 `test_value_objects_terminated_reason_unit.py::TestRoundOutcomeTerminatedReason` **未覆盖** 的字段——`handoff_target` / `handoff_content` / `tool_calls` 默认值（分别为 `None` / `""` / `()`）、frozen 不可变（`dataclasses.FrozenInstanceError`）；不重复其已断言的 `terminated_reason` 默认 / 显式取值。
    - _需求: 4.1, 4.2, 4.3, 4.4_ ; _design 测试策略 1 / Property 1、2、5、8_
    - 验证：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/agent/test_agent_loop_policy_unit.py -q`（全绿）。

---

## Checkpoint 1：领域模块就绪 + 垫片可解析 + 零基础设施依赖（门禁）

- [x] 2. CP1 Wave 1 门禁校验（全部通过方可进入 Wave 2）
  - 领域模块 + 垫片可 import 且为同一类：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run python -c "from domain.agent.agent_loop_policy import RoundOutcome, compute_total_tokens, is_token_budget_exceeded, detect_handoff, outcome_to_agent_result; from infrastructure.agent.round_outcome import RoundOutcome as R2; assert RoundOutcome is R2; print('ok')"`（无报错、同一类，Property 2、4）。
  - 领域单测全绿：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/agent/test_agent_loop_policy_unit.py -q`（0 failed，Property 1、2、5）。
  - 该子域回归无破坏（此波仅新增领域模块 + 改垫片，`react_agent_adapter.py` 未动，全量应仍绿）：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent test/domain/agent -q`（既有 + 新增全绿）。
  - 领域纯净度 / 无反向依赖（Property 8）：`cd /workspace/epsilon-boot && grep -rnE "import (application|infrastructure|fastapi|pydantic)|from (application|infrastructure|fastapi|pydantic)" src/domain/agent/agent_loop_policy.py`（期望零命中）。
  - 规范合规（需求 3 AC3.3/AC3.4/AC3.5，Property 8）：`cd /workspace/epsilon-boot && uv run ruff check src/domain/agent/agent_loop_policy.py src/infrastructure/agent/round_outcome.py` 与 `cd /workspace/epsilon-boot && uv run pyright src/domain/agent/agent_loop_policy.py src/infrastructure/agent/round_outcome.py`（零新增错误、无裸 `Any`；中文 docstring 人工核对齐备）。
  - 疑点 2 锁定：单测中 `handoff` 分支断言 `result.model == outcome.response.model`（人工核对，Property 5）。
  - _需求: 1.1, 1.2, 3.1, 3.3, 3.4, 3.5, 4.1, 4.2, 4.3_ ; _design Property 1、2、5、8_

---

## Wave 2：基础设施委托（`react_agent_adapter.py` 去薄封装 + 既有测试 import/调用）

> **迁移原则**：把 `react_agent_adapter.py` 内 4 个 `@staticmethod` 定义删除、改从领域模块 import 4 个纯函数与 `RoundOutcome`，5 处调用点改直调领域函数；`_iter_rounds` 循环控制主体、`_execute_tool_call`、审批筛选 `_collect_pending_actions`、流式累加、guardrail / trace / checkpoint 副作用、`RoundOutcome(...)` 构造与类型注解**一律字面不变**（需求 6）。`_log_token_budget_exceeded` 本体留基础设施，仅内部改调领域计算。
> **正交与串行判定**：T-3（`react_agent_adapter.py`）为单文件，其内多处改动归单一任务串行；T-4 的两处既有测试分处不同文件，可并发，但均须在 T-3 落地后跑全量验证。

- [x] 3. `react_agent_adapter.py` 去薄封装、改 import 与调用点委托（单文件串行）
  - [x] 3.1 改 import + 删 4 个 `@staticmethod` 定义
    - 修改 `src/infrastructure/agent/react_agent_adapter.py`（design 组件 7 / 调用点全表 src 内引用）：**import 改动（`:88`）**——把 `from infrastructure.agent.round_outcome import RoundOutcome` 替换为 `from domain.agent.agent_loop_policy import (RoundOutcome, compute_total_tokens, detect_handoff, is_token_budget_exceeded, outcome_to_agent_result)`。
    - **删除 4 个类内 `@staticmethod` 定义**：`_compute_total_tokens`（现网 `:980-991`）、`_is_token_budget_exceeded`（`:994-998`）、`_detect_handoff`（`:1838-1865`）、`_outcome_to_agent_result`（`:2255-2303`）——真身已在领域层，删除后不留空壳（去薄封装，AC1.7 二选一取「直接委托」）。
    - **保留**：`_log_token_budget_exceeded`（含 `logger`，ADR-0010 判据 4 留基础设施，本体不动，仅在 T-3.2 微调内部一行）；所有 `RoundOutcome(...)` 构造（`_iter_rounds` yield 处）与类型注解（`-> AsyncIterator[RoundOutcome]`、`_build_model_call_trace(outcome: RoundOutcome, ...)`、`_build_approval_trace(outcome: RoundOutcome)` 等）**字面不变**——引用的仍是名为 `RoundOutcome` 的符号，仅 import 源变更为领域模块（与垫片同一类，构造 / frozen / 字段全等价）。
    - _需求: 1.7, 1.8, 2.2, 6.1, 6.4_ ; _design 组件 7 / 调用点全表（src 内引用）/ Property 3、6_
    - 验证：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run python -c "import infrastructure.agent.react_agent_adapter; print('import ok')"`；`cd /workspace/epsilon-boot && grep -nE "_compute_total_tokens|_is_token_budget_exceeded|_detect_handoff|_outcome_to_agent_result" src/infrastructure/agent/react_agent_adapter.py`（期望仅 `_log_token_budget_exceeded` 相关无命中被删函数名——即零命中这四个被删定义名）。
  - [x] 3.2 5 处调用点改直调领域函数
    - 继续修改 `src/infrastructure/agent/react_agent_adapter.py`，按 design 调用点全表逐点改委托（语句位置 / 前后顺序 / 判定时机不变，仅把内联的类方法自引用换成等价领域函数调用）：
      - `_log_token_budget_exceeded` 内（现网 `:1014`，`"accumulated_total_tokens": ReActAgentAdapter._compute_total_tokens(total_usage)`）→ `compute_total_tokens(total_usage)`（`_log_token_budget_exceeded` 本体与 `logger.warning` 记账时机不变）。
      - `_iter_rounds` 内（`:1970`）`handoff = self._detect_handoff(context)` → `handoff = detect_handoff(context)`。
      - `_iter_rounds` 内（`:2186`）`if self._is_token_budget_exceeded(config, total_usage):` → `if is_token_budget_exceeded(config, total_usage):`。
      - 执行入口（`:2569`）`return self._outcome_to_agent_result(outcome)` → `return outcome_to_agent_result(outcome)`。
      - 执行入口（`:2791`）`return self._outcome_to_agent_result(outcome)` → `return outcome_to_agent_result(outcome)`。
    - **不动**：`_iter_rounds` 的 `for round_num in range(...)` 推进、`terminal_round` 边界、`RoundOutcome` 产出协议、`_execute_tool_call`、`_collect_pending_actions`、`_RoundStreamAccumulator` 流式累加、guardrail 运行时累加、`ToolAbuseDetector`、OTel trace、`ApprovalStateStorePort` I/O、序列化、`handoff_context`、`merge_usage`（需求 6 AC6.1/AC6.2）。
    - _需求: 1.7, 1.8, 2.2, 2.3, 6.1, 6.2_ ; _design 组件 7 / 调用点全表（src 内引用）/ 事务与并发边界 / Property 3、6_
    - 验证：`cd /workspace/epsilon-boot && grep -nE "self\._detect_handoff|self\._is_token_budget_exceeded|self\._outcome_to_agent_result|ReActAgentAdapter\._compute_total_tokens" src/infrastructure/agent/react_agent_adapter.py`（期望零命中——旧自引用已全部改委托）；`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent -q`（含特征化测试全绿，Property 3、6）。

- [x] 4. 两处既有测试改 import / 调用形式（不改断言，可并发）
  - [x] 4.1 `test/domain/agent/test_value_objects_terminated_reason_unit.py` 改 import 指领域模块
    - 修改 `test/domain/agent/test_value_objects_terminated_reason_unit.py`（design 调用点全表 test 内引用第 1 行，现网 `:20`）：`from infrastructure.agent.round_outcome import RoundOutcome` → `from domain.agent.agent_loop_policy import RoundOutcome`（体现真身已上提；垫片存在时保持原 import 亦可解析，此处取推荐方案改指领域模块，属 `P2_Invariants` 第 6 条允许的 import 调整）。**断言语义零改动**（`TestRoundOutcomeTerminatedReason` 各断言不动）。
    - _需求: 2.6, 4.5_ ; _design 调用点全表（test 内引用）/ Property 2、4、7_
    - 验证：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/agent/test_value_objects_terminated_reason_unit.py -q`（全绿）；`cd /workspace/epsilon-boot && git diff -- test/domain/agent/test_value_objects_terminated_reason_unit.py`（期望仅 import 行变更、断言行未变）。
  - [x] 4.2 `test/infrastructure/agent/test_react_agent_token_budget_unit.py` 改 import + 调用形式
    - 修改 `test/infrastructure/agent/test_react_agent_token_budget_unit.py`（design 调用点全表 test 内引用第 2/3 行）：函数内局部 import（现网 `:289`）`from infrastructure.agent.round_outcome import RoundOutcome` → `from domain.agent.agent_loop_policy import RoundOutcome, outcome_to_agent_result`；调用行（现网 `:299`）`result = ReActAgentAdapter._outcome_to_agent_result(outcome)` → `result = outcome_to_agent_result(outcome)`（从「适配器 `@staticmethod`」改为「领域纯函数直调」，输入 `outcome` 与后续断言不变，行为等价）。**断言语义零改动**（如 `:300` `result.terminated_reason == "token_budget_exceeded"` 不动）；若其余用例仍需 `RoundOutcome` 从垫片 import 亦可，不强制统一。
    - _需求: 2.6, 4.5_ ; _design 调用点全表（test 内引用）/ Property 1、4、7_
    - 验证：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent/test_react_agent_token_budget_unit.py -q`（全绿）；`cd /workspace/epsilon-boot && git diff -- test/infrastructure/agent/test_react_agent_token_budget_unit.py`（期望仅 import 行 + `:299` 调用形式行变更、断言行未变）。

---

## Checkpoint 2：全量测试绿 + 特征化基线绿（门禁）

- [x] 5. CP2 Wave 2 门禁校验
  - 全量测试绿（`Existing_Test_Suite_Green`，Property 3、4、6、7）：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest`（0 failed）。
  - 特征化基线绿（Property 3、6）：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent/test_react_agent_characterization_*.py -q`（终止四态 / 流式时序 / 审批中断恢复 / handoff / token budget 全绿）。
  - 无遗留旧自引用（Property 3）：`cd /workspace/epsilon-boot && grep -nE "self\._detect_handoff|self\._is_token_budget_exceeded|self\._outcome_to_agent_result|ReActAgentAdapter\._compute_total_tokens|_outcome_to_agent_result|_compute_total_tokens|_is_token_budget_exceeded|_detect_handoff" src/infrastructure/agent/react_agent_adapter.py`（期望仅 `_log_token_budget_exceeded` 命中，被删 4 函数名与自引用零命中）。
  - 既有测试仅改 import/调用形式（Property 7）：`cd /workspace/epsilon-boot && git diff -- test/domain/agent/test_value_objects_terminated_reason_unit.py test/infrastructure/agent/test_react_agent_token_budget_unit.py`（人工核对仅 import / `:299` 调用形式行变更，断言语义未改）。
  - 相关文件 lint 零新增错误：`cd /workspace/epsilon-boot && uv run ruff check src/infrastructure/agent/react_agent_adapter.py src/infrastructure/agent/round_outcome.py src/domain/agent/agent_loop_policy.py` 与 `cd /workspace/epsilon-boot && uv run pyright src/infrastructure/agent/react_agent_adapter.py src/infrastructure/agent/round_outcome.py src/domain/agent/agent_loop_policy.py`。
  - `AgentPort` 签名未变（Property 6，AC2.1）：`cd /workspace/epsilon-boot && grep -nE "def run|def run_streaming|def run_events|def resume" src/domain/agent/ports.py`（四签名字面未变，人工核对）。
  - _需求: 1.7, 2.1, 2.2, 2.3, 2.4, 2.6, 4.5_ ; _design Property 1、3、4、6、7_

---

## Wave 3：ADR-0011 + 文档同步（仅改 docs/，与代码正交，可最后执行）

> **正交证据**：本波只改 `docs/adr/` 与 `docs/` 主题文档，与 `epsilon-boot/` 源码与测试零交集。落地前实读既有 `docs/adr/0010-relocate-agent-loop-to-domain-direction.md` 与 `0009-*.md` 沿用其写作深度与回链风格，遵循 `docs/steering/adr.md` 四段式与 `docs/steering/doc-sync.md` 索引同步。

- [x] 6. ADR-0011 及文档同步
  - [x] 6.1 新建 ADR-0011
    - 在 `docs/adr/` 新建 `0011-relocate-agent-loop-leaf-orchestration-to-domain.md`（编号紧接现有 0010），遵循 `docs/adr/0000-template.md` 四段式。front-matter：`status: Accepted`、`date: 2026-07-06`、`deciders: [后端架构维护者]`、`supersedes:` **留空**（**不 supersede ADR-0001 / ADR-0010**，落地其方向）、`superseded-by:` 留空。标题：`上提 Agent Loop 纯编排叶子逻辑与 RoundOutcome 值对象至领域层（P2 首片）`。
    - **一、背景（AC5.2）**：落地 ADR-0010「ReAct Agent Loop 编排逻辑应归属领域层」方向的首片；ADR-0010 已确立切分线判据、据实候选清单与 `P2_Invariants` 六条，但只定方向未搬任何一行。整合报告识别的 `Domain_Logic_In_Infrastructure`（约 3313 行 `react_agent_adapter.py` 承载自研编排算法、非 SDK 封装）需以最低风险起步纠偏。
    - **二、决策（AC5.2、AC5.3）**：引入 `Domain_Agent_Loop_Module`（`src/domain/agent/agent_loop_policy.py`）承载 `First_Slice_Scope` 五项——4 个模块级纯编排函数（`compute_total_tokens` / `is_token_budget_exceeded` / `detect_handoff` / `outcome_to_agent_result`）+ `RoundOutcome` / `RoundOutcomeKind` 值对象；`ReActAgentAdapter` 调用点直接委托领域实现（去薄封装、删 4 个 `@staticmethod`），`infrastructure/agent/round_outcome.py` 降为 re-export 兼容垫片；采用**分片增量策略**，本首片只搬零 I/O、给定输入即定输出的纯叶子构件。声明为 `Behavior_Equivalent_Refactor`，不改任何对外可观测行为，遵守 `P2_Invariants` 六条。
    - **三、后果（AC5.2、AC5.3，回链 ADR-0010 后果节）**：正面——领域层承载 Agent Loop 编排构件的第一块落地打通、建立领域模块 + 单测样板、为后续片降风险；`_log_token_budget_exceeded`（日志，判据 4）留基础设施。负面 / 临时性——`round_outcome.py` re-export 垫片是首片临时产物，待后续片 `_iter_rounds` 主体上提完成后可清理；**`_iter_rounds` 循环控制主体、`_execute_tool_call`、审批中断决策 `_collect_pending_actions`、流式累加明确留后续片**（回链 ADR-0010 后果节「高度交织」警示与方案 C「一次性大爆炸搬迁 3313 行」否决）。后续影响——若实施中发现某构件与循环主体 / 技术记账存在未预期耦合而无法零风险剥离，处置为「缩小该构件首片范围并登记于本 ADR 后果节，留后续片」，不借首片之名扩张至 Out of Scope（需求 6 AC6.5）。
    - **四、备选方案（含未采纳原因）**：(a) 一次性大爆炸搬迁全部编排逻辑——被否（ADR-0010 方案 C，风险极高）；(b) 保留 4 个空壳 `@staticmethod` 薄封装再委托——被否（造成「两处都像入口」认知负担、遗留 infrastructure 冗余定义，AC1.7 允许直接委托）；(c) 全量改 import 路径、不留 re-export 垫片——被否（改动面更大、漏改风险高，违背最小改动纪律）；(d) 引入领域事件 / 事件总线承载循环——被否（违反 ADR-0001，`P2_Invariants` 第 5 条）；(e) 把 `RoundOutcome` 拆入 `domain/agent/value_objects.py` 而非新模块——不采纳（值对象与消费它的翻译函数强内聚，同处 `agent_loop_policy.py` 更利首片样板边界清晰，且避免与 `value_objects.py` 循环引用风险）。
    - **合规硬约束（AC5.4）**：`supersedes:` 保持为空、不 supersede ADR-0001 与 ADR-0010（落地其方向）；正文 SHALL NOT 复活领域事件 / 事件总线承载循环逻辑。
    - _需求: 5.1, 5.2, 5.3, 5.4_ ; _design ADR-0011 草案要点 / Property 6_
    - 验证：`test -f docs/adr/0011-relocate-agent-loop-leaf-orchestration-to-domain.md`；`grep -nE "^supersedes:" docs/adr/0011-relocate-agent-loop-leaf-orchestration-to-domain.md`（字段存在且值为空）；人工核对四段式齐备、`status: Accepted`、含分片增量策略 + 首片范围 + 为何 `_iter_rounds` 主体 / `_execute_tool_call` / 审批中断 / 流式留后续片 + 垫片临时性登记，且无「领域事件承载循环」推荐。
  - [x] 6.2 更新 `docs/adr/README.md` 索引
    - 在 `docs/adr/README.md` 索引表 0010 行之后追加 0011 索引行（编号链接 / 标题「上提 Agent Loop 纯编排叶子逻辑与 RoundOutcome 值对象至领域层（P2 首片）」/ `Accepted` / `2026-07-06`），遵循 `docs/steering/doc-sync.md`。
    - _需求: 5.5_ ; _design 文档同步 / Property 6_
    - 验证：`grep -n "0011" docs/adr/README.md`（有命中）。
  - [x] 6.3 同步主题文档 `docs/architecture.md` / `docs/domain-model.md`
    - 修改 `docs/architecture.md`（design 文档同步「建议同步」）：在「Port/Adapter 映射」与「ReAct Agent Loop 流程」相关章节补一句——Agent Loop 的纯编排叶子判定（token 预算计算 / 超限、handoff 检测、结果翻译）与 `RoundOutcome` 值对象已上提领域层 `domain/agent/agent_loop_policy.py`（ADR-0011 首片），适配器改为委托；`round_outcome.py` 现为 re-export 兼容垫片。
    - 修改 `docs/domain-model.md`（design 文档同步）：新增对 `domain/agent/agent_loop_policy.py`（`RoundOutcome` / `RoundOutcomeKind` 值对象 + 4 个编排纯函数）的领域模型说明，标注其为 Agent Loop 轮次终止形态通用语言与纯编排判定，回链 ADR-0011。
    - _需求: 5.5_ ; _design 文档同步 / Property 6_
    - 验证：`grep -n "agent_loop_policy" docs/architecture.md docs/domain-model.md`（两文件均有命中）。

---

## Checkpoint 3：最终门禁（Property 全量验收）

- [x] 7. CP3 最终门禁校验（必须全部通过）
  - Property 3/4/6/7（全量绿）：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest`（0 failed，`Existing_Test_Suite_Green` 前后成立）。
  - 特征化基线绿：`cd /workspace/epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent/test_react_agent_characterization_*.py -q`（全绿）。
  - Property 8（领域模块零反向依赖 / 零基础设施）：`cd /workspace/epsilon-boot && grep -rnE "import (application|infrastructure|fastapi|pydantic)|from (application|infrastructure|fastapi|pydantic)" src/domain/agent/agent_loop_policy.py`（期望零命中）。
  - Property 8（规范合规）：`cd /workspace/epsilon-boot && uv run ruff check src/domain/agent/agent_loop_policy.py src/infrastructure/agent/round_outcome.py src/infrastructure/agent/react_agent_adapter.py` 与 `cd /workspace/epsilon-boot && uv run pyright src/domain/agent/agent_loop_policy.py`（零新增错误、无裸 `Any`；中文 docstring 齐备）。
  - Property 6（`AgentPort` 四签名未变，AC2.1）：`cd /workspace/epsilon-boot && grep -nE "def run|def run_streaming|def run_events|def resume" src/domain/agent/ports.py`（四签名字面未变，人工核对）。
  - Property 5（疑点 2 不修正，AC1.6）：`cd /workspace/epsilon-boot && grep -n "outcome.response.model if outcome.response" src/domain/agent/agent_loop_policy.py`（handoff 分支 `model` 仍取父模型，有命中）。
  - 范围锁定（需求 6 AC6.1–6.5）：`cd /workspace/epsilon-boot && git diff --name-only` 中源码改动仅落 `src/domain/agent/agent_loop_policy.py`（新增）+ `src/infrastructure/agent/round_outcome.py`（改垫片）+ `src/infrastructure/agent/react_agent_adapter.py`（去薄封装 + 委托）+ `test/domain/agent/`（新增单测 + 改 import）+ `test/infrastructure/agent/test_react_agent_token_budget_unit.py`（改 import/调用），文档改动仅落 `docs/`；**未动** `react_agent_adapter.py` 的 `_iter_rounds` 循环主体 / `_execute_tool_call` / `_collect_pending_actions` / `_RoundStreamAccumulator` 流式累加 / guardrail / trace / 序列化（人工核 `git diff` 仅命中 import + 4 处调用点 + 删 4 定义 + `_log_token_budget_exceeded` 1 行）；未改前端 `epsilon-client/`、未改依赖清单（`pyproject.toml` / `uv.lock`）、未改 `AgentPort`（`src/domain/agent/ports.py`）。
  - ADR / 文档合规（AC5.1–5.5）：`test -f docs/adr/0011-relocate-agent-loop-leaf-orchestration-to-domain.md`；`grep -nE "^supersedes:" docs/adr/0011-*.md`（值为空）；`grep -n "0011" docs/adr/README.md`（命中）；`grep -n "agent_loop_policy" docs/architecture.md docs/domain-model.md`（命中）。
  - _需求: 1.1, 1.6, 1.7, 1.8, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1, 3.4, 4.5, 5.1, 5.2, 5.3, 5.4, 5.5, 6.1, 6.2, 6.3, 6.4, 6.5_ ; _design Property 1–8_

---

## 任务 → 需求 AC → design 组件 → 正确性属性 追溯表

| 任务 | 覆盖需求 AC | design 组件 | 正确性属性 |
|---|---|---|---|
| 1.1（建领域模块 + 真身 + 4 函数） | 1.1/1.2/1.3/1.4/1.5/1.6/3.1/3.3/3.4/3.5 | 组件 0/1/2/3/4/5、数据模型、反向依赖复核 | Property 1、2、5、8 |
| 1.2（round_outcome.py 改垫片） | 1.2/2.6/4.5 | 组件 6、调用点全表（src） | Property 2、4 |
| 1.3（领域单测） | 4.1/4.2/4.3/4.4 | 测试策略 1 | Property 1、2、5、8 |
| 2（CP1） | 1.1/1.2/3.1/3.3/3.4/3.5/4.1/4.2/4.3 | 全领域组件 | Property 1、2、5、8 |
| 3.1（改 import + 删 4 定义） | 1.7/1.8/2.2/6.1/6.4 | 组件 7、调用点全表（src） | Property 3、6 |
| 3.2（5 处调用点委托） | 1.7/1.8/2.2/2.3/6.1/6.2 | 组件 7、调用点全表（src）、事务并发边界 | Property 3、6 |
| 4.1（terminated_reason 测试改 import） | 2.6/4.5 | 调用点全表（test） | Property 2、4、7 |
| 4.2（token_budget 测试改 import/调用） | 2.6/4.5 | 调用点全表（test） | Property 1、4、7 |
| 5（CP2） | 1.7/2.1/2.2/2.3/2.4/2.6/4.5 | 全组件 | Property 1、3、4、6、7 |
| 6.1（ADR-0011） | 5.1/5.2/5.3/5.4 | ADR-0011 草案要点 | Property 6 |
| 6.2（README 索引） | 5.5 | 文档同步 | Property 6 |
| 6.3（主题文档同步） | 5.5 | 文档同步 | Property 6 |
| 7（CP3 最终门禁） | 1.1/1.6/1.7/1.8/2.1–2.6/3.1/3.4/4.5/5.1–5.5/6.1–6.5 | 全组件 | Property 1–8 |

> **需求 3 AC3.2（反向依赖复核）**：为 design 阶段产物（design「反向依赖复核」表已据实完成），本清单由 T-1.1 落地满足、CP1/CP3 grep 门禁保障，无独立实现任务。

---

## 备注

- **真身迁移 + 垫片原子性（最关键）**：`RoundOutcome` / `RoundOutcomeKind` 真身从 `infrastructure/agent/round_outcome.py` **迁入** `domain/agent/agent_loop_policy.py`；T-1.1（建领域真身）与 T-1.2（`round_outcome.py` 改垫片）**必须同一波内、T-1.2 紧随 T-1.1**，中间不得让两处同时持有同名 `@dataclass` 定义（否则重复定义 / 两处不同类，破坏 `isinstance` 与 `==`）。二者不可并发、不可跨波。
- **反断裂顺序**：Wave 1 只建领域模块 + 改垫片（`react_agent_adapter.py:88` 经垫片仍可解析）→ Wave 2 才去薄封装并改既有测试 import → Wave 3 文档，保证每个 Checkpoint 测试可绿。
- **疑点 2 不修正（AC1.6）**：`outcome_to_agent_result` 的 `handoff` 分支 `model` 照搬 `outcome.response.model if outcome.response else ""`，不借上提之名修正 ADR-0010 疑点 2（另开 spec 决策）。
- **去薄封装（AC1.7）**：删 `react_agent_adapter.py` 内 4 个 `@staticmethod` 定义、不留空壳，调用点直调领域函数；领域构件唯一权威落点在领域层。
- **不下沉技术关注点 / 不搬后续片（AC1.8、需求 6）**：`_log_token_budget_exceeded`（日志）留基础设施，仅内部改调领域 `compute_total_tokens`；`_iter_rounds` 循环主体、`_execute_tool_call`、`_collect_pending_actions`、`_RoundStreamAccumulator` 流式累加、guardrail 运行时累加、`ToolAbuseDetector`、OTel trace、`ApprovalStateStorePort` I/O、序列化、`handoff_context`、`merge_usage` 位置与时机一律不动。
- **只改 import/调用形式不改断言（`P2_Invariants` 第 6 条）**：两处既有测试仅调整 import（T-4.1）与 import + 调用形式（T-4.2），断言语义零改动；`RoundOutcome(...)` 构造与类型注解在 `react_agent_adapter.py` 内字面不变（仅 import 源变更）。
- **值对象等价（AC1.2）**：`RoundOutcome` 上提后字段名称 / 类型 / 默认值 / `RoundOutcomeKind` 与 `AgentTerminationReason` 取值 / frozen 语义与源逐一等价，字段级 docstring 随真身一并搬入。
- **ADR 纪律**：ADR-0011 `Accepted` 后只增不改；`supersedes:` 留空、不 supersede ADR-0001 与 ADR-0010（落地其方向）；不把领域事件列为落地形态。
- **回滚**：领域新模块为独立新增、`round_outcome.py` 与 `react_agent_adapter.py` 为局部改动、ADR 与文档独立新增，可按波次 `git revert`；因行为等价，回滚不影响既有测试基线。
- **行号说明**：本文 `@行号` 引自 design.md 并经现网 grep 核对（4 个 `@staticmethod` 在 980/994/1838/2255，调用点 1970/2186/2569/2791，`_log_token_budget_exceeded` 内引用 1014，`round_outcome.py` 真身 18/35，既有测试 `test_value_objects_terminated_reason_unit.py:20`、`test_react_agent_token_budget_unit.py:289/299`），落地前再以 grep 逐点核对防偏移。
