# 需求文档：业界 DDD 主流实现方案调研与 P2 后续任务评估

## 简介

### 背景与动机

本仓库后端 `epsilon-boot`（FastAPI + DDD 六边形架构）此前完成两批 DDD 纠偏与 P2（Agent Loop 归属重划）两片落地：

- `docs/spec/ddd-gap-analysis/report.md` 已沉淀「业界主流 DDD 实现方案调研 + 本项目差距评估」的整合基线，并给出优先级路线 `规范先行（已完成）→ P1 单子域充血试点 → P2 Agent Loop 归属重划（先 ADR）→ P3 应用层拆分`。
- `docs/spec/ddd-agent-loop-relocation`（P2 首片，ADR-0011）与 `docs/spec/ddd-agent-loop-relocation-slice2`（P2 第二片，ADR-0012）的 `tasks.md` 均已全部完成、evaluator 均裁决 PASS。首片搬出纯编排叶子函数 + `RoundOutcome` 值对象至 `domain/agent/agent_loop_policy.py`；第二片以「领域服务 `AgentLoopOrchestrator` + 领域端口 `AgentLoopEffects`」上提循环编排主体与工具控制流纯判定，`round_outcome.py` 垫片已删。
- 当前三层 LOC：domain 9430 / application 9918 / infrastructure 24242（domain 较基线 +1122，infrastructure 略降，P2 上提已生效）。

P2 两片落地后，仍有若干 follow-up 悬而未决，最大一项是「P2 第三片：工具并发骨架是否上提领域层」。这些 follow-up 缺乏**正式评估与决策登记**，若不收敛，后续 agent 可能重新提出已被隐性否决的方案、或误开高风险第三片，违反 `change-discipline.md`「不擅自推翻已定结论」与 `adr.md`「防止后续 agent 重新提出已被否决方案」的护栏意图。

本 feature 是一个**调研 + 评估 + 方向决策登记型**任务，交付物为文档产出与决策登记，**不涉及任何生产源码行为变更**。

### 范围内行为（In-Scope）

1. **调研结论沉淀**：确认并复用 `ddd-gap-analysis/report.md` 作为业界 DDD 主流实现方案调研与本项目对照的评估基线，结合 P2 两片落地后的新事实（LOC、已上提构件、已剥离纯判定），在本 spec 中给出「调研基线仍成立、无需重复造」的对照结论。
2. **P2 第三片评估与决策登记**：对工具并发骨架 `_dispatch_concurrent_tool_calls` / `_stream_concurrent_tool_progress` / `_events_concurrent_tool_calls`（`src/infrastructure/agent/react_agent_adapter.py`）是否上提领域层，按 ADR-0010 `Orchestration_Infrastructure_Split_Line` 判据给出评估结论与依据，登记「不开第三片」的方向收敛。
3. **其余 P2 follow-up 登记**：将 ADR-0010 疑点 2（handoff 分支 `model` 取父模型）等剩余 follow-up 作为独立、需求驱动的低优先事项登记，标注优先级与承载方式（另开 spec，本 feature 只登记不修）。
4. **后续主线回归标注**：登记 P2 收敛后应回归 gap report 优先级序列中的 P1（贫血子域充血试点），且 P1 另立 spec、不在本 feature 内启动。
5. **必要的文档 / ADR 收敛标注**：在合适的既有 ADR「后续影响」节或本 spec 中，登记「第三片经评估不再开」的方向收敛，并遵循 `doc-sync.md` 同步相关索引（若新增文档）。

### 范围外边界（Out-of-Scope，明确不做）

- **不做任何生产源码行为变更**：不搬迁工具并发骨架、不修 ADR-0010 疑点 2、不动 `AgentPort` 契约、不改任何 `src/` 下运行时行为。
- **不启动 P1 充血试点实施**：P1 属独立 spec + ADR 的一等抽象决策，本 feature 仅登记回归标注。
- **不改写已 `Accepted` 的 ADR 正文**：遵循 `adr.md` 只增不改原则，历史 ADR 仅允许在「后续影响」节以增量方式登记方向收敛，或按需新增 ADR。
- **不重复业界调研**：`ddd-gap-analysis/report.md` 已是调研基线，本 feature 复用而非重写。
- **不处理构建产物残留**：`SOURCES.txt` 中 `round_outcome.py` 残留为 egg-info 打包自动再生产物，无需处理。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 需求文档 | Requirement_Document | 本 feature 交付的 `docs/spec/ddd-impl-research/requirement.md`，承载调研对照结论与 P2 后续任务评估决策登记。 |
| 业界调研基线 | Industry_Research_Baseline | `docs/spec/ddd-gap-analysis/report.md`，业界主流 DDD 实现方案调研 + 本项目差距评估的既有整合结论，本 feature 复用为基线。 |
| P2 后续任务 | P2_Followup | P2 两片（ADR-0011 / ADR-0012）落地后仍悬而未决的评估项，含工具并发骨架上提评估、ADR-0010 疑点 2 等。 |
| 工具并发骨架 | Concurrent_Tool_Skeleton | `src/infrastructure/agent/react_agent_adapter.py` 中 `_dispatch_concurrent_tool_calls` / `_stream_concurrent_tool_progress` / `_events_concurrent_tool_calls` 三方法，负责 `asyncio.gather` 并发、`set/reset_parent_context`(ContextVar)、`_record_tool_call_trace`(OTel)、事件配对相邻 yield 时序。 |
| 编排—基础设施切分线 | Orchestration_Infrastructure_Split_Line | ADR-0010 确立的可操作切分判据：封装外部技术/运行时或把观测记账缝进循环 → 留基础设施；可脱离运行时的纯业务判定 → 属领域。 |
| 第三片评估结论 | Third_Slice_Evaluation | 对 `Concurrent_Tool_Skeleton` 是否上提领域层的评估结论：按 `Orchestration_Infrastructure_Split_Line` 判为技术并发编排 + 可观测性缝合，应留基础设施，故不开第三片。 |
| 疑点 2 | Handoff_Model_Discrepancy | ADR-0010 登记的潜在语义问题：`agent_loop_policy.py` handoff 分支 `model=outcome.response.model`（父模型）而非 handoff 目标模型；两片按行为等价正当未修。 |
| 优先级路线 | Priority_Roadmap | `Industry_Research_Baseline` 给出的改进优先级序列：P1（充血试点）> P2（已落地）> P3（应用层拆分）> 治理收尾。 |
| 方向收敛标注 | Direction_Convergence_Note | 在合适的既有 ADR「后续影响」节或本 spec 中登记「第三片经评估不再开」的方向收敛，防止后续 agent 重开。 |
| 架构决策记录 | ADR | `docs/adr/` 下只增不改、可追溯的架构/方向级决策日志，写作规则见 `docs/steering/adr.md`。 |

## 需求

### 需求 1：确认并复用业界 DDD 调研基线

**用户故事：** 作为后端架构维护者，我希望本 feature 复用既有的业界 DDD 调研评估结论并结合 P2 落地后的新事实做对照确认，以便不重复造调研、且保证结论仍与当前代码基一致。

#### 验收标准

1. THE Requirement_Document SHALL 引用 Industry_Research_Baseline 作为业界主流 DDD 实现方案调研与本项目对照的评估基线。
2. THE Requirement_Document SHALL 记录 P2 两片落地后的三层 LOC 现状（domain 9430 / application 9918 / infrastructure 24242）作为对照事实。
3. THE Requirement_Document SHALL 给出「Industry_Research_Baseline 仍成立、无需重复业界调研」的对照结论。
4. THE Requirement_Document SHALL NOT 重写或替换 Industry_Research_Baseline 的内容。

### 需求 2：登记 P2 两片落地已完成的既定事实

**用户故事：** 作为后端架构维护者，我希望本 feature 明确登记 P2 首片与第二片已全部完成并 PASS 的既定事实，以便后续评估建立在不可推翻的前提上。

#### 验收标准

1. THE Requirement_Document SHALL 记录 `ddd-agent-loop-relocation`（ADR-0011）与 `ddd-agent-loop-relocation-slice2`（ADR-0012）的 tasks 均已全部完成且 evaluator 裁决 PASS。
2. THE Requirement_Document SHALL 记录 P2 已上提构件（`agent_loop_policy.py` 纯叶子 + `RoundOutcome`、`AgentLoopOrchestrator` 领域服务、`AgentLoopEffects` 领域端口）与已删除的 `round_outcome.py` 垫片。
3. THE Requirement_Document SHALL NOT 提出重新实施或推翻 ADR-0011 / ADR-0012 已定结论的需求。

### 需求 3：评估工具并发骨架是否上提并登记第三片决策

**用户故事：** 作为后端架构维护者，我希望对工具并发骨架是否上提领域层给出基于既定判据的评估结论并正式登记，以便防止后续 agent 误开高风险第三片或反复摇摆。

#### 验收标准

1. THE Requirement_Document SHALL 指名 Concurrent_Tool_Skeleton 的三个符号（`_dispatch_concurrent_tool_calls` / `_stream_concurrent_tool_progress` / `_events_concurrent_tool_calls`）及其所在文件位置。
2. THE Third_Slice_Evaluation SHALL 依据 Orchestration_Infrastructure_Split_Line 逐条判定，指出 Concurrent_Tool_Skeleton 属技术并发编排（`asyncio.gather`）+ 运行时上下文传参（ContextVar）+ 可观测性缝合（OTel trace）+ 事件时序，故应留基础设施。
3. THE Third_Slice_Evaluation SHALL 给出「不开 P2 第三片」的结论及其依据（避免把 asyncio/ContextVar/OTel 拖进领域层，属过度设计；真正领域纯判定已由 slice2 剥离）。
4. WHEN 登记第三片结论时，THE Direction_Convergence_Note SHALL 遵循 ADR 只增不改原则，仅在既有 ADR「后续影响」节增量登记或按需新增 ADR，不得改写已 Accepted 的 ADR 正文。

### 需求 4：登记 ADR-0010 疑点 2 为独立低优先 follow-up

**用户故事：** 作为后端架构维护者，我希望把 handoff 分支模型取值的潜在语义问题作为独立、需求驱动的低优先 follow-up 登记，以便它既不被遗忘、也不在本 feature 内被擅自修改。

#### 验收标准

1. THE Requirement_Document SHALL 登记 Handoff_Model_Discrepancy（handoff 分支 `model` 取父模型而非目标模型）为独立 follow-up。
2. THE Requirement_Document SHALL 标注 Handoff_Model_Discrepancy 为潜在语义问题、低优先级，并说明应作为独立行为变更 spec、需求驱动承载。
3. THE Requirement_Document SHALL NOT 在本 feature 内修改 Handoff_Model_Discrepancy 对应的任何生产源码。

### 需求 5：登记后续主线回归 P1 且不在本 feature 启动

**用户故事：** 作为后端架构维护者，我希望本 feature 明确 P2 收敛后应回归 P1 主线且 P1 另立 spec，以便路线清晰、范围不越界。

#### 验收标准

1. THE Requirement_Document SHALL 依据 Priority_Roadmap 记录 P2 收敛后应回归 P1（贫血子域充血试点）作为下一步主线。
2. THE Requirement_Document SHALL 声明 P1 实施属独立 spec + ADR，不在本 feature 内启动。
3. THE Requirement_Document SHALL 记录 P3（应用层大文件拆分）与治理收尾项按 Priority_Roadmap 排在 P1 之后。

### 需求 6：全程遵守文档产出型范围纪律

**用户故事：** 作为后端架构维护者，我希望本 feature 严格限定为文档产出与决策登记、不触碰生产行为，以便符合最小改动纪律并可被 evaluator 客观判定。

#### 验收标准

1. THE Requirement_Document SHALL 明确声明本 feature 不做任何生产源码行为变更。
2. FOR ALL 本 feature 产出的改动，THE Requirement_Document SHALL 限定其类型为 spec 文档、ADR 后续影响登记或文档索引同步。
3. IF 新增了任何 steering / 主题 / ADR 文档，THEN THE Requirement_Document SHALL 要求按 doc-sync.md 同步所有登记它的索引。
4. THE Requirement_Document SHALL NOT 要求处理 `SOURCES.txt` 中的 `round_outcome.py` 构建产物残留。
