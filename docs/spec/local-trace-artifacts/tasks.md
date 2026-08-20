# 实现计划：统一运行产物与存储等级抽象（Local Trace & Artifacts / Storage Tier）

## 概述

本计划把 `design.md` 拆分为可独立提交的实现切片，严格遵循 DDD 依赖方向（domain → infrastructure → application → config → docs）与「最小改动清单」，复用 `structured-agent-trace` 已交付的 trace 存储层不重造。

编排顺序（尊重依赖）：

1. 无 DDL / migration 脚本：本特性所有产物均为本地文件系统 append-only JSONL 或轮转日志，不涉及数据库表、索引或数据 backfill 脚本，故无「DDL 前置桶 / 数据 backfill 后置桶」；改为按分层自底向上编排。
2. domain（StorageTier → ArtifactTrace → Port 签名）→ infrastructure（resolver → trace adapter 改造 → artifact adapter → 配置源 → 日志 sink → schema meta）→ application（DI 装配 → 持久化默认迁移 → CLI 装配）→ config（`config.properties` 配置键）→ docs（doc-sync）。
3. 「会话主状态默认迁移」（LocalPersistenceConfig）作为 application 层收尾切片，在 DI 装配 resolver 之后进行。

约定：

- 所有文件路径相对于 `epsilon-boot/`（除 `docs/`、`config.properties` 等根级说明外）；命令均在 `epsilon-boot/` 目录下执行。
- 每个实现切片验证步骤统一含：`uv run ruff check`（lint）、`uv run pyright`（类型）、`PYTHONPATH=src uv run --frozen pytest <目标测试>`（uv-package-manager.md：禁 pip/poetry；python-typing-lint.md）。
- 所有新增模块 / 类 / 公开函数须带中文 docstring（code-documentation.md）；配置类继承 `PropertiesBaseSettings`（pydantic-model.md）；最小改动、复用不重造（change-discipline.md）。
- ADR 已建（`docs/adr/0002~0006`），任务不再新建 ADR，仅回链。

## Tasks

- [x] 1. `StorageTier` 领域枚举（domain 新增，design 组件 1）
  - [x] 1.1 创建 `src/domain/storage/__init__.py` 与 `src/domain/storage/storage_tier.py`
    - `domain/storage/__init__.py` 导出 `StorageTier`
    - `storage_tier.py` 定义 `class StorageTier(StrEnum)`，取值 `USER="user"` / `PROJECT="project"` / `TENANT="tenant"`（TENANT 仅预留）
    - 模块与类中文 docstring；`from __future__ import annotations`；仅依赖标准库 `enum`，禁止 import 任何 `infrastructure/*`、禁止出现 `.epsilon`/`~`/`WORKSPACE_ROOT`/`OSS`/`S3` 字面量
    - _需求: 1.1、1.2、6.1；ADR-0002；正确性属性 Property 3（部分）_
  - [x] 1.2 验证任务：创建 `test/domain/storage/test_storage_tier.py`
    - 断言枚举含 USER/PROJECT/TENANT、取值为 str、`StorageTier.PROJECT == "project"`
    - 断言模块源码不含物理路径 / 后端字符串字面量（AST 或字符串扫描，复用既有依赖方向静态断言风格）
    - _需求: 1.1、1.2；Property 3（部分）_
    - 验证步骤：`uv run ruff check src/domain/storage test/domain/storage` / `uv run pyright src/domain/storage` / `PYTHONPATH=src uv run --frozen pytest test/domain/storage/test_storage_tier.py`
    - 前置：1.1

- [x] 2. `ArtifactTrace` 值对象与截断常量（domain 改动 `trace_value_objects.py`，design 组件 2）
  - [x] 2.1 在 `src/domain/agent/trace_value_objects.py` 追加 `ArtifactTrace` 与截断常量
    - 新增常量 `ARTIFACT_SUMMARY_MAX_LEN = 256`、`ARTIFACT_LOGICAL_PATH_MAX_LEN = 512`（与既有 `ARGUMENTS_SUMMARY_MAX_LEN` 等并列）
    - 新增 `@dataclass(frozen=True) class ArtifactTrace`，字段 `session_id/logical_path/artifact_type/timestamp_epoch/size_bytes/content_summary/source_tool`，`kind: Literal["artifact"] = field(default="artifact", init=False)`
    - **不并入** `AgentStepTrace` 联合类型，**不进入** `LocalFileTraceStoreAdapter._KIND_MAP`；不改动既有类型
    - 中文 docstring 说明大字段须由写入方按截断常量截断、不记录完整敏感内容
    - _需求: 3.1、3.2、6.1；ADR-0003_
  - [x] 2.2 验证任务：创建 `test/domain/agent/test_artifact_trace.py`
    - 断言 `ArtifactTrace` frozen（赋值抛 `FrozenInstanceError`）、`kind == "artifact"` 且 `init=False`
    - 断言 `dataclasses.asdict(...)` → `ArtifactTrace(**{去 kind})` 可 round-trip
    - 断言两个截断常量存在且取值正确
    - _需求: 3.1、3.2；Property 6/7（部分）_
    - 验证步骤：`uv run ruff check` / `uv run pyright src/domain/agent/trace_value_objects.py` / `PYTHONPATH=src uv run --frozen pytest test/domain/agent/test_artifact_trace.py`
    - 前置：2.1

- [x] 3. `TraceStorePort` 加 tier 参数 + 新增 `ArtifactStorePort`（domain 改动 `ports.py`，design 组件 3）**【最高风险改动点】**
  - [x] 3.1 修改 `src/domain/agent/ports.py`
    - TYPE_CHECKING 块新增 `from domain.agent.trace_value_objects import ArtifactTrace` 与 `from domain.storage.storage_tier import StorageTier`
    - `TraceStorePort` 三方法 `append_step`/`get_session_trace`/`list_traces` 各追加 **keyword-only、默认 `StorageTier.PROJECT`** 的 `tier` 参数（`*, tier: StorageTier = StorageTier.PROJECT`）
    - 新增 `class ArtifactStorePort(Protocol)`，方法 `append_artifact(session_id, artifact, *, tier=StorageTier.PROJECT) -> None` 与 `list_artifacts(session_id, *, tier=StorageTier.PROJECT) -> list[ArtifactTrace]`
    - 仅用 Python Protocol；禁止 import `infrastructure/*`；禁止出现物理路径 / 后端字符串；中文 docstring
    - **风险控制**：此改动波及既有 adapter 实现、DI、`api/routers/traces.py`、`ReActAgentAdapter` 调用点；因新参数为 keyword-only 默认值，既有不传 tier 的调用点行为不变
    - _需求: 3.3、6.1、6.2、8.3；ADR-0003；Property 3、6_
  - [x] 3.2 验证任务：创建 `test/domain/agent/test_ports_tier_signature.py`
    - 用 `inspect.signature` 断言三个 trace 方法含 keyword-only `tier` 参数且默认 `StorageTier.PROJECT`
    - 断言 `ArtifactStorePort` 定义了 `append_artifact` / `list_artifacts` 且签名含 keyword-only `tier`
    - 断言 `ports.py` 源码不含 `.epsilon`/`~`/`WORKSPACE_ROOT`/`OSS`/`S3` 字面量、不 import `infrastructure`
    - _需求: 3.3、6.1、8.3；Property 3、6_
    - 验证步骤：`uv run ruff check src/domain/agent/ports.py test/domain/agent/test_ports_tier_signature.py` / `uv run pyright src/domain/agent/ports.py` / `PYTHONPATH=src uv run --frozen pytest test/domain/agent/test_ports_tier_signature.py`
    - 前置：1.1、2.1、3.1

- [x] 4. **检查点 C1（domain 层边界）**：`PYTHONPATH=src uv run --frozen pytest test/domain`（domain 层全量回归），确认 domain 改动未破坏既有 trace 值对象 / 端口测试；并跑 `uv run pyright src/domain` / `uv run ruff check src/domain` 验证依赖方向与类型基线。
  - _对应 design「测试策略 → 依赖方向静态断言」；前置：1、2、3_

- [x] 5. `LocalFileTierResolver`（infrastructure 新增，design 组件 4）
  - [x] 5.1 创建 `src/infrastructure/storage/__init__.py` 与 `src/infrastructure/storage/local_file_tier_resolver.py`
    - 定义 `_EPSILON_DIR_NAME = ".epsilon"`、`_SUBDIRS = ("sessions","traces","artifacts","logs")`
    - `@dataclass(frozen=True) class ResolvedTierLayout`：`home: Path` + `subdir(name, *, create=True)` + `sessions_dir/traces_dir/artifacts_dir/logs_dir`（create=True 时 `mkdir(parents=True, exist_ok=True)`）
    - `class LocalFileTierResolver`：`__init__(project_base, user_base=None)`，`resolve(tier)` PROJECT→`<project_base>/.epsilon/`、USER→`<user_base>/.epsilon/<project-hash>/`、TENANT→`raise ValueError`
    - `project_hash()`：**全仓库唯一生成点**，`sha256(str(self._project_base))[:16]`，不含路径明文
    - `user_persistence_root()`：`<user_base>/.epsilon/persistence/<project-hash>/`，与 `resolve(USER)` 共享同一 `project_hash()`
    - 仅本模块知晓 `.epsilon`/`~`/`WORKSPACE_ROOT`；中文 docstring
    - _需求: 1.4、1.5、1.6、2.2；ADR-0002/0005/0006；Property 1、2、10_
  - [x] 5.2 验证任务：创建 `test/infrastructure/storage/test_local_file_tier_resolver.py`
    - PROJECT/USER 映射确定性（同基点恒返回同一 home）、子目录创建幂等；`resolve(TENANT)` 抛 `ValueError`（Property 1）
    - 用 tmp_path 作 project_base 且 == 进程 CWD 场景断言 `resolve(PROJECT).traces_dir()` 与 `<CWD>/.epsilon/traces` 等价（Property 2）
    - 用临时 HOME / user_base 断言 `resolve(USER).logs_dir()` 落 `~/.epsilon/<project-hash>/logs/`、`user_persistence_root()` 落 `~/.epsilon/persistence/<project-hash>/`、二者 hash 一致
    - `project_hash()` 确定性、长度 16 位十六进制、不含原始路径子串（Property 10）
    - `pytest.mark.parametrize` 覆盖多基点组合（属性风格，Property 1/10）
    - _需求: 1.4、1.5、1.6、2.2、8.1；Property 1、2、10_
    - 验证步骤：`uv run ruff check src/infrastructure/storage test/infrastructure/storage` / `uv run pyright src/infrastructure/storage/local_file_tier_resolver.py` / `PYTHONPATH=src uv run --frozen pytest test/infrastructure/storage/test_local_file_tier_resolver.py`
    - 前置：1.1

- [x] 6. `LocalFileTraceStoreAdapter` 改造（infrastructure 改动，design 组件 5）**【最高风险改动点】**
  - [x] 6.1 修改 `src/infrastructure/trace/local_file_trace_store_adapter.py`
    - 构造签名由 `__init__(self, store_dir: str)` 改为 `__init__(self, tier_resolver: LocalFileTierResolver)`，保存 `self._resolver`
    - `append_step`/`get_session_trace`/`list_traces` 各追加 `*, tier: StorageTier = StorageTier.PROJECT`，内部先 `store_dir = self._resolver.resolve(tier).traces_dir(create=...)`
    - 私有 `_append_line`/`_read_steps`/`_file_to_summary` 改为接受 `store_dir: Path` 参数（不再依赖构造期 `self._store_dir`）
    - 既有 `_KIND_MAP`/`_step_to_dict`/`_dict_to_step` 序列化逻辑**保持不变**；保留故障隔离 `logger.warning(..., exc_info=True)`
    - _需求: 1.6、8.1、8.5；ADR-0002/0003；Property 2、6、7_
  - [x] 6.2 验证任务（回归）：创建 `test/infrastructure/trace/test_trace_store_tier_compat.py`
    - 注入 resolver（project_base=tmp_path==CWD）后，不传 `tier` 的 append→get→list 同 session round-trip 与迁移前行为等价
    - 断言 PROJECT tier 默认写入位置与 `<CWD>/.epsilon/traces` 等价（复用 5.1 语义）
    - 断言既有 router / ReActAgentAdapter 调用点（不传 tier）无需改动即可工作
    - _需求: 8.1、8.2、8.5；Property 2、6_
    - 验证步骤：`uv run ruff check` / `uv run pyright src/infrastructure/trace/local_file_trace_store_adapter.py` / `PYTHONPATH=src uv run --frozen pytest test/infrastructure/trace/test_trace_store_tier_compat.py`
    - 前置：3.1、5.1

- [x] 7. **检查点 C2（trace 改造后回归，最高风险切片收尾）**：跑 `PYTHONPATH=src uv run --frozen pytest`（全量），重点确认既有 `test_local_file_trace_store_adapter.py`、trace 查询 API 相关测试、`ReActAgentAdapter` 追踪集成测试全绿；`uv run pyright src` / `uv run ruff check src` 通过。
  - _需求: 8.1；前置：6_

- [x] 8. `LocalFileArtifactStoreAdapter` + `ArtifactConfig`（infrastructure 新增，design 组件 6、7）
  - [x] 8.1 创建 `src/infrastructure/artifact/__init__.py` 与 `src/infrastructure/artifact/local_file_artifact_store_adapter.py`
    - `class LocalFileArtifactStoreAdapter`：`__init__(self, tier_resolver: LocalFileTierResolver)`
    - `append_artifact(session_id, artifact, *, tier=PROJECT)`：`store_dir = resolver.resolve(tier).artifacts_dir()` → `json.dumps(asdict(artifact))` → `asyncio.to_thread(self._append_line, ...)`；IO 失败 `logger.warning` 隔离不抛
    - `list_artifacts(session_id, *, tier=PROJECT)`：`artifacts_dir(create=False)`，文件不存在返回 `[]`，坏行跳过并 warning，异常返回 `[]`
    - 私有 `_append_line`/`_read_artifacts`（去除 `kind` 后 `ArtifactTrace(**d)`）；与 trace adapter 同构；中文 docstring
    - _需求: 3.4、3.5、6.1、6.2；ADR-0003；Property 6、7_
  - [x] 8.2 创建 `src/infrastructure/artifact/artifact_config.py`
    - `class ArtifactConfig(PropertiesBaseSettings)`，`model_config = SettingsConfigDict(env_prefix="ARTIFACT_")`，`enabled: bool = True`
    - 模块级 `artifact_config = create_config(ArtifactConfig)`；中文 docstring
    - _需求: 8.4；ADR-0003_
  - [x] 8.3 验证任务：创建 `test/infrastructure/artifact/test_local_file_artifact_store_adapter.py`
    - append→list round-trip（用临时 resolver）
    - 注入 mkdir/写入抛错的 fake，断言 `append_artifact` 不抛、记录 warning、返回 None（Property 7）
    - `list_artifacts` 对缺失文件返回 `[]`、对坏行跳过（Property 7）
    - _需求: 3.4、3.5；Property 6、7_
    - 验证步骤：`uv run ruff check src/infrastructure/artifact test/infrastructure/artifact` / `uv run pyright src/infrastructure/artifact` / `PYTHONPATH=src uv run --frozen pytest test/infrastructure/artifact/test_local_file_artifact_store_adapter.py`
    - 前置：2.1、3.1、5.1

- [x] 9. `config.local.properties` 配置源支持（common 改动，design 组件 10）
  - [x] 9.1 修改 `src/common/configuration/configuration_utils.py`
    - 新增 `_find_local_properties_file()`：优先 `<WORKSPACE_ROOT 或 CWD>/.epsilon/config.local.properties`，缺失兜底 `_find_file("config.local.properties")`
    - 模块级 `_LOCAL_PROPERTIES_FILE = _find_local_properties_file()`
    - `PropertiesBaseSettings.settings_customise_sources` 返回顺序改为：`init_settings, env_settings, PropertiesFileSettingsSource(settings_cls, properties_path=_LOCAL_PROPERTIES_FILE), PropertiesFileSettingsSource(settings_cls), dotenv_settings, file_secret_settings`（env > local > properties > .env）
    - 复用既有 `PropertiesFileSettingsSource`（不新增源类）；缺失文件由 `_parse_properties_file` 返回空 dict 不报错
    - _需求: 5.1、5.2、5.3、5.4、5.5、5.6；ADR-0004；Property 4、5_
  - [x] 9.2 修改 `src/common/configuration/config_proxy.py`
    - `ConfigProxy.__init__` 的 `source_files` 追加 `_LOCAL_PROPERTIES_FILE`（存在时纳入 mtime 热更新监听），既有双重检查锁定不变
    - _需求: 5；ADR-0004_
  - [x] 9.3 验证任务：创建 `test/common/configuration/test_configuration_local_properties.py`
    - `pytest.mark.parametrize` 多源同键覆盖，断言取值顺序 env > local > properties > .env（Property 4）
    - `config.local.properties` 缺失时行为与基线一致、不报错（Property 5）
    - `ConfigProxy` mtime 列表在 local 文件存在时包含它
    - _需求: 5.2、5.3、5.4、5.5；Property 4、5_
    - 验证步骤：`uv run ruff check src/common/configuration test/common/configuration` / `uv run pyright src/common/configuration` / `PYTHONPATH=src uv run --frozen pytest test/common/configuration/test_configuration_local_properties.py`
    - 前置：无（common 层独立）

- [x] 10. **检查点 C3（配置源改造后回归）**：配置解析契约跨模块，跑 `PYTHONPATH=src uv run --frozen pytest`（全量），确认既有全部配置类（TraceConfig/LocalPersistenceConfig/各 Provider 配置等）解析行为不变；`uv run pyright src` / `uv run ruff check src` 通过。
  - _需求: 5.5、5.6、8.5；前置：9_

- [x] 11. `Local_File_Log_Sink` + `SensitiveRedactionFilter` + `LogSinkConfig`（infrastructure 新增，design 组件 8）
  - [x] 11.1 创建 `src/infrastructure/storage/log_sink_config.py`
    - `class LogSinkConfig(PropertiesBaseSettings)`，`env_prefix="EPSILON_LOG_"`，字段 `to_file: bool = True`、`level: str = "INFO"`、`rotation_max_bytes: int = 10_485_760`、`rotation_backup_count: int = 5`；中文 docstring
    - _需求: 4.1、4.2；ADR-0005_
  - [x] 11.2 创建 `src/infrastructure/storage/local_file_log_sink.py`
    - `class SensitiveRedactionFilter(logging.Filter)`：`__init__(sensitive_keys: frozenset[str])` 编译 `key[:=]value` 正则，`filter()` 就地把 value 替换为 `****`，恒返回 True
    - `def configure_local_file_logging(tier_resolver, config, sensitive_keys, *, tier=StorageTier.USER) -> logging.Handler | None`：`to_file=False` 返回 None；否则 `resolve(USER).logs_dir()` → `RotatingFileHandler(epsilon.log, maxBytes, backupCount)` + level + Filter + Formatter，挂到 root logger
    - 中文 docstring；故障隔离由调用方 try 兜底（design「错误处理」）
    - _需求: 4.1、4.3、4.4；ADR-0005；Property 9_
  - [x] 11.3 验证任务：创建 `test/infrastructure/storage/test_log_sink_redaction.py`
    - `SensitiveRedactionFilter` 对 `api_key=xxx`、`"authorization":"Bearer x"`、`token=...`、`cookie=...` 脱敏为 `****`
    - `configure_local_file_logging` 用临时 HOME 断言默认经 `resolve(USER)` 落 `~/.epsilon/<project-hash>/logs/epsilon.log`（不落项目工作区）
    - `to_file=False` 返回 None（不装配 handler）
    - _需求: 4.1、4.2、4.3、4.4；Property 9_
    - 验证步骤：`uv run ruff check src/infrastructure/storage test/infrastructure/storage` / `uv run pyright src/infrastructure/storage` / `PYTHONPATH=src uv run --frozen pytest test/infrastructure/storage/test_log_sink_redaction.py`
    - 前置：5.1

- [x] 12. `write_schema_meta`（infrastructure 新增，design 组件 9）
  - [x] 12.1 创建 `src/infrastructure/storage/schema_meta.py`
    - `SCHEMA_VERSION = 1`；`def write_schema_meta(home: Path) -> None`：`mkdir(parents=True, exist_ok=True)` → 幂等写 `<home>/meta.json`（含 `schema_version`，已存在且版本一致时跳过）；失败 `logger.warning` 不中断
    - 中文 docstring
    - _需求: 6.3；ADR-0002；Property 7_
  - [x] 12.2 验证任务：创建 `test/infrastructure/storage/test_schema_meta.py`
    - 断言写入后 `meta.json` 含 `{"schema_version": 1}`；重复调用幂等（不重写已一致版本）
    - 注入只读目录 / mkdir 抛错断言 `write_schema_meta` 不抛（Property 7）
    - _需求: 6.3；Property 7_
    - 验证步骤：`uv run ruff check src/infrastructure/storage/schema_meta.py test/infrastructure/storage/test_schema_meta.py` / `uv run pyright src/infrastructure/storage/schema_meta.py` / `PYTHONPATH=src uv run --frozen pytest test/infrastructure/storage/test_schema_meta.py`
    - 前置：无（仅依赖 pathlib）

- [x] 13. DI 装配（application 改动 `container_config.py`，design 组件 11）
  - [x] 13.1 修改 `src/application/container_config.py`：tier resolver 与 artifact store 工厂
    - 新增模块级单例 `_tier_resolver: LocalFileTierResolver | None = None` 与 `def _create_tier_resolver()`（PROJECT 基点=`workspace_config.root` 或空时 `Path.cwd()`，惰性缓存）
    - 改 `_create_trace_store()`：由 `LocalFileTraceStoreAdapter(store_dir=trace_config.store_dir)` 改为 `LocalFileTraceStoreAdapter(tier_resolver=_create_tier_resolver())`（保留 `trace_config.enabled` 关闭返回 None 语义）
    - 新增 `def _create_artifact_store() -> "ArtifactStorePort | None"`：`artifact_config.enabled` 关闭返回 None，否则 `LocalFileArtifactStoreAdapter(tier_resolver=_create_tier_resolver())`
    - `container.register(ArtifactStorePort, _create_artifact_store, Scope.SINGLETON)`（与 `TraceStorePort` 绑定并列，写读共享单例）
    - 在本地文件后端（FILE session backend）就绪后对 PROJECT tier `home` 调用一次 `write_schema_meta`
    - _需求: 3.6、8.2、8.3；ADR-0002/0003；Property 6_
  - [x] 13.2 验证任务：创建 `test/application/test_container_artifact_trace_wiring.py`
    - `configure_container` 后 `TraceStorePort` 与 `ArtifactStorePort` 各解析为共享单例（两次 resolve 同一实例）
    - `ARTIFACT_ENABLED=false` / `TRACE_ENABLED=false` 时对应 Port 解析为 None（Property 6）
    - 断言 `_create_tier_resolver()` 返回缓存单例
    - _需求: 3.6、8.2；Property 6_
    - 验证步骤：`uv run ruff check src/application/container_config.py test/application/test_container_artifact_trace_wiring.py` / `uv run pyright src/application/container_config.py` / `PYTHONPATH=src uv run --frozen pytest test/application/test_container_artifact_trace_wiring.py`
    - 前置：3.1、5.1、6.1、8.1、8.2、12.1

- [x] 14. 会话主状态 USER tier 默认迁移（application + infrastructure 改动，design 组件 11 + 迁移与兼容）
  - [x] 14.1 修改 `src/infrastructure/persistence/local_file/config/local_persistence_config.py`
    - `root` 默认值由 `"../.local_persistence/epsilon-boot"` 改为**空串标记**（`root: str = ""`），空串表示启用 USER tier 默认迁移
    - 保留 `extra="forbid"`、`_reject_deprecated_ttl_env` 黑名单校验、`hot_reload=False` 不变；更新 `root` docstring 说明空串语义与显式配置优先
    - _需求: 8.5、8.6；ADR-0006；Property 8_
  - [x] 14.2 修改 `src/application/container_config.py`：`_init_local_persistence` 默认路径与迁移提示
    - `_validate_local_persistence_root` 之前：若 `local_persistence_config.root` 为空且 `SESSION_STORE_BACKEND != redis`，用 `_create_tier_resolver().user_persistence_root()` 解析为默认根；显式配置 / redis 尊重原值不迁移
    - 保留既有 `_validate_local_persistence_root` 启动校验与安全禁令（不弱化）
    - 首次启动一次性提示：解析出 USER tier 默认路径后，若旧默认目录 `../.local_persistence/epsilon-boot` 存在（非空）且新默认目录为空，`logger.info` 输出中文提示（旧路径 + 新路径 + 手动迁移/显式保留两选项），**不自动搬运**；检测失败静默跳过
    - _需求: 2.2、2A.1、2A.3、8.5、8.6；ADR-0006；Property 8_
  - [x] 14.3 验证任务：创建 `test/application/test_local_persistence_default_migration.py`
    - 未显式配 `LOCAL_PERSISTENCE_ROOT`（root 为空）时装配路径解析为 `~/.epsilon/persistence/<project-hash>/`（临时 HOME 断言，Property 8）
    - 显式 `LOCAL_PERSISTENCE_ROOT=<abs>` 时不迁移、`_validate_local_persistence_root` 校验仍生效（相互包含仍 fail-fast）
    - `SESSION_STORE_BACKEND=redis` 时不走 USER tier 迁移
    - 首次启动一次性提示：旧默认目录非空且新目录为空时触发 `logger.info`，不自动搬运数据
    - _需求: 2.2、2A.1、2A.3、8.5、8.6；Property 8_
    - 验证步骤：`uv run ruff check` / `uv run pyright src/infrastructure/persistence/local_file/config/local_persistence_config.py src/application/container_config.py` / `PYTHONPATH=src uv run --frozen pytest test/application/test_local_persistence_default_migration.py`
    - 前置：5.1、13.1

- [x] 15. CLI 入口装配本地文件日志（application 改动 `cli/main.py` / `runtime.py`，design 组件 8 装配点）
  - [x] 15.1 修改 `src/application/cli/main.py`（必要时 `src/application/cli/runtime.py`）
    - `_run_tui` / `_run_exec` 在 `CliRuntime` 启动后调用 `configure_local_file_logging(tier_resolver, LogSinkConfig(), sensitive_keys, tier=StorageTier.USER)`
    - `tier_resolver` 由容器解析（`_create_tier_resolver()` 或容器 resolve）；`sensitive_keys` 取自 `RequestLoggingConfig().get_sensitive_body_fields_set()`
    - `serve` 路径**不装配**，既有 FastAPI 日志链路不受影响
    - _需求: 4.1、4.2；ADR-0005；Property 9_
  - [x] 15.2 验证任务：创建 `test/application/cli/test_cli_file_logging.py`
    - 断言 TUI/exec 入口装配后 root logger 挂上 RotatingFileHandler（临时 HOME，落 USER tier logs）
    - 断言 `serve` 路径不装配文件日志 handler
    - `EPSILON_LOG_TO_FILE=false` 时不装配
    - _需求: 4.1、4.2；Property 9_
    - 验证步骤：`uv run ruff check src/application/cli test/application/cli` / `uv run pyright src/application/cli` / `PYTHONPATH=src uv run --frozen pytest test/application/cli/test_cli_file_logging.py`
    - 前置：11.1、11.2、13.1

- [x] 16. `config.properties` 配置键（config 改动，design「配置键」+ 迁移与兼容）
  - [x] 16.1 修改 `config.properties`（`epsilon-boot/config.properties`）
    - 新增 `ARTIFACT_ENABLED=true`
    - 新增 `EPSILON_LOG_TO_FILE=true`、`EPSILON_LOG_LEVEL=INFO`、`EPSILON_LOG_ROTATION_MAX_BYTES=10485760`、`EPSILON_LOG_ROTATION_BACKUP_COUNT=5`
    - `LOCAL_PERSISTENCE_ROOT=../.local_persistence/epsilon-boot` 一行改为**注释留空**（决策 1a），并补充：留空即启用 USER tier 默认 `~/.epsilon/persistence/<project-hash>/`、显式设置尊重原值、`SESSION_STORE_BACKEND=redis` 时不生效、保留 NFS/SMB/OSS FUSE 与多容器共享禁止注释
    - 遵循 config-source.md：`config.properties` 仍为主配置源，`config.local.properties` 仅本地覆盖
    - _需求: 5.6、7.2、8.5、8.6；ADR-0004/0005/0006；决策 1a_
    - 验证步骤：`PYTHONPATH=src uv run --frozen pytest test/common/configuration test/application/test_local_persistence_default_migration.py`（确认新键被正确解析、默认迁移生效）
    - 前置：8.2、11.1、14.1

- [x] 17. **检查点 C4（DI + 迁移 + CLI + 配置键全量回归）**：跑 `PYTHONPATH=src uv run --frozen pytest`（全量），确认容器装配、会话恢复相关测试（`tui-session-resume`/`long-task-continuation-*`）、trace 查询 API 回归测试全绿；`uv run pyright src` / `uv run ruff check src` 通过。
  - [x] 17.1 验证任务：补充 `test/api/routers/test_traces_router_regression.py`（若既有回归测试未覆盖 tier 签名）（既有 trace 查询 API 测试已在 C2/C4 全量回归中覆盖 tier 签名变更、结构不变，无需新增文件）
    - 断言既有 trace 查询 API 在签名变更后返回结构不变；trace 关闭时返回空 / 404
    - _需求: 8.1；Property 6_
  - _需求: 8.1；前置：13、14、15、16_

- [x] 18. 文档同步（doc-sync，design「最小改动清单 → 文档同步」）
  - [x] 18.1 更新 `docs/configuration.md`
    - 新增 `config.local.properties` 章节：格式、`.epsilon/` 定位、优先级链（env > local > properties > .env）、缺失不报错、不入库
    - `LOCAL_PERSISTENCE_ROOT` 默认迁移说明：留空→USER tier 默认、旧数据两种搬迁指引（手动拷贝 / 显式保留旧绝对路径）、首次启动提示、安全禁令保留
    - 新增 `ARTIFACT_*` / `EPSILON_LOG_*` 键说明与默认值
    - _需求: 2.6、5.6、7；ADR-0004/0005/0006；doc-sync.md_
  - [x] 18.2 更新 `docs/architecture.md`
    - 新增 `StorageTier` 抽象、`ArtifactStorePort`、本地文件 tier→目录映射、`LocalFileTierResolver` 单一 project-hash 生成点、schema meta
    - _需求: 6；ADR-0002/0003；doc-sync.md_
  - [x] 18.3 更新 `docs/tools.md` 及相关索引（`CLAUDE.md` / `docs/README.md` 主题索引如涉及）
    - artifact Port 与工具产物记录的关系说明（写入方后续 spec）
    - _需求: 2.6；doc-sync.md_
  - [x] 18.4 更新 `TODO.md`
    - 勾选 P0.2 各子项（StorageTier / config.local.properties / artifact 抽象 / TUI 文件日志）
    - 勾选「推荐 Spec 拆分」中 `local-trace-artifacts` 项
    - _需求: 全特性收尾；doc-sync.md_
    - 验证步骤：文档改动无需编译；人工核对链接有效、与代码一致（doc-sync.md）
    - 前置：全部实现任务（1~16）完成

- [x] 19. **检查点 C5（终检）**：末次全量回归 `PYTHONPATH=src uv run --frozen pytest`，`uv run pyright src`，`uv run ruff check src` 三项全绿；核对「最小改动清单」全部新增/改动文件已覆盖、无范围外改动（change-discipline.md）。
  - _前置：1~18 全部_

## 需求覆盖对照

| 需求验收 | 覆盖任务 |
|---|---|
| 1.1 | 1.1、1.2 |
| 1.2 | 1.1、1.2、3.2 |
| 1.3 | 3.1、3.2 |
| 1.4 | 5.1、5.2 |
| 1.5 | 5.1、5.2 |
| 1.6 | 5.1、5.2、6.1、6.2 |
| 2.1 | 14.1、14.2（Sessions_Dir 不承载主状态，经文档 18.3 明确） |
| 2.2 | 5.1、5.2、14.2、14.3 |
| 2.3 | 6.1（traces 写入方复用）、18.2 |
| 2.4 | 8.1（artifacts 结构持久化） |
| 2.5 | 11.2（logs 写入） |
| 2.6 | 18.1、18.2、18.3（tier 归属/写读方/清理文档化） |
| 2A.1 | 14.2、14.3；18.1 |
| 2A.2 | 14.2、16.1（redis 分流 + 文档） |
| 2A.3 | 14.2、14.3、16.1 |
| 2A.4 | 18.1、18.2（TenantVisibilityPolicy 仅记录，ADR-0006） |
| 3.1 | 2.1、2.2 |
| 3.2 | 2.1、2.2 |
| 3.3 | 3.1、3.2 |
| 3.4 | 8.1、8.3 |
| 3.5 | 8.1、8.3 |
| 3.6 | 13.1、13.2 |
| 4.1 | 11.2、11.3、15.1、15.2 |
| 4.2 | 11.1、15.1、15.2、16.1（默认开启策略，ADR-0005） |
| 4.3 | 11.2、11.3 |
| 4.4 | 11.2、11.3 |
| 5.1 | 9.1、9.3 |
| 5.2 | 9.1、9.3 |
| 5.3 | 9.1、9.3 |
| 5.4 | 9.1、9.3 |
| 5.5 | 9.1、9.3、10 |
| 5.6 | 9.1、16.1、18.1 |
| 5.7 | ADR-0004（已建，design 回链）+ 9.1 |
| 6.1 | 1.1、2.1、3.1、3.2 |
| 6.2 | 3.1、3.2、13.1 |
| 6.3 | 12.1、12.2、13.1 |
| 6.4 | 3.1、18.2（抽象可复用性由 Port + schema 证明，ADR-0002/0003） |
| 6.5 | ADR-0003（已建，design 回链）+ 2.1、3.1 |
| 7.1 | 18.1（.gitignore 建议；既有规则已覆盖，见 design 迁移与兼容） |
| 7.2 | 16.1、18.1 |
| 7.3 | 18.1 |
| 8.1 | 6.1、6.2、7、17.1 |
| 8.2 | 3.1、8.1、13.1、13.2 |
| 8.3 | 3.1、3.2、13.1 |
| 8.4 | 8.2、9.1（配置类继承 PropertiesBaseSettings + 类型/docstring） |
| 8.5 | 6.1、6.2、9.1、14.1、14.2、16.1 |
| 8.6 | 14.1、14.2、14.3、16.1 |

## 备注

- **最高风险切片**：任务 3（Port 签名）与任务 6（trace adapter 改造）波及既有 adapter、DI、`api/routers/traces.py`、`ReActAgentAdapter`；已通过 keyword-only 默认参数保证既有调用点零改动，并在 C1/C2 检查点安排全量回归。
- **检查点位置**：C1（domain 后，任务 4）、C2（trace 改造后，任务 7）、C3（配置源改造后，任务 10）、C4（DI+迁移+CLI+配置键后，任务 17）、C5（终检，任务 19）。
- **无 DDL / 数据 backfill 脚本**：本特性产物为本地文件系统 JSONL / 轮转日志，不涉及数据库表、索引、迁移或 backfill 脚本，故无 SQL/migration 目录任务；会话主状态旧数据不自动搬迁，仅提供文档指引与首次启动一次性提示（任务 14.2、18.1）。
- 命令统一在 `epsilon-boot/` 下执行，测试固定 `PYTHONPATH=src uv run --frozen pytest`（uv-package-manager.md）。
- 每个实现切片粒度控制在 ≤5 生产文件 / ≤200 变更行；验证任务紧随其所验证的组件。
