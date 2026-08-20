# 实现计划：业界 DDD 主流实现方案调研与 P2 后续任务评估

## 概述

本 feature 为**调研 + 评估 + 方向决策登记型**任务，交付物全部为文档，**不做任何生产源码行为变更**（`src/` 零改动）。本计划把 `design.md` 的 5 个交付物组件、7 条正确性属性与 8 条检查清单落为可独立执行、可评审的文档撰写与客观核验任务。

编排者已裁定以下设计取舍，本计划据此执行，不再设「待确认」任务：

1. 采用**新增轻量 ADR-0013**（`docs/adr/0013-defer-concurrent-tool-skeleton-relocation.md`）登记第三片收敛，不改写已 `Accepted` 的 ADR-0012 正文。
2. ADR-0013 **不 supersede** 0012，用普通回链闭合其 open follow-up。
3. 主题文档 `architecture.md` / `domain-model.md` 对照标注**不做**（并发骨架归属未变，由 report + ADR-0013 承载）。
4. 基线不符时**只登记差异不重写**。

波次划分：

- **波次 A（撰写主报告）**：Task 1 —— 主交付物 `report.md`，承载需求 1/2/3/4/5/6。
- **波次 B（登记方向决策）**：Task 2（新增 ADR-0013）→ Task 3（同步 README 索引，依赖 Task 2）。
- **波次 C（客观核验）**：Task 4 —— 落地 design 测试策略 8 条检查清单（依赖 Task 1/2/3）。

代码事实基线（撰写与核验共用，已在当前 `TEST` 分支核对）：

- 三符号均命中 `src/infrastructure/agent/react_agent_adapter.py`：`_dispatch_concurrent_tool_calls`（:2137）、`_stream_concurrent_tool_progress`（:2205）、`_events_concurrent_tool_calls`（:2273）。
- 真实符号：`asyncio.gather`（:2184/:2251/:2325）、`set_parent_context` / `reset_parent_context`（:2153/:2203 等）、`_record_tool_call_trace`（:693 定义，:2190/:2259/:2339 调用）。
- 三层 LOC：domain 9430 / application 9918 / infrastructure 24242（domain 较基线 +1122）。

## Tasks

- [x] 1. 撰写主交付物报告 `report.md`（波次 A · 需求 1/2/3/4/5/6）
  - [x] 1.1 搭建 `report.md` 骨架与调研基线复用确认（第一节）
    - 在 `docs/spec/ddd-impl-research/report.md` 创建文件
    - 顶部按 `docs/spec/ddd-gap-analysis/report.md` 风格写 `> 范围 / 依据 / 截至日期 2026-07-07 / 代码事实以当前 TEST 分支为准`
    - 「一、调研基线复用确认」：引用 `docs/spec/ddd-gap-analysis/report.md` 为业界主流 DDD 调研 + 本项目对照的评估基线；给出「基线仍成立、无需重复业界调研」结论；显式声明不重写 / 不替换基线内容
    - 若发现基线与代码事实不符：仅登记差异并标注「基线待另行修订」，不在本 feature 改基线
    - _需求: 1.1、1.3、1.4_
  - [x] 1.2 撰写 P2 落地后新事实对照（第二节）
    - 在 `docs/spec/ddd-impl-research/report.md` 追加「二、P2 落地后新事实对照」
    - 登记三层 LOC 现状：domain 9430 / application 9918 / infrastructure 24242（domain 较基线 +1122、infrastructure 略降）
    - 登记 P2 首片（ADR-0011）+ 第二片（ADR-0012）tasks 全部完成、evaluator 均裁决 PASS
    - 登记已上提构件：`domain/agent/agent_loop_policy.py` 纯叶子 + `RoundOutcome`、`AgentLoopOrchestrator` 领域服务、`AgentLoopEffects` 领域端口；登记 `round_outcome.py` 垫片已删
    - 显式声明不推翻 ADR-0011 / ADR-0012 已定结论
    - _需求: 1.2、2.1、2.2、2.3_
  - [x] 1.3 撰写 P2 第三片四判据评估（第三节）
    - 在 `docs/spec/ddd-impl-research/report.md` 追加「三、P2 第三片评估（工具并发骨架）」
    - 指名三符号（`_dispatch_concurrent_tool_calls` / `_stream_concurrent_tool_progress` / `_events_concurrent_tool_calls`）及位置 `src/infrastructure/agent/react_agent_adapter.py:2137+`
    - 用「判据 → 证据（真实符号）→ 归层」表格，按 ADR-0010 `Orchestration_Infrastructure_Split_Line` 四条判据逐条判定，引用真实符号 `asyncio.gather`、`set_parent_context` / `reset_parent_context`、`_record_tool_call_trace`
    - 结论：判据 1、4 命中「留基础设施」，判据 2、3 命中「无可上提领域纯判定」；故属技术并发编排 + 运行时上下文传参（ContextVar）+ 可观测性缝合（OTel）+ 流式事件时序，应留基础设施，**不开 P2 第三片**
    - 依据补充：把 asyncio / ContextVar / OTel 拖进领域层属过度设计（违反 `ddd-architecture.md` 领域禁框架依赖）；真正领域纯判定已由 slice2 剥离；指向 ADR-0013 登记
    - _需求: 3.1、3.2、3.3_
  - [x] 1.4 撰写 follow-up 登记、主线回归与范围纪律声明（第四/五/六节）
    - 在 `docs/spec/ddd-impl-research/report.md` 追加三节
    - 「四、follow-up 登记」：把 `Handoff_Model_Discrepancy`（疑点 2，handoff 分支 `model` 取父模型而非目标模型）登记为独立、低优先、需求驱动、另开行为变更 spec 承载的 follow-up，声明本 feature 不修对应生产源码
    - 「五、主线回归标注」：依 `Priority_Roadmap` 记 P2 收敛后回归 P1（贫血子域充血试点），P1 属独立 spec + ADR、不在本 feature 启动；P3（应用层大文件拆分）+ 治理收尾排 P1 之后
    - 「六、范围纪律声明」：声明本 feature 零生产源码行为变更；改动类型仅限 spec 文档 / ADR 登记 / 文档索引；显式声明不处理 `SOURCES.txt` 中 `round_outcome.py` 构建残留
    - _需求: 4.1、4.2、4.3、5.1、5.2、5.3、6.1、6.2、6.4_

- [x] 2. 新增 ADR-0013 登记第三片方向收敛（波次 B · 需求 3、6）
  - [x] 2.1 核验 ADR 序号未占用
    - 执行 `ls docs/adr/` 确认 `0013-*` 序号未被占用（当前索引最大为 0012）
    - 若已被占用则取下一个未用序号，并同步调整 Task 2.2 / 3 文件名与索引行
    - _需求: 6.3_
  - [x] 2.2 撰写 `docs/adr/0013-defer-concurrent-tool-skeleton-relocation.md`
    - 复制 `docs/adr/0000-template.md` 结构，取序号 0013，中文四段式
    - front-matter：`status: Accepted`、`date: 2026-07-07`、`deciders: [后端架构维护者]`、`supersedes:` 与 `superseded-by:` 留空（不 supersede 0012）
    - 标题：「ADR-0013：工具并发骨架经评估留基础设施、不开 P2 第三片（方向收敛）」
    - 背景与问题：承接 ADR-0010 `Split_Line` 判据与 ADR-0012 后续影响 open 项「工具并发编排是否继续上提领域服务 + 端口」；回链 0010 / 0011 / 0012；指名三符号与 `react_agent_adapter.py:2137+`
    - 决策：按四判据判 `Concurrent_Tool_Skeleton` 为技术并发编排 + 运行时上下文 + 可观测性缝合，留基础设施、不开 P2 第三片；本 ADR 零生产代码改动
    - 后果：正面（闭合 open 项、防后续 agent 重开高风险第三片、领域层免于 asyncio/ContextVar/OTel 污染）；负面/代价（同轮多工具执行编排仍在 `react_agent_adapter.py`，可接受属技术封装）；后续影响（P2 视为收敛，主线回归 P1，回链本 spec `report.md`）
    - 备选方案（未采纳）：(a) 上提整套并发骨架为领域服务 + 端口 —— 会把 asyncio/ContextVar/OTel 拖进领域，违反 `Domain_Dependency_Rule`，过度设计；(b) 只上提「同轮工具编排顺序」纯序 —— 无独立纯判定可剥离，slice2 已提净；(c) 不写 ADR、留 0012 open 项悬置 —— 违反 `adr.md` 防重开护栏；(d) 改写 0012 后续影响节登记 —— 违反只增不改
    - _需求: 3.4、6.2_
  - [x] 3. 同步 `docs/adr/README.md` 索引（波次 B · 依赖 Task 2 · 需求 6.3）
    - 在 `docs/adr/README.md` 索引表末尾（0012 行之后）追加一行，对齐既有列格式：
      `| [0013](0013-defer-concurrent-tool-skeleton-relocation.md) | 工具并发骨架经评估留基础设施、不开 P2 第三片（方向收敛） | Accepted | 2026-07-07 |`
    - _需求: 6.3_

- [x] 4. 检查点：落地 design 测试策略 8 条客观核验（波次 C · 依赖 Task 1/2/3）
  - [x] 4.1 零源码改动 + ADR 只增不改断言（检查 1、2）
    - `git diff --stat -- ':!docs'` 与 `git diff --stat -- src/` 均期望零输出（对应 Property 2）
    - `git diff --stat docs/adr/` 仅出现 `README.md`（改）与 `0013-*.md`（增），0001–0012 各文件零变更（对应 Property 1）
    - _需求: 3.4、4.3、6.1、6.2_
  - [x] 4.2 索引一致性与四判据论证断言（检查 3、4）
    - `grep -n "0013" docs/adr/README.md` 命中且链接指向 `0013-defer-concurrent-tool-skeleton-relocation.md`（对应 Property 4）
    - `grep -cE "判据 1|判据 2|判据 3|判据 4|留基础设施|不开.*第三片" docs/spec/ddd-impl-research/report.md` 四判据与结论均命中；`grep -n "备选方案" docs/adr/0013-*.md` 命中（对应 Property 5）
    - _需求: 3.2、3.3、6.3_
  - [x] 4.3 代码事实一致断言（检查 5）
    - `grep -nE "_dispatch_concurrent_tool_calls|_stream_concurrent_tool_progress|_events_concurrent_tool_calls|asyncio.gather|set_parent_context|reset_parent_context|_record_tool_call_trace" src/infrastructure/agent/react_agent_adapter.py` 各符号均命中，佐证 report 引用真实符号
    - 核对 report 三层 LOC 与 requirement 登记一致（domain 9430 / application 9918 / infrastructure 24242，对应 Property 3）
    - _需求: 1.2、3.1、3.2_
  - [x] 4.4 基线复用、主线登记与疑点 2 登记断言（检查 6、7、8）
    - `grep -nE "ddd-gap-analysis|仍成立|回归 P1|独立 spec|P3" docs/spec/ddd-impl-research/report.md` 命中；`git diff --stat docs/spec/ddd-gap-analysis/report.md` 零变更（对应 Property 7）
    - `grep -nE "疑点 2|Handoff_Model_Discrepancy|低优先|另开.*spec" docs/spec/ddd-impl-research/report.md` 命中（对应 Property 6）
    - `grep -n "SOURCES.txt\|round_outcome" docs/spec/ddd-impl-research/report.md` 确认 report 声明不处理构建残留（检查 8）
    - _需求: 1.1、1.3、1.4、4.1、4.2、5.1、5.2、5.3、6.4_

## 备注

### 任务 → 需求 AC → design 组件 → 正确性属性 追溯表

| 任务 | 覆盖需求 AC | design 组件 | 正确性属性 | 验证检查 |
| --- | --- | --- | --- | --- |
| 1.1 | 1.1、1.3、1.4 | 组件 1（一节） | Property 7 | 检查 6 |
| 1.2 | 1.2、2.1、2.2、2.3 | 组件 1（二节） | Property 1/3/7 | 检查 2/5/6 |
| 1.3 | 3.1、3.2、3.3 | 组件 2（三节内嵌四判据） | Property 3/5 | 检查 4/5 |
| 1.4 | 4.1、4.2、4.3、5.1、5.2、5.3、6.1、6.2、6.4 | 组件 1（四/五/六节） | Property 2/6/7 | 检查 1/6/7/8 |
| 2.1 | 6.3 | 边界情形（序号唯一） | Property 4 | 检查 3 |
| 2.2 | 3.4、6.2 | 组件 3（ADR-0013） | Property 1/5 | 检查 2/4 |
| 3 | 6.3 | 组件 4（README 索引） | Property 4 | 检查 3 |
| 4.1 | 3.4、4.3、6.1、6.2 | 决策表/边界 | Property 1/2 | 检查 1/2 |
| 4.2 | 3.2、3.3、6.3 | 组件 2/3/4 | Property 4/5 | 检查 3/4 |
| 4.3 | 1.2、3.1、3.2 | 组件 2 | Property 3 | 检查 5 |
| 4.4 | 1.1、1.3、1.4、4.1、4.2、5.1、5.2、5.3、6.4 | 组件 1 | Property 6/7 | 检查 6/7/8 |

### 约束与纪律

- 遵循 `docs/steering/adr.md`：ADR 四段式、只增不改、序号全局唯一、备选方案含未采纳原因（防重开护栏）。
- 遵循 `docs/steering/doc-sync.md`：新增 ADR-0013 必同步 `docs/adr/README.md` 索引；文档述当前真实状态。
- 遵循 `docs/steering/code-documentation.md`：全程中文撰写、结论可核验。
- 全程不改 `src/`、不改写已 `Accepted` 的 ADR（0001–0012）正文、不重写 `ddd-gap-analysis/report.md` 基线。
- 主题文档 `architecture.md` / `domain-model.md` 对照标注按编排者裁定不做（组件 5 可选项本 feature 不落地）。

### 覆盖性说明

- 需求覆盖：20 条 AC 全部被 Task 1–4 覆盖（见追溯表）。
- 组件覆盖：组件 1（Task 1）、组件 2（Task 1.3）、组件 3（Task 2.2）、组件 4（Task 3）均有实现任务；组件 5 按裁定不做。
- 正确性属性覆盖：7 条 Property 均由 Task 4 的 8 条检查客观核验。
