# 运行时后端指南（Runtime Backends）

> **本期起默认会话后端已从 `redis` 切换为 `file`。** 零配置 `uv run` 启动即可工作，不再要求本地部署 Redis 或 MySQL。
>
> **本期同时移除了领域事件基础设施**（`EventBusPort` / `EventStorePort` / `DomainEvent`）。既有 MySQL `event_records` / `event_handler_results` 表不再被本服务读写，建议运维在升级时手动 `DROP`（见"升级指南"章节）。

本文档说明 `epsilon-boot` 服务在不同后端组合下的运行时行为、切换方式、健康检查差异与运维注意事项。

---

## 一、后端组合总览

会话存储（`SessionContextStorePort`）在本期保留 **二选一** 后端：

| 后端标识 | `SESSION_STORE_BACKEND` 取值 | 默认 | 外部依赖 | 典型部署形态 |
| --- | --- | --- | --- | --- |
| 本地文件 | `file` | **是** | 无 | 单主机 / 本地开发 / 单 Pod |
| Redis | `redis` | 否 | 需部署 Redis | 集群 / 生产 |

两种后端 **互斥**：不支持双写、不支持数据同步、不支持从 Redis 迁移会话到本地文件（反之亦然）。切换后端等同于"换一套会话仓库"，历史会话仅保留在旧后端中。

> 领域事件链路本期不再提供：`EVENT_STORE_BACKEND` 键**不存在**；应用运行过程中也不再装配 `EventBusPort` / `EventStorePort`。若未来业务确有真实消费者需求，按新 feature 单独引入。

---

## 二、配置键参考

所有新增键均可通过环境变量、`config.properties` 或 `.env` 注入。当前项目配置源优先级为：构造参数 > 环境变量 > `config.properties` > `.env` > secrets 文件源 > 字段默认值；因此 `.env` 仅适合作为本地兜底配置，不能覆盖 `config.properties`。

### 2.1 后端选择

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `SESSION_STORE_BACKEND` | `file` | 取值 `file` 或 `redis`。设为 `file` 时整个服务零外部依赖启动；设为 `redis` 时退回到 `RedisSessionContextAdapter`，TTL 行为由该 Adapter 内聚维持。 |

### 2.2 本地文件持久化

以下键**仅在** `SESSION_STORE_BACKEND=file` 时生效；切回 `redis` 时这些键被忽略但不会触发校验失败（保留键不会引起启动期异常）。

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `LOCAL_PERSISTENCE_ROOT` | `../.local_persistence/epsilon-boot` | 本地持久化根目录。相对路径在进程启动时规范化为绝对路径（以进程 cwd 为基准）。默认值会落在 cwd 上级目录，避免与默认 `WORKSPACE_ROOT=cwd` 相互包含。日志会打印最终绝对路径便于排查。**不得**与有效 `WORKSPACE_ROOT` 共用或相互包含，否则启动期 fail-fast。 |
| `LOCAL_PERSISTENCE_CREATE_IF_MISSING` | `true` | 目录不存在时是否自动创建（含父级）。`false` 时不存在路径会导致启动失败。生产若希望由运维预置目录并拒绝"服务误创建"，可显式设为 `false`。 |
| `LOCAL_PERSISTENCE_FSYNC_ON_WRITE` | `true` | 每次写入后是否 `os.fsync`。关闭可显著提高吞吐，但**放弃断电一致性保证**；仅限开发/测试场景使用。 |
| `LOCAL_PERSISTENCE_LOCK_ACQUIRE_TIMEOUT_MS` | `5000` | 跨进程文件锁获取超时（毫秒）。超过此时间抛中文异常 `"获取本地持久化锁超时"`，不会无限阻塞。 |
| `LOCAL_PERSISTENCE_TMP_SWEEP_MAX_AGE_SECONDS` | `3600` | 启动期 `TmpFileSweeper` 清理 `*.tmp-<pid>-<uuid>` **半写残留**的 mtime 阈值秒数。**仅作用于 `.tmp-*` 前缀的临时文件**；不影响 `.json` 会话文件的生命周期（本期会话无 TTL，见下章）。 |

> **本期明确不提供**：`LOCAL_PERSISTENCE_SESSION_TTL_SECONDS`、`LOCAL_PERSISTENCE_REAPER_INTERVAL_SECONDS`。
> 若环境变量意外注入这两个键，`LocalPersistenceConfig` 的 Pydantic 严格模式会以 `ValidationError` 拒绝启动，避免"老配置悄悄失效"的静默降级。

---

## 三、`file` 后端的无 TTL 语义

这是本期相对既有 Redis 后端**最关键的语义差异**，运维与业务方必须理解：

1. **会话文件仅在调用方显式 `SessionContextStorePort.delete(session_id)` 时被删除。** 服务进程不运行任何定时回收任务、不基于 `mtime` 做惰性过期判断、不对 `.json` 做任何基于时间的 unlink。
2. **`load(session_id)` 永远返回文件内容的反序列化结果**（若文件存在且 JSON 合法），不论该文件在磁盘上已存在多久。哪怕 `mtime` 被回拨到 30 天前，`load` 仍然会正常返回原 `ConversationContext`。
3. **`save(session_id, ctx)` 不会刷新任何"TTL 计数器"**；`mtime` 自然由 `os.replace` 更新，但该时间戳仅被 `TmpFileSweeper` 用于识别 `.tmp-*` 半写残留，与会话 JSON 无关。
4. **启动期会执行一次性的 `TmpFileSweeper.sweep_once()`**：
   - 扫描 `<root>/sessions/<bucket>/` 下**名称含 `.tmp-` 的条目**；
   - 对 `now - mtime > LOCAL_PERSISTENCE_TMP_SWEEP_MAX_AGE_SECONDS` 的执行 `unlink`；
   - **不触碰任何 `.json` 或 `.lock` 文件**；
   - 扫描结束后以 `logger.info("TmpFileSweeper 扫描完成 scanned=%d deleted=%d errored=%d", ...)` 输出摘要；
   - 本组件**不会**在后台循环运行，更不会作为 asyncio 任务存活。

### 与 Redis 后端 TTL 行为的对比

| 行为 | `file` 后端（本期默认） | `redis` 后端（显式启用） |
| --- | --- | --- |
| 会话自动过期 | **无** | 有（由 `RedisSessionContextAdapter` 内聚的 TTL 维持） |
| `load` 行为 | 文件存在即返回；不读 `mtime` | Redis 键过期后返回空 `ConversationContext` |
| `save` 行为 | 原子写 `.tmp → fsync → os.replace` | `SETEX` 带 TTL 刷新 |
| `delete` 行为 | `unlink(missing_ok=True)` 幂等 | `DEL` 幂等 |
| 清理策略 | 调用方显式 `delete` | Redis 到期自动丢弃 |

> **如果你的业务依赖"会话自动过期以防止无限增长"语义**，请务必显式设置 `SESSION_STORE_BACKEND=redis`，不要使用本期的 `file` 默认。

---

## 四、单主机约束与不支持场景

`file` 后端**只保证单主机单实例**部署形态下的正确性。以下场景**不支持**，文档 + `config.properties` 注释 + 启动日志三处均会显式警告：

### 4.1 不支持的存储介质

- **网络盘**：NFS / SMB / CIFS / OSS FUSE。`portalocker` 在这些文件系统上的锁语义未定义；可能出现锁失效、写错乱、数据不可见等问题。
- **分布式文件系统**：GlusterFS / Ceph FUSE / HDFS FUSE 等。同上。

### 4.2 不支持的部署形态

- **多容器通过 Docker volume 共享 `LOCAL_PERSISTENCE_ROOT`**：overlayfs / bind mount 下的跨容器 `flock` / `LockFileEx` 行为未验证。属于"未定义行为"，不在回归测试覆盖范围。
- **K8S 多 Pod 共享 PV/PVC**：同上。
- **跨主机分布式一致性**：本期完全不涉及共识算法 / 主从复制 / 多主机同步。

### 4.3 推荐的生产部署

- **集群 / 生产**：显式 `SESSION_STORE_BACKEND=redis`，退回到成熟的 Redis 会话链路。
- **单机 / 本地开发 / 单 Pod**：保持默认 `file` 后端即可，零外部依赖。

---

## 五、健康检查差异

`/readiness`（就绪探针）响应体中 `checks` 列表按"实际装配的资源"**动态组装**：**未装配的中间件完全不出现**（不显示"跳过/未启用"占位）。

| 场景 | 装配的异步资源 | `/readiness.checks` 名称集合 |
| --- | --- | --- |
| `SESSION_STORE_BACKEND=file`（默认） | `local_persistence` | `{local_persistence}` |
| `SESSION_STORE_BACKEND=redis`（显式） | `redis` | `{redis}` |

> 本期移除 MySQL 默认装配：若未来新增 MySQL 消费者才会恢复 `MysqlHealthCheckAdapter` 注册（类定义保留为死代码备用）。

### `local_persistence` 健康检查的判断链

1. `LOCAL_PERSISTENCE_ROOT` 路径是目录；
2. 进程对该目录同时拥有 `R_OK | W_OK`；
3. 能在该目录下成功创建并立即释放一个 `.health-*` 临时文件。

任一步骤失败返回 `status=DOWN` 并给出中文 `reason`；`OSError` 场景下仅 `logger.warning`，不向上抛。

---

## 六、跨平台支持

`file` 后端基于第三方依赖 `portalocker` 屏蔽平台差异：

| 平台 | 锁原语 | 特性 |
| --- | --- | --- |
| Linux | `fcntl.flock`（整文件级，fd 关闭即释放） | 进程崩溃内核自动释放；POSIX 语义 |
| Windows | `LockFileEx`（通过 `portalocker` 调用） | 整文件级；进程崩溃 OS 自动释放；支持共享/独占模式 |
| macOS / BSD | `fcntl.flock` 回退 | 仅作为开发自测支持；生产建议 Linux |

跨平台注意事项：

- **Windows 路径长度**：绝对路径长度超过 260 字符会在启动期 / 运行期被拒绝，错误消息提示 `"请启用 Windows 长路径或缩短 LOCAL_PERSISTENCE_ROOT"`。
- **Windows 保留文件名**：`session_id` 通过 `sha256` 哈希后作为文件名，天然不会碰到 `CON/PRN/AUX/NUL/COM1-9/LPT1-9` 等保留名。`LOCAL_PERSISTENCE_ROOT` 自身含保留名会被 `CrossPlatformPathPolicy.check_dirname` 拒绝。
- **`os.replace` 跨卷失败**：`TempFileAtomicWriter` 强制 tmp 与 target 在**同目录**写入，规避 Windows 跨卷 rename 失败。
- **CI 覆盖**：`.github/workflows/ci.yml` 同时在 `ubuntu-latest` 与 `windows-latest` 两个 runner 上运行 `uv run pytest -m "not benchmark"`，两者均必选。

---

## 七、数据位置与清理

### 7.1 目录布局

`SESSION_STORE_BACKEND=file` 运行时，`LOCAL_PERSISTENCE_ROOT` 下的结构如下：

```text
<LOCAL_PERSISTENCE_ROOT>/
└── sessions/
    ├── ab/                              # bucket = sha256(session_id) 前 2 位
    │   ├── cd1234...ef.json             # stem = sha256(session_id) 后 62 位
    │   ├── cd1234...ef.json.lock        # 每文件独立锁
    │   └── cd1234...ef.json.tmp-<pid>-<uuid>  # 写过程崩溃留下的半写残留（下次启动被 TmpFileSweeper 清理）
    └── ff/…
```

### 7.2 手工清理

- **单个会话**：找到 `sha256(session_id)` 前 2 位目录下对应 `.json` 文件直接删除。生产通常不建议手工 unlink，优先走 `SessionContextStorePort.delete(session_id)`。
- **全量清空**：服务停机后 `rm -rf <LOCAL_PERSISTENCE_ROOT>/sessions/`；下次启动服务会在空目录下继续工作。
- **`.gitignore`**：仓库根 `.gitignore` 已忽略 `epsilon-boot/.local_persistence/` 与 `.local_persistence/`，防止开发过程把会话数据意外提交。从 `epsilon-boot/` 启动时，默认 `../.local_persistence/epsilon-boot/` 会命中仓库根的 `.local_persistence/` 忽略规则。

---

## 八、升级指南

### 8.1 从既有 Redis 部署升级

既有生产部署如果希望**保持现状**（继续使用 Redis 会话链路），只需在 `config.properties` 显式添加一行：

```properties
SESSION_STORE_BACKEND=redis
```

服务启动后：

- 会话读写回退到 `RedisSessionContextAdapter`；
- `/readiness.checks` 仅包含 `redis`；
- `LOCAL_PERSISTENCE_*` 键被忽略（但不会引发校验失败）。

### 8.2 从 Redis 切换到 file

1. 停机；
2. `config.properties` 中移除 `SESSION_STORE_BACKEND` 或显式设为 `file`；
3. 确认 `LOCAL_PERSISTENCE_ROOT` 指向的目录可读写、不与 `WORKSPACE_ROOT` 冲突；
4. 启动服务，观察日志中 `_local_persistence_root` 打印的绝对路径；
5. 既有 Redis 中的会话**不会**迁移到本地文件；新会话从空状态开始。

### 8.3 领域事件表的清理（运维手动执行）

本期已移除 `EventBusPort` / `EventStorePort` / `DomainEvent` 相关的全部基础设施与 ORM 模型。既有 MySQL 中 `event_records` 与 `event_handler_results` 表**不再被本服务读写**。

由于本仓库当前不使用 Alembic，迁移脚本以**文档化 SQL 片段**的形式交付，运维在升级后自行执行：

```sql
-- 前置：若业务侧认为该表审计数据有保留价值，请先 mysqldump 落地备份
-- mysqldump <db> event_records event_handler_results > event_tables_backup.sql

DROP TABLE IF EXISTS event_records;
DROP TABLE IF EXISTS event_handler_results;
```

> **回退预案**：若升级后发现某业务链路仍依赖领域事件基础设施，不要自行恢复表结构，请提交新 feature 规划；上一版代码已打 tag，可回滚 commit。

### 8.4 向后兼容保证的边界

- "**显式选择 `redis` 后的会话读写行为**" 与本期上线前基本一致；
- "**事件总线的存续**" **不**在向后兼容范围内 —— 本期已移除，不会提供"保留 event bus"的开关；
- `EventBusPort` / `EventStorePort` 及其依赖的任何 Python 符号 `import` 后会触发 `ModuleNotFoundError`；相关的测试门槛断言由 Phase 5 的 `test/integration/test_domain_event_decommission_gate.py` 引入。

---

## 九、故障排查（Troubleshooting）

### 9.1 启动失败：`LOCAL_PERSISTENCE_ROOT 为空`

`SESSION_STORE_BACKEND=file`（或走默认）且显式把 `LOCAL_PERSISTENCE_ROOT` 置为空字符串时触发。移除该键或设为非空字符串即可。

### 9.2 启动失败：`LOCAL_PERSISTENCE_ROOT 不得与 WORKSPACE_ROOT 共用或相互包含`

`LOCAL_PERSISTENCE_ROOT` 与有效 `WORKSPACE_ROOT` 共享目录或存在父子包含关系。`WORKSPACE_ROOT` 留空时有效值为进程 cwd；二者必须是**不相交的绝对路径**。

### 9.3 运行期：`获取本地持久化锁超时`

说明该 `session_id` 对应的锁被其他进程长时间独占。生产中通常是"前一轮请求写操作异常未释放锁"或"多容器误共享 volume"。

排查步骤：

1. 检查日志上下文，看前一次 `save` 是否成功或抛异常；
2. 若是单主机单进程场景，该错误通常指向代码 bug，请提 issue；
3. 若是多容器共享 volume 场景，请**停止共享**并改用 `redis` 后端。

### 9.4 运行期：`路径越出 LOCAL_PERSISTENCE_ROOT`

异常包装自 `PathPolicyViolation`，触发说明 `session_id` 在某处被误拼接进路径（正常路径全部走 `sha256` 哈希，不会出现此错误）。请提 issue。

---

## 十、相关链接

- 配置源约定：`docs/steering/config-source.md`
- DDD 分层约定：`docs/steering/ddd-architecture.md`
- uv 包管理约定：`docs/steering/uv-package-manager.md`
- 需求与设计：`docs/spec/local-file-persistence/`
- CI 工作流：`.github/workflows/ci.yml`（`ubuntu-latest` + `windows-latest` 双 runner 矩阵）
