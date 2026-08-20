# Spec 交付总结：Local Trace & Artifacts / 存储等级抽象

## Feature

`local-trace-artifacts`（对应 `TODO.md` P0.2「统一运行产物与日志」）：引入 `StorageTier` 存储等级抽象作为产物存储的唯一逻辑定位维度，令 trace/artifact 存储只依赖「等级」而非物理路径/后端；补齐 artifact 一等抽象、TUI/CLI 本地文件日志、`config.local.properties` 本地覆盖配置与 `Schema_Version` 元数据，为云端后端（对象存储/分布式 FS）预留同构 schema。

## 交付物清单

### Spec 文档
- `requirement.md`：8 需求 / 40 条 EARS 验收标准。
- `design.md`：10 组件 / 10 正确性属性 / 决策 1a·2b·3a / 回链 5 份 ADR。
- `tasks.md`：19 任务 / 5 检查点，需求覆盖 100%，全部勾选。
- `review-log.md`：各切片自查记录（见下「评审说明」）。

### ADR（`docs/adr/`）
- `0002-storage-tier-abstraction.md`：StorageTier 抽象与本地文件 tier→目录映射（含 `LOCAL_PERSISTENCE_ROOT` USER tier 迁移）。
- `0003-artifact-first-class-abstraction.md`：`ArtifactTrace`/`ArtifactStorePort` 与 `TraceStorePort` tier 兼容策略。
- `0004-config-local-properties-precedence.md`：配置源优先级 env > local > properties > .env。
- `0005-tui-cli-file-logging-default.md`：文件日志默认开启、落 USER tier（决策 2b）。
- `0006-tenant-visibility-and-user-tier-persistence-boundary.md`：多租户可见性与 USER tier 默认路径安全边界。

### 后端代码（`epsilon-boot/`）

新增：
| 路径 | 用途 |
|---|---|
| `src/domain/storage/storage_tier.py` | `StorageTier(StrEnum)`：USER/PROJECT/预留 TENANT |
| `src/infrastructure/storage/local_file_tier_resolver.py` | tier→目录映射；全仓库唯一 `project_hash()` 生成点 |
| `src/infrastructure/storage/local_file_log_sink.py` | `SensitiveRedactionFilter` + `configure_local_file_logging`（落 USER tier） |
| `src/infrastructure/storage/log_sink_config.py` | `LogSinkConfig`（`EPSILON_LOG_` 前缀） |
| `src/infrastructure/storage/schema_meta.py` | `write_schema_meta`（`.epsilon/meta.json`，幂等） |
| `src/infrastructure/artifact/local_file_artifact_store_adapter.py` | `ArtifactStorePort` 本地 JSONL 实现 |
| `src/infrastructure/artifact/artifact_config.py` | `ArtifactConfig`（`ARTIFACT_` 前缀） |

改动：
| 路径 | 改动 |
|---|---|
| `src/domain/agent/trace_value_objects.py` | 追加 `ArtifactTrace` 值对象 + 截断常量 |
| `src/domain/agent/ports.py` | `TraceStorePort` 三方法加 keyword-only `tier`；新增 `ArtifactStorePort` |
| `src/infrastructure/trace/local_file_trace_store_adapter.py` | 构造改注入 `LocalFileTierResolver`；方法接受 `tier`（序列化逻辑不变） |
| `src/common/configuration/configuration_utils.py` | 新增 `config.local.properties` 源，优先级 env > local > properties > .env |
| `src/common/configuration/config_proxy.py` | 热更新 source_files 追加 local 文件 |
| `src/application/container_config.py` | `_create_tier_resolver`/`_create_artifact_store`；改 `_create_trace_store`；会话主状态默认迁移 + 一次性提示；PROJECT tier 写 schema meta |
| `src/infrastructure/persistence/local_file/config/local_persistence_config.py` | `root` 默认改空串（启用 USER tier 迁移） |
| `src/application/cli/main.py` | TUI/exec 入口装配文件日志（serve 不装配） |
| `config.properties` | 新增 `ARTIFACT_*`/`EPSILON_LOG_*`；`LOCAL_PERSISTENCE_ROOT` 注释留空（决策 1a） |
| 文档 | `docs/configuration.md`、`docs/architecture.md`、`docs/tools.md`、`TODO.md` 同步 |

测试：新增 14 个测试文件 + 修正 4 个既有测试。

## 关键设计决策

1. **StorageTier 抽象**：domain 只认 USER/PROJECT/TENANT，物理路径/后端下沉 infrastructure。基点之争（CWD vs WORKSPACE_ROOT）被 tier 抽象吸收为 adapter 内部细节。
2. **复用不重造**：`structured-agent-trace` 的 trace 存储层全部复用，仅纳入 tier 抽象。
3. **兼容策略**：Port 新参数 keyword-only + 默认 `PROJECT`，既有调用点零改动。
4. **决策 1a**：`LOCAL_PERSISTENCE_ROOT` 留空 → USER tier 默认 `~/.epsilon/persistence/<project-hash>/`；显式配置/redis 尊重原值不迁移；旧数据不自动搬运，首次启动 INFO 提示。
5. **决策 2b**：文件日志落 USER tier `~/.epsilon/<project-hash>/logs/`，不污染项目工作区；与会话主状态共享 `project_hash()` 分区键。
6. **决策 3a**：PROJECT tier 基点用 `WORKSPACE_ROOT`（空则 CWD），本地默认场景与既有 `.epsilon/traces` 等价。
7. **云端同构**：schema + tier 抽象与后端解耦，换 Redis/DB/OSS 只动 infrastructure。

## 测试结果

- 全量回归：**2787 passed / 0 failed / 3 skipped**（基线 655 passed；本特性净增测试且零回归）。
- `ruff check src`：All checks passed。
- 本特性改动/新增文件 `pyright`：0 errors。
- 波次并行 + 5 检查点（C1~C5）逐段门控回归，全部通过。
- 附带修复 3 个既有失败测试（`test_create_agent_*` 的 mock resolve_map 缺 `TraceStorePort`）。

## 范围与后续（Follow-ups）

本 spec 交付**抽象 + 本地文件后端**，以下明确为后续 spec：
- `.epsilon/sessions/` 会话摘要 / `.epsilon/artifacts/` 产物的**写入方接入**（`TODO.md` 中标 `[~]` 部分完成：抽象/Port/adapter/DI 已就绪，写入方待接）。
- P0.3 `ErrorTrace` 异常路径补记（Out of Scope）。
- P0.4 `/status`·`/diff`·`/files`·`epsilon exec --json`、P0.5 前端 trace/artifact 浏览与 `/api/task/execute` 返回 artifact 引用 —— 消费本 spec 的 `ArtifactStorePort` 与目录规范。
- 云端 `ObjectStorageArtifactStoreAdapter`、分布式 FS adapter、`TenantVisibilityPolicy`（SSO/多租户可见性）—— 仅术语与 ADR 方向记录，未实现。

## 评审说明

各实现切片由 `spec-generator` 完成并本地严格自查（每切片 ruff+pyright+pytest 全绿）；子代理执行上下文未启用 `spec-evaluator`（Agent 工具不可用），改由 orchestrator 在 5 个检查点亲自跑全量回归作为评审门。`review-log.md` 已记录，如需正式 evaluator 评审可在工具可用时补跑。

## 交付状态

✅ 19 个任务 + 5 检查点全部完成，终检（C5）三项全绿。
