# 需求文档：会话持久化的本地文件实现 + 领域事件基础设施清理

## 简介

### 背景

当前 `epsilon-boot` 后端存在两个关键状态持久化路径，都强依赖**外部中间件**：

- **会话上下文**：`SessionContextStorePort`（`epsilon-boot/src/domain/chat/ports.py`）的生产实现是 `RedisSessionContextAdapter`（`epsilon-boot/src/infrastructure/session/redis_session_context_adapter.py`），将 `ConversationContext.to_dict()` 以 JSON 形式写入 Redis，带 TTL 自动过期；
- **事件存储**：`EventStorePort`（`epsilon-boot/src/common/event_bus/ports.py`）的生产实现是 `DatabaseEventStoreAdapter`（`epsilon-boot/src/infrastructure/event_bus/database_event_store_adapter.py`），通过 `SessionProviderPort` 把 `EventRecord` / `HandlerResultRecord` 写入 MySQL。

本期对这两条链路做**两类独立处理**（在同一 feature 中一并落地），根本动机都围绕"降低生产部署与本地开发的外部依赖"这一主题，但技术路径截然不同：

1. **会话上下文**：设计/落地基于本地文件系统的对等实现 `Local_File_Session_Context_Adapter`，作为 `RedisSessionContextAdapter` 的对等替代，并将**本地文件后端设为开箱即用的默认方案**。本期仅保证**单主机单实例**部署形态下的正确性（同一 OS 镜像下 `uv run` 多 worker 或辅助脚本的跨进程协同），**明确不支持多容器通过 volume 共享目录**（overlayfs / 网络盘 / bind mount 共享等场景属于未定义行为）。
2. **领域事件基础设施**：经过对当前代码的系统核查（见下文"领域事件必要性评估"），`EventStorePort` / `EventBusPort` / `DomainEvent` 在本项目中属于**零生产消费者的架构原型**：
   - 当前仓库中**不存在任何** `DomainEvent` 的生产子类（`common/events.py` 仅有基类）；
   - 当前仓库中**不存在任何**生产代码调用 `EventBusPort.publish` / `EventBusPort.subscribe`；
   - `EventStorePort.query` / `replay` 仅在测试代码中被调用；
   - `ChatServiceAdapter` / `TaskAgentAdapter` / HTTP API 层**均未注入** `EventBusPort` 或 `EventStorePort`。

  因此本期**不**为 `EventStorePort` 提供本地文件实现，而是**直接移除**事件总线与事件存储的全部基础设施代码与对应数据库建模，避免引入 `Local_File_Event_Store_Adapter` 这类"零消费者但需跨进程锁 + append-only 日志 + id 分配器 + rotation"的高复杂度实现。未来若业务出现真实的领域事件消费者（例如系统审计、跨限界上下文通知、CQRS 读模型），再按新需求单独引入。

### 动机

- **会话侧**：在不改动任何 `SessionContextStorePort` 调用方的前提下，让 `uv run` 零外部依赖启动即可工作；生产集群仍可显式切回 Redis；
- **领域事件侧**：消除"零消费者但承载了 ~3000 行基础设施 + 测试代码 + MySQL 表"的沉没复杂度，使健康检查、组合根、配置表面更贴近项目真实需要。

两项工作同属"本地部署可运行性收敛"这一主题，且"移除事件存储"是"不为事件存储做本地文件实现"这一决策的必然延续，因此在同一 feature 中一并落地。

### 范围

**纳入（In Scope）**：

1. 定义并落地 `Local_File_Session_Context_Adapter`，实现既有 `SessionContextStorePort` 的全部 `save` / `load` / `delete` 语义；**本期明确不对会话文件设置 TTL / 过期回收**——会话文件仅在调用方显式 `delete(session_id)` 时被删除，不存在基于时间的自动清理；Redis 会话的既有 TTL 行为在显式切回 `redis` 后端时保持不变（语义隔离在 `RedisSessionContextAdapter` 内部）；
2. 跨平台文件锁抽象 `Cross_Platform_File_Lock`：基于第三方 `portalocker` 依赖统一封装 Linux `fcntl.flock` 与 Windows `LockFileEx`，保证同一主机多进程并发 `save` 的数据一致性；
3. 崩溃一致性策略：会话上下文写入采用"**先写临时文件 + rename 原子替换**"（`Temp_File_Atomic_Rename`）；
4. 跨平台路径合法性校验 `Cross_Platform_Path_Policy`：禁止 Windows 保留文件名（CON/PRN/AUX/NUL/COM1-9/LPT1-9）、禁止 `:` / `*` / `?` / `<` / `>` / `"` / `|` 等 Windows 非法字符；
5. 后端选择配置模型 `Session_Store_Config`，支持 `redis` / `file` 两种取值，**默认 `file`**；
6. 数据根目录 `Local_Persistence_Root` 的声明、默认值与启动期校验（fail-fast），与 `docs/spec/workspace/requirement.md` 中 `Workspace_Root` 概念**独立**、**不共用目录**；
7. 性能底线与可量化的 p99 写入延迟基准；
8. 可观测性：沿用既有 `logger`/OpenTelemetry 约定，错误路径不得静默吞；
9. Linux / Windows 双平台 CI 矩阵（新增 `windows-latest` runner），并提供对应的集成测试与 property-based 测试覆盖；
10. **领域事件基础设施清理**：
    - 移除 `common/events.py`（基类 `DomainEvent`）；
    - 移除 `common/event_bus/`（`EventBusPort` / `EventStorePort` / `serializer.py` / 包 `__init__`）；
    - 移除 `infrastructure/event_bus/in_memory_event_bus_adapter.py` 与 `database_event_store_adapter.py`；
    - 移除 `infrastructure/database/models/event_record.py` 及其 MySQL 建表迁移（保留历史迁移文件，但新增一份"drop table"迁移以在已部署环境中回收表）；
    - 移除 `application/container_config.py` 中 `EventBusPort` / `EventStorePort` 的注册；
    - 移除 `test/` 下所有 `test_event_bus*`、`test_serializer*`、`test_event_store_integration*`、`test_database_event_store_adapter*` 等专属测试文件；
    - `config.properties` 模板中移除 `EVENT_STORE_BACKEND`（本期未引入）以及任何事件存储相关键（若存在）；
    - `docs/` 中涉及 "领域事件 / event bus / event store" 的章节一并删除或更新为 "当前版本未内置领域事件基础设施，如需引入请提交新 feature"。

**不在本期范围（Out of Scope）**：

- **跨主机分布式一致性**：本特性明确**不涉及**多主机同步、共识算法、主从复制；明确不保证在多主机共享网络盘（NFS / SMB / CIFS / OSS 挂载）上的并发正确性；
- **容器化场景下通过 Docker volume 共享 `LOCAL_PERSISTENCE_ROOT`**：本期**明确只针对单主机单实例本地部署**；如运维在多容器 / K8s 场景强行使用本地文件后端并共享 volume，overlayfs / bind mount / 网络卷上的 `portalocker` 锁语义未验证，属于未定义行为；文档 + `config.properties` 注释 + 启动日志三处均须显式警告；
- **水平扩展**：不考虑高 QPS 下的分片、归档、冷热分离；
- **与 Redis 的双写/同步**：不实现"先写文件再异步回刷 Redis"之类的双后端组合策略；两种后端是**二选一**，不共享数据；
- **历史会话数据迁移工具**：不提供 Redis → 文件的一次性迁移脚本；
- **加密 / 静态数据保护**：落盘数据不做加密；敏感信息保护依赖宿主操作系统权限与容器挂载策略；
- **前端改动**：`epsilon-client/` 零改动；
- **对 `common/tools/common_tools.py` 文件工具 I/O 的改造**：与 `docs/spec/workspace/` 中 `Local_Filesystem_Workspace` 解耦，本期的本地文件后端**不复用** `Workspace` 抽象；
- **为事件存储做任何本地文件实现**：基于领域事件必要性评估的结论，本期**不**落地 `Local_File_Event_Store_Adapter`；
- **事件数据向 NDJSON 归档导出**：既有 MySQL `event_records` 表在本期被直接 drop，不提供数据导出工具（理由：已确认生产无消费者，历史数据保留价值仅限审计追溯，如确有必要由运维手动 `mysqldump` 落地即可，不作为需求硬约束）。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 本地文件会话适配器 | `Local_File_Session_Context_Adapter` | 基于本地文件系统实现的 `SessionContextStorePort`，以单会话一个 JSON 文件的方式持久化 `Conversation_Context_Snapshot`。**本期不设置 TTL / 过期回收**：会话文件仅在调用方显式 `delete(session_id)` 时被删除。作为 `Redis_Session_Context_Adapter` 的对等替代；Redis 既有的 TTL 行为不跨越后端边界迁移到本地文件实现。 |
| 会话上下文快照 | `Conversation_Context_Snapshot` | `ConversationContext.to_dict()` 返回的 JSON 可序列化结构（既有语义），是 `Local_File_Session_Context_Adapter` 的写入单元。 |
| 本地持久化根 | `Local_Persistence_Root` | 本特性专用的本地数据根目录，由配置项 `LOCAL_PERSISTENCE_ROOT` 指定。进程生命周期内不可变。**不得**与 `Workspace_Root` 共用目录。 |
| 会话后端标识 | `Session_Store_Backend_Kind` | 配置字段 `SESSION_STORE_BACKEND` 的取值枚举，取值为 `redis` 或 `file`；默认为 `file`。 |
| 跨平台文件锁 | `Cross_Platform_File_Lock` | 屏蔽 Linux / Windows 差异的文件级互斥锁抽象：内部使用 `portalocker` 依赖统一 Linux `fcntl.flock` 与 Windows `LockFileEx`。支持独占锁（`EXCLUSIVE`）与共享锁（`SHARED`）两种模式、超时等待。 |
| 临时文件原子替换 | `Temp_File_Atomic_Rename` | 写入策略：先把新内容完整写入同目录下的临时文件，`fsync` 到磁盘，再使用 `os.replace` 原子替换目标文件。 |
| 会话文件命名 | `Session_File_Name_Scheme` | 单个会话文件命名规则：`<sha256(session_id)[0:2]>/<sha256(session_id)[2:64]>.json`；使用不可逆哈希避免 Windows 保留名 / 非法字符冲突。 |
| 跨平台路径策略 | `Cross_Platform_Path_Policy` | 纯函数式校验：拒绝 NUL、拒绝 Windows 保留字符与保留文件名、长度上限（Windows 默认 260）、禁止 `..` 逃逸。 |
| 临时文件清理扫描 | `Tmp_File_Sweeper` | 启动期一次性扫描，基于前缀与 mtime 阈值清理 `*.tmp-<pid>-<uuid>` 残留（写入过程崩溃留下的半写文件）；**不负责基于 TTL 删除会话 JSON 文件**——本期会话无 TTL。 |
| 启动失败 | `Startup_Failure` | `Local_Persistence_Root` 校验不通过、`portalocker` 不可用等可检测异常，应用以 fail-fast 方式拒绝启动。 |
| 写入 p99 延迟 | `Write_P99_Latency` | `Local_File_Session_Context_Adapter.save` 在单会话上下文 ≤ 10MB 规模下的 99 分位写入延迟。 |
| 领域事件清理 | `Domain_Event_Decommission` | 本期同步执行的移除动作：删除 `common/events.py`、`common/event_bus/`、`in_memory_event_bus_adapter`、`database_event_store_adapter`、`event_record` ORM 模型、相关测试与容器注册；并新增一份"drop 表"的数据库迁移。 |

## 需求

### 需求 1：`SessionContextStorePort` 的本地文件实现保持接口对等

**用户故事：** 作为后端开发者，我希望 `Local_File_Session_Context_Adapter` 在 DI 容器中能原地替换 `Redis_Session_Context_Adapter`，所有 `SessionContextStorePort` 的调用方（`TaskAgentAdapter` / `ChatServiceAdapter` 等）代码零改动。

#### 验收标准

1. THE `Local_File_Session_Context_Adapter` SHALL 实现 `domain.chat.ports.SessionContextStorePort` 协议的 `save(session_id, context)` / `load(session_id)` / `delete(session_id)` 三个方法，签名与异步语义与 `Redis_Session_Context_Adapter` 完全一致。
2. WHEN 调用方传入 `session_id` 与 `ConversationContext`, THE `Local_File_Session_Context_Adapter.save` SHALL 将 `context.to_dict()` 以 UTF-8 JSON（`ensure_ascii=False`）写入 `Local_Persistence_Root` 下按 `Session_File_Name_Scheme` 规则生成的文件，写入完成后**才**从调用方视角返回成功。
3. WHEN 调用方 `load(session_id)` 的目标文件不存在, THE `Local_File_Session_Context_Adapter` SHALL 返回一个空的 `ConversationContext`，与 `Redis_Session_Context_Adapter` 针对 "Redis 中键不存在" 的行为一致，不得抛出 `FileNotFoundError`；WHEN 目标文件存在且可解析, THE 适配器 SHALL 无条件返回其反序列化结果，**不得基于 mtime / 时间差做过期判断**——会话文件在本地后端下没有 TTL 概念。
4. WHEN `load` 的目标文件存在但 JSON 反序列化失败, THE `Local_File_Session_Context_Adapter` SHALL 记录 `logger.error` 并返回空的 `ConversationContext`。
5. WHEN `delete(session_id)` 目标文件不存在, THE `Local_File_Session_Context_Adapter` SHALL 静默返回成功（幂等），不得抛出异常。
6. THE `Local_File_Session_Context_Adapter` SHALL 通过构造参数接收 `Local_Persistence_Root`、`Cross_Platform_File_Lock` 工厂、`Cross_Platform_Path_Policy` 实例、`Temp_File_Atomic_Rename` 实例（`Temp_File_Atomic_Writer`），**不接收 `ttl_seconds`、`Ttl_Reaper` 等 TTL 相关参数**（本期无 TTL）；不得通过全局 import 隐式依赖任何配置或文件系统客户端。
7. FOR ALL 对外错误消息, THE `Local_File_Session_Context_Adapter` SHALL 使用中文可读文案，且不得在日志或异常文本中拼接敏感信息（例如 token、API Key）。

### 需求 2：多进程并发安全与跨平台文件锁差异

**用户故事：** 作为单机部署的运维者，我希望同一主机上多个业务进程同时调用 `save` 时数据不丢失、不错乱，且不必关心 Linux 与 Windows 的 API 差异。

#### 验收标准

1. THE `Cross_Platform_File_Lock` SHALL 以第三方依赖 `portalocker` 作为实现基座：Linux 平台内部调用 `fcntl.flock`（整文件级，fd 关闭即释放，进程崩溃内核自动释放）；Windows 平台内部调用 `LockFileEx`（比标准库 `msvcrt.locking` 更现代的 Win32 API，语义对齐"整文件锁"）。`portalocker` 以 `uv add portalocker` 注入 `pyproject.toml` 并同步 `uv.lock`。
2. THE `Cross_Platform_File_Lock` SHALL 同时支持独占锁 `EXCLUSIVE`（写入）与共享锁 `SHARED`（读取并行）两种模式，以及通过轮询实现的可配置获取超时 `lock_acquire_timeout_ms`（默认 `5000`）。
3. WHEN 两个进程同时调用 `Local_File_Session_Context_Adapter.save(session_id, ctx_A)` 与 `save(session_id, ctx_B)` 对同一 `session_id`, THE 系统 SHALL 保证 `load(session_id)` 读取到的永远是 `ctx_A` 或 `ctx_B` 之一的**完整** `to_dict()` 输出，不得读取到两者的字节片段交叉、截断或零字节。
4. FOR ALL `Cross_Platform_File_Lock` 的获取调用, THE 实现 SHALL 在超时时抛出中文可读错误 `"获取本地持久化锁超时"`，不得无限阻塞。
5. THE `Cross_Platform_File_Lock` SHALL 在进程崩溃时由操作系统自动释放（`fcntl.flock` / `LockFileEx` 的 fd 关闭行为均满足此约束），禁止实现成"写一个 `.lock` 标记文件并在退出时 `os.remove`"的方案，因为该方案在进程崩溃时会遗留死锁。
6. WHEN 运行平台既不是 Linux 也不是 Windows（如 macOS / FreeBSD）, THE `Cross_Platform_File_Lock` SHALL 回退到 `portalocker` 在该平台的 fcntl 路径；IF `portalocker` 不可用 或 `import` 失败, THEN THE 适配器初始化 SHALL 触发 `Startup_Failure` 而非静默使用不加锁的实现。
7. THE `Local_File_Session_Context_Adapter` 及其共享工具的文档 SHALL 明确：本期仅在"单主机单实例"的部署形态下验证锁语义；**多容器通过 Docker volume 共享 `LOCAL_PERSISTENCE_ROOT` 属于未定义行为**，不纳入验证范围，不作为回归测试的覆盖条件。

### 需求 2.补：本地会话无 TTL / 无过期回收

**用户故事：** 作为单机部署的运维者与架构维护者，我希望本地文件会话的生命周期完全由**调用方显式 `delete` 决定**，而不引入任何基于时间的自动回收，避免"调用方认为会话仍存活但物理文件已被回收"导致的隐式数据丢失。

#### 验收标准

1. THE `Local_File_Session_Context_Adapter` **SHALL NOT** 拥有任何后台扫描 / 惰性过期 / 定时回收逻辑；进程中**不得**创建任何以 "TTL 回收"、"过期清理" 为职责的 asyncio 任务或线程。
2. FOR ALL `load(session_id)` 的返回路径, THE 实现 **SHALL NOT** 读取 `path.stat().st_mtime` 做任何与"过期"相关的判断；唯一允许的返回空路径是 "文件不存在" 与 "JSON 反序列化失败"。
3. FOR ALL `save(session_id, context)` 的返回路径, THE 实现 **SHALL NOT** 刻意刷新或保留 `mtime` 以"延长 TTL"；`mtime` 仅作为 `sweep_stale_tmp`（需求 3.2）识别 `*.tmp-*` 残留的判据，与会话 JSON 文件的寿命无关。
4. THE `config.properties` 模板 **SHALL NOT** 包含 `LOCAL_PERSISTENCE_SESSION_TTL_SECONDS`、`LOCAL_PERSISTENCE_REAPER_INTERVAL_SECONDS` 等任何 TTL / Reaper 相关键；若既有 design.md / tasks.md 中出现此类键，落地时 SHALL 一并移除。
5. THE `LocalPersistenceConfig` 的字段定义 **SHALL NOT** 包含 `session_ttl_seconds`、`reaper_interval_seconds`；若 Pydantic 校验层面检测到外部环境变量注入了这两个键，SHALL 按 `PropertiesBaseSettings` 的严格模式以 `ValidationError` 拒绝加载（避免"老配置悄悄失效"的静默降级）。
6. `Redis_Session_Context_Adapter` 的 TTL 行为 **SHALL** 保留在其内部实现中不受本需求影响；向后兼容保证的边界是"显式选择 `redis` 后端后 TTL 行为不变"。
7. 文档 (`docs/operations/runtime-backends.md`、README) **SHALL** 在 "file 后端" 章节显式提示：
   - "本地文件会话无 TTL 自动回收；如需清理会话请调用 `SessionContextStorePort.delete(session_id)` 或手动移除对应 JSON 文件"；
   - "如生产环境依赖 Redis TTL 的自动过期语义，必须显式设置 `SESSION_STORE_BACKEND=redis`"。
8. `Domain_Event_Decommission` 清理完成后, THE 本特性 **SHALL NOT** 再引入任何与"本地文件 TTL" 相关的领域术语或基础设施工具；`Tmp_File_Sweeper` 仅负责 `.tmp-*` 前缀文件清理，不得扩展为通用 TTL Reaper。

### 需求 3：崩溃一致性策略

**用户故事：** 作为单机部署的运维者，我希望在写入过程中断电、被 `kill -9`、或进程 OOM 时，既有数据不坏，至多丢失最后一次未完成的写入。

#### 验收标准

1. THE `Local_File_Session_Context_Adapter.save` SHALL 采用 `Temp_File_Atomic_Rename`：先写入 `<target>.tmp-<pid>-<uuid>`，调用 `fsync` 确保数据落盘，再用 `os.replace` 原子替换目标文件。
2. WHEN 写入过程在 "临时文件写入尚未完成" 时崩溃, THE 系统 SHALL 在下次启动时通过 `Tmp_File_Sweeper`（启动期一次性扫描）把遗留的 `*.tmp-<pid>-<uuid>` 文件识别为"写半失败"并清理；清理判据为"前缀匹配 `.tmp-` 且 `mtime` 早于启动时的可配置阈值（默认 3600 秒）"；该扫描**不得**扫描或删除任何 `.json` 会话文件。
3. WHEN 写入过程在 "临时文件写完但 `os.replace` 之前" 崩溃, THE 系统 SHALL 保证目标文件仍然是崩溃前的上一版本，未发生部分覆盖。
4. FOR ALL 写入路径, THE 实现 SHALL 在配置项 `LOCAL_PERSISTENCE_FSYNC_ON_WRITE=false` 时允许关闭 `fsync` 以换取吞吐（开发/测试场景），但配置注释 SHALL 明确标注"关闭 fsync 将放弃断电一致性保证"。

### 需求 4：跨平台路径与文件命名合法性

**用户故事：** 作为在 Windows 上做开发自测的工程师，我希望服务启动和运行期间不会因为路径分隔符、保留文件名、大小写敏感差异等 Windows 特性而崩溃或产生静默错乱。

#### 验收标准

1. FOR ALL 面向本地文件系统的路径拼接, THE `Local_File_Session_Context_Adapter` SHALL 使用 `pathlib.Path` 或 `os.path.join`，不得硬编码 `/` 或 `\` 作为分隔符。
2. THE `Local_File_Session_Context_Adapter` SHALL 将 `session_id` 通过 `sha256(session_id).hexdigest()` 不可逆哈希后使用；哈希结果是 64 位十六进制小写串，天然不包含 NUL、Windows 非法字符、Windows 保留文件名，也不存在大小写敏感冲突风险。
3. THE 哈希后的文件名 SHALL 采用"前 2 位分桶 + 后 62 位做 stem"的方式组成 `<bucket>/<stem>.json`，以避免单目录 inode 爆炸，同时保证不同 `session_id` 的文件名全局唯一。
4. WHEN 在 Windows 平台且未启用长路径支持, THE `Local_File_Session_Context_Adapter` SHALL 确保单个文件绝对路径长度不超过 260 个字符；超过时 SHALL 在启动期可预判时抛 `Startup_Failure`，或在运行期以中文消息提示 `"路径过长，请启用 Windows 长路径或缩短 LOCAL_PERSISTENCE_ROOT"`。
5. WHEN `Local_Persistence_Root` 自身含有 Windows 非法字符 / 保留名 / 超长, THE 启动期校验 SHALL 以 `Startup_Failure` 拒绝启动。
6. THE `Cross_Platform_Path_Policy` SHALL 提供 `ensure_within_root(root, candidate)` 接口，拒绝 `..` 逃逸出 `Local_Persistence_Root` 的路径。

### 需求 5：可选择性启用与配置模型

**用户故事：** 作为平台运维，我希望通过 `config.properties` 显式声明当前环境使用 `redis` 还是 `file`，不必部署 Redis。

#### 验收标准

1. THE `config.properties` SHALL 新增 `SESSION_STORE_BACKEND` 键，取值为 `redis` 或 `file`，**默认为 `file`**。
2. THE `config.properties` SHALL 新增 `LOCAL_PERSISTENCE_ROOT` 键，用于指定 `Local_Persistence_Root`；默认值形如 `./.local_persistence`（相对启动 cwd 的相对路径，运行期由启动流程规范化为绝对路径），以确保零配置 `uv run` 启动即可工作。
3. THE `config.properties` SHALL 新增以下附加配置键（带默认值）：
   - `LOCAL_PERSISTENCE_CREATE_IF_MISSING`（**默认 `true`**）；
   - `LOCAL_PERSISTENCE_FSYNC_ON_WRITE`（默认 `true`）；
   - `LOCAL_PERSISTENCE_LOCK_ACQUIRE_TIMEOUT_MS`（默认 `5000`）；
   - `LOCAL_PERSISTENCE_TMP_SWEEP_MAX_AGE_SECONDS`（默认 `3600`，`Tmp_File_Sweeper` 清理 `*.tmp-*` 残留的 mtime 阈值，仅作用于半写 tmp，不影响会话 JSON）。
   - **本期明确不引入** `LOCAL_PERSISTENCE_SESSION_TTL_SECONDS` / `LOCAL_PERSISTENCE_REAPER_INTERVAL_SECONDS`（见需求 2.补）。
4. WHEN 服务启动时 `SESSION_STORE_BACKEND=file`（即默认路径）且 `LOCAL_PERSISTENCE_ROOT` 未通过 `config.properties` / `.env` 配置, THE 应用 SHALL 使用需求 5.2 规定的模板默认值（`./.local_persistence`）继续启动；仅当 `LOCAL_PERSISTENCE_ROOT` 被显式置为**空字符串**时才以 `Startup_Failure` 终止启动，错误消息为 `"LOCAL_PERSISTENCE_ROOT 为空，服务拒绝启动"`。
5. WHEN 服务启动时 `LOCAL_PERSISTENCE_ROOT` 指向不存在的路径且 `LOCAL_PERSISTENCE_CREATE_IF_MISSING=false`, THE 应用 SHALL 以 `Startup_Failure` 终止启动。
6. WHEN 服务启动时 `LOCAL_PERSISTENCE_ROOT` 指向不存在的路径且 `LOCAL_PERSISTENCE_CREATE_IF_MISSING=true`（**默认情形**）, THE 应用 SHALL 创建该目录（含父级）并规范化为绝对路径。
7. WHEN 服务启动时 `LOCAL_PERSISTENCE_ROOT` 指向已存在但不是目录的路径, THE 应用 SHALL 以 `Startup_Failure` 终止启动。
8. WHEN 服务启动时 `LOCAL_PERSISTENCE_ROOT` 指向进程不可读或不可写的目录, THE 应用 SHALL 以 `Startup_Failure` 终止启动并在错误消息中指明缺失的权限位。
9. THE `Local_Persistence_Root` SHALL 在进程生命周期内不可变。
10. THE `Local_Persistence_Root` SHALL 与 `Workspace_Root`（`docs/spec/workspace/requirement.md`）**不共用**同一目录；WHEN 启动期检测到二者指向同一规范化路径或存在父子包含关系, THE 应用 SHALL 以 `Startup_Failure` 终止启动，错误消息为 `"LOCAL_PERSISTENCE_ROOT 不得与 WORKSPACE_ROOT 共用或相互包含"`。
11. THE `config.properties` 新增键的注释 SHALL 明确说明：
    - `file` 后端**仅保证单主机单实例**协同，不保证跨主机一致性；
    - 显式警告**不得**挂载到 NFS / SMB / OSS FUSE 等网络盘；
    - 显式警告**不得**在多容器通过 Docker volume 共享 `LOCAL_PERSISTENCE_ROOT` 的场景下使用（overlayfs / bind mount 下锁语义未验证）；
    - 生产集群部署建议显式设为 `redis` 并切换到 `RedisSessionContextAdapter`。
12. THE 仓库根目录的 `.gitignore` SHALL 显式忽略 `LOCAL_PERSISTENCE_ROOT` 的模板默认路径（`./.local_persistence/`），避免开发过程中把会话数据意外提交到版本库。

### 需求 6：DI 容器装配与调用方零改动

**用户故事：** 作为后端开发者，我希望 `Local_File_Session_Context_Adapter` 接入后，`TaskAgentAdapter`、`ChatServiceAdapter` 等既有调用方不需要任何代码改动。

#### 验收标准

1. THE `application/container_config.py` 中的 `_create_session_store()` SHALL 按 `SESSION_STORE_BACKEND` 取值分发：值为 `redis` 时返回 `RedisSessionContextAdapter`（既有路径），值为 `file`（**含默认未显式配置的情形**）时返回 `Local_File_Session_Context_Adapter`。
2. WHEN `SESSION_STORE_BACKEND=file` 被选用（**包括走默认值的情形**）, THE 容器启动过程 SHALL 允许 `redis` 异步资源缺失（Redis 未部署）而不阻断启动。
3. THE `ReadinessAggregator` 的 `HealthCheckPort` 组装 SHALL 根据当前后端选择**动态组装**，仅将"当前实际被装配、对外会产生 I/O 的中间件"对应的健康检查注册进来；**未装配的中间件必须完全忽略**，具体含义如下：
   1. WHEN `SESSION_STORE_BACKEND=redis`, THE `_create_readiness_aggregator` SHALL 注册 `RedisHealthCheckAdapter`；WHEN `SESSION_STORE_BACKEND=file`（含默认未显式配置的情形）且本次进程内**无任何**其他组件真正消费 Redis 客户端, THE `_create_readiness_aggregator` SHALL **不构造** `RedisHealthCheckAdapter`、**不读取**模块级 `_redis_client`，也不得把"Redis 未连接"算作 DOWN。
   2. 由于本期同步移除事件总线与事件存储（见需求 8），`DatabaseEventStoreAdapter` 不再存在，因此 MySQL 在默认配置下**不再是本服务的已装配中间件**；`_create_readiness_aggregator` SHALL **不构造** `MysqlHealthCheckAdapter`、**不调用** `get_session_factory()`，也不得把"MySQL 未连接"算作 DOWN。仅当未来新增其他 MySQL 消费者（本期不在范围内）时才恢复注册。
   3. THE "其他组件是否真正消费 Redis / MySQL" 的判定 SHALL 以 `register_async_resource` 注册结果为准（即：若 `redis` / `database` 异步资源在本次容器启动中**未被注册**，则视为未装配）；design.md 需要给出这一判定的具体实现点。
   4. WHEN Redis / MySQL 任一被判定为未装配, THE 对应健康检查的"缺席"本身 SHALL NOT 记为 WARNING 或 DOWN，`/health/ready` 响应体中也 SHALL 不出现该检查项（不显示"跳过/未启用"占位），避免误导运维。
   5. 在默认配置下（`SESSION_STORE_BACKEND=file` 且 Redis / MySQL 异步资源均未注册）, THE 就绪探针 SHALL 至少包含一个本地持久化目录可读写性的健康检查（由 design.md 决定是否新增 `LocalPersistenceHealthCheckAdapter`），避免"零健康检查"导致 `ReadinessAggregator` 因空列表而恒为 UP 的假阳性。
   6. 动态组装 SHALL 覆盖两种组合：(a) `redis`（显式回退，不含 MySQL），(b) `file`（默认；只含本地持久化健康检查）。design.md 必须显式枚举这两种组合并对齐 `_create_readiness_aggregator` 的分支逻辑。
   7. THE 动态组装逻辑 SHALL 有单元测试覆盖上述 (a)(b) 两种组合，并断言 `ReadinessResult.checks` 的**类型集合**精确等于期望集合，杜绝"某后端关闭后健康检查仍被误注册"的回归。
4. FOR ALL 既有调用 `SessionContextStorePort` 的生产代码（`TaskAgentAdapter`、`ChatServiceAdapter` 等）, THE 本特性 SHALL 不要求其源文件有任何修改。
5. THE Local 后端的启动期校验（目录存在、权限、类型等）SHALL 作为 `register_async_resource` 在 `_create_session_store` **之前**执行，以便 `Startup_Failure` 触发容器的回滚清理语义。
6. THE `Local_File_Session_Context_Adapter` 的注册 SHALL 遵循既有 `Scope.SINGLETON` 与 `register_async_resource` 使用模式。

### 需求 7：性能底线与可量化的 p99 写入延迟

**用户故事：** 作为单机部署的评估者，我希望对本地文件后端的写入延迟有可测、可验证的底线，而不是"能跑就行"。

#### 验收标准

1. WHILE `Local_Persistence_Root` 位于本地 SSD 或 ext4/NTFS 类本地文件系统, THE `Local_File_Session_Context_Adapter.save` SHALL 在单会话上下文 ≤ 10MB 的场景下满足 `Write_P99_Latency` ≤ 200ms（`fsync` 开启）/ ≤ 50ms（`fsync` 关闭）。
2. THE 性能验收 SHALL 通过独立的基准脚本（位于 `epsilon-boot/test/benchmarks/`）以 `uv run` 触发，产出 `p50 / p95 / p99` 延迟数据和吞吐数据。
3. WHEN 实测 p99 超出验收阈值, THE 本特性 SHALL 不被视为完成；仅允许通过"标注运行环境差异（如磁盘类型 / 文件系统类型）"作为豁免说明，不得通过降低阈值绕过。
4. THE 性能基准 SHALL 在同一脚本里对比 `redis` 会话后端与 `file` 会话后端的写入延迟；对比数据仅用于文档与决策，不作为验收硬门槛。
5. FOR ALL 目标规模以外的使用（单会话 > 10MB）, THE 文档 SHALL 明确标注"本期不保证性能"，避免运维误用。

### 需求 8：领域事件基础设施清理（`Domain_Event_Decommission`）

**用户故事：** 作为架构维护者，我希望在引入本地文件会话后端的同时，连带清理"零生产消费者"的领域事件脚手架代码，避免未来维护成本继续累积。

#### 领域事件必要性评估（证据）

以下事实是本需求的依据，落地阶段若证据失真需要在评估阶段重新 gate 本需求：

1. `epsilon-boot/src/common/events.py` 中**仅存在** `DomainEvent` 基类，无任何生产子类；
2. `epsilon-boot/src/` 下全量 grep 无 `EventBusPort.publish` / `subscribe` 的生产调用，仅 `InMemoryEventBusAdapter.publish` 内部调用 `EventStorePort.store` 与 `record_handler_result`；
3. `EventStorePort.query` / `replay` 仅在 `test/` 下被调用（`test_event_store_integration.py` 等）；
4. `ChatServiceAdapter` / `TaskAgentAdapter` / HTTP API 层构造函数签名中均无 `EventBusPort` / `EventStorePort` 注入；
5. 既有 `event_records` / `event_handler_results` MySQL 表在生产环境中的数据价值**仅限审计追溯**，且审计流程未在其他任何生产需求中被引用。

#### 验收标准

1. THE 本特性 SHALL 从 `epsilon-boot/src/` 中**移除**以下文件或目录：
   - `src/common/events.py`
   - `src/common/event_bus/`（包含 `__init__.py`、`ports.py`、`serializer.py`，以及目录下其他所有模块）
   - `src/infrastructure/event_bus/in_memory_event_bus_adapter.py`
   - `src/infrastructure/event_bus/database_event_store_adapter.py`
   - `src/infrastructure/event_bus/__init__.py`（如目录变空则同步移除整个目录）
   - `src/infrastructure/database/models/event_record.py`
2. THE 本特性 SHALL 从 `src/application/container_config.py` 中**移除**以下注册与启动钩子：
   - `EventBusPort` 的 `register_factory`
   - `EventStorePort` 的 `register_factory`
   - `InMemoryEventBusAdapter` / `DatabaseEventStoreAdapter` 相关的任何 import
   - 仅被事件总线使用的 `register_async_resource` 注册（如 "database" 在本期不再被任何生产组件消费时一并移除注册）
3. THE 本特性 SHALL 移除以下测试文件（按 `Explore` 调研给出的文件名精确删除，tasks.md 阶段落地时需再次 grep 验证）：
   - `test/common/events/test_event_bus.py` 及其 property 变体
   - `test/common/events/test_serializer.py` 及其 property 变体
   - `test/infrastructure/event_bus/test_database_event_store_adapter.py` 及其 property 变体
   - `test/integration/test_event_store_integration.py`
   - 与 `event_record` / `handler_result_record` ORM 建模相关的数据库迁移测试（若存在）
4. THE 本特性 SHALL 新增一份 Alembic 迁移脚本（位置在既有 `src/infrastructure/database/migrations/versions/` 或等价目录），在 `upgrade()` 中 `drop_table("event_records")` 与 `drop_table("event_handler_results")`（以实际既有表名为准）；`downgrade()` 中恢复表结构以兼容回滚。如果在目标环境中这些表从未被创建（例如 `file` 默认后端跑的环境），迁移 SHALL 以"表不存在则 no-op"方式处理（使用 `op.execute("DROP TABLE IF EXISTS ...")` 而非 `op.drop_table`）。
5. THE 本特性 SHALL 在 `epsilon-boot/README.md` 与 `docs/` 中涉及"领域事件 / event bus / event store"的章节做如下处理：
   - 既有章节改为一句话注释："当前版本未内置领域事件基础设施；如有需求请提交新 feature。"
   - 相关术语表条目（如 `Event_Record`、`Handler_Result_Record`、`Event_Store_Backend_Kind`）一并移除，避免术语污染；
   - `docs/operations/runtime-backends.md`（本 feature 新增文档）中**不**出现 `EVENT_STORE_BACKEND` 键，只讲 `SESSION_STORE_BACKEND`。
6. THE 本特性 SHALL 在 `src/application/container_config.py` 与 `src/infrastructure/health/` 中移除 `MysqlHealthCheckAdapter` 的默认注册路径，但**保留** `MysqlHealthCheckAdapter` 类定义（位于 `src/infrastructure/health/mysql_health_check_adapter.py`）作为死代码备用；当未来新增 MySQL 消费者时可直接复用。这是"让易恢复的能力保留、让维护成本高的脚手架清理"的平衡点。
7. THE 本特性 SHALL 在 `config.properties` 模板中删除（若存在）：
   - `EVENT_STORE_BACKEND`
   - 与 event store 相关的所有键与注释
8. THE 本特性完成后，`grep -r "DomainEvent\|EventBusPort\|EventStorePort\|publish.*event\|InMemoryEventBusAdapter\|DatabaseEventStoreAdapter"` 在 `src/` 与 `test/` 下**返回零匹配**（允许 `docs/` 与迁移脚本 `downgrade` 中出现），作为本需求落地的量化门槛。
9. **回退预案**：若落地过程中发现某生产代码路径确实消费了领域事件（例如 Explore 调研遗漏了某处间接依赖），THE 本特性 SHALL 停止清理工作，退化为"仅落地会话本地文件后端、保留领域事件基础设施"，并在 tasks.md 中以 `CLARIFICATION` 形式向用户汇报，由用户决策是否后续独立立项清理。

### 需求 9：可观测性与错误路径不得静默

**用户故事：** 作为平台运维，我希望 `file` 后端在失败时有结构化日志与 trace 关联，能定位问题，避免"写静默失败"。

#### 验收标准

1. WHEN `Local_File_Session_Context_Adapter` 的 `save` / `load` / `delete` 捕获 `OSError` 子类（`PermissionError`、`FileNotFoundError`、`IsADirectoryError`、`NotADirectoryError`、磁盘满 `OSError.errno == ENOSPC` 等）, THE 适配器 SHALL 以 `logger.error` 输出结构化日志（至少包含 `session_id`、`operation`、`error_class`、`errno`），并对 `save` / `delete` 向上抛出原生异常（与 `Redis_Session_Context_Adapter` 对 `RedisError` 的处理一致），对 `load` 则返回空 `ConversationContext`（与需求 1 一致）。
2. THE 所有结构化日志 SHALL **不得**包含 `API_KEY`、`PASSWORD`、`SECRET`、`TOKEN`、`CREDENTIAL` 等敏感字段；若 `ConversationContext` 自身含有疑似敏感子串，日志 SHALL 仅输出长度与哈希摘要，不输出原文。
3. WHEN 日志框架启用了 OpenTelemetry 关联（`OTEL_LOG_CORRELATION=true`）, THE `file` 后端产生的日志 SHALL 自动携带 `trace_id` / `span_id`。
4. THE `file` 后端 SHALL NOT 吞掉写入异常：任何 `save` 失败必须至少一次到达日志 **且**（除 `load` 外）抛出到调用方，禁止"记日志后 `return None`"式的静默降级。
5. THE `Tmp_File_Sweeper` SHALL 在启动期扫描结束后以 `logger.info` 输出结构化摘要（至少包含 `scanned_count`、`deleted_count`、`errored_count`，消息前缀为 `"TmpFileSweeper 扫描完成"`，明确与任何历史 "TtlReaper" 命名区分）；不得重复启动或作为后台循环运行。

### 需求 10：测试覆盖（含 Linux / Windows 双平台 CI）

**用户故事：** 作为 QA/SRE，我希望 `file` 后端在 Linux 与 Windows 两个平台下都有可执行的集成测试与并发属性测试，而不是只依赖人工验证。

#### 验收标准

1. THE 单元测试 SHALL 位于 `epsilon-boot/test/infrastructure/session/`、`epsilon-boot/test/infrastructure/persistence/local_file/`（按 DDD 分层镜像组织），至少覆盖：
   - `save` / `load` / `delete` 基本语义；
   - `load` 不存在文件返回空 `ConversationContext`；
   - `load` 遇到损坏 JSON 时的容错；
   - `Temp_File_Atomic_Rename` 在模拟崩溃（中途抛异常）后的残留清理；
   - `Cross_Platform_Path_Policy` 对 Windows 保留文件名、非法字符、NUL、过长路径的拒绝或转义；
   - `Tmp_File_Sweeper` 对 `*.tmp-*` 残留的启动期清理行为；断言该组件**不**触碰 `.json` 会话文件；
   - 会话**无 TTL 过期**的回归断言：`save` 后将 `mtime` 调至 1 天前，`load` 仍须返回原 `ConversationContext` 而非空（锁死需求 2.补.2）；
   - 后端选择分发：`SESSION_STORE_BACKEND=redis|file` 各自返回对应类型。
2. THE property-based 测试 SHALL 使用 Hypothesis（遵循仓库 `_property.py` 命名约定），至少覆盖：
   - 任意 `session_id` 字符串经 `Session_File_Name_Scheme` 哈希后 `load(save(ctx)) == ctx`；
   - `save` 在并发下的最终状态一定等于某次输入的完整 `ConversationContext`。
3. THE 并发属性测试 SHALL 使用 `multiprocessing`（而不是仅 `threading`，以便覆盖真正的跨进程锁语义），至少包含：
   - N 个进程对同一 `session_id` 并发 `save`，收敛后 `load` 必须是合法的 `ConversationContext`。
4. THE CI SHALL 在 **Linux**（`ubuntu-latest`）与 **Windows**（`windows-latest`）两种 runner 上分别执行 上述单元测试与并发属性测试；两者均**必选**；macOS 可选不强制。Windows runner 以 GitHub Actions `windows-latest` 或等价 self-hosted runner 提供。
5. WHEN 任一平台的 CI 执行失败, THE 本特性 SHALL 不被视为完成。
6. THE 基准脚本（需求 7.2）SHALL 在 CI 中以 `-m benchmark` 或等价标记**默认不跑**，仅支持手动触发，以免拖慢常规 CI。
7. THE 测试 SHALL 不依赖外部 Redis 服务；`file` 后端的测试必须完全离线可跑。
8. THE 领域事件基础设施清理（需求 8）落地后的 CI SHALL 断言以下命令 exit code 为 0：
   ```
   python -c "import importlib; [importlib.import_module(m) for m in ['common.events', 'common.event_bus', 'infrastructure.event_bus.in_memory_event_bus_adapter']]" 2>&1 | grep -q 'ModuleNotFoundError'
   ```
   即"这些模块必须 import 失败"。具体断言语法可由 design.md 细化为 pytest 用例，但语义等价。

### 需求 11：文档与向后兼容

**用户故事：** 作为运维与二次开发者，我希望本特性落地后文档能讲清楚"什么时候用 `file` 后端、什么时候用 Redis"以及差异，并了解领域事件基础设施已被移除。

#### 验收标准

1. THE `epsilon-boot/` 下 SHALL 新增 `docs/operations/runtime-backends.md`，说明 `SESSION_STORE_BACKEND` 的取值、适用场景、互斥限制与不在范围的注意事项（特别是 NFS / SMB / OSS FUSE 不被支持、多容器共享 volume 不被支持）。
2. THE `config.properties` 的模板 SHALL 包含需求 5 所列的全部新增配置键，每个键均带中文注释，说明含义、默认值、推荐值与安全警告。
3. THE 所有新增 Python 模块、类、函数 SHALL 带中文 docstring，遵循 `.kiro/steering/code-documentation.md` 的约定。
4. WHEN 用户**显式**将 `SESSION_STORE_BACKEND` 设为 `redis`（原先的事实默认值），THE 现有 `config.properties`、启动流程、HTTP API 行为 SHALL 与本特性上线前基本一致；唯一的差异是：领域事件基础设施已被移除（需求 8），因此 `EventBusPort` / `EventStorePort` 不再可注入。向后兼容保证的是"**显式选择 redis 后的会话读写行为**"，而非"事件总线的存续"。
5. FOR ALL 新增依赖, THE 依赖声明 SHALL 通过 `uv add` 写入 `pyproject.toml` 并同步 `uv.lock`，遵循 `.kiro/steering/uv-package-manager.md` 的硬约束。具体新增依赖：`portalocker`（跨平台文件锁）。
6. FOR ALL 新增配置键, THE 配置加载 SHALL 通过 `PropertiesBaseSettings` 从 `config.properties` 读取，遵循 `.kiro/steering/config-source.md` 的硬约束（`config.properties` 优先，`.env` 仅作覆盖）。
7. THE `docs/operations/runtime-backends.md` SHALL 在文档开头明确标注：
   - "本期起默认会话后端已从 `redis` 切换为 `file`"；
   - "本期移除了领域事件基础设施（`EventBusPort` / `EventStorePort` / `DomainEvent`），`event_records` / `event_handler_results` MySQL 表将在升级迁移中被 drop"；
   - 升级指南：既有生产部署若希望保持 Redis 会话链路，需显式在 `config.properties` 中声明 `SESSION_STORE_BACKEND=redis`；若依赖领域事件基础设施，在升级前停止升级并提交新 feature。
8. THE `epsilon-boot/README.md` SHALL 在"快速开始"章节更新以下事实：零配置 `uv run` 启动即可工作、不再要求本地拉起 Redis / MySQL；若需要接入集群级会话后端，引导到 `docs/operations/runtime-backends.md`。

### 需求 12：DDD 分层与模块位置

**用户故事：** 作为架构维护者，我希望 `file` 后端的代码严格遵循现有 DDD 分层，新增代码不得让领域层感知任何文件系统细节。

#### 验收标准

1. THE `Local_File_Session_Context_Adapter` SHALL 位于 `epsilon-boot/src/infrastructure/session/local_file_session_context_adapter.py`（与 `RedisSessionContextAdapter` 同目录），作为 `SessionContextStorePort` 的另一实现。
2. THE `Cross_Platform_File_Lock`、`Cross_Platform_Path_Policy`、`Temp_File_Atomic_Rename`、`Ttl_Reaper` 等与"本地文件"强相关的**基础设施共享工具** SHALL 位于 `epsilon-boot/src/infrastructure/persistence/local_file/`（新建目录）。
3. THE `domain/chat/ports.py` SHALL 不因本特性而修改（接口对等约束由 Protocol 的结构化子类型保证）。
4. THE `domain/` 目录下 SHALL 不新增任何"文件系统"、"锁"、"fsync"、"fcntl"、"msvcrt"、"portalocker" 相关导入或类型。
5. THE `Session_Store_Config` / `Local_Persistence_Config` SHALL 位于 `infrastructure/` 层（具体子目录由 design.md 决定，但不得位于 `domain/`）。
6. FOR ALL 新增单元测试, THE 测试目录 SHALL 镜像 `src/` 的 DDD 分层结构，放置在 `epsilon-boot/test/infrastructure/session/` 与 `epsilon-boot/test/infrastructure/persistence/local_file/` 下。

## 留给 design.md 的开放问题

以下问题不作为本需求的硬约束，交由设计阶段决策，但必须在 design.md 中给出明确答案：

1. ~~`Ttl_Reaper` 落点：后台 asyncio 任务、启动一次性扫描、每次 `load` 惰性删除，三种策略的组合方式。~~ **（已消解：需求 2.补 明确本期不引入 TTL / Reaper；仅保留 `Tmp_File_Sweeper` 作启动期一次性 `.tmp-*` 清理。）**
2. `ReadinessAggregator` 的动态健康检查组装，是在 `configure_container()` 里按 backend 分支 `append`，还是为每个 backend 定义独立的 `_create_readiness_aggregator_*`。
3. 是否需要暴露 `get_adapter_metrics()` 样式的运行期自检端点，以及如何与既有 `/health/ready` 集成。
4. 若将来 `SessionContextStorePort` 新增 `list_sessions` / `touch` 等方法，`file` 后端如何规划扩展点。
5. 领域事件清理后，`infrastructure/event_bus/` 整个目录是否保留（保留空目录 + `README.md` 说明，还是直接 `rm -rf`）。
6. Alembic 迁移脚本的编号与命名约定：本 feature 引入的"drop event tables"迁移是否作为新的 head、与既有迁移链的衔接处理。

## 待澄清事项（不凭空编造的部分）

以下事项在编写本需求文档时无法通过现有代码明确推断，需在进入 design 阶段前与业务/架构侧确认：

1. **Windows runner 在当前 CI 体系下是否已就绪**：需求 10.4 要求 Linux + Windows 双平台 CI 必选；如果当前 CI 仅有 Linux runner，本需求落地时需要评估引入 Windows runner 的成本与时间；本期默认假设**一并引入** `windows-latest`。
2. **生产环境 `event_records` 表是否存在残留数据**：如果业务侧认为该表的审计数据不可丢失，需在 design.md 中决定迁移前是否做 `mysqldump` 备份；本期默认假设"零消费者 → 可直接 drop"，但留此澄清口子。
3. ~~**Session TTL 默认值**：保持 `3600` 秒与 Redis 对齐。若生产 Redis TTL 已被覆盖为其他值，需要同步调整 `LOCAL_PERSISTENCE_SESSION_TTL_SECONDS` 默认值。~~ **（已消解：需求 2.补 明确本期 file 后端不设置 TTL；Redis 后端 TTL 行为内聚在 `RedisSessionContextAdapter`，不跨后端迁移。）**
4. **`.env` 优先级**：按 `config-source.md` 约定"`.env` 仅作本地覆盖"；仓库根 `.env` 与用户家目录 `.env` 的区分，在本项目中是否有明确约定，需在 design 阶段确认后写入文档。
