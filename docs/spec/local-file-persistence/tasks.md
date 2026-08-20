# 实现计划：会话持久化的本地文件实现 + 领域事件基础设施清理

## 概述

本计划把 `docs/spec/local-file-persistence/requirement.md` 与 `design.md` 转换为可执行任务列表。整体目标两件事：

1. **Scope A（主实现）**：新增 `Local_File_Session_Context_Adapter`，把 `SessionContextStorePort` 默认后端从 Redis 切换为本地文件，零外部依赖 `uv run` 启动即可；Redis 在显式配置下仍可用。包含跨平台文件锁（`portalocker`）、原子写、路径策略、启动期一次性 `TmpFileSweeper`（仅清理 `*.tmp-*` 残留；**本期会话无 TTL / 无后台 Reaper**，见需求 2.补）、动态 ReadinessAggregator 组装、workspace 冲突校验，并在 CI 中增加 `windows-latest` 矩阵。
2. **Scope B（清理）**：移除"零消费者的领域事件基础设施"：`common/events.py`、`common/event_bus/`、`infrastructure/event_bus/`、`infrastructure/database/models/event_record.py` + `handler_result_record.py` 及其测试、`container_config.py` 中 `EventBusPort` / `EventStorePort` 的注册；同时将 MySQL 默认装配一并移除（`MysqlHealthCheckAdapter` 类保留为死代码）。因此 `design.md` 中的 `LocalFileEventStoreAdapter`、`AppendOnlyEventLog`、`FileBackedIdAllocator`、`EventStoreConfig` 等章节在本期**不落地**（见"备注"第 1 条）。

执行顺序总览：

- 阶段 0：依赖、配置项默认值、`.gitignore`、CI 矩阵脚手架。
- 阶段 1：Scope B 清理（先做，降低后续耦合，避免同时写两套事件基础设施代码）。
- 阶段 2：Scope A 核心实现（共享工具 → Adapter → 配置与启动期校验 → DI 装配 → 动态 ReadinessAggregator）。
- 阶段 3：测试（单元 → property → 多进程并发 → Windows 特性）。
- 阶段 4：CI 与文档（`windows-latest` runner 启用、`docs/operations/runtime-backends.md`、README 升级指南）。
- 阶段 5：端到端验证与验收门槛。

约束：

- 所有 Python 代码带中文 docstring（`.kiro/steering/code-documentation.md`）。
- 依赖新增必须 `uv add`（`.kiro/steering/uv-package-manager.md`）；工作目录在 `epsilon-boot/`。
- 新增配置项必须写入 `epsilon-boot/config.properties`（`.kiro/steering/config-source.md`）。
- DDD 分层严格：`domain/` 不感知文件系统；适配器位于 `infrastructure/`；共享工具位于 `infrastructure/persistence/local_file/`（`.kiro/steering/ddd-architecture.md`）。
- 本项目不存在 Alembic 迁移目录（见`需求 8.4` 与"备注"第 2 条的处理）。

## Tasks

- [x] 0. 阶段 0：依赖、默认值与 CI 脚手架
  - [x] 0.1 引入 `portalocker` 依赖
    - 在 `epsilon-boot/` 下执行 `uv add portalocker` 更新 `pyproject.toml` 与 `uv.lock`
    - 验证 `python -c "import portalocker; print(portalocker.__version__)"` 可运行
    - _需求：2.1、11.5_
  - [x] 0.2 在 `epsilon-boot/config.properties` 中追加本期新增配置键
    - 追加章节"后端选择与本地文件持久化"，包含 `SESSION_STORE_BACKEND=file`（默认 `file`）、`LOCAL_PERSISTENCE_ROOT=./.local_persistence`、`LOCAL_PERSISTENCE_CREATE_IF_MISSING=true`、`LOCAL_PERSISTENCE_FSYNC_ON_WRITE=true`、`LOCAL_PERSISTENCE_LOCK_ACQUIRE_TIMEOUT_MS=5000`、`LOCAL_PERSISTENCE_TMP_SWEEP_MAX_AGE_SECONDS=3600`
    - 每个键上方写中文注释：含义、默认值、安全警告（"仅单主机单实例；禁止挂载到 NFS/SMB/OSS FUSE；禁止多容器通过 Docker volume 共享"）；`TMP_SWEEP_MAX_AGE_SECONDS` 注释显式说明"仅清理 `*.tmp-*` 半写残留，不影响 `.json` 会话文件寿命"
    - **本期会话无 TTL**（需求 2.补）：**禁止**追加 `LOCAL_PERSISTENCE_SESSION_TTL_SECONDS` 与 `LOCAL_PERSISTENCE_REAPER_INTERVAL_SECONDS` 键；config.properties 注释中需新增一句"本期 file 会话后端不设置 TTL；如依赖自动过期语义，显式 SESSION_STORE_BACKEND=redis"
    - **禁止**追加 `EVENT_STORE_BACKEND` 键（需求 8.7：本期不引入；design.md 的 `EventStoreConfig` 不落地）
    - _需求：5.1、5.2、5.3、5.11、2.补.4、8.7、11.2_
  - [x] 0.3 更新仓库根 `.gitignore`
    - 在 `/workspace/.gitignore` 追加 `epsilon-boot/.local_persistence/` 与 `.local_persistence/`（双重保险：模板默认 cwd 路径 + 子目录路径）
    - _需求：5.12_
  - [x] 0.4 新建（或确认存在）GitHub Actions CI 工作流框架
    - 创建 `/workspace/.github/workflows/ci.yml` 骨架（现仓库无 `.github/workflows/`）；使用 `actions/checkout@v4` + `astral-sh/setup-uv@v3` + `uv sync --frozen` + `uv run pytest -m "not benchmark"`；工作目录 `epsilon-boot`
    - 在 `jobs.test.strategy.matrix.os` 中配置 `[ubuntu-latest]`（本任务先上线 Linux，windows 在阶段 4.1 加入）
    - _需求：10.4（先打地基）_

- [x] 1. 阶段 1：领域事件基础设施清理（Domain_Event_Decommission）
  - [x] 1.1 移除 `common/event_bus/` 与 `common/events.py`
    - 删除 `epsilon-boot/src/common/events.py`
    - 删除 `epsilon-boot/src/common/event_bus/__init__.py`、`ports.py`、`serializer.py`
    - 若 `common/event_bus/` 目录清空则一并删除（开放问题 5：**直接删除**，不保留空目录）
    - _需求：8.1_
  - [x] 1.2 移除 `infrastructure/event_bus/` 整目录
    - 删除 `epsilon-boot/src/infrastructure/event_bus/__init__.py`、`in_memory_event_bus_adapter.py`、`database_event_store_adapter.py`
    - 删除整个 `epsilon-boot/src/infrastructure/event_bus/` 目录
    - _需求：8.1_
  - [x] 1.3 移除 `infrastructure/database/models/` 下事件相关 ORM
    - 删除 `epsilon-boot/src/infrastructure/database/models/event_record.py`
    - 删除 `epsilon-boot/src/infrastructure/database/models/handler_result_record.py`
    - 更新 `epsilon-boot/src/infrastructure/database/models/__init__.py`：移除对 `EventRecord` / `HandlerResultRecord` 的 re-export
    - _需求：8.1_
  - [x] 1.4 从 `src/application/container_config.py` 移除事件相关装配
    - 移除模块级 import：`from common.event_bus.ports import EventBusPort, EventStorePort`
    - 移除 `_create_event_store()` 与 `_create_event_bus()` 两个工厂函数
    - 移除 `container.register(EventStorePort, ...)` 与 `container.register(EventBusPort, ...)` 两行
    - _需求：8.2_
  - [x] 1.5 从 `src/application/container_config.py` 移除默认 MySQL 装配
    - 移除 `container.register_async_resource("database", _init_db, _cleanup_db)` 这一行（因本期无任何 MySQL 生产消费者，需求 7.4.2 要求不再默认装配）
    - 移除 `container.register(SessionProviderPort, _create_session_provider, Scope.SINGLETON)` 一行，以及对应的 `_create_session_provider` 函数（`SessionProviderPort` 仅被 `DatabaseEventStoreAdapter` 使用，本期被删除）
    - 注意：**保留** `infrastructure/database/engine.py`、`database_config.py`、`ports.py`、`session_provider.py` 源代码作为死代码备用（需求 8.6 的平衡原则，未来新增 MySQL 消费者时恢复装配）
    - _需求：7.4.2、8.2、8.6_
  - [x] 1.6 删除事件相关测试文件
    - 删除 `epsilon-boot/test/common/event_bus/` 整目录（`test_event_bus.py`、`test_event_bus_property.py`、`test_serializer.py`、`test_serializer_property.py`、`__init__.py`）
    - 删除 `epsilon-boot/test/infrastructure/event_bus/` 整目录（`test_database_event_store_adapter.py`、`test_database_event_store_adapter_property.py`、`__init__.py`）
    - 删除 `epsilon-boot/test/infrastructure/database/models/test_event_record.py`、`test_handler_result_record.py`
    - 删除 `epsilon-boot/test/integration/test_event_store_integration.py`（若存在）
    - _需求：8.3_
  - [x] 1.7 校验检查点：grep 零残留 + 既有测试仍通过
    - 在 `epsilon-boot/` 下执行：`grep -rE "DomainEvent|EventBusPort|EventStorePort|InMemoryEventBusAdapter|DatabaseEventStoreAdapter" src test` 应返回 0 行（允许 `docs/` 出现，但 docs 目录本期已清理）
    - 在 `epsilon-boot/` 下执行：`uv run pytest -x --ignore=test/benchmarks`，确保删除后无 `ImportError`、现有其他测试不因 `container_config.py` 修改而崩溃（`test/application/test_container_config.py` 可能因此 tasks 1.4/1.5 失败，需在 1.8 重写）
    - _需求：8.8_
  - [x] 1.8 更新 `test/application/test_container_config.py`
    - 移除对 `EventBusPort` / `EventStorePort` / `InMemoryEventBusAdapter` / `DatabaseEventStoreAdapter` 的 import 与断言（需求 8 已移除的符号）
    - 改为仅断言 `SessionContextStorePort` 被注册为 `Scope.SINGLETON`、`ToolRegistry` 按预期注册
    - _需求：8.3_
  - [x] 1.9 修改 `test/conftest.py` 与 `test/application/test_agent_delegation_config_properties.py` 中可能的事件相关 mock
    - 搜索 `test/` 下残留 `EventStorePort` / `EventBusPort` mock，修改为本期不再需要的形式或直接删除
    - 验收：`grep -rE "EventBusPort|EventStorePort" epsilon-boot/test` 返回 0 行
    - _需求：8.3、8.8_

- [x] 2. 阶段 2：会话本地文件后端核心实现
  - [x] 2.1 新建基础设施共享工具包骨架
    - 创建 `epsilon-boot/src/infrastructure/persistence/__init__.py`（空文件 + 模块级中文 docstring"本地文件持久化基础设施包"）
    - 创建 `epsilon-boot/src/infrastructure/persistence/local_file/__init__.py`（导出后续模块的公开类型）
    - _需求：12.2_
  - [x] 2.2 实现 `CrossPlatformPathPolicy`
    - 创建 `epsilon-boot/src/infrastructure/persistence/local_file/path_policy.py`
    - 定义 `PathPolicyViolation(ValueError)` + `CrossPlatformPathPolicy` 类
    - 实现 `hash_session_id(session_id: str) -> tuple[str, str]`：`sha256().hexdigest()` 前 2 位为 bucket、后 62 位为 stem
    - 实现 `check_dirname(name: str) -> None`：匹配正则 `[\x00/\\:*?"<>|]` 拒绝非法字符；`name.split(".",1)[0].upper()` 命中 `{CON, PRN, AUX, NUL, COM1-9, LPT1-9}` 则拒绝
    - 实现 `check_absolute_path_length(absolute_path: Path) -> None`：仅 `os.name == "nt"` 时检查 260 字符上限
    - 实现 `ensure_within_root(root: Path, candidate: Path) -> Path`：`(root / candidate).resolve().relative_to(root.resolve())`
    - 所有错误消息中文可读
    - _需求：4.1、4.2、4.3、4.4、4.6、12.2、12.4_
  - [x] 2.3 路径策略单元测试
    - 创建 `epsilon-boot/test/infrastructure/persistence/local_file/__init__.py` 与 `test_path_policy_unit.py`
    - 用例：`hash_session_id` 稳定性、同输入同输出；`check_dirname` 对 CON/COM1/NUL、`:` / `*` / `?` / `<` 等拒绝；`check_absolute_path_length` 在 `monkeypatch.setattr(os, "name", "nt")` 下 261 长度抛异常；`ensure_within_root` 阻止 `../../etc` 逃逸
    - _需求：10.1；正确性属性：Property 7_
  - [x] 2.4 实现 `CrossPlatformFileLock` + `LockFactory`
    - 创建 `epsilon-boot/src/infrastructure/persistence/local_file/file_lock.py`
    - 定义 `LockMode(Enum)`（EXCLUSIVE / SHARED）、`LockTimeout(TimeoutError)`、`LockHandle`（dataclass，实现 `__enter__` / `__exit__` 在退出时 `portalocker.unlock(fd)` + `fd.close()`）
    - 定义 `CrossPlatformFileLock`：构造参数 `(lock_path, acquire_timeout_ms, poll_interval_ms=20)`；`_ensure_backend_supported` 检测平台（Linux/Windows/macOS 直接 OK；其他平台 `import fcntl` 失败则冒泡）
    - 实现 `acquire(mode: LockMode) -> LockHandle`：`open(lock_path, "a+b")` + `portalocker.lock(fd, NON_BLOCKING | EXCLUSIVE/SHARED)`；`portalocker.exceptions.LockException` 时 `time.monotonic()` 比对 deadline，超时抛 `LockTimeout("获取本地持久化锁超时")`
    - 定义 `LockFactory`：`__init__(acquire_timeout_ms)`；`__call__(lock_path)` 返回新 `CrossPlatformFileLock`
    - _需求：2.1、2.2、2.4、2.5、2.6、12.2_
  - [x] 2.5 文件锁单元测试
    - 创建 `epsilon-boot/test/infrastructure/persistence/local_file/test_file_lock_unit.py`
    - 用例：`LockMode.EXCLUSIVE` 两次同目标 `acquire` 在未释放时第二次超时；`SHARED` 可并发；`LockTimeout` 的中文消息前缀为 `"获取本地持久化锁超时"`；`LockHandle.__exit__` 关闭 fd 后锁可再次获取
    - _需求：10.1；正确性属性：Property 6 的单进程侧验证_
  - [x] 2.6 实现 `TempFileAtomicWriter`
    - 创建 `epsilon-boot/src/infrastructure/persistence/local_file/atomic_writer.py`
    - 定义 `TempFileAtomicWriter(fsync_on_write: bool)`
    - 实现 `write_bytes_atomic(target: Path, payload: bytes) -> None`：`target.parent.mkdir(parents=True, exist_ok=True)` → `tmp = target.with_name(f"{target.name}.tmp-{os.getpid()}-{uuid.uuid4().hex}")` → `open(tmp, "wb")` 写入 → `flush` → 若 `fsync_on_write` 则 `os.fsync(f.fileno())` → `os.replace(tmp, target)`；异常时 `tmp.unlink(missing_ok=True)` 并重新 raise
    - **不**在本类中实现 `sweep_stale_tmp`；该职责拆分到独立的 `TmpFileSweeper`（任务 2.8，语义解耦、边界清晰）
    - _需求：3.1、3.3、3.4_
  - [x] 2.7 原子写单元测试
    - 创建 `epsilon-boot/test/infrastructure/persistence/local_file/test_atomic_writer_unit.py`
    - 用例：写入后 `read_bytes()` 等于 payload；`write_bytes_atomic` 中途 `monkeypatch` 让 `os.fsync` 抛 `OSError`，验证 `target` 不存在且 tmp 已清理；`fsync_on_write=False` 时不调用 `os.fsync`
    - _需求：10.1；正确性属性：Property 5_
  - [x] 2.8 实现 `TmpFileSweeper`（替代原 `TtlReaper`；本期会话无 TTL）
    - 创建 `epsilon-boot/src/infrastructure/persistence/local_file/tmp_file_sweeper.py`
    - 定义 `TmpFileSweeper(sessions_root: Path, max_age_seconds: int)`
    - 实现 `sweep_once() -> dict`：遍历 `sessions_root/<bucket>/`；**严格限定**只看文件名包含 `.tmp-` 的条目；`now - mtime > max_age_seconds` 则 `unlink`；返回 `{"scanned", "deleted", "errored"}` 三字段；`logger.info("TmpFileSweeper 扫描完成 scanned=%d deleted=%d errored=%d", ...)`
    - **禁止**在本类中实现 `is_expired` / `start` / `stop` / 后台循环；本类是同步、一次性组件，调用方在启动期同步调用 `sweep_once()` 后即可丢弃
    - **禁止**对任何 `.json` / `.lock` 文件做 `unlink`（需求 2.补.8、3.2）
    - _需求：3.2、9.5、term "Tmp_File_Sweeper"；正确性属性：Property 8_
  - [x] 2.9 `TmpFileSweeper` 单元测试
    - 创建 `epsilon-boot/test/infrastructure/persistence/local_file/test_tmp_file_sweeper_unit.py`
    - 用例：预置 `sessions/ab/<stem>.json.tmp-123-abcd` 且将其 `mtime` 回拨 2 小时 → `sweep_once` 返回 `deleted==1` 且文件被删；预置 `.json` 会话文件且 `mtime` 同样回拨 2 小时 → 仍存在（`scanned` 不计 `.json`）；`.tmp-` 文件 `mtime` 距今 60s 且 `max_age=3600` → 保留；`sessions_root` 不存在 → 返回全零摘要
    - **反向断言**：`hasattr(TmpFileSweeper, "is_expired") is False`、`hasattr(TmpFileSweeper, "start") is False`、`hasattr(TmpFileSweeper, "stop") is False`
    - _需求：10.1、2.补.1、2.补.2、2.补.8；正确性属性：Property 8_
  - [x] 2.10 新增 `SessionStoreConfig` + `LocalPersistenceConfig`
    - 创建 `epsilon-boot/src/infrastructure/persistence/local_file/config/__init__.py`
    - 创建 `epsilon-boot/src/infrastructure/persistence/local_file/config/backend_config.py`：定义 `SessionStoreBackendKind(str, Enum)`（`REDIS = "redis"`、`FILE = "file"`）与 `SessionStoreConfig(PropertiesBaseSettings)`（`env_prefix="SESSION_STORE_"`，字段 `backend: SessionStoreBackendKind = FILE`，`hot_reload: ClassVar[bool] = False`）；导出 `session_store_config = create_config(SessionStoreConfig)`
    - 创建 `epsilon-boot/src/infrastructure/persistence/local_file/config/local_persistence_config.py`：定义 `LocalPersistenceConfig(PropertiesBaseSettings)`（`env_prefix="LOCAL_PERSISTENCE_"`），字段：`root: str = "./.local_persistence"`、`create_if_missing: bool = True`、`fsync_on_write: bool = True`、`lock_acquire_timeout_ms: int = 5000`、`tmp_sweep_max_age_seconds: int = 3600`；`hot_reload: ClassVar[bool] = False`；导出 `local_persistence_config`
    - **禁止**定义 `session_ttl_seconds` / `reaper_interval_seconds` 字段（需求 2.补.5：Pydantic 严格模式下外部若注入此键将触发 `ValidationError`，避免静默降级）
    - **不定义** `EventStoreConfig` / `event_log_rotation`（本期不落地）
    - _需求：5.1、5.2、5.3、5.9、2.补.4、2.补.5、11.6、12.5_
  - [x] 2.11 配置类单元测试
    - 创建 `epsilon-boot/test/infrastructure/persistence/local_file/test_local_persistence_config_unit.py`
    - 用例：默认值等于 `./.local_persistence`、`fsync_on_write=True`、`tmp_sweep_max_age_seconds=3600` 等；通过 `monkeypatch.setenv("SESSION_STORE_BACKEND", "redis")` 覆盖后 `session_store_config.backend == REDIS`；非法取值（如 `"memory"`）抛 `ValidationError`
    - **反向断言**：通过 `monkeypatch.setenv("LOCAL_PERSISTENCE_SESSION_TTL_SECONDS", "3600")` 注入"已废弃"TTL 键后，`LocalPersistenceConfig()` 抛 `ValidationError` 或实例 `hasattr(..., "session_ttl_seconds") is False`（锁死需求 2.补.5）
    - _需求：10.1；对应需求 5.1 / 5.3 / 2.补.5_
  - [x] 2.12 实现 `LocalFileSessionContextAdapter`
    - 创建 `epsilon-boot/src/infrastructure/session/local_file_session_context_adapter.py`
    - 实现 `LocalFileSessionContextAdapter(SessionContextStorePort)`：构造参数 `(root: Path, lock_factory: LockFactory, path_policy: CrossPlatformPathPolicy, atomic_writer: TempFileAtomicWriter)`；`self._sessions_root = root / "sessions"`
    - **禁止**接收 `ttl_seconds` / `reaper` 参数（需求 2.补.1、2.补.6）；构造函数签名中不得出现这两个名字
    - 私有 `_resolve_path(session_id)`：`policy.hash_session_id` → `sessions_root / bucket / f"{stem}.json"` → `policy.check_absolute_path_length`
    - `async save(session_id, context)`：`json.dumps(ctx.to_dict(), ensure_ascii=False).encode("utf-8")`；`lock_path = path.with_suffix(".json.lock")`；`with lock_factory(lock_path).acquire(EXCLUSIVE)`: `writer.write_bytes_atomic(path, data)`；`OSError` 走 `logger.error("save 会话上下文失败 session_id=%s operation=save error_class=%s errno=%s", ...)` 后 raise
    - `async load(session_id) -> ConversationContext`：路径不存在返回空；**禁止**读 `stat().st_mtime` 或做任何"过期"判断（需求 2.补.2）；`SHARED` 锁读 `read_bytes()`；`json.loads` + `ConversationContext.from_dict`；`OSError` / `JSONDecodeError` / `KeyError` / `TypeError` 统一 `logger.error` + 返回空
    - `async delete(session_id)`：`path.unlink(missing_ok=True)`；`OSError` 走 `logger.error` + raise
    - 顶部文件级 docstring 说明"本地文件会话适配器，默认后端；本期无 TTL / 无过期回收（需求 2.补）"
    - _需求：1.1、1.2、1.3、1.4、1.5、1.6、1.7、2.补.1-2.补.3、9.1、9.2、9.4、12.1_
  - [x] 2.13 适配器单元测试
    - 创建 `epsilon-boot/test/infrastructure/session/__init__.py` 与 `test_local_file_session_context_adapter_unit.py`
    - 用例：`save` → `load` 往返等价；`load` 不存在返回空 `ConversationContext`；`load` 对损坏 JSON 返回空 + logger.error；`delete` 不存在幂等；**`save` 后将 `.json` 的 `mtime` 回拨到 1 天前 / 30 天前，`load` 仍返回原 `ConversationContext`**（锁死需求 2.补.2，会话无 TTL）；`save` 遇 `PermissionError` 抛出且日志含 `error_class=PermissionError`
    - _需求：10.1；正确性属性：Property 1、Property 2、Property 5、Property 8_
  - [x] 2.14 实现 `LocalPersistenceHealthCheckAdapter`
    - 创建 `epsilon-boot/src/infrastructure/health/local_persistence_health_check_adapter.py`
    - 实现 `LocalPersistenceHealthCheckAdapter(HealthCheckPort)`：构造参数 `(root: Path)`；`async check() -> HealthCheckResult`
    - 检查链：`is_dir` → `os.access(root, R_OK|W_OK)` → `tempfile.NamedTemporaryFile(dir=root, prefix=".health-", delete=True)` touch 写
    - 失败路径返回 `HealthCheckResult(name="local_persistence", status=DOWN, reason=...)`；成功返回 `status=UP`
    - `OSError` 时 `logger.warning(...)` 但仍转为 DOWN 结果返回（不向上抛）
    - _需求：6.3.5、9.1_
  - [x] 2.15 健康检查单元测试
    - 创建 `epsilon-boot/test/infrastructure/health/test_local_persistence_health_check_unit.py`
    - 用例：路径不存在 → DOWN；路径是文件 → DOWN；路径可读写 → UP；`os.access` mock 返回 False → DOWN reason 提及缺失权限；临时文件 touch 失败（monkeypatch `tempfile.NamedTemporaryFile` 抛 `OSError`）→ DOWN
    - _需求：10.1；对应需求 6.3.5_
  - [x] 2.16 启动期校验：`_validate_local_persistence_root`
    - 在 `epsilon-boot/src/application/container_config.py` 新增私有辅助函数 `_validate_local_persistence_root(cfg: LocalPersistenceConfig) -> Path`（模仿已有 `_create_local_filesystem_workspace` 的 7 步校验风格）
    - 步骤：1) cfg.root 显式置空 → `ConfigurationError("LOCAL_PERSISTENCE_ROOT 为空，服务拒绝启动")`；2) `Path(cfg.root).resolve()` 规范化；3) 不存在且 `create_if_missing=False` → `ConfigurationError`；4) 不存在且 `create_if_missing=True` → `mkdir(parents=True, exist_ok=True)`；5) `is_dir()` 否则 `ConfigurationError`；6) `os.access(root, R_OK|W_OK)`，缺失位在错误消息中列出；7) 调用 `CrossPlatformPathPolicy().check_absolute_path_length(root)` 提前拦截 Windows 长路径
    - 所有错误消息中文可读，使用 `ConfigurationError`
    - _需求：4.4、4.5、4.6、5.4、5.5、5.6、5.7、5.8_
  - [x] 2.17 启动期校验：workspace 冲突检测
    - 在 `_validate_local_persistence_root` 内或其调用点，拿到规范化后的 `lp_root` 后，读取 `workspace_config.root`（若非空且绝对路径），计算 `ws_root = Path(workspace_config.root).resolve()`
    - 若 `lp_root == ws_root` 或 `lp_root` 与 `ws_root` 之间存在父子包含关系（`lp_root.is_relative_to(ws_root)` 或 `ws_root.is_relative_to(lp_root)`），抛 `ConfigurationError("LOCAL_PERSISTENCE_ROOT 不得与 WORKSPACE_ROOT 共用或相互包含")`
    - workspace_config.root 为空串或未绝对化时跳过冲突检测（不会构造 Workspace，本期不冲突）
    - _需求：5.10_
  - [x] 2.18 启动期异步资源钩子
    - 在 `container_config.py` 新增模块级变量 `_local_persistence_root: Path | None = None`、`_atomic_writer: TempFileAtomicWriter | None = None`、`_path_policy: CrossPlatformPathPolicy | None = None`、`_lock_factory: LockFactory | None = None`
    - **禁止**新增 `_ttl_reaper` 模块级变量（需求 2.补.1）
    - 新增 `async def _init_local_persistence() -> None`：调用 `_validate_local_persistence_root(local_persistence_config)` 赋值 `_local_persistence_root`；构造 `_path_policy`、`_lock_factory(acquire_timeout_ms=local_persistence_config.lock_acquire_timeout_ms)`、`_atomic_writer(fsync_on_write=local_persistence_config.fsync_on_write)`；**同步**构造一次性 `TmpFileSweeper(sessions_root=_local_persistence_root/"sessions", max_age_seconds=local_persistence_config.tmp_sweep_max_age_seconds)` 并调用 `sweeper.sweep_once()` 清理 `*.tmp-*` 残留（不保存 sweeper 实例，启动期一次性消费）；`logger.info` 输出最终绝对路径
    - 新增 `async def _cleanup_local_persistence() -> None`：空实现，目录与工具无需主动清理
    - **禁止**新增 `_init_ttl_reaper` / `_cleanup_ttl_reaper` 协程（需求 2.补.1）；不注册任何 "ttl_reaper" 异步资源
    - _需求：3.2、6.5、6.6、2.补.1、2.补.8_
  - [x] 2.19 DI 装配：按后端动态注册异步资源
    - 修改 `configure_container()`：在 `register_async_resource("telemetry", ...)` 与 `("model_client", ...)` 之后，按 backend 分支：
      - 若 `session_store_config.backend == FILE`（含默认）：`register_async_resource("local_persistence", _init_local_persistence, _cleanup_local_persistence)`；**不**注册 `"redis"`；**不**注册 `"ttl_reaper"`（需求 2.补.1）
      - 若 `session_store_config.backend == REDIS`：维持既有 `register_async_resource("redis", _init_redis, _cleanup_redis)`；**不**注册 local_persistence
    - 保留 `"gateway"` 与 `"workspace"` 的注册（与本期无关）
    - _需求：6.1、6.2、7.4.1、7.4.3、2.补.1_
  - [x] 2.20 DI 装配：重写 `_create_session_store`
    - 在 `_create_session_store()` 中按 `session_store_config.backend` 分发：
      - `REDIS` → 现有 `RedisSessionContextAdapter(redis_client=_redis_client)` 路径
      - `FILE` → `LocalFileSessionContextAdapter(root=_local_persistence_root, lock_factory=_lock_factory, path_policy=_path_policy, atomic_writer=_atomic_writer)`
    - **禁止**向 `LocalFileSessionContextAdapter` 传入 `ttl_seconds` / `reaper` 等参数（构造函数已删除这些形参，传入会直接 `TypeError`）
    - 保持 `Scope.SINGLETON` 注册不变；调用方 `TaskAgentAdapter` / `ChatServiceAdapter` 零改动
    - _需求：6.1、6.4、6.6、2.补.1、2.补.6_
  - [x] 2.21 DI 装配：重写 `_create_readiness_aggregator`
    - 修改 `_create_readiness_aggregator()`：依据容器已注册异步资源动态组装 `checks` 列表
    - 在 `common/container.py` 的 `Container` 上新增方法 `has_async_resource(self, name: str) -> bool`：遍历 `self._async_resources` 返回 `any(e.name == name)`
    - `_create_readiness_aggregator` 伪代码：
      - `checks: list[HealthCheckPort] = []`
      - `if container.has_async_resource("redis"): checks.append(RedisHealthCheckAdapter(...))`
      - `if container.has_async_resource("database"): checks.append(MysqlHealthCheckAdapter(...))`（本期默认不装配）
      - `if container.has_async_resource("local_persistence"): checks.append(LocalPersistenceHealthCheckAdapter(root=_local_persistence_root))`
      - `return ReadinessAggregator(checks=checks)`
    - _需求：6.3.1、6.3.2、6.3.3、6.3.4、6.3.5、6.3.6_
  - [x] 2.22 Container.has_async_resource 单元测试
    - 在 `epsilon-boot/test/common/container_test.py` 追加用例：未注册的资源名返回 False；注册后返回 True
    - _需求：10.1；对应需求 6.3.3_
  - [x] 2.23 集成测试：启动期校验
    - 创建 `epsilon-boot/test/integration/test_local_persistence_startup_validation.py`
    - 用例：`LOCAL_PERSISTENCE_ROOT=""` 抛 `ConfigurationError` 含中文"为空"；`CREATE_IF_MISSING=false` + 不存在路径抛 `ConfigurationError`；指向文件（非目录）抛 `ConfigurationError`；`LOCAL_PERSISTENCE_ROOT == WORKSPACE_ROOT` 抛 `ConfigurationError` 含"共用或相互包含"；`LOCAL_PERSISTENCE_ROOT` 位于 `WORKSPACE_ROOT` 之下抛同样异常
    - 使用 `monkeypatch.setenv` 覆盖配置；不真实启动 FastAPI，直接调用 `_validate_local_persistence_root`
    - _需求：10.1；对应需求 5.4-5.10_
  - [x] 2.24 集成测试：DI 装配与 ReadinessAggregator 动态组装
    - 创建 `epsilon-boot/test/application/test_container_config_backend_dispatch.py`
    - 用例 (a)：`SESSION_STORE_BACKEND=redis` → `container.resolve(SessionContextStorePort)` 是 `RedisSessionContextAdapter`，`_create_readiness_aggregator().checks` 类型集合 == `{RedisHealthCheckAdapter}`（**不**含 MysqlHealthCheckAdapter，因为本期 database 默认不注册）
    - 用例 (b)：`SESSION_STORE_BACKEND=file`（默认）→ 是 `LocalFileSessionContextAdapter`，`checks` 类型集合 == `{LocalPersistenceHealthCheckAdapter}`
    - 断言精确类型集合（防止回归把 Redis/Mysql 健康检查误注册回来）
    - 使用本地临时 `LOCAL_PERSISTENCE_ROOT=tmp_path` 避免污染仓库
    - _需求：10.1；对应需求 6.1、6.3.1-6.3.7、7.1_

- [x] 3. 阶段 3：测试（property + 多进程 + Windows 特性）
  - [x] 3.1 Property-based 测试：会话读写幂等
    - 创建 `epsilon-boot/test/infrastructure/persistence/local_file/test_local_file_session_property.py`
    - 用 `hypothesis` 策略生成 `session_id: st.text(min_size=1, max_size=64)`（允许 Unicode / NUL / 保留字，哈希后自然规避）+ 生成 `ConversationContext`（可序列化字段 ≤ 10KB）
    - 断言：`save(id, ctx); loaded = load(id); loaded.to_dict() == ctx.to_dict()`
    - _需求：10.2；正确性属性：Property 1_
  - [x] 3.2 Property-based 测试：delete 幂等 + load 空返回 + 无 TTL 回归
    - 追加到上文 property 测试文件
    - 断言：`delete(id); delete(id)` 无异常；`load(id)` 返回空 `ConversationContext`
    - 新增断言：对任意 `save(id, ctx)` 后通过 `os.utime(path, (past, past))` 把 mtime 回拨到 `now - 86400` / `now - 30*86400`，`load(id).to_dict() == ctx.to_dict()` 仍成立（需求 2.补.2 反向锁死）
    - _需求：10.2；正确性属性：Property 2、Property 8_
  - [x] 3.3 Property-based 测试：路径策略拒绝
    - 创建 `epsilon-boot/test/infrastructure/persistence/local_file/test_path_policy_property.py`
    - 策略：从 `{"CON","PRN","AUX","NUL","COM1"..."COM9","LPT1"..."LPT9"}` + `st.text(alphabet="\x00/\\:*?\"<>|")` 派生 name
    - 断言：`policy.check_dirname(name)` 一律抛 `PathPolicyViolation`；用 `policy.hash_session_id(s)` 后的结果通过 `check_dirname` 不抛
    - _需求：10.2；正确性属性：Property 7_
  - [x] 3.4 多进程并发测试：同 session_id 写收敛
    - 创建 `epsilon-boot/test/integration/test_multiprocess_concurrency.py`
    - 用例 A：N=8 个 `multiprocessing.Process` 对同一 `session_id` 并发 `save` 不同 payload（payload 用 `pid` 作为区分字段）；主进程 `load` 必须返回合法 `ConversationContext` 且其"区分字段"等于 8 个中某一个
    - 用例 B：保留给未来多会话扩展（本期可选）
    - 注意：在 Windows 下 `multiprocessing.get_context("spawn")` 显式指定以保持跨平台一致
    - _需求：10.3；正确性属性：Property 6_
  - [x] 3.5 Windows 平台特性测试
    - 创建 `epsilon-boot/test/infrastructure/persistence/local_file/test_windows_specific_unit.py`
    - 所有用例 `@pytest.mark.skipif(os.name != "nt")` 或通过 `monkeypatch.setattr(os, "name", "nt")` 驱动：`check_absolute_path_length` 对 261 字符路径抛异常；Windows 保留名 `CON.txt` 被拒；非法字符 `a:b.json` 被拒
    - _需求：10.1；对应需求 4.3、4.4、4.5_
  - [x] 3.6 校验检查点：全量测试通过
    - 在 `epsilon-boot/` 下执行 `uv run pytest -m "not benchmark"`，要求全绿
    - 在 Windows runner 上通过阶段 4 的 CI 完成最终跨平台验证（本任务先保证 Linux 通过）
    - _需求：10.5、10.7_

- [x] 4. 阶段 4：CI 与文档
  - [x] 4.1 CI 增加 `windows-latest` 矩阵
    - 修改 `/workspace/.github/workflows/ci.yml` 的 `strategy.matrix.os` 为 `[ubuntu-latest, windows-latest]`，`fail-fast: false`
    - 为 windows-latest 显式设置 `shell: bash`（uv 安装支持）以减少跨平台语法差异
    - `uv run pytest -m "not benchmark"` 默认不跑基准（需求 10.6）
    - _需求：10.4、10.6_
  - [x] 4.2 新增 `docs/operations/runtime-backends.md`
    - 创建 `docs/operations/runtime-backends.md`
    - 开头醒目说明：**本期起默认会话后端已从 `redis` 切换为 `file`**；**本期移除了领域事件基础设施** (`EventBusPort` / `EventStorePort` / `DomainEvent`)；相应 MySQL `event_records` / `event_handler_results` 表不再使用（建议运维手动 `DROP TABLE IF EXISTS`，见备注 2）
    - 章节：两种后端组合（`file` / `redis`）；切换到 `file` 的注意事项（单主机、禁 NFS/SMB/OSS FUSE、禁多容器 volume 共享）；从 `file` 切回 `redis` 的一行 `config.properties` 改动示例；健康检查差异表格；性能特征（fsync 开/关 p99 阈值）；数据位置与清理；升级指南（既有 Redis 部署保持现状需显式 `SESSION_STORE_BACKEND=redis`）
    - **不出现** `EVENT_STORE_BACKEND` 键（需求 11.7）
    - _需求：11.1、11.7_
  - [x] 4.3 更新 `epsilon-boot/README.md`
    - 在"快速开始"章节追加一段：`uv sync` + `uv run uvicorn src.application.server_app:app`（或现行启动命令）零配置即可工作；不再要求本地拉起 Redis / MySQL；link 到 `docs/operations/runtime-backends.md`
    - 移除/更新任何涉及"领域事件 / event bus / event store"的旧章节，改为"当前版本未内置领域事件基础设施；如有需求请提交新 feature"
    - _需求：11.8、8.5_
  - [x] 4.4 清理项目内 docs 中的事件总线遗留描述
    - `grep -rE "DomainEvent|EventBusPort|EventStorePort|event_records|event_handler_results" docs/operations/runtime-backends.md epsilon-boot/README.md` 找到所有残留引用并按"当前版本未内置领域事件基础设施"模板替换
    - _需求：8.5、8.8_
  - [ ] 4.5 性能基准脚本（可选落地；本期跳过，详见 review-log）
    - [ ]* 4.5.1 创建 `epsilon-boot/test/benchmarks/__init__.py` 与 `bench_local_file.py`
      - 实现 `save` 在单上下文 ≤ 10MB 规模下的 p50/p95/p99 测算；支持 `--fsync/--no-fsync` 开关；输出 JSON 摘要
      - 使用 `pytest.mark.benchmark`，CI 默认不跑（需求 10.6）
      - 若系统上能启动 Redis（通过 docker compose 或 `REDIS_HOST` 可连），附带对比 Redis 后端的 p99
      - _需求：7.1、7.2、7.4_

- [x] 5. 阶段 5：验证与验收
  - [x] 5.1 回归门槛：grep 零残留
    - 在 `epsilon-boot/` 下执行：`grep -rE "DomainEvent|EventBusPort|EventStorePort|publish.*event|InMemoryEventBusAdapter|DatabaseEventStoreAdapter" src test` → 0 行（允许 `docs/` 与本 tasks.md 出现）
    - 在 `epsilon-boot/` 下执行：`uv run python -c "import importlib; [importlib.import_module(m) for m in ['common.events', 'common.event_bus', 'infrastructure.event_bus.in_memory_event_bus_adapter']]"` 期望 exit code 非 0 且 stderr 含 `ModuleNotFoundError`
    - 把该命令加入 `test/integration/test_domain_event_decommission_gate.py`，作为 pytest 用例常驻
    - _需求：8.8、10.8_
  - [x] 5.2 逐条对照 requirement.md 验收
    - 建立一张表格（作为 PR 描述的一部分）对照需求 1-12 每条 EARS 验收标准 → 对应 tasks 编号 + 对应测试文件
    - 对无法自动化的（文档类如需求 5.11 配置注释、需求 11.3 docstring 中文约定）由 reviewer 人工走查
    - _需求：全部_
  - [x] 5.3 跨平台 CI 必过
    - 确认 `.github/workflows/ci.yml` 在 `ubuntu-latest` + `windows-latest` 两个 runner 上均通过（阶段 3.6 + 阶段 4.1）
    - _需求：10.4、10.5_
  - [x] 5.4 最终烟测：零配置冷启动
    - 在干净的 `epsilon-boot/` 目录下执行 `uv sync --frozen` + `uv run uvicorn src.application.server_app:app --port 7777`（或现行启动命令），不预先创建 `./.local_persistence`，期望：启动成功且日志显示 `_local_persistence_root` 的绝对路径、`TmpFileSweeper 扫描完成 scanned=0 deleted=0 errored=0`（冷启动无残留）、**日志中不得出现 "TtlReaper" 关键字**、`/health/ready` 响应 200 且 `checks` 仅含 `local_persistence`
    - 发送一次 `/v1/chat/completions`（或当前的聊天入口）触发 `save` + `load` 链路，`./.local_persistence/sessions/<bucket>/<stem>.json` 文件存在
    - _需求：5.2、5.4、5.6、6.1、6.2、6.3、9.5_

## 备注

1. **design.md 与 requirement/用户指示的口径差异**：`design.md` 同时保留了"本地文件会话存储"和"本地文件事件存储"两条链路；但 `requirement.md` 的需求 8（`Domain_Event_Decommission`）与本 tasker 接收的用户明确指示都是"**移除**领域事件基础设施，不做本地文件事件存储"。因此本 tasks.md **不**落地 `LocalFileEventStoreAdapter`、`AppendOnlyEventLog`、`FileBackedIdAllocator`、`EventStoreConfig`、`LOCAL_PERSISTENCE_EVENT_LOG_ROTATION` 等 design.md 涉及"事件本地文件实现"的组件。`design.md` 仍可作为这些组件的历史设计参考，未来若有新 feature 引入领域事件可复用其技术方案。

2. **Alembic 迁移缺失处理**：`epsilon-boot/src/infrastructure/database/` 下未发现 Alembic `migrations/` 目录（`glob` 为空），项目当前通过 ORM 模型即席建表或由运维 SQL 管理。因此需求 8.4"新增 drop_table 迁移脚本"在本期**降级为文档化**：在 `docs/operations/runtime-backends.md` 与 README 升级指南中显式提供一段 SQL（`DROP TABLE IF EXISTS event_records; DROP TABLE IF EXISTS event_handler_results;`），由运维在升级时手动执行。这是本 tasker 对 design.md 开放问题 6 的务实回应。如未来项目引入 Alembic，再补一份正式迁移脚本。

3. **开放问题确认**（与 design.md 开放问题回应清单对齐）：
   - ~~Ttl_Reaper 落点~~：**已消解（需求 2.补）**：本期**不引入** TTL / 后台 Reaper；仅保留 `TmpFileSweeper`，在 `_init_local_persistence` 启动期同步跑一次，仅清理 `*.tmp-*` 残留，跑完即丢弃实例（任务 2.8、2.18）。
   - ReadinessAggregator 动态组装：在 `configure_container()` 按 backend 决定是否 `register_async_resource`，`_create_readiness_aggregator` 通过新增的 `Container.has_async_resource(name)` 查询（任务 2.21、2.22）。
   - 不暴露 `get_adapter_metrics()` 端点（与既有 `/health/ready` 风格保持一致）。
   - `SessionContextStorePort` 未来扩展（`list_sessions`/`touch`）本期不实现，Adapter 预留 `_resolve_path` 私有方法支持未来扩展零侵入。

4. **未纳入本期的 Out-of-Scope 项再明确**（与需求"不在本期范围"完全对齐）：无跨主机分布式一致性；无 Redis ↔ file 双写；无历史会话迁移工具；无落盘加密；前端零改动；`common/tools/common_tools.py` 不改；不为事件存储做任何本地文件实现；不做 NDJSON 归档导出。

5. **Open Question 待用户确认**（用户回复"全部接受" → Q1/Q3 已 resolved；Q2 已因需求 2.补 自然消解）：
   - **Q1（resolved, accepted）**：备注 2 中"以文档化 SQL 片段代替 Alembic 迁移脚本"已按用户确认接受。运维升级指南会在 `docs/operations/runtime-backends.md` 与 README 中附 `DROP TABLE IF EXISTS event_records; DROP TABLE IF EXISTS event_handler_results;` SQL 片段。
   - **~~Q2~~（dissolved by 需求 2.补）**：原 Q2 讨论"load 过期惰性删除是否触发写 I/O"。**随需求 2.补 引入会话无 TTL，`load` 路径已完全不读 mtime、不做过期判断、不 unlink，该问题不再存在**。`TmpFileSweeper` 只在启动期动一次，且仅针对 `.tmp-*` 半写残留，不涉及 `.json` 运行期 unlink。
   - **Q3（resolved, accepted）**：用户确认 `SessionProviderPort` 随 MySQL 默认装配一并移除；任务 1.5 按此执行；落地前会再次 grep 验证生产代码无残留引用（任务 1.7）。
