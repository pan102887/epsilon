# 需求文档：统一运行产物与存储等级抽象（Local Trace & Artifacts / Storage Tier）

## 简介

### 背景与动机

本仓库同时承担两条主线：本地 coding-agent runtime（`epsilon` TUI / `epsilon exec`）与面向云端的 Agent 工作台基座。两条主线共享同一套领域模型、Port/Adapter 与 DI 容器（见 `/workspace/TODO.md` P0）。当前运行产物存在“散落、入口各异、无统一约定”的问题：

- 结构化 trace 已落地（spec `structured-agent-trace`），默认写入 `.epsilon/traces/`（`TRACE_STORE_DIR`，相对进程 CWD）。
- 会话 / Run / checkpoint 持久化走另一条路径 `LOCAL_PERSISTENCE_ROOT`（默认 `../.local_persistence/epsilon-boot`，故意落在 CWD 之外，避免与 `WORKSPACE_ROOT=cwd` 相互包含）。
- TUI/CLI 默认没有文件日志（`/workspace/TODO.md` P0.2 明确）。
- 任务产物 / 命令输出摘要 / 生成文件清单没有一等持久化抽象（`ArtifactTrace` 在 P0.3 中明确“仍缺”）。
- 本地覆盖配置只能用 `.env`，尚无 P0.2 要求的 `.epsilon/config.local.properties`。

本特性（对应 `/workspace/TODO.md` P0.2）的目标：把运行产物抽象为**存储等级（Storage Tier）**——领域层只认「等级」（用户级 / 项目级 / 租户级），不认物理路径与后端；具体「存哪、怎么存」只由对应 adapter 关心。在此抽象下：本地部署用本地文件系统 adapter，把不同等级映射到不同目录（项目级 → `<workspace>/.epsilon/`、用户级 → `~/.epsilon/`）；云端部署用对象存储（OSS/S3/R2）或分布式文件系统 adapter，多租户可见性由 SSO/权限校验（而非文件系统路径）保证。本特性据此：补齐 artifact 持久化抽象；引入本地覆盖配置文件并明确其优先级；并交付**同构 trace/artifact schema + StorageTier 抽象**，使未来可在同一 tier 语义下切换本地文件 / 对象存储 / 分布式 FS 后端，而不改动写入方、domain 与 schema。

> 设计取向说明：`.epsilon/` 不再是需求层面的“硬约定目录”，而降级为**本地文件 adapter 对各 tier 的实现约定**。domain 与 Port 只依赖 `StorageTier` 枚举，物理路径（`.epsilon`、`~`、`WORKSPACE_ROOT`）被存储等级抽象吸收，仅本地文件 adapter 知道。

### 业界主流实现方案调研（选型依据）

| 方案 | 目录/产物约定 | 对本特性的启示 |
| --- | --- | --- |
| Claude Code | 项目级 `.claude/`（settings/skills/agents）；会话历史 JSONL 存于用户级 `~/.claude/projects/<hash>/*.jsonl`；配置分层 enterprise > project local(`settings.local.json`) > project > user | 单一项目级隐藏目录 + JSONL 会话历史 + 分层本地覆盖配置的做法可直接借鉴 |
| Aider | 项目级散落文件 `.aider.chat.history.md`、`.aider.input.history`、`.aider.tags.cache.v*/`、`.aider.conf.yml`（分层 home > repo-root > cwd）；建议加入 `.gitignore` | 反面教材：产物散落根目录不易管理，本特性改用单一顶层目录聚合；产物默认应入 `.gitignore` |
| Cursor / Continue.dev | `.cursor/`、`.continue/` 目录存放本地配置与日志；session/dev data 以 JSON/JSONL 持久化 | 印证“单一隐藏目录 + 子目录分类”是行业主流 |
| OpenTelemetry / 结构化日志范式 | trace 以 JSONL 结构化事件表达（每行一事件、append-only、易增量读取与截断），敏感字段脱敏、大字段截断 | 本项目现有 trace adapter 已遵循；artifact/logs 沿用同一范式 |
| git 风格隐藏目录 | 单一 `.epsilon/` 顶层隐藏目录 + 子目录 + 内部元数据（schema version）以支持迁移 | 采用 `.epsilon/` 作为唯一顶层目录，内置 schema 版本元数据支持未来迁移 |

综合结论：采用**存储等级（StorageTier）抽象**统一产物定位——domain/Port 只认 USER/PROJECT/TENANT 等级；本地部署由本地文件 adapter 把 PROJECT 映射到 `<workspace>/.epsilon/`、USER 映射到 `~/.epsilon/`，并沿用**子目录分类（sessions/traces/artifacts/logs）+ JSONL 结构化产物 + 分层本地覆盖配置 + `.gitignore` 默认忽略运行产物**；云端由对象存储 / 分布式 FS adapter 在同一 tier 语义下互换，多租户可见性由 SSO/权限校验保证。此方案既落地本地闭环，又为云端后端切换预留同构 schema 与 tier 抽象。

### In Scope（本特性范围内）

1. 定义 **`StorageTier` 存储等级抽象**（domain 层枚举/值对象，至少 `USER` / `PROJECT`，预留 `TENANT`），作为产物存储的定位维度。
2. 令产物存储 Port（`TraceStorePort` / `ArtifactStorePort` 家族）以 `StorageTier` 作为定位维度之一，而非硬编码目录。
3. 交付**本地文件 adapter 的 tier→目录映射**（`LocalFileTierResolver`）：PROJECT → `<workspace>/.epsilon/`、USER → `~/.epsilon/`，并沿用子目录分类（sessions/traces/artifacts/logs）。
4. 将既有 trace 存储（`LocalFileTraceStoreAdapter` / `TRACE_STORE_DIR`）纳入 tier 抽象，**复用现状，不重造 trace store**；PROJECT tier 解析结果与既有 `.epsilon/traces` 等价、行为不变。
5. 定义 artifact 一等抽象：领域 `ArtifactTrace` 值对象 + `ArtifactStorePort` + 本地 file backend 默认实现（含 USER/PROJECT 两个 tier 映射）。
6. 引入 TUI/CLI 本地文件日志到 `Logs_Dir`（本地文件 adapter 在对应 tier 下的日志子目录）。
7. 引入 `config.local.properties` 本地覆盖配置，并明确其在配置源优先级链中的位置（低于环境变量、高于 `config.properties`）。
8. 交付**同构 trace/artifact schema + StorageTier 抽象与后端解耦**（含 schema version 元数据），使未来可在同一 tier 语义下切换本地文件 / 对象存储 / 分布式 FS 后端。
9. `.gitignore` 建议：本地文件 adapter 落地的 `.epsilon/` 运行产物默认不入库。

### Out of Scope（本特性明确不做）

1. **不实现云端后端**：`ObjectStorageArtifactStoreAdapter`（OSS/S3/R2）、分布式 FS adapter、Redis/DB 的 trace/artifact adapter 均不实现——本特性只交付 tier 抽象 + Port 签名 + 本地文件 adapter（USER/PROJECT 两 tier），并证明抽象可被云端复用。
2. **不实现多租户可见性/隔离策略**：`TenantVisibilityPolicy`（SSO/权限校验控制不同用户对产物可见性）仅作术语与 ADR 方向记录，不实现；`TENANT` tier 仅预留枚举取值。
3. **不重造 trace store**：`structured-agent-trace` 已交付的值对象、`TraceStorePort`、`LocalFileTraceStoreAdapter`、trace 查询 API 均复用。
4. 不新增第二套会话主状态存储层：`Sessions_Dir` 摘要/恢复索引应与既有 `LocalFileRunStoreAdapter` / checkpoint store 对齐，不重复承载会话主状态。
5. 不实现 `/status`、`/diff`、`/files` 等 coding workflow 命令（属 P0.4，spec `coding-workflow-commands`）。
6. 不改造前端控制台的 trace/artifact 浏览（属 P1.4）。
7. 不在 Agent Loop 异常路径补记 `ErrorTrace`（属 P0.3 遗留项，非本特性目标）。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 存储等级 | `StorageTier` | 领域枚举/值对象，作为产物存储的**逻辑定位维度**，至少含 `USER`（用户级，跨项目、单用户）、`PROJECT`（项目级，随工作区/仓库），预留 `TENANT`（租户级，云端多租户）。domain 只依赖此枚举，**不含任何物理路径或后端字符串**。 |
| 本地文件等级解析器 | `LocalFileTierResolver` | 本地文件 adapter 的**内部实现细节**，把 `StorageTier` 映射到具体目录：`PROJECT` → `<workspace>/.epsilon/`、`USER` → `~/.epsilon/`。属纯 infrastructure，不进入 domain。 |
| 本地运行目录 | `Epsilon_Home` | 本地文件 adapter 在某个 tier 下的顶层隐藏目录（PROJECT 为 `<workspace>/.epsilon/`，USER 为 `~/.epsilon/`），聚合该 tier 的本地运行产物与本地配置。仅本地文件 adapter 关心此概念。 |
| 会话产物子目录 | `Sessions_Dir` | 本地文件 adapter 在 `Epsilon_Home` 下的 `sessions/` 子目录，保存 TUI 会话摘要与恢复索引；不承载会话主状态。 |
| 追踪产物子目录 | `Traces_Dir` | 本地文件 adapter 在 `Epsilon_Home` 下的 `traces/` 子目录，保存每轮模型调用、tool call、approval decision、usage、latency；由既有 `LocalFileTraceStoreAdapter` 写入。 |
| 任务产物子目录 | `Artifacts_Dir` | 本地文件 adapter 在 `Epsilon_Home` 下的 `artifacts/` 子目录，保存任务产物、命令输出摘要、生成文件清单。 |
| 日志子目录 | `Logs_Dir` | 本地文件 adapter 在 `Epsilon_Home` 下的 `logs/` 子目录，保存 TUI/CLI 本地文件日志。 |
| 对象存储产物 adapter | `ObjectStorageArtifactStoreAdapter` | 云端 `ArtifactStorePort` 实现的**预留**（OSS/S3/R2），在同一 tier 语义下与本地文件 adapter 互换；本特性仅记录术语，不实现。 |
| 租户可见性策略 | `TenantVisibilityPolicy` | 云端多租户场景下通过 SSO/权限校验控制不同用户对产物可见性的策略抽象；由对应 adapter 层保证，**不靠文件系统路径隔离**。本特性仅记录术语，不实现。 |
| 追踪存储端口 | `TraceStorePort` | 已存在的领域 Port（`src/domain/agent/ports.py`），定义 `append_step`/`get_session_trace`/`list_traces`；本特性令其方法签名接受 `StorageTier` 作为定位维度之一。 |
| 追踪值对象 | `SessionTrace` | 已存在的会话级追踪聚合值对象；其 `steps` 为 `AgentStepTrace` 联合类型。 |
| 追踪步骤值对象 | `AgentStepTrace` | 已存在的联合类型 `ModelCallTrace \| ToolCallTrace \| ApprovalTrace \| ErrorTrace`，通过 `kind` 判别字段序列化。 |
| 产物追踪值对象 | `ArtifactTrace` | 新增的 frozen dataclass 值对象，携带 `kind: Literal["artifact"]` 判别字段，记录任务产物元数据（如逻辑路径、类型、大小/摘要、来源工具、时间戳）；不记录完整敏感内容。 |
| 产物存储端口 | `ArtifactStorePort` | 新增的领域 Port（Python Protocol），定义 artifact 的持久化与查询能力，方法签名接受 `StorageTier` 作为定位维度之一，由 infrastructure 层提供本地 file backend 与（未来）对象存储实现。 |
| 本地产物 file 后端 | `Local_File_Artifact_Store_Adapter` | `ArtifactStorePort` 的本地 JSONL/文件系统实现，写入 `Artifacts_Dir`，遵循故障隔离与大字段截断。 |
| 本地文件日志适配器 | `Local_File_Log_Sink` | 将 TUI/CLI 日志写入 `Logs_Dir` 的本地文件日志处理器/配置装配。 |
| 本地覆盖配置 | `config.local.properties` | `.epsilon/config.local.properties`，Java Properties 格式的本地覆盖配置文件；优先级低于环境变量、高于 `config.properties`。 |
| 配置基类 | `PropertiesBaseSettings` | 既有配置基类（`src/common/configuration/configuration_utils.py`），通过 `settings_customise_sources` 定义配置源优先级链。 |
| Properties 配置源 | `PropertiesFileSettingsSource` | 既有自定义配置源，从 `config.properties` 加载并按 `env_prefix` 匹配字段。 |
| 同构产物 schema | `Isomorphic_Trace_Artifact_Schema` | trace/artifact 的序列化结构定义（含 schema version 元数据），与后端实现及 `StorageTier` 解耦，使同一 tier 语义下本地文件 / 对象存储 / 分布式 FS 后端共用同一 schema。 |
| Schema 版本元数据 | `Schema_Version` | 本地文件 adapter 写入 `Epsilon_Home` 的元数据（如 `.epsilon/meta.json` 中的版本字段），标识产物 schema 版本以支持未来迁移。 |
| 工作区边界 | `Workspace` | 既有工作区抽象，约束文件/执行类工具的影响面；仅 `LocalFileTierResolver` 依赖它把 `PROJECT` tier 映射到 `<workspace>/.epsilon/`，domain 与 Port 不感知。 |

## 需求

### 需求 1：存储等级（StorageTier）抽象与本地文件映射

**用户故事：** 作为平台开发者，我希望产物存储只依赖「存储等级」而非物理路径/后端，以便同一套 domain 与写入方在本地文件、对象存储、分布式 FS 之间无缝切换，各入口（TUI/CLI/HTTP）不再硬编码目录。

#### 验收标准

1. THE `StorageTier` SHALL 定义于 domain 层，为枚举/值对象，至少包含 `USER` 与 `PROJECT` 取值，并预留 `TENANT` 取值。
2. THE `StorageTier` 及依赖它的领域 Port SHALL NOT 出现任何物理路径或后端字符串（如 `.epsilon`、`~`、`WORKSPACE_ROOT`、`OSS`/`S3` 等），亦不导入任何 `src/infrastructure/*` 模块或 Web/持久化框架 API。
3. THE 产物存储 Port（`TraceStorePort` / `ArtifactStorePort` 家族）SHALL 以 `StorageTier` 作为定位维度之一（方法签名接受 tier），而非硬编码目录。
4. THE `LocalFileTierResolver` SHALL 位于 `src/infrastructure/`，负责把 `StorageTier` 映射到具体目录：`PROJECT` → `<workspace>/.epsilon/`、`USER` → `~/.epsilon/`；且仅此 adapter 内部知晓物理路径。
5. FOR ALL 子目录（`Sessions_Dir`、`Traces_Dir`、`Artifacts_Dir`、`Logs_Dir`），THE `LocalFileTierResolver` SHALL 提供一致的“不存在时创建”策略，且该策略对各子目录行为一致。
6. WHEN 本地文件 adapter 解析 `PROJECT` tier 的 `Traces_Dir` 时，THE 解析结果 SHALL 与既有 `TRACE_STORE_DIR`（默认 `.epsilon/traces`）等价，使既有 `LocalFileTraceStoreAdapter` 复用而不改变已落地的写入位置语义。

### 需求 2：产物子目录职责与会话主状态的 tier 归属

**用户故事：** 作为平台开发者，我希望各产物的存储等级归属与子目录职责清晰不重叠，以便运行产物可预测、不与既有持久化层重复，且会话主状态的 tier 语义正确。

#### 验收标准

1. THE `Sessions_Dir` SHALL 仅保存 TUI 会话摘要与恢复索引，且不重复承载 `LocalFileRunStoreAdapter` / checkpoint store 已持有的会话主状态。
2. THE 会话主状态（session context/index、run/checkpoint）SHALL 归属 `USER` tier（跨项目、单用户、强一致）；本地文件 adapter 默认可落到 `~/.epsilon/persistence/<project-hash>/`，取代现默认 `../.local_persistence/epsilon-boot`。
3. THE `Traces_Dir` SHALL 由既有 `LocalFileTraceStoreAdapter` 写入，保存每轮模型调用、tool call、approval decision、usage 与 latency（即 `AgentStepTrace` 序列化结果）。
4. THE `Artifacts_Dir` SHALL 保存任务产物、命令输出摘要与生成文件清单，且以 `ArtifactTrace` 定义的结构持久化。
5. THE `Logs_Dir` SHALL 保存 TUI/CLI 本地文件日志。
6. FOR ALL 上述子目录，THE 规范文档 SHALL 明确各产物的 tier 归属、写入方、读取方与保留/清理语义。

### 需求 2A：会话主状态 tier 迁移的安全边界

**用户故事：** 作为平台安全负责人，我希望会话主状态迁移到 USER tier 默认目录只是本地文件 adapter 的实现选择，且多实例/多租户生产不被文件系统路径“隔离”误导，以便不引入跨实例一致性与租户越权风险。

#### 验收标准

1. THE `~/.epsilon/persistence/<project-hash>/` 默认路径 SHALL 仅作为本地文件 adapter 对 `USER` tier 的默认实现，不得成为多实例/多租户生产的持久化后端。
2. WHEN 云端/多实例生产部署时，THE 会话主状态 SHALL 走 Redis（`SESSION_STORE_BACKEND=redis`）或对象存储 adapter，禁止把 `~` 本地路径用于多租户/多实例。
3. THE 本特性 SHALL 保留既有本地持久化安全禁令（`config.properties` 中 NFS/SMB/OSS FUSE、多容器共享 volume 的禁止）与既有启动校验（`_validate_local_persistence_root`）语义，不因默认路径迁移而弱化。
4. THE 多租户可见性 SHALL 由 `TenantVisibilityPolicy`（SSO/权限校验）在对应 adapter 层保证，SHALL NOT 依赖文件系统路径隔离。

### 需求 3：定义 artifact 一等抽象（值对象 + Port + 本地实现）

**用户故事：** 作为平台开发者，我希望有统一的 artifact 抽象记录任务产物，以便 coding-agent 与 Agent 工作台各入口共享同一产物持久化能力。

#### 验收标准

1. THE `ArtifactTrace` SHALL 为 frozen dataclass 值对象，携带 `kind: Literal["artifact"]` 判别字段，并记录产物逻辑路径、类型、大小或摘要、来源工具与时间戳等元数据。
2. THE `ArtifactTrace` SHALL NOT 记录完整敏感文件内容，大字段须按既有 trace 截断常量范式截断。
3. THE `ArtifactStorePort` SHALL 定义于 `src/domain/*/ports.py`，仅使用 Python Protocol，方法签名以 `StorageTier` 作为定位维度之一，且不依赖任何 infrastructure 实现或物理路径字符串。
4. THE `Local_File_Artifact_Store_Adapter` SHALL 位于 `src/infrastructure/`，实现 `ArtifactStorePort`，经 `LocalFileTierResolver` 将产物写入对应 tier 的 `Artifacts_Dir`，并至少支持 `USER` 与 `PROJECT` 两个 tier。
5. WHEN artifact 写入 IO 失败时，THE `Local_File_Artifact_Store_Adapter` SHALL 隔离故障（记录 warning 而不中断主流程），与既有 `LocalFileTraceStoreAdapter` 的故障隔离语义一致。
6. THE `ArtifactStorePort` SHALL 通过 DI 容器（`src/application/container_config.py`）装配，供各入口共享同一实例，且写入侧与读取侧共享同一实例。

### 需求 4：TUI/CLI 本地文件日志

**用户故事：** 作为使用 TUI/CLI 的用户，我希望运行日志被持久化到本地文件，以便排障与审计，而不是仅存在于终端。

#### 验收标准

1. THE `Local_File_Log_Sink` SHALL 将 TUI/CLI 日志写入 `Logs_Dir` 下的文件。
2. IF 本地文件日志未显式启用，THEN THE `Local_File_Log_Sink` SHALL 采用文档化的默认行为（默认开启或默认关闭须记为“需 ADR”的决策点并在需求文档中确定其一）。
3. THE `Local_File_Log_Sink` SHALL 通过 `LocalFileTierResolver` 解析对应 tier 的日志目录，而非硬编码路径。
4. THE `Local_File_Log_Sink` SHALL NOT 将凭证/密钥（如 API Key、authorization、cookie、token）明文写入日志文件，须复用既有敏感字段脱敏约定。

### 需求 5：`.epsilon/config.local.properties` 本地覆盖配置与优先级

**用户故事：** 作为开发者，我希望在 `.epsilon/config.local.properties` 中做本地覆盖配置，其优先级低于环境变量、高于 `config.properties`，以便本地调试而不污染主配置源、也不越过部署期环境变量。

#### 验收标准

1. THE `config.local.properties` SHALL 采用与 `config.properties` 相同的 Java Properties 格式与键名到字段的映射规则（`PropertiesFileSettingsSource` 的转换约定）。
2. THE `PropertiesBaseSettings` 配置源优先级链 SHALL 为（高→低）：构造参数 > 环境变量 > `config.local.properties` > `config.properties` > `.env` > secrets > 默认值。
3. WHEN 同一配置键同时存在于环境变量与 `config.local.properties` 时，THE 配置系统 SHALL 采用环境变量的值。
4. WHEN 同一配置键同时存在于 `config.local.properties` 与 `config.properties` 时，THE 配置系统 SHALL 采用 `config.local.properties` 的值。
5. IF `config.local.properties` 不存在，THEN THE 配置系统 SHALL 保持与当前完全一致的行为（缺失文件不报错、按现有链路解析）。
6. THE `config.local.properties` 引入 SHALL NOT 违反 steering `config-source.md`：`config.properties` 仍为“新增/修改配置项优先写入”的主配置源，`config.local.properties` 仅用于本地覆盖。
7. THE 配置优先级插入位置 SHALL 记为“需 ADR”的决策点（因其修订跨模块配置解析契约）。

### 需求 6：同构 schema + StorageTier 抽象与后端解耦

**用户故事：** 作为平台架构负责人，我希望 trace/artifact 的 schema 与 `StorageTier` 抽象同时与后端实现解耦，以便同一 tier 语义下本地文件 / 对象存储 / 分布式 FS 后端可互换，而写入方与 domain 不变。

#### 验收标准

1. THE `Isomorphic_Trace_Artifact_Schema` SHALL 由 domain 层值对象（`AgentStepTrace` 系列与 `ArtifactTrace`）定义，且不绑定任何具体后端（file/对象存储/分布式 FS/Redis/DB）实现细节。
2. THE `TraceStorePort` 与 `ArtifactStorePort`（以 `StorageTier` 为定位维度）SHALL 是写入方与读取方唯一依赖的抽象，使在同一 tier 语义下替换后端时写入方与 domain 代码无需改动。
3. THE `Epsilon_Home` SHALL 包含 `Schema_Version` 元数据（如 `.epsilon/meta.json`），标识当前产物 schema 版本以支持未来迁移。
4. WHEN 未来接入 `ObjectStorageArtifactStoreAdapter` 或分布式 FS adapter 时，THE `Isomorphic_Trace_Artifact_Schema` 与 `StorageTier` 抽象 SHALL 可被云端后端复用而无需重定义产物结构或 tier 语义（本特性仅需证明抽象满足此约束，不实现云端后端）。
5. THE artifact schema 与 `StorageTier` 抽象定义 SHALL 记为“需 ADR”的决策点（引入新的一等抽象属方向级决策）。

### 需求 7：`.gitignore` 建议与运行产物不入库

**用户故事：** 作为仓库维护者，我希望 `.epsilon/` 运行产物默认不进入版本库，以便避免噪声提交与潜在敏感信息泄露。

#### 验收标准

1. THE 目录规范 SHALL 建议将 `.epsilon/` 运行产物子目录（sessions/traces/artifacts/logs）纳入 `.gitignore`。
2. THE `config.local.properties` SHALL 默认被 `.gitignore` 忽略（本地覆盖配置不入库，避免本地调试值污染仓库）。
3. THE `.gitignore` 建议 SHALL 与既有 `.local_persistence/` 忽略规则保持风格一致，不破坏既有忽略约定。

### 需求 8：不破坏既有 spec 与运行时行为

**用户故事：** 作为平台开发者，我希望本特性以最小改动落地，不破坏 `structured-agent-trace`、会话恢复与既有配置行为。

#### 验收标准

1. THE 本特性 SHALL 复用既有 `TraceStorePort`、`SessionTrace`、`AgentStepTrace`、`LocalFileTraceStoreAdapter` 与 trace 查询 API，不重造 trace 存储层。
2. WHEN 未配置 `ArtifactStorePort`（如 `artifact_store=None`）时，THE 各入口 SHALL 静默跳过 artifact 记录且零运行时行为变化，与既有 trace 的可选注入语义一致。
3. THE 本特性 SHALL 遵循 DDD 依赖方向：domain 定义 Port/值对象、infrastructure 实现 Adapter、application 装配，禁止反向依赖。
4. THE 本特性引入的配置类 SHALL 继承 `PropertiesBaseSettings` 并遵循 pydantic-model / python-typing-lint steering（全量类型标注、禁裸 `Any`、中文 docstring）。
5. THE 既有配置项 `TRACE_ENABLED` 与 `TRACE_STORE_DIR` 的默认行为 SHALL 在本特性落地后保持不变；WHEN 未显式设置 `LOCAL_PERSISTENCE_ROOT` 时，THE 本地文件 adapter 默认路径 SHALL 迁移到 `USER` tier 的 `~/.epsilon/persistence/<project-hash>/`（见需求 2/2A），且此迁移仅改变默认实现、不弱化既有安全禁令与启动校验。
6. THE 显式设置 `LOCAL_PERSISTENCE_ROOT`、`SESSION_STORE_BACKEND=redis` 的既有部署 SHALL 在本特性落地后行为保持不变（尊重显式配置，不被 tier 默认迁移覆盖）。

## 需要 ADR 的决策点清单

以下决策属架构/方向级，依据 steering `adr.md` 须在 design 阶段落 ADR（`docs/adr/NNNN-*.md`），并在 `design.md` 回链：

1. **引入 StorageTier 存储等级抽象**（需求 1、2、2A）：等级枚举取值（`USER`/`PROJECT`/`TENANT`）、产物存储 Port 以 tier 为定位维度、本地文件 adapter 的 tier→目录映射约定（`LocalFileTierResolver`：PROJECT→`<workspace>/.epsilon/`、USER→`~/.epsilon/`），以及会话主状态 `LOCAL_PERSISTENCE_ROOT` 默认路径向 USER tier 的迁移——确立产物存储的方向级抽象。（取代原「双基点 CWD vs WORKSPACE_ROOT」纠结，基点差异被 tier 抽象吸收为 adapter 内部细节。）
2. **配置源优先级插入位置**（需求 5）：`config.local.properties` 位于环境变量与 `config.properties` 之间，修订 `PropertiesBaseSettings.settings_customise_sources` 的跨模块配置解析契约。
3. **artifact 一等抽象引入**（需求 3、6）：新增 `ArtifactTrace` 值对象与 `ArtifactStorePort`（以 tier 为定位维度），以及与后端解耦的同构 schema + `Schema_Version` 元数据——引入新的一等抽象与持久化选型。
4. **TUI/CLI 本地文件日志默认策略**（需求 4）：默认开启还是默认关闭，及日志文件轮转/保留与脱敏策略。
5. **多租户可见性/隔离机制**（需求 2A）：云端多租户产物可见性由 `TenantVisibilityPolicy`（SSO/权限校验）保证而非文件系统路径隔离——云端方向级决策，本 spec 仅记录不实现。

## 对既有 spec / 代码的影响与非目标

- **复用 `structured-agent-trace`**：`src/domain/agent/trace_value_objects.py`、`src/domain/agent/ports.py`（`TraceStorePort`）、`src/infrastructure/trace/local_file_trace_store_adapter.py`、`src/infrastructure/trace/trace_config.py` 与 trace 查询 API 均不重造；本特性令 `TraceStorePort` 方法签名接受 `StorageTier`，并让本地 adapter 经 `LocalFileTierResolver` 解析目录（PROJECT tier 结果与既有 `.epsilon/traces` 等价）。
- **与 P0.3 的边界**：`ArtifactTrace` 由本特性补齐（P0.3 曾标注“待 artifact 存储实现”）；`ErrorTrace` 在 Agent Loop 异常路径的补记属 P0.3 遗留项，非本特性目标。
- **与 P0.4 / P0.5 的边界**：`/status`、`/diff`、`/files`、`epsilon exec --json` 与前端 trace/artifact 浏览、`/api/task/execute` 返回 artifact 引用 ID 等，均由后续 spec 消费本特性交付的 `StorageTier` 抽象与 `ArtifactStorePort`，不在本特性实现。
- **会话恢复与持久化默认迁移**：`Sessions_Dir` 只存摘要与恢复索引；会话主状态归 `USER` tier，本地默认路径由 `../.local_persistence/...` 迁移至 `~/.epsilon/persistence/<project-hash>/`——涉及 `src/infrastructure/run/local_file_run_store_adapter.py`、checkpoint store 及 spec `tui-session-resume` / `long-task-continuation-*`，须保留既有安全禁令与 `_validate_local_persistence_root` 启动校验，不新增第二套会话主状态存储。
- **云端预留（不实现）**：`ObjectStorageArtifactStoreAdapter`、分布式 FS adapter、`TenantVisibilityPolicy`（SSO/多租户可见性）仅作术语与 ADR 方向记录。
- **文档同步（doc-sync）**：落地后须同步 `docs/configuration.md`（新增 `config.local.properties` 与优先级、`LOCAL_PERSISTENCE_ROOT` 默认迁移说明）、`docs/tools.md` / `docs/architecture.md`（`StorageTier` 抽象、artifact Port、本地文件 tier 映射）与相关索引。
