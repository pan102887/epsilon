# 架构决策记录（ADR）

本目录存放本项目的**架构决策记录**。写作与维护规则见 [../steering/adr.md](../steering/adr.md)。

新增决策：复制 [`0000-template.md`](0000-template.md) 为 `NNNN-kebab-case-标题.md`（取下一个未用序号），填写后在下表登记。ADR 一旦 `Accepted` 即**只增不改**；结论变化时新增条目并将旧条目状态改为 `Superseded by ADR-NNNN`。

## 索引

| 编号 | 标题 | 状态 | 日期 |
|---|---|---|---|
| [0001](0001-remove-domain-event-bus.md) | 移除领域事件总线基础设施 | Accepted | 2026-07-05 |
| [0002](0002-storage-tier-abstraction.md) | 引入 StorageTier 存储等级抽象与本地文件 tier→目录映射 | Accepted | 2026-07-05 |
| [0003](0003-artifact-first-class-abstraction.md) | 引入 ArtifactTrace 值对象与 ArtifactStorePort，及 TraceStorePort 的 tier 兼容策略 | Accepted | 2026-07-05 |
| [0004](0004-config-local-properties-precedence.md) | config.local.properties 本地覆盖配置的优先级插入位置 | Accepted | 2026-07-05 |
| [0005](0005-tui-cli-file-logging-default.md) | TUI/CLI 本地文件日志的默认策略 | Accepted | 2026-07-05 |
| [0006](0006-tenant-visibility-and-user-tier-persistence-boundary.md) | 多租户可见性机制与会话主状态 USER tier 默认路径的安全边界 | Accepted | 2026-07-05 |
| [0007](0007-establish-domain-tactical-modeling-and-pydantic-boundary.md) | 确立领域层战术建模范式与 Pydantic 边界 | Accepted | 2026-07-06 |
| [0008](0008-extract-domain-serialization-to-infrastructure-mappers.md) | 将领域对象序列化职责外移至基础设施层序列化映射器 | Accepted | 2026-07-06 |
| [0009](0009-introduce-domain-services-in-task-subdomain.md) | 在 domain/task 引入领域服务一等抽象（充血化试点） | Accepted | 2026-07-06 |
| [0010](0010-relocate-agent-loop-to-domain-direction.md) | 将 ReAct Agent Loop 编排逻辑归属领域层的方向决策（P2 前置） | Accepted | 2026-07-06 |
| [0011](0011-relocate-agent-loop-leaf-orchestration-to-domain.md) | Agent Loop 纯编排叶子与 RoundOutcome 上提领域层（P2 落地首片） | Accepted | 2026-07-06 |
| [0012](0012-relocate-agent-loop-body-and-tool-controlflow-to-domain.md) | 上提 Agent Loop 循环编排主体与工具执行控制流至领域层（P2 第二片，引入 AgentLoopOrchestrator 领域服务与 AgentLoopEffects 端口回调） | Accepted | 2026-07-07 |
| [0013](0013-defer-concurrent-tool-skeleton-relocation.md) | 暂缓上提工具并发骨架至领域层（P2 第三片方向收敛，工具并发编排留基础设施） | Accepted | 2026-07-07 |
| [0014](0014-introduce-guardrail-domain-service-in-agent-subdomain.md) | 在 domain/agent 子域引入护栏策略领域服务一等抽象（充血化试点第二子域，StaticAgentGuardrailPolicy 上提领域层） | Accepted | 2026-07-07 |
| [0015](0015-uplift-agent-config-normalization-approval-lookup-and-relocate-segment-continuation.md) | 在 domain/agent 上提委派深度规范化与审批默认查表、平移分段续跑判定（充血化后续片） | Accepted | 2026-07-07 |
| [0016](0016-application-chat-workflow-and-handoff-policy-boundaries.md) | 应用层 Chat workflow/service 与 Handoff policy 边界收敛 | Accepted | 2026-07-08 |
| [0017](0017-establish-task-application-workflow-boundary.md) | 确立 Task application workflow 边界 | Accepted | 2026-07-09 |
| [0018](0018-split-composition-root-into-application-container-package.md) | 拆分组合根为 application/container 子包 | Accepted | 2026-07-09 |
</content>
