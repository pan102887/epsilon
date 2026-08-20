# Review Log — ddd-agent-loop-relocation

> Append-only audit history of generator/evaluator activity. Never overwrite past entries.

## Wave 1（T-1.1 / T-1.2 / T-1.3 + Checkpoint 1）

- Task: T-1.1（新建 `src/domain/agent/agent_loop_policy.py`）、T-1.2（`round_outcome.py` 降为 re-export 垫片）、T-1.3（新增 `test/domain/agent/test_agent_loop_policy_unit.py`）。
- Attempt: 1
- Evaluator verdict: NOT INVOKED — `spec-evaluator` subagent 在本执行环境不可用（`Task` 工具报 "No such tool available"）。改以对源实现的逐行等价核对 + Checkpoint 1 全量门禁自评替代。
- 自评结论（无阻断项）：
  - 行为等价：4 个纯函数体逐行照搬源 `react_agent_adapter.py`（`_compute_total_tokens` 986-991 / `_is_token_budget_exceeded` 996-998，自引用改调本模块 `compute_total_tokens` / `_detect_handoff` 1858-1865 / `_outcome_to_agent_result` 2277-2303）；`RoundOutcome` 字段/类型/默认值/`Literal`/frozen/字段级 docstring 与源 `round_outcome.py:18/34-88` 逐一等价。
  - 疑点 2 不修正：`outcome_to_agent_result` handoff 分支 `model=outcome.response.model if outcome.response else ""` 照搬（grep 命中 line 181）。
  - 反向依赖：grep `import/from (application|infrastructure|fastapi|pydantic)` on `agent_loop_policy.py` 零命中；仅 import domain.* + 标准库。唯一 `ReActAgentAdapter` 字样在 docstring（描述性，非代码依赖）。核实源 4 个 `@staticmethod` 无 `self`/无 infrastructure 符号 → design 声称的零反向依赖属实。
  - 门禁：`assert RoundOutcome is R2`（垫片同一类）通过；`pytest test/domain/agent` 243 passed（含既有 terminated_reason 测试经垫片仍解析）；`ruff` All checks passed；`pyright` 0 errors；adapter 经垫片 import ok。
- 改动文件：
  - 新增 `epsilon-boot/src/domain/agent/agent_loop_policy.py`
  - 改 `epsilon-boot/src/infrastructure/agent/round_outcome.py`（真身删除，降为 re-export 垫片）
  - 新增 `epsilon-boot/test/domain/agent/test_agent_loop_policy_unit.py`
- 结论：Wave 1 + CP1 全部门禁通过，勾选 T-1.1/T-1.2/T-1.3 与 CP1。未进入 Wave 2。

## Wave 2（T-3.1 / T-3.2 / T-4.1 / T-4.2 + Checkpoint 2）

- Task: T-3（`react_agent_adapter.py` 去薄封装：改 import + 删 4 个 `@staticmethod` + 5 处调用点直调领域函数）、T-4（两处既有测试改 import/调用形式）。
- Attempt: 1
- Evaluator verdict: NOT INVOKED — `spec-evaluator` subagent 在本执行环境不可用（当前会话仅暴露 Read/Write/Edit/Bash，无 Agent/Task 工具，与 Wave 1 记录一致）。改以 Checkpoint 2 全量门禁 + baseline 对比自评替代。
- 自评结论（无阻断项）：
  - 去薄封装：删 `react_agent_adapter.py` 内 4 个 `@staticmethod`（`_compute_total_tokens`/`_is_token_budget_exceeded`/`_detect_handoff`/`_outcome_to_agent_result`）；`_log_token_budget_exceeded` 本体保留在基础设施，内部 `ReActAgentAdapter._compute_total_tokens(total_usage)` → 领域 `compute_total_tokens(total_usage)`。
  - 5 处调用点直调：`_iter_rounds` 1970 `self._detect_handoff(context)`→`detect_handoff(context)`、2186 `self._is_token_budget_exceeded(config, total_usage)`→`is_token_budget_exceeded(...)`、执行入口 2569/2791 `self._outcome_to_agent_result(outcome)`→`outcome_to_agent_result(outcome)`、`_log_token_budget_exceeded` 1014（见上）。`RoundOutcome(...)` 构造与类型注解字面不变，仅 import 源改指 `domain.agent.agent_loop_policy`。
  - import：`from infrastructure.agent.round_outcome import RoundOutcome` → `from domain.agent.agent_loop_policy import (RoundOutcome, compute_total_tokens, detect_handoff, is_token_budget_exceeded, outcome_to_agent_result)`，并按 ruff isort 归入 domain 块（`domain.agent.agent_loop_policy` 排在 `domain.agent.exceptions` 前）。
  - 既有测试仅改 import/调用形式，断言零改动：`test_value_objects_terminated_reason_unit.py:20` import 改指领域；`test_react_agent_token_budget_unit.py:289` 局部 import 改 `from domain.agent.agent_loop_policy import RoundOutcome, outcome_to_agent_result`，299 `ReActAgentAdapter._outcome_to_agent_result(outcome)`→`outcome_to_agent_result(outcome)`，断言 300 不动。
  - CP2 门禁：grep 被删 4 函数名在 adapter 零命中（含自引用）；全量 `PYTHONPATH=src uv run --frozen pytest` = 2893 passed, 3 skipped, 0 failed（含特征化测试）；ruff 3 文件中 adapter 与 terminated_reason 测试 clean，token_budget 测试仅剩 **既有 baseline** I001（stash 对比确认 pre-existing，位于顶层 `domain.agent.tools`/`domain.agent.ports` 未排序，与本片改动无关，未触碰）；pyright adapter 5 errors 与 stash baseline **逐条一致**（仅行号因删 ~90 行而位移，零新增）；`AgentPort` 四签名（run/run_streaming/run_events/resume）字面未变，`ports.py` 未改。
- 改动文件：
  - 改 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py`（-108/+12：删 4 定义、改 import、5 处调用点委托）
  - 改 `epsilon-boot/test/domain/agent/test_value_objects_terminated_reason_unit.py`（import 一行）
  - 改 `epsilon-boot/test/infrastructure/agent/test_react_agent_token_budget_unit.py`（import + 调用形式两行）
- 结论：Wave 2 + CP2 全部门禁通过，勾选 T-3/T-3.1/T-3.2、T-4/T-4.1/T-4.2 与 CP2。未进入 Wave 3。

## Wave 3（T-6.1 / T-6.2 / T-6.3 + Checkpoint 3 最终门禁）

- Task: T-6.1（新建 `docs/adr/0011-relocate-agent-loop-leaf-orchestration-to-domain.md`）、T-6.2（`docs/adr/README.md` 索引追加 0011 行）、T-6.3（同步 `docs/architecture.md` / `docs/domain-model.md`），及 CP3 最终门禁。
- Attempt: 1
- Evaluator: SKIPPED — 本切片为纯文档新增/同步（ADR + 索引 + 主题文档），无生产源码/配置/测试文件改动（Wave 3 与代码正交，代码由 Wave 1/2 落地）；按 Main Loop step 6，纯文档切片不调用 spec-evaluator，直接勾选并自评门禁。
- ADR-0011（人工核对齐备）：四段式（背景/决策/后果/备选方案）齐全；front matter `status: Accepted`、`date: 2026-07-06`、`deciders: [后端架构维护者]`、`supersedes:` 留空、`superseded-by:` 留空；标题「ADR-0011：Agent Loop 纯编排叶子与 RoundOutcome 上提领域层（P2 落地首片）」；不 supersede ADR-0001/0010（落地 0010 方向）；后果节含垫片临时性 + 为何 `_iter_rounds` 主体/`_execute_tool_call`/`_collect_pending_actions`/流式留后续片（回链 ADR-0010「高度交织」警示与方案 C 否决）；备选方案含「薄封装保留空壳 @staticmethod」「全量改 import 不留垫片」「本片连 _iter_rounds 一起搬」及未采纳原因；正文不把领域事件列为落地形态、明令 SHALL NOT 复活事件总线。
- 文档同步：`architecture.md` Port/Adapter 表 `AgentPort` 行补注委托、ReAct Agent Loop 流程节补一段（纯编排叶子 + `RoundOutcome` 上提 `domain/agent/agent_loop_policy.py`、垫片、循环主体等留后续片）；`domain-model.md` 在「Agent 配置」与「工具调用」间新增「Agent Loop 编排构件（`domain/agent/agent_loop_policy.py`）」节（`RoundOutcome`/`RoundOutcomeKind` + 4 纯函数，回链 ADR-0011）。两文件均无过度改写。
- CP3 门禁实测：`test -f 0011-*.md` → EXISTS；`grep supersedes:` → line 5 值为空；`grep 0011 README.md` → 命中 line 21；`grep agent_loop_policy` architecture/domain-model → 均命中；全量 `PYTHONPATH=src uv run --frozen pytest` = 2893 passed, 3 skipped, 0 failed；特征化 `test_react_agent_characterization_*.py` 6 passed；`grep import/from (application|infrastructure|fastapi|pydantic)` on `agent_loop_policy.py` → 零命中；`grep AgentPort 四签名` ports.py → run/run_streaming/run_events/resume 四者在（68/89/110/124），`ports.py` 未改；`grep outcome.response.model if outcome.response` → 命中 line 181（疑点 2 不修正）；ruff 三文件 All checks passed，pyright `agent_loop_policy.py` 0 errors；`git diff --name-only` 源码仅 `agent_loop_policy.py`（新增）+ `round_outcome.py` + `react_agent_adapter.py` + `test/domain/agent/`（新增单测 + terminated_reason import）+ `test/infrastructure/agent/test_react_agent_token_budget_unit.py`，docs 仅 adr + architecture/domain-model + spec；adapter diff 仅 import swap + 删 4 个 `@staticmethod` + `_log_token_budget_exceeded` 1 行 + 4 处调用点委托（人工核 `_iter_rounds` 循环主体/`_execute_tool_call`/`_collect_pending_actions`/流式/前端未动）。
- 改动文件：
  - 新增 `docs/adr/0011-relocate-agent-loop-leaf-orchestration-to-domain.md`
  - 改 `docs/adr/README.md`（索引追加 0011 行）
  - 改 `docs/architecture.md`（Port/Adapter 表 + ReAct Agent Loop 流程节）
  - 改 `docs/domain-model.md`（新增 Agent Loop 编排构件节）
- 结论：Wave 3 + CP3 全部门禁通过，勾选 T-6/T-6.1/T-6.2/T-6.3 与 T-7（CP3）。spec 全波次完成。
