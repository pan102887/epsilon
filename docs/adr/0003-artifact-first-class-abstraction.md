---
status: Accepted
date: 2026-07-05
deciders: [spec-designer, 平台架构负责人]
supersedes:
superseded-by:
---

# ADR-0003：引入 ArtifactTrace 值对象与 ArtifactStorePort，及 TraceStorePort 的 tier 兼容策略

## 背景与问题（Context）

`structured-agent-trace` 已交付 trace 一等抽象（值对象 + `TraceStorePort` + `LocalFileTraceStoreAdapter` + 查询 API）。但「任务产物 / 命令输出摘要 / 生成文件清单」尚无一等持久化抽象（P0.3 标注 `ArtifactTrace` 仍缺）。同时，ADR-0002 要求产物存储 Port 以 `StorageTier` 为定位维度，而 `TraceStorePort` 已被 `ReActAgentAdapter`、trace 查询 router、DI、四个 Agent 入口在用——若直接把 tier 变为必填参数，会破坏既有调用点与「可选注入零行为变化」语义。

## 决策（Decision）

我们将：

1. 新增 **`ArtifactTrace`** frozen dataclass 值对象（放入既有 `src/domain/agent/trace_value_objects.py`，与 trace 系列同构），判别字段 `kind: Literal["artifact"]`，字段含逻辑路径、类型、大小、摘要、来源工具、时间戳；大字段沿用既有截断常量范式（新增 `ARTIFACT_SUMMARY_MAX_LEN`），**不记录完整敏感内容**。`ArtifactTrace` 是独立联合类型（不并入 `AgentStepTrace`，避免污染既有 trace 序列化的 `_KIND_MAP`）。
2. 新增 **`ArtifactStorePort`** Protocol（放入 `src/domain/agent/ports.py`，与 `TraceStorePort` 同域），方法 `append_artifact` / `list_artifacts`，签名以 `tier: StorageTier = StorageTier.PROJECT` 作为定位维度之一。
3. 新增 **`LocalFileArtifactStoreAdapter`**（`src/infrastructure/artifact/`），经 `LocalFileTierResolver` 写入对应 tier 的 `artifacts/`，JSONL append-only、`asyncio.to_thread` 包 IO、try/except 故障隔离，与 `LocalFileTraceStoreAdapter` 语义一致。
4. **`TraceStorePort` 兼容策略：新增可选、带默认值的 `tier` 关键字参数**——`append_step(session_id, step, *, tier: StorageTier = StorageTier.PROJECT)`、`get_session_trace(session_id, *, tier=...)`、`list_traces(limit=20, *, tier=...)`。既有调用点（`ReActAgentAdapter._record_trace`、trace router）**不传 tier 即取默认 PROJECT**，行为与今日等价；`LocalFileTraceStoreAdapter` 内部改为经 resolver 解析 PROJECT tier 的 `traces/`，结果与既有 `.epsilon/traces` 等价。

## 后果（Consequences）

- **正面**：artifact 与 trace 共享同构 schema 与 tier 语义；写入方（含未接入 artifact 的旧入口）零改动即可继续工作；未来云端 adapter 复用同一 Port 与 schema。
- **负面 / 代价**：`TraceStorePort` 签名新增 keyword-only 参数，`LocalFileTraceStoreAdapter` 构造从 `store_dir: str` 改为注入 `tier_resolver`——这是本特性对既有代码最有风险的改动点，须以回归测试锁定「PROJECT-traces 路径等价」与「不传 tier 行为不变」。
- **后续影响**：DI `_create_trace_store` 改为注入 resolver；新增 `_create_artifact_store` 工厂与 `ArtifactStorePort` 绑定；各 Agent 入口对 artifact store 采用与 trace 相同的 `None` 静默跳过语义。

## 备选方案（Alternatives）

- **方案 A：把 tier 作为 `TraceStorePort` 必填位置参数** —— 未采纳：破坏所有既有调用点与「可选注入零行为变化」，违背需求 8。
- **方案 B：`ArtifactTrace` 并入 `AgentStepTrace` 联合类型** —— 未采纳：artifact 与「Agent 执行步骤」语义不同，混入会污染 trace 查询与 `_KIND_MAP`，且 artifact 独立存于 `artifacts/` 而非 session trace JSONL。
- **方案 C：为 artifact 新建独立 domain 子包** —— 未采纳：artifact 与 trace 同属 Agent 运行产物、共享截断范式与故障隔离语义，同域内聚更符合 SRP 与最小改动。
