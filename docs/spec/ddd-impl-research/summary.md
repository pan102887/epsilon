# ddd-impl-research — 落地总结（业界 DDD 调研对照 + P2 后续任务评估）

## Feature

`ddd-impl-research`：业界 DDD 主流实现方案调研，并对 P2（Agent Loop 归属重划）两片（`ddd-agent-loop-relocation` / `ddd-agent-loop-relocation-slice2`）落地后的**后续任务做正式评估与方向决策登记**。定位为**调研 + 评估 + 决策登记型** feature，交付物为文档产出，**全程零生产源码行为变更**。

evaluator 独立实跑核验 8 条检查清单 + 20 条验收标准，裁决 **PASS**。

## 最终产物清单

### 新增（评估报告）
- `docs/spec/ddd-impl-research/report.md` — 主交付物，六节：
  1. 业界 DDD 调研基线复用确认（复用 `docs/spec/ddd-gap-analysis/report.md`，结合 P2 新事实给「基线仍成立、无需重复调研」对照结论）；
  2. P2 落地新事实（三层 LOC domain 9430 / application 9918 / infrastructure 24242；ADR-0011/0012 两片 tasks 全完成且 PASS；已上提构件 + `round_outcome.py` 垫片已删）；
  3. **P2 第三片评估（核心）**：按 ADR-0010 `Orchestration_Infrastructure_Split_Line` 四判据逐条判定工具并发骨架三方法 → 留基础设施、**不开第三片**；
  4. 疑点 2（`Handoff_Model_Discrepancy`）登记为独立低优先 follow-up；
  5. 主线回归 P1（贫血子域充血试点）+ P3/治理排后；
  6. 范围纪律声明。

### 新增（ADR）
- `docs/adr/0013-defer-concurrent-tool-skeleton-relocation.md`（`Accepted`）— 正式登记「暂缓上提工具并发骨架、P2 以两片收敛」的方向决策。四段式齐全，**不 supersede** 0012，普通回链 0010/0011/0012，闭合 ADR-0012 后续影响节「工具并发编排继续上提」这一 open follow-up；备选方案 A/B/C 含未采纳理由（防后续 agent 重开第三片）。

### 修改（文档索引，doc-sync）
- `docs/adr/README.md` — 索引追加 0013 行，格式与既有条目一致。

### 规划文档（spec 三件套）
- `docs/spec/ddd-impl-research/requirement.md`（6 需求 / 20 AC）、`design.md`（交付物清单 + 7 条文档正确性属性 + 8 条检查清单）、`tasks.md`（14 项全勾选）。

## 关键评估结论

| 议题 | 结论 | 依据 |
|---|---|---|
| P2 第三片（工具并发骨架 `_dispatch/_stream/_events_concurrent_tool_calls`）是否上提领域层 | **不开第三片**，留基础设施 | 三方法本体为 `asyncio.gather` 技术并发 + `set/reset_parent_context`(ContextVar) 运行时传参 + `_record_tool_call_trace`(OTel) 可观测性缝合 + 流式事件时序；按 `Orchestration_Infrastructure_Split_Line` 判据 1/4 归基础设施；上提会把 asyncio/ContextVar/OTel 拖进领域违反 `Domain_Dependency_Rule`，属过度设计。真正领域纯判定已由 slice2 剥离。 |
| 收敛登记落点 | 新增轻量 **ADR-0013**（非改 0012 正文） | `adr.md` 只增不改；独立 ADR 的「备选方案」节是防重开护栏；口诀「三个月后新 agent 会问为什么不切第三片」 |
| 疑点 2（handoff 分支 model 取父模型） | 独立、低优先、需求驱动 follow-up，另开行为变更 spec，本 feature 不修 | 两片按行为等价正当未修；属潜在语义问题非纯重构 |
| 下一步主线 | 回归 **P1 贫血子域充血试点**（独立 spec + ADR，不在本 feature 启动） | gap report `Priority_Roadmap` |

## 验证结论

- **evaluator 独立实跑**：8 条检查 + 20 条 AC 全 PASS。
- **零源码改动**：`git diff --stat -- epsilon-boot/` 空；`src/` 无任何变更。
- **ADR 只增不改**：`git diff docs/adr/0010/0011/0012-*.md` 空；adr 变更仅 README（+1 行）+ 0013（新增）。
- **基线未被改写**：`git diff --stat docs/spec/ddd-gap-analysis/report.md` 零变更。
- **代码事实一致（防编造）**：report 引用的三符号、`asyncio.gather`/`set·reset_parent_context`/`_record_tool_call_trace`、行号（2137/2205/2273 等）、三层 LOC（9430/9918/24242）均实测精确命中；`round_outcome.py` 垫片实测已删；handoff `model=outcome.response.model` 于 `agent_loop_policy.py:186/194/202` 实测在位（本 feature 未改）。
- **ADR-0013 合规**：四段式齐全、frontmatter 正确（`Accepted`/2026-07-07/deciders/supersedes 空）、序号唯一、回链而非 supersede。

## 后续事项（Follow-ups）

- **P1 贫血子域充血试点**：gap report 序列中 P2 之后的下一步主线，另开独立 spec + ADR，行为等价、择 `domain/task` 或 `domain/agent` 试点。
- **P3 应用层大文件拆分**、**治理收尾**（循环 import、序列化零残留选项 B）：排在 P1 之后。
- **疑点 2（`Handoff_Model_Discrepancy`）**：若确认应透传 handoff 目标模型，另开行为变更 spec（需求驱动、非等价重构）。
- **P2 收敛已成定论**：ADR-0013 登记后，工具并发骨架不再评估整段上提；仅当未来析出真正领域纯判定时可局部另开 spec。
