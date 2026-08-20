# Review Log — ddd-anemic-domain-pilot

> Append-only 审查历史。记录每次评审调用（任务序号、尝试次数、结论、阻断问题摘要、复评前改动）与跳过评审的切片说明。

## Wave 1 + Checkpoint 1

- 任务 1.1（新建 `domain/task/enums.py`）+ 1.2（新建 `domain/task/policy.py`）+ 1.3–1.6（4 个单测）：本切片新增生产源码与测试文件，按主循环规则应交由 `spec-evaluator` 评审。**评审跳过原因**：当前执行环境未提供 `Agent`/`spec-evaluator` 子代理工具（仅 Read/Write/Edit/Bash 可用），无法发起评审调用。改以设计正确性属性 + Checkpoint 1 门禁命令作自验证，全部通过（详见完成报告）。落地前已逐字段核对 `domain/agent/exceptions.py` 异常构造签名、`domain/agent/value_objects.py` 值对象字段、`domain/task/value_objects.py` 的 `TaskStatus` 成员，与 design 代码块一致，无偏差。
- 任务 2（Checkpoint 1 门禁）：纯校验任务（无源码/测试改动），直接执行门禁命令并勾选，未触发评审。

## Wave 2 + Checkpoint 2

- 任务 3.1–3.4 / 4.1 / 4.2 / 5.1 / 5.2（委派深度 5 处委托 `DelegationDepthPolicy`；`TaskAgentAdapter` 委托 `TaskContinuationPolicy` 与 `ApprovalResumePrecondition`；应用层两处委托 `TaskStatusMapping` + 本层装配）：本切片修改生产源码，按主循环规则应交 `spec-evaluator` 评审。**评审跳过原因**：当前执行环境仍未提供 `Agent`/`spec-evaluator` 子代理工具（仅 Read/Write/Edit/Bash 可用），无法发起评审调用。改以 design 组件 1/2/3/4 的 before/after 调用点表 + 正确性属性 1/2/3/4/6 + Checkpoint 2 门禁作自验证，全部通过。落地逐处 grep 定位实际行（design @行号已偏移）、逐字符核对新服务判据与被删内联逻辑等价，尤其 3.4 两处刻意用不同方法（`_one`→`exceeds_for_current_depth`、`handoff`→`exceeds_for_next_depth`，差异保留 AC2.4）。
- 任务 4.1 pyright 修正：`should_pause(terminated_reason)` 新调用因 `getattr` 返回 `Any | str` 触发 2 处 `reportArgumentType` 新增错误（原 `not in (...)` 元组成员测试可让 pyright 窄化 Literal，收敛后丢失窄化）。按 design「line 299 getattr 保留」约束，对 getattr 结果补显式注解 `terminated_reason: AgentTerminationReason = getattr(...)`（保留 getattr 调用、恢复 Literal 类型、行为不变），并 import `AgentTerminationReason`。修正后 `task_agent_adapter.py` pyright 零错。
- 任务 5.3（按需迁移测试 import）：全量与分域回归确认既有测试 import 未断裂（`ApprovalDecisionCount/Order/NotAllowedError` 仍可从 `domain.agent.exceptions` 导入），无需改动，直接勾选。
- 任务 6（Checkpoint 2 门禁）：纯校验任务。全量 `pytest` 2869 passed / 3 skipped / 0 failed；委派深度内联比较 grep 仅命中 delegation_adapter.py 两条**注释/docstring**（非代码判据）；`ApprovalDecision*` 在 `task_agent_adapter.py` 零命中；`ruff check` 三目录全过；`pyright` 改动文件仅余 4 处**既存基线错误**（asdict/awaitable/_collaboration_summary，与本波改动无关，已通过 git stash 对照确认），零新增错误。

## Wave 3 + Checkpoint 3

- 任务 7.1（新增 ADR-0009）+ 7.2（登记 `docs/adr/README.md` 索引）：本切片**纯文档**（仅落 `docs/adr/`），无生产源码 / 配置 / 测试改动，按主循环 step 6 直接勾选、**跳过 `spec-evaluator` 评审**。ADR 严格套用 `0000-template.md` 四段式与 front matter；`status: Accepted`、`date: 2026-07-06`、`supersedes:` **留空**（不 supersede ADR-0001）、`superseded-by:` 留空；四段（背景 / 决策 / 后果 / 备选方案）按 design「ADR-0009 草案要点」逐条落地，备选 (a)–(e) 一一给出未采纳原因；README 索引在 0008 行后追加 0009 行、列格式对齐。
- 任务 8（Checkpoint 3 最终门禁）：纯校验任务，逐条执行门禁命令。ADR 文件存在（EXISTS）；`supersedes:` 字段存在且值为空；README 命中 0009；全量 `pytest` **2869 passed / 3 skipped / 0 failed**；`grep` `domain/task` 反向 / 框架依赖——**import 语句零命中**，7 处命中均为 `enums.py` / `policy.py` 的 docstring / 注释中出现的 `domain/run` 字样（说明「刻意不引用」的约束文本，非 import，可接受）；`ruff check` 四目录 `All checks passed!`；`git status --porcelain` 确认源码改动仅落 `src/domain/task/`（新增 policy.py/enums.py）+ design 列出的 7 处调用点 + `test/domain/task/` 4 个单测，文档仅落 `docs/`，未碰四处正向样板与 `react_agent_adapter.py`。
