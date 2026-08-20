---
status: Accepted
date: 2026-07-05
deciders: [架构评审]
supersedes:
superseded-by:
---

# ADR-0001：移除领域事件总线基础设施

## 背景与问题（Context）

早期运行时曾包含一套领域事件基础设施：`EventBusPort`、`EventStorePort` 与 `DomainEvent`，用于在领域内以发布/订阅方式解耦副作用。随着运行时向 coding-agent + Agent 工作台双主线收敛，实际的可观测与恢复需求由结构化 trace（`SessionTrace` / `ModelCallTrace` / `ToolCallTrace` / `ApprovalTrace` 等）承担；事件总线既无稳定订阅方，又增加了领域层的抽象负担与理解成本。

> 本条 ADR 为**回溯记录**：决策已在运行时评估中执行，此处补录以固化「为什么移除」，避免后续 agent 重新引入事件总线。

## 决策（Decision）

我们将移除 `EventBusPort` / `EventStorePort` / `DomainEvent`，不再把领域事件总线作为当前运行时能力。跨入口的可观测性统一由 trace 抽象承载，副作用通过 Port/Adapter 直接编排。

## 后果（Consequences）

- **正面**：领域层更聚焦，减少无消费者的抽象；可观测性单一收敛到 trace，避免「事件 + trace」两套并行机制。
- **负面 / 代价**：若未来出现真正的多订阅方异步解耦需求，需要重新设计（届时应新增 ADR 而非直接复活旧接口）。
- **后续影响**：新增跨模块副作用编排时，走 Port/Adapter 与 trace，不得以「补回事件总线」为默认方案。

## 备选方案（Alternatives）

- **保留事件总线并补齐订阅方**：未采纳——当前无稳定订阅场景，投入产出比低，且与 trace 职责重叠。
- **用事件总线替代 trace 做可观测性**：未采纳——trace 已覆盖模型轮次/工具/审批/错误的结构化记录，且被多入口复用。
</content>
