---
status: Accepted
date: 2026-07-05
deciders: [spec-designer, 平台架构负责人]
supersedes:
superseded-by:
---

# ADR-0002：引入 StorageTier 存储等级抽象与本地文件 tier→目录映射

## 背景与问题（Context）

本仓库同时承载「本地 coding-agent runtime（epsilon TUI / exec）」与「云端 Agent 工作台基座」两条主线，共享同一套 domain / Port / DI。当前运行产物存储散落且入口各异：

- 结构化 trace 默认写 `.epsilon/traces/`（`TRACE_STORE_DIR`，相对进程 CWD）。
- 会话 / Run / checkpoint 主状态走 `LOCAL_PERSISTENCE_ROOT`（默认 `../.local_persistence/epsilon-boot`，故意落在 CWD 之外以规避与 `WORKSPACE_ROOT=cwd` 相互包含）。
- 任务产物（artifact）无一等抽象；TUI/CLI 无文件日志。

若继续让每种产物各自硬编码物理路径，写入方与 domain 会与「本地文件系统」这一后端强耦合，未来云端切换对象存储 / 分布式 FS 时须逐处改写。需要一个方向级抽象，把「存哪、怎么存」从 domain 与写入方剥离。原先纠结的「trace 用 CWD 基点 vs 持久化用 WORKSPACE_ROOT 外基点」这一双基点问题，也应被更高层抽象吸收。

## 决策（Decision）

我们将引入 **`StorageTier` 存储等级枚举**作为产物存储的唯一逻辑定位维度：

- `StorageTier` 定义于 domain 层（`src/domain/storage/storage_tier.py`，新增 `domain/storage` 子包），取值 `USER` / `PROJECT`，并预留 `TENANT`。它是纯 `StrEnum`，**不含任何物理路径、后端字符串**，也不 import 任何 `infrastructure`。
- 产物存储 Port（`TraceStorePort` / `ArtifactStorePort` 家族）的方法签名以 `StorageTier` 作为定位维度之一（带默认值以保证向后兼容，见 ADR-0003）。
- 物理路径映射下沉到 infrastructure 的 `LocalFileTierResolver`（`src/infrastructure/storage/local_file_tier_resolver.py`）：`PROJECT → <workspace_root>/.epsilon/`、`USER → ~/.epsilon/`（USER tier 运行产物按 `<project-hash>` 分区，即 `~/.epsilon/<project-hash>/…`，详见 ADR-0005/0006），并统一创建子目录 `sessions/`、`traces/`、`artifacts/`、`logs/`。仅此 resolver 知晓 `.epsilon`、`~`、`WORKSPACE_ROOT`。
- PROJECT tier 的 `traces/` 解析结果与既有 `TRACE_STORE_DIR=.epsilon/traces`（相对 CWD）在「CWD == WORKSPACE_ROOT」的本地默认场景下等价；resolver 以 `WORKSPACE_ROOT`（空则 CWD）为 PROJECT 基点，保证既有写入位置语义不变。
- 会话主状态归属 `USER` tier；当 `LOCAL_PERSISTENCE_ROOT` **未显式配置**时，本地文件 adapter 默认路径由 `../.local_persistence/epsilon-boot` 迁移到 `~/.epsilon/persistence/<project-hash>/`；显式配置与 `SESSION_STORE_BACKEND=redis` 尊重原值不迁移（安全边界见 ADR-0006）。

## 后果（Consequences）

- **正面**：domain 与写入方只依赖 `StorageTier`，未来接入对象存储 / 分布式 FS adapter 无需改写入方与 schema；产物定位可预测、集中于 resolver。双基点纠结被 tier 抽象吸收为 adapter 内部细节。
- **负面 / 代价**：新增 `domain/storage` 子包与一层 resolver；`LOCAL_PERSISTENCE_ROOT` 默认路径迁移需要迁移说明与回归测试，避免既有本地用户「找不到旧数据」。
- **后续影响**：`TraceStorePort` 签名变更波及既有实现与调用点（见 ADR-0003）；DI 容器需装配 resolver 与 artifact store；`docs/architecture.md` / `docs/configuration.md` 须同步。

## 备选方案（Alternatives）

- **方案 A：保留物理路径入参（各 Port 传 `store_dir`）** —— 未采纳：写入方与 domain 直接感知文件系统语义，云端后端无法在同一抽象下互换，违背特性目标。
- **方案 B：`StorageTier` 放 `common/`** —— 未采纳：tier 是产物存储的业务定位维度，属领域概念；`common/` 不应承载业务语义（steering ddd-architecture「common 不承载具体业务编排」）。
- **方案 C：把物理映射也放 domain（domain 直接算路径）** —— 未采纳：domain 禁止依赖 infrastructure / 文件系统，映射必须落在 infrastructure。
