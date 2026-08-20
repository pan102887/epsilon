# 实现计划：TUI Session Resume

## 概述

本计划按 `design.md` 的分层设计拆分：先补齐领域值对象和 Port，再实现本地文件 / Redis 会话索引与存在性探测，随后接入 approval 摘要、ChatService 索引同步、CLI runtime 与 slash 命令，最后完成容器装配、配置和回归验证。实现必须遵循 `docs/steering/ddd-architecture.md` 的依赖方向，新增公开模块、类、方法使用中文 docstring，测试命令在 `epsilon-boot/` 下通过 `uv run --frozen pytest ...` 执行。

## Tasks

- [x] 1.1 新增会话元数据值对象
  - 在 `epsilon-boot/src/domain/chat/value_objects.py` 中新增 `@dataclass(frozen=True, slots=True) class SessionMetadata`
  - 字段：`session_id: str`、`updated_at_epoch_ms: int`、`message_count: int`、`preview: str`、`created_at_epoch_ms: int | None = None`、`model: str | None = None`
  - 增加 `__post_init__()` 校验：`session_id` 非空、时间戳为非负整数、`message_count >= 0`、`preview` 非空；保持领域层只依赖标准库
  - _需求: 3.2, 5.1, 5.3, 5.4, 9.1, 9.5_

- [x] 1.2 扩展聊天领域 Port
  - 在 `epsilon-boot/src/domain/chat/ports.py` 中为 `SessionContextStorePort` 增加 `async def exists(self, session_id: str) -> bool`
  - 在同文件新增 `class SessionIndexPort(Protocol)`，包含 `upsert(metadata: SessionMetadata) -> None`、`get(session_id: str) -> SessionMetadata | None`、`list_recent(limit: int = 20) -> list[SessionMetadata]`、`delete(session_id: str) -> None`
  - 在 `TYPE_CHECKING` 或运行时导入路径中按现有风格引用 `SessionMetadata`，避免循环依赖
  - _需求: 2.4, 5.1, 5.2, 5.5, 6.5, 9.1_

- [x] 1.3 编写聊天领域契约测试
  - 在 `epsilon-boot/test/domain/chat/test_session_metadata_unit.py` 中创建值对象测试
  - 覆盖合法构造、空 `session_id`、负 `message_count`、空 `preview`、负时间戳
  - 在 `epsilon-boot/test/domain/chat/test_hitl_chat_ports_unit.py` 或新增 `test_session_index_ports_unit.py` 中覆盖 `SessionIndexPort` 与 `SessionContextStorePort.exists` 的 Protocol 签名静态可用性
  - **验证: 需求 3.2, 5.1, 5.5, 9.1, 9.5**

- [x] 1.4 新增 approval 摘要值对象与只读查询 Port
  - 在 `epsilon-boot/src/domain/agent/value_objects.py` 中新增 `@dataclass(frozen=True, slots=True) class ApprovalInterruptSummary`
  - 字段：`session_id: str`、`approval_id: str`、`action_count: int`、`created_at_epoch: float`、`expires_at_epoch: float`、`expired: bool`、`tool_names: tuple[str, ...] = ()`
  - 在 `epsilon-boot/src/domain/agent/ports.py` 的 `ApprovalStateStorePort` 增加 `async def list_pending_by_session(self, session_id: str) -> list[ApprovalInterruptSummary]`
  - 该方法语义为只读摘要，不 consume、不 delete 未过期 approval
  - _需求: 2.5, 2.6, 7.3, 7.4, 9.1, 9.5_

- [x] 1.5 编写 approval 领域契约测试
  - 在 `epsilon-boot/test/domain/agent/test_approval_value_objects_unit.py` 中增加 `ApprovalInterruptSummary` 构造测试
  - 在 `epsilon-boot/test/domain/agent/test_approval_ports_unit.py` 中覆盖 `list_pending_by_session(session_id)` Protocol 签名
  - **验证: 需求 2.5, 2.6, 7.3, 7.4, 9.1**

- [x] 2.1 实现会话上下文存在性探测
  - 在 `epsilon-boot/src/infrastructure/session/local_file_session_context_adapter.py` 中实现 `async def exists(self, session_id: str) -> bool`，通过 `_resolve_path(session_id).exists()` 判断 JSON 文件存在，不读取正文
  - 在 `epsilon-boot/src/infrastructure/session/redis_session_context_adapter.py` 中实现 `async def exists(self, session_id: str) -> bool`，通过 `await self._redis.exists(self._make_key(session_id)) > 0` 判断，不刷新 TTL
  - 保持 `OSError` / `RedisError` 的日志与向上传播风格
  - _需求: 2.4, 5.5, 6.2, 6.4, 10.3, 10.7_

- [x] 2.2 编写会话上下文 exists 测试
  - 在 `epsilon-boot/test/infrastructure/session/test_local_file_session_context_adapter_unit.py` 中覆盖 save 前 false、save 后 true、delete 后 false
  - 在 `epsilon-boot/test/infrastructure/session/test_redis_session_context_adapter_unit.py` 或既有 Redis session 测试中覆盖 Redis `exists` 返回 0/1 的行为
  - **验证: 需求 2.4, 5.5, 6.2, 6.4, 10.3, 10.7**

- [x] 2.3 实现本地文件会话索引 Adapter
  - 创建 `epsilon-boot/src/infrastructure/session/local_file_session_index_adapter.py`
  - 实现 `class LocalFileSessionIndexAdapter(SessionIndexPort)`，构造参数为 `root: Path`、`lock_factory: Callable[[Path], CrossPlatformFileLock]`、`path_policy: CrossPlatformPathPolicy`、`atomic_writer: TempFileAtomicWriter`
  - 使用布局 `<root>/session_index/<bucket>/<stem>.json` 与 `<root>/session_index/<bucket>/<stem>.json.lock`
  - 实现 `upsert/get/list_recent/delete`；`upsert` 使用 EXCLUSIVE 锁 + `TempFileAtomicWriter.write_bytes_atomic()`，`get` 使用 SHARED 锁，`list_recent(limit)` 扫描索引 JSON、跳过损坏文件并按 `updated_at_epoch_ms` 倒序截断
  - _需求: 3.1, 3.2, 3.3, 3.4, 5.1, 5.3, 5.4, 6.1, 6.5, 10.6_

- [x] 2.4 编写本地文件会话索引测试
  - 创建 `epsilon-boot/test/infrastructure/session/test_local_file_session_index_adapter_unit.py`
  - 覆盖 `upsert/get/list_recent/delete`、同一 session 二次 upsert 覆盖字段、按更新时间倒序、`limit` 截断、损坏 JSON 被跳过、delete 幂等
  - **验证: 需求 3.1, 3.2, 3.3, 3.4, 5.3, 5.4, 6.1, 10.6**

- [x] 2.5 新增 Redis 会话 TTL 配置
  - 创建 `epsilon-boot/src/infrastructure/session/session_ttl_config.py`
  - 定义 `class SessionRedisTtlConfig(PropertiesBaseSettings)`，`model_config = SettingsConfigDict(env_prefix="SESSION_REDIS_")`，字段 `ttl_seconds: int = 3600`，`hot_reload: ClassVar[bool] = False`
  - 在 `epsilon-boot/config.properties` 增加 `SESSION_REDIS_TTL_SECONDS=3600` 及中文注释
  - 后续 `_create_session_store()` 与 `_create_session_index()` 必须共用该 TTL
  - _需求: 6.2, 6.3, 6.5, 9.2, 9.6_

- [x] 2.6 编写 Redis TTL 配置测试
  - 创建 `epsilon-boot/test/infrastructure/session/test_session_ttl_config_unit.py`
  - 覆盖 `SessionRedisTtlConfig` 默认值、`SESSION_REDIS_TTL_SECONDS` 覆盖解析、非法值校验或 settings 框架既有失败语义
  - 后续容器测试再覆盖 `_create_session_store()` 与 `_create_session_index()` 共用 TTL，本任务只覆盖配置对象本身
  - **验证: 需求 6.2, 6.3, 6.5, 9.6**

- [x] 2.7 检查点 — 领域、本地会话与 TTL 基础契约
  - 使用项目自身的测试命令验证；如有问题请向用户确认
  - 在 `epsilon-boot/` 运行 `uv run --frozen pytest test/domain/chat test/domain/agent test/infrastructure/session/test_local_file_session_context_adapter_unit.py test/infrastructure/session/test_local_file_session_index_adapter_unit.py test/infrastructure/session/test_session_ttl_config_unit.py`
  - 运行项目中的全部测试用例，并要求全部通过：`uv run --frozen pytest`

- [x] 2.8 实现 Redis 会话索引 Adapter
  - 创建 `epsilon-boot/src/infrastructure/session/redis_session_index_adapter.py`
  - 实现 `class RedisSessionIndexAdapter(SessionIndexPort)`，构造参数为 `redis_client: aioredis.Redis`、`key_prefix: str = "session:index:"`、`recent_zset_key: str = "session:index:recent"`、`ttl_seconds: int = 3600`
  - `upsert()` 使用 pipeline 执行 `SET session:index:<id> <json> EX ttl_seconds` 与 `ZADD session:index:recent {updated_at_epoch_ms: session_id}`
  - `get()` 读取 JSON 并反序列化为 `SessionMetadata`；反序列化失败记录日志并返回 `None`
  - `list_recent()` 使用 `ZREVRANGE` 取最近 ID，批量读取 metadata，metadata 缺失时 `ZREM` stale member
  - `delete()` 删除 metadata key 并 `ZREM`
  - _需求: 3.1, 3.2, 3.3, 3.4, 5.1, 5.3, 5.4, 6.2, 6.3, 6.5, 10.7_

- [x] 2.9 编写 Redis 会话索引测试
  - 创建 `epsilon-boot/test/infrastructure/session/test_redis_session_index_adapter_unit.py`
  - 使用项目既有 Redis fake / mock 风格覆盖 `SET EX`、`ZADD`、`ZREVRANGE`、metadata 缺失时 `ZREM`、`delete` 幂等
  - 覆盖 adapter 构造时接收的 `ttl_seconds` 会传给 `SET ... EX`；context/index 共用 TTL 的组合根行为由容器测试覆盖
  - **验证: 需求 3.1, 3.2, 5.3, 5.4, 6.2, 6.3, 6.4, 6.5, 10.7**

- [x] 3.1 实现 approval store 按会话列出摘要
  - 在 `epsilon-boot/src/infrastructure/agent/approval_state_store.py` 中为 `LocalFileApprovalStateStore` 实现 `list_pending_by_session(session_id)`
  - 扫描 `<root>/approvals/<bucket>/<stem>/*.json`，通过既有 `approval_interrupt_from_dict` 反序列化，过滤过期项；过期文件可顺手 `unlink(missing_ok=True)`，未过期项不得删除
  - 为 `RedisApprovalStateStore` 实现同名方法，使用 `scan_iter` 匹配现有 approval key 前缀，读取、反序列化、过滤过期项；未过期项不得 consume
  - 摘要转换为 `ApprovalInterruptSummary`，填充 `approval_id`、`action_count`、`tool_names`、`created_at_epoch`、`expires_at_epoch`、`expired`
  - _需求: 2.5, 2.6, 7.3, 7.4, 9.1, 10.8_

- [x] 3.2 编写 approval store 摘要查询测试
  - 在 `epsilon-boot/test/infrastructure/agent/test_local_file_approval_state_store_unit.py` 中覆盖未过期摘要、过期过滤、不 consume、不同 session 隔离
  - 在 `epsilon-boot/test/infrastructure/agent/test_redis_approval_state_store_unit.py` 中覆盖 Redis 版本同等行为
  - **验证: 需求 2.5, 2.6, 7.3, 7.4, 10.8**

- [x] 3.3 将会话索引同步接入 ChatServiceAdapter
  - 修改 `epsilon-boot/src/infrastructure/chat/chat_service_adapter.py`
  - 构造函数新增可选参数 `session_index: SessionIndexPort | None = None`，保存为 `self._session_index`
  - 新增私有方法 `_save_context_and_index(session_id: str, context: ConversationContext, *, model: str | None = None) -> None`
  - 该方法先调用 `self._session_store.save(session_id, context)`；若 `self._session_index` 非空，则根据 `ConversationContext.get_messages()` 生成 `SessionMetadata` 并调用 `upsert`
  - preview 规则：逆序取最后一条非 system 且非空内容，压缩空白，截断 120 字符；无内容为 `"(空会话)"`
  - _需求: 3.2, 3.4, 5.3, 5.4, 6.5, 9.2, 9.3, 9.5, 10.6_

- [x] 3.4 替换 ChatServiceAdapter 保存与删除路径
  - 在 `epsilon-boot/src/infrastructure/chat/chat_service_adapter.py` 中把所有直接 `self._session_store.save(...)` 调用改为 `_save_context_and_index(...)`
  - 覆盖同步 chat、stream_chat、stream_chat_events、continue/resume/分段保存路径；不得改变原有上下文内容保存语义
  - 修改 `clear_session()`：在删除 context、approval 后调用 `self._session_index.delete(session_id)`，保持幂等和异常传播
  - _需求: 1.2, 4.2, 4.3, 4.4, 5.3, 5.4, 7.2, 9.4_

- [x] 3.5 编写 ChatServiceAdapter 索引同步测试
  - 在 `epsilon-boot/test/infrastructure/chat/test_chat_service_adapter_unit.py` 或新增 `test_chat_service_session_index_unit.py` 中覆盖保存成功后 `session_index.upsert()` 的 metadata 字段
  - 覆盖 preview 生成：跳过 system、压缩换行空白、截断 120 字符、空上下文 preview 为 `"(空会话)"`
  - 覆盖 session store 保存失败时不 upsert，index upsert 失败时异常向上传播且 context 保存已发生
  - 覆盖 `clear_session()` 删除 context、approval、index 的调用顺序和缺少 `session_index` 时的兼容行为
  - **验证: 需求 1.2, 4.2, 4.3, 4.4, 5.3, 5.4, 7.2, 10.5, 10.6**

- [x] 4.1 扩展 CliRuntime 会话恢复 facade
  - 修改 `epsilon-boot/src/application/cli/runtime.py`
  - 新增 `@dataclass(frozen=True) class ResumeSessionResult`，字段 `found: bool`、`metadata: SessionMetadata | None = None`、`approval_summaries: list[ApprovalInterruptSummary] | None = None`、`missing_reason: str | None = None`
  - `CliRuntime.__init__` 新增属性 `session_store: SessionContextStorePort | None`、`session_index: SessionIndexPort | None`、`approval_store: ApprovalStateStorePort | None`
  - `start()` 解析 `SessionContextStorePort`、`SessionIndexPort`、可选 `ApprovalStateStorePort`
  - 新增 `_require_session_store()`、`_require_session_index()` helper
  - _需求: 2.1, 2.2, 2.3, 5.2, 5.5, 7.3, 9.2, 9.3, 10.8_

- [x] 4.2 实现 CliRuntime 会话列表、恢复与显式删除
  - 在 `epsilon-boot/src/application/cli/runtime.py` 中实现 `async def list_sessions(self, limit: int = 20) -> list[SessionMetadata]`
  - 实现 `async def resume_session(self, session_id: str) -> ResumeSessionResult`：`index.get()` 缺失返回 `found=False`；metadata 存在时调用 `session_store.exists()`；不存在则 `index.delete()` 并返回 `missing_reason="expired_or_missing"`；存在则调用 `approval_store.list_pending_by_session()`（若已注入）并返回成功结果
  - 实现 `async def delete_session(self, session_id: str) -> bool`：先用 index 或 `session_store.exists()` 判断删除前存在性，再委托 `ChatServicePort.clear_session(session_id)`；返回删除前是否存在
  - 不在 runtime 中直接 new infrastructure adapter
  - _需求: 2.1, 2.2, 2.3, 2.4, 4.2, 4.3, 4.4, 4.6, 5.5, 6.4, 7.3, 7.4, 9.3_

- [x] 4.3 编写 CliRuntime 会话 facade 测试
  - 修改 `epsilon-boot/test/application/cli/test_runtime.py`
  - 覆盖 `start()` 解析并保存 `SessionContextStorePort`、`SessionIndexPort`、可选 `ApprovalStateStorePort`
  - 覆盖 `list_sessions()` 委托 `SessionIndexPort.list_recent()`
  - 覆盖 `resume_session()` 的 index missing、context missing 且清理 stale index、context exists 且 approval store absent/present、已索引空会话成功
  - 覆盖 `delete_session()` 返回删除前存在性并只委托 `ChatServicePort.clear_session()`
  - **验证: 需求 2.1, 2.2, 2.3, 2.4, 4.2, 4.3, 4.4, 4.6, 5.5, 6.4, 7.3, 7.4, 10.2, 10.3, 10.5, 10.8**

- [x] 4.4 检查点 — Redis、approval、ChatService 与 runtime facade
  - 使用项目自身的测试命令验证；如有问题请向用户确认
  - 在 `epsilon-boot/` 运行 `uv run --frozen pytest test/infrastructure/session test/infrastructure/agent test/infrastructure/chat test/application/cli/test_runtime.py`
  - 运行项目中的全部测试用例，并要求全部通过：`uv run --frozen pytest`

- [x] 4.5 更新 SlashCommandRouter 会话命令
  - 修改 `epsilon-boot/src/application/cli/commands.py`
  - 在 `HELP_TEXT` 中增加 `/sessions`、`/resume <session_id>`、`/delete! <session_id>`；保持既有 `/model`、`/config doctor`、`/run ...`、`/runs`、`/quit`
  - 定义 `RESUME_USAGE`、`DELETE_USAGE`、`DELETE_EXPLICIT_HINT`、`SESSION_MISSING_MESSAGE`、`NO_SESSIONS_MESSAGE`
  - `/new` 只调用 `state.reset_session()` 并返回新 ID，不调用 `runtime.clear_session()` 或 `runtime.delete_session()`
  - 实现 `/sessions`、`/resume <session_id>`、`/delete <session_id>` 提示、`/delete! <session_id>` 删除；删除当前 session 后调用 `state.reset_session()` 并提示新 ID
  - _需求: 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 4.1, 4.5, 4.6, 8.1, 8.2, 8.3, 8.4, 8.5_

- [x] 4.6 编写 SlashCommandRouter 会话命令测试
  - 修改 `epsilon-boot/test/application/cli/test_commands.py`
  - 更新既有 `/new` 测试：断言 fake runtime 未记录 clear/delete 调用
  - 覆盖 `/sessions` 空列表、多项列表格式和排序显示
  - 覆盖 `/resume` 缺参、success 设置 `state.session_id`、missing 不改 state、approval summary 文本
  - 覆盖 `/delete` 未带 `!` 只提示、`/delete!` 缺参、missing、删除非当前、删除当前后 reset
  - 覆盖 `/help` 包含新命令，未知命令不进入模型调用
  - **验证: 需求 1.1, 1.2, 1.3, 1.4, 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 4.1, 4.5, 4.6, 8.1, 8.2, 8.3, 8.4, 8.5, 10.1, 10.2, 10.3, 10.4, 10.5**

- [x] 4.7 更新 Textual TUI 命令回归测试
  - 修改 `epsilon-boot/test/application/cli/test_tui_textual.py`
  - 覆盖新增 slash 命令由 `SlashCommandRouter` 处理，不调用 `stream_main_agent_events`
  - 覆盖 `/new` 后旧会话可通过 fake runtime `/resume` 恢复的 UI 路径
  - 保持 Ctrl+C、Run watch、既有 slash 命令测试不回退
  - **验证: 需求 1.1, 1.2, 2.1, 7.4, 8.4, 8.5, 10.8**

- [x] 4.8 检查点 — CLI 命令行为
  - 使用项目自身的测试命令验证；如有问题请向用户确认
  - 在 `epsilon-boot/` 运行 `uv run --frozen pytest test/application/cli`
  - 运行项目中的全部测试用例，并要求全部通过：`uv run --frozen pytest`

- [x] 5.1 装配 SessionIndexPort 与 Redis TTL
  - 修改 `epsilon-boot/src/application/container_config.py`
  - 导入并注册 `SessionIndexPort`，新增 `_create_session_index() -> SessionIndexPort`
  - `SESSION_STORE_BACKEND=file` 时返回 `LocalFileSessionIndexAdapter(root=_local_persistence_root, lock_factory=_lock_factory, path_policy=_path_policy, atomic_writer=_atomic_writer)`
  - `SESSION_STORE_BACKEND=redis` 时返回 `RedisSessionIndexAdapter(redis_client=_redis_client, ttl_seconds=session_redis_ttl_config.ttl_seconds)`
  - 修改 `_create_session_store()` Redis 分支，向 `RedisSessionContextAdapter` 传入同一个 `session_redis_ttl_config.ttl_seconds`
  - _需求: 5.2, 6.2, 6.3, 6.5, 9.2, 9.6_

- [x] 5.2 将 SessionIndexPort 注入 ChatServiceAdapter
  - 修改 `epsilon-boot/src/application/container_config.py` 中创建 `ChatServicePort` 的工厂
  - 解析 `SessionIndexPort` 并传入 `ChatServiceAdapter(session_index=session_index, ...)`
  - 保持既有 `SessionContextStorePort`、`ApprovalStateStorePort`、`ModelRegistryPort`、`PromptRegistryPort`、`ContextBuilderPort`、`AgentPort` 注入顺序和生命周期不变
  - _需求: 5.2, 5.3, 5.4, 7.2, 9.2, 9.3_

- [x] 5.3 编写容器装配与配置测试
  - 修改 `epsilon-boot/test/application/test_container_config_backend_dispatch.py` 或 `test/application/test_run_container_wiring_unit.py`
  - 覆盖 `SESSION_STORE_BACKEND=file` 时 `SessionIndexPort` 解析为本地文件 Adapter
  - 覆盖 `SESSION_STORE_BACKEND=redis` 时 `SessionIndexPort` 解析为 Redis Adapter，且 `SESSION_REDIS_TTL_SECONDS` 同时传给 Redis context 和 index
  - 覆盖 local persistence 初始化后 index adapter 使用同一 root/lock/path_policy/atomic_writer
  - **验证: 需求 5.2, 6.2, 6.3, 6.5, 9.2, 9.3, 10.7**

- [x] 5.4 更新测试替身与静态类型兼容点
  - 修改实现新增 Port 方法后受影响的测试 fake / stub：`tests/evaluation/stubs/session_context_store.py`、`epsilon-boot/test/application/test_long_task_phase1_integration.py`、`epsilon-boot/test/infrastructure/chat/*` 中的 fake session store
  - 为 fake session store 增加 `exists(session_id) -> bool`，为 fake approval store 增加 `list_pending_by_session(session_id) -> list[ApprovalInterruptSummary]`
  - 确保新增 Protocol 方法不会导致既有测试因 mock 缺方法失败
  - _需求: 5.1, 7.3, 8.5, 9.1, 10.8_

- [x] 5.5 运行面向本需求的完整回归验证
  - 在 `epsilon-boot/` 运行 `uv run --frozen pytest test/application/cli test/infrastructure/session test/infrastructure/agent test/infrastructure/chat test/application/test_container_config_backend_dispatch.py`
  - 若某些文件名与实际测试布局不同，使用最接近的本需求相关测试路径并在 review-log 中记录
  - **验证: 需求 1.1, 1.2, 2.1, 2.3, 3.1, 4.2, 5.2, 6.4, 7.3, 8.1, 9.6, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7, 10.8**

- [x] 5.6 检查点 — 容器装配与全量测试
  - 使用项目自身的测试命令验证；如有问题请向用户确认
  - 在 `epsilon-boot/` 运行 `uv run --frozen pytest`
  - 全部测试必须通过后，才能进入 evaluator 阶段

## 备注

- 本期不创建 `manifest.json`，漂移检查使用 `requirement.md`、`design.md`、`tasks.md` 的 mtime 与内容一致性。
- `/delete! <session_id>` 是本设计选择的显式不可逆删除命令；`/delete <session_id>` 只返回提示，不执行删除。
- `/new` 不注册空会话索引；会话首次成功保存后进入 `SessionIndexPort`。已有索引中的空会话仍必须允许 `/resume`。
- `Session_Index` 是发现索引而不是聊天主数据。恢复路径必须用 `SessionContextStorePort.exists()` 再确认 context 真实存在。
- Redis context 与 index 的 TTL 必须共用 `SESSION_REDIS_TTL_SECONDS`，避免索引和上下文生命周期分裂。
