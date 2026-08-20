# 实现计划：Workspace 工作区抽象与本地文件系统实现

## 概述

本计划把 `design.md` 拆解为可独立评审、可独立合并的细粒度任务。整体执行顺序为：

1. **前置定位**（创建骨架目录）
2. **领域层**：值对象（含 `WorkspacePath.join` 自洽实现） → 领域错误 → `WorkspacePolicy` → `Workspace` Port（7 个 I/O 方法带 `context` 参数）+ `LocallyMaterializable` 子协议
3. **基础设施层**：`WorkspaceConfig` → `_guards` → `_common_impl` → `LocalFilesystemWorkspace`（含 `_LOG_CONTEXT_WHITELIST` 与 `_sanitize_context`）各方法分片落地 → `infrastructure/workspace/oss/` 占位
4. **配置与 DI 装配**：`config.properties` 键 → `_create_local_filesystem_workspace` 工厂 + `_init_workspace` 资源 → 工具注册注入
5. **工具层改造**：4 个受控文件工具 + 2 个受控执行工具逐个改造（调用 Port 时构造并传入 `context={"tool_name": ..., "trace_id": ..., "agent_id": ...}`）
6. **`ChatConfig.system_prompt` 追加**（使用 `model_validator(mode="after")` 幂等追加，位置在 `infrastructure/chat/chat_config.py`）
7. **观测与集成**：结构化日志（通过 `context` 白名单透传）+ 最小脱敏 + 启动期二次校验 + 集成测试
8. **文档收尾**：`common_tools.py` 薄壳 docstring、`oss/README.md`、`docs/tools.md` 同步

所有任务类型以前缀标注：
- `[impl]` 生产代码实现
- `[test]` 测试
- `[config]` 配置文件改动
- `[docs]` 文档改动
- `[refactor]` 仅结构迁移、不改变外部行为
- `[checkpoint]` 里程碑校验（类型检查 + 全量测试）

所有源码路径以仓库根为准：`epsilon-boot/src/...`；所有测试路径为 `epsilon-boot/test/...`。

每个任务均给出 **需求追溯**（requirement.md 的 AC 编号）与 **设计追溯**（design.md 的章节号）。

## Tasks

- [x] 1. 前置定位与骨架创建
  - [x] 1.1 `[impl]` 创建领域层骨架目录
    - 新建 `epsilon-boot/src/domain/workspace/__init__.py`（空文件即可，后续任务在其中显式 `from ... import ...` 重新导出公共 API）
    - 暂不创建 `ports.py` / `policy.py` / `value_objects.py` / `exceptions.py`，它们由后续任务分别新建
    - _需求：1.1 / 9.5_
    - _设计：§架构 → 包/目录结构_
    - _前置：无_
  - [x] 1.2 `[impl]` 创建基础设施层骨架目录
    - 新建 `epsilon-boot/src/infrastructure/workspace/__init__.py`（空文件）
    - 新建 `epsilon-boot/src/infrastructure/workspace/local_filesystem/__init__.py`（空文件）
    - _需求：9.6_
    - _设计：§架构 → 包/目录结构_
    - _前置：无_
  - [x] 1.3 `[docs]` 创建 OSS 后端占位目录
    - 新建 `epsilon-boot/src/infrastructure/workspace/oss/README.md`
    - 内容按设计要求声明：本期不实现 OSS 后端；扩展点包含 `Backend_Location = (bucket, key)`、流式读写、分片上传、`supports_atomic_write=False` 的降级契约；本目录刻意不放置 `__init__.py` 以避免空包被测试发现
    - _需求：1.3 / 9.6_
    - _设计：§架构 → 包/目录结构_
    - _前置：无_

- [x] 2. 领域层：值对象与领域错误
  - [x] 2.1 `[impl]` 实现 `WorkspacePath` / `WorkspaceStatEntry` / `WorkspaceCapabilities` / `WorkspaceBackendKind`（`join` 方法**不导入** `WorkspacePolicy`）
    - 在 `epsilon-boot/src/domain/workspace/value_objects.py` 新增：
      - `WorkspaceBackendKind(str, Enum)`：仅 `LOCAL_FILESYSTEM = "local_filesystem"`；注释标注"未来可追加 `OSS = "oss"`"
      - `WorkspacePath`：`@dataclass(frozen=True, slots=True)`，持有 `_posix: PurePosixPath`，提供 `to_posix()` / `join(segment)` / `parent()` / `name()` / `__str__()`
      - `WorkspaceStatEntry`：`@dataclass(frozen=True, slots=True)`，字段 `path / is_file / is_dir / size: int | None / mtime: float | None`
      - `WorkspaceCapabilities`：`@dataclass(frozen=True, slots=True)`，6 个布尔字段均带默认值 `False`
    - **`join(segment)` 实现要点（设计决策："`WorkspacePath.join` 实现"行 + §数据模型 `join` 伪码）**：
      1. 类型校验：`segment` 必须为 `str`，否则 `TypeError`
      2. 调用模块内私有 `_reject_illegal_chars(segment)`：拒绝 NUL、反斜杠、Windows 盘符（字符集与 `WorkspacePolicy` 并列维护，本期不共享常量模块）
      3. `combined = PurePosixPath(self._posix) / segment`；手动折叠 `combined.parts`：`..` 回退一段，越根时抛 `WorkspaceConfinementViolation(reason=ABSOLUTE_OUTSIDE)`；`"."` / `""` 跳过；其他段追加
      4. 重组为 `/`-起始 POSIX 路径，返回新 `WorkspacePath`
      5. **禁止** `from domain.workspace.policy import WorkspacePolicy`；禁止运行时延迟 import `policy`
    - 模块 docstring 使用中文；所有类、方法、字段的 docstring 使用中文
    - 禁止 import `infrastructure/`、FastAPI、pydantic-settings、任何存储 SDK、也禁止 import `domain.workspace.policy`；仅允许 `pathlib.PurePosixPath`、`enum`、`dataclasses`、`domain.workspace.exceptions`
    - _需求：1.1 / 1.3 / 2.1 / 3.1 / 3.4 / 5.2 / 9.5_
    - _设计：§设计决策表"`WorkspacePath.join` 实现"行 / §组件与接口 1 / §数据模型 `WorkspacePath` + `join` 伪码_
    - _前置：1.1_
  - [x] 2.2 `[test]` 单元测试：值对象的冻结性、等价性、`join/parent/name` 基础行为
    - 新建 `epsilon-boot/test/domain/workspace/test_workspace_value_objects_unit.py`
    - 用例：`WorkspacePath` 冻结（`dataclasses.FrozenInstanceError`）；同值相等；`to_posix()` 返回 `/-`起始字符串；`WorkspaceCapabilities` 默认字段全 `False`；`WorkspaceBackendKind.LOCAL_FILESYSTEM.value == "local_filesystem"`；`parent() / name()` 基础用例
    - _需求：3.1 / 3.4_
    - _设计：§数据模型_
    - _前置：2.1_
  - [x] 2.3 `[test]` 单元测试：`WorkspacePath.join` 的 happy-path 与越根拒绝
    - 新建 `epsilon-boot/test/domain/workspace/test_workspace_path_join_unit.py`
    - 用例：
      - 合法段 `"a.md"` / `"sub/x"` / `"./x"` / `"a/../b"` → 预期输出（逐条）
      - 越根段 `"../../etc"` / `"../../../"` → 抛 `WorkspaceConfinementViolation(reason=ABSOLUTE_OUTSIDE)`
      - 非法字符：含 NUL / `\\` / Windows 盘符（`"C:/a"`）的段 → 抛 `WorkspaceConfinementViolation`
      - `segment` 非 str → `TypeError`
      - 连续 `..` 配合已嵌套父级的 happy-path：`WorkspacePath("/a/b/c").join("../../x") == WorkspacePath("/a/x")`
    - _需求：2.2 / 2.5 / 2.6_
    - _设计：§设计决策表"`WorkspacePath.join` 实现"行 / §数据模型 `join` 伪码_
    - _前置：2.1_
  - [x] 2.4 `[test]` 静态检查：`value_objects.py` 不 import `workspace_policy` 模块（避免循环依赖）
    - 新建 `epsilon-boot/test/domain/workspace/test_value_objects_imports_static.py`
    - 用 Python `ast` 模块解析 `epsilon-boot/src/domain/workspace/value_objects.py` 的 AST：
      - 遍历 `ast.Import` / `ast.ImportFrom` 节点，断言不存在以下任意形态的导入：
        - `import domain.workspace.policy`
        - `from domain.workspace.policy import ...`
        - `from domain.workspace import policy`（避免间接暴露）
      - 同时对 `ast.FunctionDef` / `ast.AsyncFunctionDef` 的方法体内部 `ast.Import` / `ast.ImportFrom` 也做扫描（防止运行时延迟 import 绕过）
    - 辅以 `importlib.import_module("domain.workspace.value_objects")` → 断言 `sys.modules` 中不含 `domain.workspace.policy`（若本测试单独运行时）
    - _需求：9.5_
    - _设计：§设计决策表"`WorkspacePath.join` 实现"行（关键决策：不导入 `WorkspacePolicy`）_
    - _前置：2.1_
  - [x] 2.5 `[impl]` 实现 `ConfinementViolationReason` 枚举与 4 种领域错误
    - 在 `epsilon-boot/src/domain/workspace/exceptions.py` 新增：
      - `ConfinementViolationReason(str, Enum)`：`NUL_BYTE / BACKSLASH / WINDOWS_DRIVE / UNC_PATH / ABSOLUTE_OUTSIDE / SYMLINK_ESCAPE / CROSS_DEVICE`
      - `_WorkspaceError(BizException)` 基类（code 段 605xx）
      - `WorkspaceConfinementViolation(code=60501)`：构造参数 `requested_path / reason / resolved_workspace_path=None`
      - `WorkspaceNotFoundError(code=60502)`：构造参数 `workspace_path`
      - `WorkspaceIoError(code=60503)`：构造参数 `operation / workspace_path / reason / underlying_error_class=""`
      - `WorkspaceUnsupportedOperationError(code=60504)`：构造参数 `operation / capability`
    - **关键约束**：4 种错误构造参数均**不包含** `context` 字段，`context` 仅进入 `logger.*(extra=...)`，永远不参与异常 message 拼装（守住需求 4.4 / 8.6 路径泄露红线）
    - `BizException` 从 `common.exceptions` 导入（已存在）
    - _需求：4.1 / 4.2 / 4.4 / 8.6_
    - _设计：§数据模型 → 领域错误_
    - _前置：2.1_
  - [x] 2.6 `[test]` 单元测试：4 种领域错误的字段、code、继承关系
    - 新建 `epsilon-boot/test/domain/workspace/test_workspace_exceptions_unit.py`
    - 用例：4 种错误均继承自 `_WorkspaceError` 与 `BizException`；每种错误的 `code` 值正确；`WorkspaceConfinementViolation.reason` 保留原始枚举；`message` 为中文且不含宿主绝对路径；构造签名不包含 `context` 参数（用 `inspect.signature` 断言）
    - _需求：4.1 / 4.4 / 8.6_
    - _设计：§数据模型 → 领域错误_
    - _前置：2.5_
  - [x] 2.7 `[impl]` 在 `domain/workspace/__init__.py` 中重新导出公共 API
    - 导出：`WorkspacePath / WorkspaceStatEntry / WorkspaceCapabilities / WorkspaceBackendKind / ConfinementViolationReason / WorkspaceConfinementViolation / WorkspaceNotFoundError / WorkspaceIoError / WorkspaceUnsupportedOperationError`
    - 暂不导出 `Workspace` / `LocallyMaterializable` / `WorkspacePolicy`（由后续任务追加）
    - _需求：9.5_
    - _设计：§架构 → 包/目录结构_
    - _前置：2.1 / 2.5_

- [x] 3. 领域层：WorkspacePolicy
  - [x] 3.1 `[impl]` 实现 `WorkspacePolicy.resolve`
    - 新建 `epsilon-boot/src/domain/workspace/policy.py`
    - 类签名：`@dataclass(frozen=True) class WorkspacePolicy: def resolve(self, requested: str) -> WorkspacePath`
    - 顺序（与 design §组件与接口 3 一致）：
      1. `requested` 为空串或 `"."` 或 `"/"` → 统一映射到工作区根 `WorkspacePath(PurePosixPath("/"))`
      2. 前置字符扫描：含 `\x00` → `NUL_BYTE`；含 `\\` → `BACKSLASH`；`re.match(r"^[A-Za-z]:", s)` → `WINDOWS_DRIVE`；以 `//` 开头且第三字符非 `/` → `UNC_PATH`
      3. 以 `/` 起始视为"工作区绝对路径"；否则锚定到 `/`（在字符串前拼接 `/`）
      4. 用 `PurePosixPath` 归一化，消除 `.` / `..` / 重复 `/`
      5. 归一化后首段仍为 `..` 或路径脱离 `/` → `ABSOLUTE_OUTSIDE`
      6. 构造 `WorkspacePath(PurePosixPath("/" + joined))`
    - 失败时抛 `WorkspaceConfinementViolation(requested_path=requested, reason=...)`，不返回任何被裁剪的路径
    - 纯函数实现，不触发任何 I/O
    - `policy.py` 允许 `from domain.workspace.value_objects import WorkspacePath`（单向依赖 value_objects），闭合循环依赖通过任务 2.1 的 `join` 自洽实现保证
    - _需求：1.1 / 2.1 / 2.2 / 2.3 / 2.4 / 2.5 / 2.6_
    - _设计：§组件与接口 3_
    - _前置：2.1 / 2.5_
  - [x] 3.2 `[test]` 单元测试：`WorkspacePolicy.resolve` example-based 边界
    - 新建 `epsilon-boot/test/domain/workspace/test_workspace_policy_unit.py`
    - 用例矩阵：相对路径 `notes.md` → `/notes.md`；绝对形式 `/a/b` → `/a/b`；`./a` / `a/./b` 归一；`a/../b` → `/b`；`../etc/passwd` → `ABSOLUTE_OUTSIDE`；`a\\b` → `BACKSLASH`；`\x00` → `NUL_BYTE`；`C:\\Windows` → `WINDOWS_DRIVE`；`\\\\server\\share` → `UNC_PATH`；空串 / `"."` / `"/"` 统一返回根
    - _需求：2.1 / 2.2 / 2.3 / 2.4 / 2.5 / 2.6_
    - _设计：§组件与接口 3_
    - _前置：3.1_
  - [x] 3.3 `[test]` 属性测试：`WorkspacePolicy.resolve` 幂等与非法字符闭合
    - 新建 `epsilon-boot/test/domain/workspace/test_workspace_policy_property.py`
    - Hypothesis 策略：
      - Property 3 幂等：任意随机字符串，若 `resolve(s)` 成功得 `wp`，则 `resolve(wp.to_posix()) == wp`
      - Property 4 非法字符闭合：包含 `\x00 / \\ / C: / \\\\` 的字符串必然抛 `WorkspaceConfinementViolation` 且 `reason` 为对应枚举
    - 遵循仓库 `_property.py` 命名约定
    - _需求：2.1 / 2.5 / 2.6（Property 3、4）_
    - _设计：§正确性属性 3 / 4_
    - _前置：3.1_

- [x] 4. 领域层：Workspace Port + LocallyMaterializable
  - [x] 4.1 `[impl]` 定义 `Workspace` Protocol（7 个 I/O 方法带 `context` 参数）与 `LocallyMaterializable` Protocol
    - 新建 `epsilon-boot/src/domain/workspace/ports.py`
    - 实现 design §组件与接口 1 定义的 `Workspace(Protocol)`：
      - **接受 `context: dict | None = None`（以 keyword-only 参数，末位）的 7 个 I/O 方法**：`exists` / `stat` / `read` / `write` / `edit` / `list_dir` / `delete`
      - **不接受 `context`** 的 3 个方法：`resolve_path`（纯函数式归一化）/ `capabilities`（静态能力查询）/ `display_root_hint`（元数据查询，返回字符串）
      - 方法签名严格按 design §组件与接口 1 落地（`exists` / `stat` / `read` / `write` / `edit` / `list_dir` / `delete` / `capabilities` / `resolve_path` / `display_root_hint`）
    - Port docstring 使用中文，需包含："观测上下文参数 `context` 的语义"小节，明确：
      - 白名单字段：`tool_name: str` / `trace_id: str` / `agent_id: str`
      - 后端实现约束：可将白名单字段合并进结构化日志；不得据 `context` 改变 I/O 行为或分支；须容忍 `context=None` / 未知 key / 缺失约定字段；**禁止**把 `context` 原样拼入异常 `message` 或其他对 LLM 可见的出口
      - `resolve_path` / `capabilities` / `display_root_hint` 不接受 `context`（纯函数或元数据查询，无 I/O 事件）
      - `context` 与 `WorkspaceCapabilities` 的区别：前者是本次调用的观测元数据，后者是后端静态能力声明
    - 同文件追加 `LocallyMaterializable(Protocol)`，仅一个方法 `materialize_cwd(self, path: WorkspacePath) -> str`
    - 所有方法使用中文 docstring；异步方法使用 `async def` 声明
    - 禁止 import `infrastructure/` 或任何外部 SDK
    - _需求：1.1 / 1.2 / 1.3 / 1.4 / 1.5 / 6.6 / 8.1 / 8.2_
    - _设计：§设计决策表"观测上下文透传"行 / §组件与接口 1_
    - _前置：2.1_
  - [x] 4.2 `[test]` 单元测试：Port 结构类型契约 + `context` 参数签名
    - 新建 `epsilon-boot/test/domain/workspace/test_workspace_port_unit.py`
    - 用例：
      - 用 `unittest.mock.MagicMock` 构造 mock 对象，断言 `isinstance(mock, Workspace)`（Protocol 结构类型判断）
      - 断言 `Workspace.__dict__` 含 10 个方法名；`LocallyMaterializable.__dict__` 含 `materialize_cwd`
      - 断言 `Workspace.read` 返回类型注解为 `bytes`（通过 `typing.get_type_hints` 提取）
    - _需求：1.2 / 1.3 / 1.4 / 8.1 / 8.2（Property 2）_
    - _设计：§组件与接口 1 / §正确性属性 2_
    - _前置：4.1_
  - [x] 4.3 `[test]` 静态检查：Port 的 7 个 I/O 方法签名统一含 `context: dict | None = None`，3 个非 I/O 方法不含
    - 新建 `epsilon-boot/test/domain/workspace/test_workspace_port_context_signature_static.py`
    - 实现：用 `inspect.signature(Workspace.<method>)` 遍历以下方法，断言其存在一个名为 `context` 的 keyword-only 参数，注解为 `dict | None`（或 `Optional[dict]`），默认值为 `None`：
      - `exists` / `stat` / `read` / `write` / `edit` / `list_dir` / `delete`
    - 并断言以下方法**没有** `context` 参数：
      - `resolve_path` / `capabilities` / `display_root_hint`
    - 用例失败消息须清晰指出哪个方法签名漂移，便于后续维护时定位
    - _需求：8.1 / 8.2_
    - _设计：§设计决策表"观测上下文透传"行 / §组件与接口 1_
    - _前置：4.1_
  - [x] 4.4 `[impl]` 在 `domain/workspace/__init__.py` 补充导出 `Workspace / LocallyMaterializable / WorkspacePolicy`
    - 增量修改任务 2.7 创建的 `__init__.py`
    - _需求：9.5_
    - _设计：§架构 → 包/目录结构_
    - _前置：3.1 / 4.1_
  - [x] 4.5 `[checkpoint]` 领域层完成度校验
    - 运行 `uv run pytest test/domain/workspace/ -q`
    - 运行 `uv run python -c "from domain.workspace import Workspace, WorkspacePolicy, WorkspacePath, WorkspaceConfinementViolation; print('ok')"` 确认公共 API 可从包根导入
    - 运行 `uv run python -m compileall src/domain/workspace` 确认无语法错误
    - 运行 `uv run python -c "import ast, pathlib; tree = ast.parse(pathlib.Path('epsilon-boot/src/domain/workspace/value_objects.py').read_text()); print('value_objects imports:', [n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)])"` 肉眼再确认无 `policy` 导入
    - _需求：1.1 / 9.5 / 9.8_
    - _设计：§架构_
    - _前置：2.2 / 2.3 / 2.4 / 2.6 / 2.7 / 3.2 / 3.3 / 4.2 / 4.3 / 4.4_

- [x] 5. 基础设施层：WorkspaceConfig
  - [x] 5.1 `[impl]` 实现 `WorkspaceConfig` + 全局单例
    - 新建 `epsilon-boot/src/infrastructure/workspace/workspace_config.py`
    - 按 design §组件与接口 4 实现：
      - `model_config = SettingsConfigDict(env_prefix="WORKSPACE_")`
      - 字段：`backend: WorkspaceBackendKind = LOCAL_FILESYSTEM` / `root: str = ""` / `follow_symlinks: bool = False` / `create_if_missing: bool = False`
      - `@model_validator(mode="after") def _reject_unsupported_backend()`：当 `backend != LOCAL_FILESYSTEM` 时 `raise ValueError("本期仅支持 WORKSPACE_BACKEND=local_filesystem，实际值：{...}")`
      - `workspace_config = create_config(WorkspaceConfig)` 模块级单例
    - 不声明 `hot_reload`（保持默认 `False`，需求 5.12）
    - _需求：5.1 / 5.2 / 5.3 / 5.12_
    - _设计：§组件与接口 4_
    - _前置：2.1_
  - [x] 5.2 `[test]` 单元测试：`WorkspaceConfig` 默认值、env_prefix、非法 backend 拒绝、hot_reload 默认关闭
    - 新建 `epsilon-boot/test/infrastructure/workspace/test_workspace_config_unit.py`
    - 用例：
      - 默认 `backend=LOCAL_FILESYSTEM / root="" / follow_symlinks=False / create_if_missing=False`
      - 通过 monkeypatch 设 `WORKSPACE_BACKEND=oss` 或其他值 → `ValidationError` / `ValueError`
      - 断言 `WorkspaceConfig.model_config["env_prefix"] == "WORKSPACE_"`
      - 断言 `getattr(WorkspaceConfig, "hot_reload", False) is False`
    - _需求：5.1 / 5.2 / 5.3 / 5.12_
    - _设计：§组件与接口 4_
    - _前置：5.1_

- [x] 6. 基础设施层：LocalFilesystemWorkspace 的 Guards 与公共实现
  - [x] 6.1 `[impl]` 实现 `SymlinkGuard` / `IdentityGuard`
    - 新建 `epsilon-boot/src/infrastructure/workspace/local_filesystem/_guards.py`
    - `SymlinkGuard(root, follow_symlinks)`：
      - `check(host_path: Path) -> None`：`follow_symlinks=False` 时从 `root` 开始逐段 `os.lstat`，命中符号链接立即抛 `WorkspaceConfinementViolation(reason=SYMLINK_ESCAPE)`；`follow_symlinks=True` 时 `Path.resolve(strict=False)`，再用 `os.path.commonpath([resolved, root]) == str(root)` 判断仍在根下
    - `IdentityGuard(root)`：启动期记录 `os.stat(root).st_ino / st_dev`；`check(host_path)` 时对 `host_path` 及其最近存在的祖先调用 `os.stat`，若 `st_dev` 不同于 root → 抛 `WorkspaceConfinementViolation(reason=CROSS_DEVICE)`；用于防御 macOS/HFS+ 大小写折叠越界
    - 两个类均不做额外逻辑；失败统一翻译为领域错误
    - _需求：5.10 / 5.11 / 9.6_
    - _设计：§设计决策表"符号链接逃逸检测算法" / "大小写处理"行 / §组件与接口 2_
    - _前置：2.1 / 2.5_
  - [x] 6.2 `[test]` 单元测试：`SymlinkGuard` 两态 + `IdentityGuard` 跨设备
    - 新建 `epsilon-boot/test/infrastructure/workspace/local_filesystem/test_guards_unit.py`
    - 用例：
      - 通过 `tmp_path` 构造 `root / link -> /etc`：`follow_symlinks=False` 抛 `SYMLINK_ESCAPE`；`follow_symlinks=True` + 指向外部 → `ABSOLUTE_OUTSIDE`（或 `SYMLINK_ESCAPE`，按实现一致即可）
      - `follow_symlinks=True` + 链接指向 root 内 → 通过
      - `IdentityGuard` 用 mock `os.stat` 模拟 `st_dev` 不同 → `CROSS_DEVICE`
      - Windows skip：`@pytest.mark.skipif(sys.platform == "win32", ...)` 跳过符号链接创建
    - _需求：5.10 / 5.11_
    - _设计：§组件与接口 2_
    - _前置：6.1_
  - [x] 6.3 `[refactor]` 把 `common_tools.common_tools` 的字节级实现迁移到 `_common_impl.py`
    - 新建 `epsilon-boot/src/infrastructure/workspace/local_filesystem/_common_impl.py`
    - 提供 4 个模块级函数（全部在 UTF-8 解码/编码之前的**字节层**工作）：
      - `_read_bytes_in_range(host_path: Path, start_line: int | None, end_line: int | None) -> bytes`：迁移 `common_tools.read_file` 的行范围切片逻辑；未指定行范围直接 `path.read_bytes()`；若指定行范围且 UTF-8 解码失败 → raise `UnicodeDecodeError`
      - `_write_bytes_atomically(host_path: Path, content: bytes) -> int`：`parent.mkdir(parents=True, exist_ok=True)` → `tempfile.NamedTemporaryFile(dir=parent, delete=False)` → `os.replace(tmp, host_path)`；跨设备 `OSError(errno.EXDEV)` 时 raise
      - `_edit_with_fallback_match(current_bytes: bytes, old_content: bytes, new_content: bytes) -> bytes`：迁移精确匹配 → 行级去空白模糊匹配逻辑
      - `_render_tree(host_root: Path, rel_workspace_path: str) -> list[tuple[str, bool]]`：迁移 DFS 列表构造
    - 本任务**只移动代码 / 调整签名，不改变外部可见行为**；`common/tools/common_tools.py` 暂保持原样，由任务 13.1 改为薄壳
    - _需求：10.5_
    - _设计：§组件与接口 2 → 关键内部算法 / §迁移与兼容性说明_
    - _前置：1.2_
  - [x] 6.4 `[test]` 单元测试：`_common_impl` 迁移后行为等价
    - 新建 `epsilon-boot/test/infrastructure/workspace/local_filesystem/test_common_impl_unit.py`
    - 用例：
      - `_read_bytes_in_range`：无行范围 = 整文件；有行范围 = 对应行闭区间；行范围超出文件尾 = 剩余行
      - `_write_bytes_atomically`：创建父级目录；原子 rename
      - `_edit_with_fallback_match`：精确匹配成功；行级去空白匹配（空白差异）成功；完全不匹配返回约定失败值
      - `_render_tree`：包含子目录、文件、空目录的混合场景
    - _需求：10.5_
    - _设计：§组件与接口 2_
    - _前置：6.3_

- [x] 7. 基础设施层：LocalFilesystemWorkspace 主体（含 `context` 白名单透传）
  - [x] 7.1 `[impl]` 实现 `LocalFilesystemWorkspace.__init__` + `resolve_path` + `capabilities` + `display_root_hint` + 日志白名单基础设施
    - 新建 `epsilon-boot/src/infrastructure/workspace/local_filesystem/local_workspace.py`
    - 类签名：`class LocalFilesystemWorkspace(Workspace, LocallyMaterializable)`
    - **模块级常量与函数**（新增，供所有 I/O 方法共享）：
      ```python
      _LOG_CONTEXT_WHITELIST: frozenset[str] = frozenset(
          {"tool_name", "trace_id", "agent_id"}
      )

      def _sanitize_context(context: dict | None) -> dict[str, Any]:
          """从 context 中仅提取白名单字段，容忍 None 与未知 key。"""
          if not context:
              return {}
          return {k: v for k, v in context.items() if k in _LOG_CONTEXT_WHITELIST}
      ```
    - 本任务实现以下方法：
      - `__init__(*, root: Path, follow_symlinks: bool, policy: WorkspacePolicy)`：初始化 `_root / _follow_symlinks / _policy / _symlink_guard / _identity_guard / _capabilities`；`_capabilities = WorkspaceCapabilities(supports_symlinks=follow_symlinks, supports_atomic_write=True, supports_append=True, supports_streaming=False, supports_large_files=True, local_materialization=True)`
      - `resolve_path(requested)` → 委托 `self._policy.resolve(requested)`
      - `capabilities()` → `self._capabilities`
      - `display_root_hint()` → `str(self._root)`（决策 3-B）
      - `_to_host_path(path: WorkspacePath) -> Path`：`self._root / path.to_posix().lstrip("/")`，不做 I/O
    - 其余 I/O 方法声明为 `async def ..., *, context: dict | None = None: raise NotImplementedError` 占位（含 `context` 末位 keyword-only），由任务 7.2 - 7.8 分别替换
    - **关键实现约束（贯穿所有 I/O 方法）**：
      - 所有 I/O 方法的 `except` 分支内，结构化日志必须走 `logger.*(extra={..., **_sanitize_context(context)})` 的模式合并白名单字段
      - **禁止**把 `context` 任意字段拼入异常 `message`
      - **禁止**把 `context` 传入 `WorkspaceIoError / WorkspaceNotFoundError / WorkspaceConfinementViolation / WorkspaceUnsupportedOperationError` 的任何构造参数（守住需求 4.4 / 8.6 路径泄露红线）
    - _需求：1.1 / 3.1 / 3.2 / 9.6 / 8.1 / 8.2 / 8.6_
    - _设计：§设计决策表"观测上下文透传"行 / §组件与接口 2 → `context` 透传到结构化日志的示意_
    - _前置：3.1 / 4.1 / 5.1 / 6.1_
  - [x] 7.2 `[impl]` 实现 `LocalFilesystemWorkspace.exists` + `stat`（含 `context` 透传）
    - 修改 `local_workspace.py`
    - `exists(path, *, context=None)`：`_to_host_path` → `SymlinkGuard.check` → `host_path.exists()` 包裹 try；`PermissionError` 翻译为 `WorkspaceIoError(reason="permission_denied")`；`except` 分支 `logger.warning("workspace_io_error", extra={"workspace_backend_kind": "local_filesystem", "operation": "exists", "workspace_path": path.to_posix(), **_sanitize_context(context)})`
    - `stat(path, *, context=None)`：`_to_host_path` → `SymlinkGuard.check` + `IdentityGuard.check` → `os.stat(host_path)`；不存在 → `WorkspaceNotFoundError`；其他 `OSError` → `WorkspaceIoError`；`except` 分支同上模式透传 `context`；返回 `WorkspaceStatEntry(path=path, is_file=S_ISREG, is_dir=S_ISDIR, size=st.st_size, mtime=st.st_mtime)`
    - _需求：1.2 / 4.2 / 8.1 / 8.2_
    - _设计：§组件与接口 2 → `context` 透传到结构化日志的示意_
    - _前置：7.1_
  - [x] 7.3 `[impl]` 实现 `LocalFilesystemWorkspace.read`（含 `context` 透传）
    - `async def read(self, path, *, start_line=None, end_line=None, context=None) -> bytes`
    - 算法：`_to_host_path` → 两 Guard → `_read_bytes_in_range(host_path, start_line, end_line)`
    - 错误翻译：`FileNotFoundError` → `WorkspaceNotFoundError`；`UnicodeDecodeError` → `WorkspaceIoError(reason="decode_failed")`；`PermissionError / OSError` → `WorkspaceIoError`
    - `except` 分支日志示例（从 design §组件与接口 2 复制，不改字段）：
      ```python
      logger.info("workspace_not_found", extra={"workspace_backend_kind": "local_filesystem", "operation": "read", "workspace_path": path.to_posix(), **_sanitize_context(context)})
      logger.warning("workspace_io_error", extra={"workspace_backend_kind": "local_filesystem", "operation": "read", "workspace_path": path.to_posix(), "underlying_error_class": type(e).__name__, **_sanitize_context(context)})
      ```
    - _需求：1.2 / 4.2 / 8.1 / 8.2_
    - _设计：§组件与接口 2 → `context` 透传到结构化日志的示意_
    - _前置：6.3 / 7.1_
  - [x] 7.4 `[impl]` 实现 `LocalFilesystemWorkspace.write`（含 `context` 透传）
    - `async def write(self, path, content: bytes, *, context=None) -> int`
    - 算法：`_to_host_path` → `SymlinkGuard.check(host_path.parent)`（允许目标自身不存在）+ `IdentityGuard.check` → `_write_bytes_atomically(host_path, content)` → 返回字节数
    - 错误翻译：`OSError(errno.EXDEV)` → `WorkspaceIoError(reason="cross_device")`；`PermissionError / OSError` → `WorkspaceIoError`
    - `except` 分支透传 `context` 模式同 7.3
    - _需求：1.2 / 4.2 / 8.1 / 8.2_
    - _设计：§组件与接口 2_
    - _前置：6.3 / 7.1_
  - [x] 7.5 `[impl]` 实现 `LocalFilesystemWorkspace.edit`（含 `fcntl.flock` advisory 锁 + `context` 透传）
    - `async def edit(self, path, old_content: bytes, new_content: bytes, *, context=None) -> int`
    - 算法：
      - `_to_host_path` → 两 Guard
      - `os.open(host_path, os.O_RDWR)` → 非 Windows：`fcntl.flock(fd, fcntl.LOCK_EX)`；Windows（`platform.system() == "Windows"`）跳过加锁并 `logger.warning("Windows 不支持 fcntl.flock，edit 将在无锁下进行", extra={..., **_sanitize_context(context)})`（首次触发时记录一次）
      - 临界区：`os.read(fd) → _edit_with_fallback_match → _write_bytes_atomically`
      - 函数退出前 `os.close(fd)`（自动释放 flock）
      - `flock` 返回 `EAGAIN / EINTR` 翻译为 `WorkspaceIoError(reason="lock_failed")`
      - 未匹配翻译为 `WorkspaceIoError(reason="no_match")`
    - 所有 `except` 日志透传 `context`
    - _需求：1.2 / 4.2 / 8.1 / 8.2（并发保护 + 观测）_
    - _设计：§设计决策表"`edit` 并发保护"行 / §组件与接口 2 → 关键内部算法_
    - _前置：6.3 / 7.1_
  - [x] 7.6 `[impl]` 实现 `LocalFilesystemWorkspace.list_dir`（含 `context` 透传）
    - `async def list_dir(self, path, *, recursive=True, context=None) -> list[WorkspaceStatEntry]`
    - 算法：`_to_host_path` → 两 Guard → `os.scandir`（非 `Path.iterdir`）；`recursive=True` 时内部迭代式 DFS；每个条目以 `path.join(entry.name)` 构建子 `WorkspacePath`（由任务 2.1 `join` 自洽校验）
    - 错误翻译：`FileNotFoundError` → `WorkspaceNotFoundError`；`NotADirectoryError` → `WorkspaceIoError(reason="not_a_directory")`
    - `except` 分支透传 `context`
    - _需求：1.2 / 4.2 / 6.4 / 7.2 / 8.1 / 8.2_
    - _设计：§组件与接口 2_
    - _前置：7.1_
  - [x] 7.7 `[impl]` 实现 `LocalFilesystemWorkspace.delete`（含 `context` 透传）
    - `async def delete(self, path, *, context=None) -> None`
    - 算法：`_to_host_path` → 两 Guard → `host_path.is_dir()` 时 `shutil.rmtree` 否则 `os.unlink`；`FileNotFoundError` → `WorkspaceNotFoundError`；其他 `OSError` → `WorkspaceIoError`
    - 方法 docstring 明确"本方法不对 LLM 直接暴露，仅供后端内部使用（例如 edit 回滚）"
    - `except` 分支透传 `context`
    - _需求：1.2 / 4.2 / 8.1 / 8.2_
    - _设计：§组件与接口 2_
    - _前置：7.1_
  - [x] 7.8 `[impl]` 实现 `LocalFilesystemWorkspace.materialize_cwd`
    - `def materialize_cwd(self, path: WorkspacePath) -> str`（**同步方法，无 `context` 参数**：`LocallyMaterializable` 协议不接受 `context`）
    - 按 design §组件与接口 2：`_to_host_path` → 两 Guard → `host_path.is_dir()` 校验（非目录抛 `WorkspaceIoError(reason="not_a_directory")`） → 返回 `str(host_path)`
    - 方法 docstring 明确"此方法是本地后端对工具层暴露的唯一物理路径出口，其返回值绝不能被放回工具的对外参数或成功消息中"
    - _需求：6.6_
    - _设计：§组件与接口 1 `LocallyMaterializable` / §组件与接口 2_
    - _前置：7.1_
  - [x] 7.9 `[impl]` 在 `infrastructure/workspace/__init__.py` 与 `local_filesystem/__init__.py` 补充导出
    - `infrastructure/workspace/local_filesystem/__init__.py`：导出 `LocalFilesystemWorkspace`
    - `infrastructure/workspace/__init__.py`：可选导出 `LocalFilesystemWorkspace`、`workspace_config`
    - _需求：9.6_
    - _设计：§架构 → 包/目录结构_
    - _前置：7.1 / 5.1_
  - [x] 7.10 `[test]` 单元测试：`LocalFilesystemWorkspace` 每个方法 happy-path + 常见错误
    - 新建 `epsilon-boot/test/infrastructure/workspace/local_filesystem/test_local_workspace_unit.py`
    - 用例（每个用例以 `tmp_path` 做 root）：
      - `exists / stat`：存在 / 不存在 / 非目录
      - `read`：整文件 / 行范围 / 二进制文件+行范围 → `WorkspaceIoError(decode_failed)` / 不存在 → `WorkspaceNotFoundError`
      - `write`：成功写入字节数；父级目录自动创建
      - `list_dir`：递归 / 非递归；空目录；不存在 → `WorkspaceNotFoundError`
      - `delete`：文件 / 目录 / 不存在
      - `materialize_cwd`：目录返回宿主路径字符串；非目录抛 `WorkspaceIoError`
      - **所有 I/O 方法**：传入 `context={"tool_name": "read_file", "trace_id": "t1"}` 与 `context=None` 均不改变 happy-path 输出（纯观测透传不改 I/O 行为）
    - _需求：1.2 / 4.2 / 6.4 / 6.6 / 8.1 / 8.2_
    - _设计：§组件与接口 2_
    - _前置：7.2 - 7.9_
  - [x] 7.11 `[test]` 单元测试：`edit` 并发互斥 + Windows 降级
    - 新建 `epsilon-boot/test/infrastructure/workspace/local_filesystem/test_local_workspace_edit_lock_unit.py`
    - 用例：
      - POSIX：`threading.Barrier(2)` + 两个 `loop.run_in_executor` Task 同时 edit 同一文件，断言最终文件内容为两次 edit 的串行叠加
      - Windows：通过 `monkeypatch.setattr("platform.system", lambda: "Windows")` 模拟 Windows 环境，断言 `edit` 正常完成 + 触发一次 `warning` 级别日志（使用 `caplog` fixture）
      - `flock` `EAGAIN` 模拟：`monkeypatch` mock `fcntl.flock` 抛 `BlockingIOError(EAGAIN)` → 断言抛 `WorkspaceIoError(reason="lock_failed")`
    - _需求：4.2（并发保护）_
    - _设计：§设计决策表"`edit` 并发保护" / §事务与并发边界_
    - _前置：7.5_
  - [x] 7.12 `[test]` 单元测试：`_sanitize_context` 白名单过滤 + 异常消息不含 `context` 字段
    - 新建 `epsilon-boot/test/infrastructure/workspace/local_filesystem/test_context_sanitize_unit.py`
    - 用例：
      - `_sanitize_context(None)` → `{}`
      - `_sanitize_context({})` → `{}`
      - `_sanitize_context({"tool_name": "read_file"})` → `{"tool_name": "read_file"}`
      - `_sanitize_context({"tool_name": "read_file", "trace_id": "t1", "agent_id": "a1"})` → 三字段全保留
      - `_sanitize_context({"tool_name": "read_file", "secret": "xxx", "password": "yyy"})` → 只保留 `tool_name`，`secret / password` 被过滤
      - `_sanitize_context({"unknown_key": "value"})` → `{}`（白名单之外的键一律过滤）
      - **关键负向断言**：构造 `WorkspaceIoError`（或任一领域错误）→ 断言其 `message` 字段不含 "tool_name" / "trace_id" / "agent_id" 字面量；断言其 `__dict__` / 构造参数中不存在 `context` 字段（通过 `inspect.signature` 验证构造签名）
    - _需求：4.4 / 8.6（路径泄露红线）_
    - _设计：§设计决策表"观测上下文透传"行 / §组件与接口 2 → `context` 透传示意的"注意"行_
    - _前置：7.1 / 2.5_
  - [x] 7.13 `[test]` 属性测试：`_to_host_path` 始终位于 root 之下
    - 新建 `epsilon-boot/test/infrastructure/workspace/local_filesystem/test_local_workspace_property.py`
    - Hypothesis 策略：随机字符串 s，若 `ws.resolve_path(s)` 成功得 `wp`，则 `os.path.commonpath([str(ws._to_host_path(wp)), str(ws._root)]) == str(ws._root)`（Property 1）
    - _需求：2.2 / 5.10 / 5.11 / 6.3（Property 1）_
    - _设计：§正确性属性 1_
    - _前置：7.1_
  - [x] 7.14 `[checkpoint]` 基础设施适配器完成度校验
    - 运行 `uv run pytest test/domain/workspace/ test/infrastructure/workspace/ -q`
    - 运行 `uv run python -m compileall src/infrastructure/workspace`
    - 确认新模块无循环 import：`uv run python -c "from infrastructure.workspace.local_filesystem import LocalFilesystemWorkspace; print('ok')"`
    - 运行 `uv run python -c "from infrastructure.workspace.local_filesystem.local_workspace import _LOG_CONTEXT_WHITELIST, _sanitize_context; print(sorted(_LOG_CONTEXT_WHITELIST))"` 预期输出 `['agent_id', 'tool_name', 'trace_id']`
    - _需求：1.1 / 4.2 / 8.1 / 8.2 / 8.6 / 9.6 / 9.8_
    - _设计：§架构_
    - _前置：5.2 / 6.2 / 6.4 / 7.10 / 7.11 / 7.12 / 7.13_

- [x] 8. 配置与 DI 装配
  - [x] 8.1 `[config]` 在 `config.properties` 新增 Workspace 配置块
    - 修改 `epsilon-boot/config.properties`
    - 新增配置块（位置在"Shell 命令执行工具配置"之前，与 exec 类配置相邻）：
      ```properties
      # -------------------------------------------
      # Workspace 工作区配置
      # -------------------------------------------
      # Workspace 后端种类，本期仅支持 local_filesystem
      WORKSPACE_BACKEND=local_filesystem
      # 工作区根目录的宿主绝对路径，Agent 的文件影响面被锁定在此目录之内
      WORKSPACE_ROOT=
      # 是否允许解引用符号链接（解引用后仍必须落在工作区之内），默认 false 更严格
      WORKSPACE_FOLLOW_SYMLINKS=false
      # 当 WORKSPACE_ROOT 不存在时是否自动创建（含父级），默认 false 触发启动失败
      WORKSPACE_CREATE_IF_MISSING=false
      ```
    - `WORKSPACE_ROOT` 刻意留空，确保未配置用户会在启动期收到 fail-fast 错误
    - _需求：5.13 / 10.2_
    - _设计：§数据模型 → 配置键_
    - _前置：5.1_
  - [x] 8.2 `[impl]` 实现 `_create_local_filesystem_workspace` 工厂
    - 修改 `epsilon-boot/src/application/container_config.py`
    - 新增私有函数 `_create_local_filesystem_workspace(cfg: WorkspaceConfig) -> Workspace`：
      1. `cfg.root` 为空 → `ConfigurationError("WORKSPACE_ROOT 未配置，服务拒绝启动")`
      2. `Path(cfg.root).is_absolute()` 检查；相对路径拒绝
      3. 不存在 + `create_if_missing=False` → `ConfigurationError`
      4. 不存在 + `create_if_missing=True` → `Path.mkdir(parents=True, exist_ok=True)`
      5. 存在但不是目录 → `ConfigurationError`
      6. `os.access(root, os.R_OK | os.W_OK)` 失败 → `ConfigurationError("WORKSPACE_ROOT 缺失 {缺失位} 权限")`
      7. 构造 `WorkspacePolicy()` 与 `LocalFilesystemWorkspace(root=root.resolve(), follow_symlinks=cfg.follow_symlinks, policy=policy)`
    - `ConfigurationError` 沿用仓库现有异常类（若不存在则新增于 `common/exceptions.py`）
    - _需求：5.4 / 5.5 / 5.6 / 5.7 / 5.8 / 5.9_
    - _设计：§组件与接口 6 / §启动期序列图_
    - _前置：5.1 / 7.1_
  - [x] 8.3 `[impl]` 新增 `_WORKSPACE_BACKEND_FACTORIES` 分发表 + `_init_workspace` / `_cleanup_workspace`
    - 修改 `epsilon-boot/src/application/container_config.py`
    - 模块级：`_WORKSPACE_BACKEND_FACTORIES: dict[WorkspaceBackendKind, Callable[[WorkspaceConfig], Workspace]] = {WorkspaceBackendKind.LOCAL_FILESYSTEM: _create_local_filesystem_workspace}`
    - 模块级 `_workspace_singleton: Workspace | None = None`
    - `async def _init_workspace()`：按 design §组件与接口 6；若 `factory is None` → `ConfigurationError(f"不支持的 WORKSPACE_BACKEND 值：{workspace_config.backend.value}")`；成功后 `logger.info("Workspace 初始化完成：backend=%s，local_materialization=%s", ...)`
    - `async def _cleanup_workspace()`：无状态 no-op
    - _需求：5.2 / 5.4 / 9.1 / 9.2_
    - _设计：§组件与接口 6_
    - _前置：8.2_
  - [x] 8.4 `[impl]` 在 `configure_container()` 注册 Workspace 资源
    - 修改 `configure_container()`
    - 在已有 `container.register_async_resource("database", ...)` 之后、`container.register(ToolRegistry, _create_tool_registry, Scope.SINGLETON)` **之前**追加：
      ```python
      container.register_async_resource("workspace", _init_workspace, _cleanup_workspace)
      container.register(Workspace, lambda: _workspace_singleton, Scope.SINGLETON)
      ```
    - _需求：9.1 / 9.2 / 9.3_
    - _设计：§组件与接口 6_
    - _前置：8.3_
  - [x] 8.5 `[test]` 单元测试：DI 装配顺序 + 启动期 fail-fast
    - 新建 `epsilon-boot/test/application/test_workspace_container_integration.py`
    - 用例：
      - happy-path：`WORKSPACE_ROOT=<tmp_path>` + `configure_container()` + `container.start()` → 能 `container.resolve(Workspace)` 得到实例，`capabilities().local_materialization is True`
      - `WORKSPACE_ROOT=""` → `container.start()` 抛 `ConfigurationError`（需求 5.5）
      - `WORKSPACE_ROOT` 指向文件而非目录 → `ConfigurationError`（需求 5.8）
      - `WORKSPACE_BACKEND=oss`（monkeypatch 绕过 validator） → `_init_workspace` 抛 `ConfigurationError`（需求 5.4）
      - 装配顺序：`Workspace` 注册在 `ToolRegistry` 之前
    - _需求：5.4 / 5.5 / 5.8 / 5.9 / 9.1 / 9.2（Property 7）_
    - _设计：§正确性属性 7_
    - _前置：8.4_
  - [x] 8.6 `[test]` 单元测试：`SHELL_EXEC_WORKING_DIR` / `PYTHON_EXEC_WORKING_DIR` 与 Workspace 的二次校验（11.3 落地后 xfail 已移除）
    - 新建 `epsilon-boot/test/application/test_workspace_exec_working_dir_validation.py`
    - 用例：配 `WORKSPACE_ROOT=/tmp/ws` + `SHELL_EXEC_WORKING_DIR=/etc`（越界）→ `container.start()` fail-fast 并给出中文错误消息提示"请将 SHELL_EXEC_WORKING_DIR / PYTHON_EXEC_WORKING_DIR 设置到工作区内，或留空使用默认"
    - 实现侧：本测试依赖任务 11.3 在 `_create_tool_registry` 中对两个 exec 配置做 `workspace.resolve_path(cfg.working_dir)` 的二次校验
    - _需求：10.3_
    - _设计：§迁移与兼容性说明_
    - _前置：8.4_

- [x] 9. 工具层改造：4 个受控文件工具（调用 Port 时注入 `context`）
  - [x] 9.1 `[impl]` 改造 `ReadFileTool`（调用 Port 时构造并传入 `context`）
    - 修改 `epsilon-boot/src/infrastructure/tools/filesystem/read_file_tool.py`
    - 构造签名改为 `__init__(self, workspace: Workspace) -> None`
    - `description` 改为动态 property：`workspace_root = self._workspace.display_root_hint()`；返回文案对齐 design §组件与接口 5：`f"读取工作区内指定文件的内容。路径相对于工作区根 {workspace_root} 解析，使用 POSIX 正斜杠分隔符。支持通过 offset/limit 分页读取大文件。"`（决策 3-B）
    - `execute` 流程：
      1. **构造 `context`**：
         ```python
         context: dict[str, Any] = {"tool_name": self.name}  # self.name == "read_file"
         trace_id = _current_trace_id_or_none()  # 从 common.logging.trace_context 或等价 ContextVar 读取
         if trace_id is not None:
             context["trace_id"] = trace_id
         agent_id = _current_agent_id_or_none()
         if agent_id is not None:
             context["agent_id"] = agent_id
         ```
      2. `ws_path = self._workspace.resolve_path(file_path)`（不接受 `context`）
      3. `raw = await self._workspace.read(ws_path, start_line=offset, end_line=offset+limit-1, context=context)`
      4. `raw.decode("utf-8", errors="replace")` → 行号拼装
    - `WorkspaceConfinementViolation / WorkspaceNotFoundError / WorkspaceIoError` 逐个翻译为 `ToolExecutionError(tool_name=self.name, message=<中文模板>)`；**翻译时不得引用 `context` 任何字段**
    - 移除对 `common/tools/common_tools.read_file` 的直接依赖；行号拼装函数 `_render_with_line_numbers` 下沉到本文件（或新建 `infrastructure/tools/filesystem/_rendering.py`）
    - 禁止 import `os / pathlib / open`
    - _需求：1.2 / 4.3 / 4.4 / 6.1 / 6.2 / 6.3 / 6.5 / 7.1 / 7.4 / 8.1 / 8.2_
    - _设计：§设计决策表"观测上下文透传"行 / §组件与接口 5_
    - _前置：4.1 / 7.3 / 7.9_
  - [x] 9.2 `[test]` 单元测试：`ReadFileTool`
    - 新建 `epsilon-boot/test/infrastructure/tools/filesystem/test_read_file_tool_unit.py`
    - 用例：
      - happy-path：相对路径 / 绝对 `/notes.md` 均读取成功
      - 越界 `../etc/passwd` → `ToolExecutionError(message=含"超出工作区边界")` 且**不含**宿主绝对路径
      - `WorkspaceNotFoundError` → `ToolExecutionError(message="路径 /xxx 不存在")`
      - `description` 包含 `display_root_hint()` 返回值（用 mock workspace 验证）
      - `execute` 源码不 import `os` / `pathlib`（AST 扫描断言）
      - **关键新增**：用 `mock workspace` 验证 `read` 被调用时 `context` 参数包含 `{"tool_name": "read_file"}`（用 `mock.call_args.kwargs["context"]` 断言）
    - _需求：4.3 / 4.4 / 6.1 / 6.2 / 6.5 / 7.4 / 8.1 / 8.2_
    - _设计：§设计决策表"观测上下文透传"行_
    - _前置：9.1_
  - [x] 9.3 `[impl]` 改造 `WriteFileTool`（调用 Port 时注入 `context`）
    - 修改 `epsilon-boot/src/infrastructure/tools/filesystem/write_file_tool.py`
    - 构造签名 `__init__(self, workspace: Workspace)`；`description` 动态 property 同 9.1 模板
    - `execute`：构造 `context={"tool_name": self.name, "trace_id": ..., "agent_id": ...}` → `resolve_path → write(ws_path, content.encode("utf-8"), context=context)`；成功返回 `"成功写入文件 {ws_path}，共 N 字节"`（`ws_path` 使用 `WorkspacePath.to_posix()`）
    - 错误翻译表与 9.1 一致
    - _需求：1.2 / 6.1 / 6.2 / 6.3 / 6.5 / 7.4 / 8.1 / 8.2_
    - _设计：§设计决策表"观测上下文透传"行 / §组件与接口 5_
    - _前置：4.1 / 7.4 / 7.9_
  - [x] 9.4 `[test]` 单元测试：`WriteFileTool`
    - 新建 `epsilon-boot/test/infrastructure/tools/filesystem/test_write_file_tool_unit.py`
    - 用例：成功消息含 `{ws_path}` 逻辑路径而非宿主绝对路径（需求 7.4）；父级目录自动创建；越界 → `ToolExecutionError`；**mock workspace 验证 `write` 调用时 `context["tool_name"] == "write_file"`**
    - _需求：6.1 / 6.2 / 6.5 / 7.4 / 8.1 / 8.2_
    - _设计：§组件与接口 5_
    - _前置：9.3_
  - [x] 9.5 `[impl]` 改造 `EditFileTool`（调用 Port 时注入 `context`）
    - 修改 `epsilon-boot/src/infrastructure/tools/filesystem/edit_file_tool.py`
    - 构造签名 `__init__(self, workspace: Workspace)`；`description` 动态 property
    - `execute`：构造 `context` → `resolve_path → edit(ws_path, old_str.encode("utf-8"), new_str.encode("utf-8"), context=context)`；`old_str == ""` 继续拒绝；成功返回 `"成功编辑文件 {ws_path}，共 N 字节"`
    - `WorkspaceIoError(reason="no_match")` → `ToolExecutionError(message="未在文件 {ws_path} 中找到匹配文本")`
    - `WorkspaceIoError(reason="lock_failed")` → `ToolExecutionError(message="文件 {ws_path} 锁获取失败，请稍后重试")`
    - _需求：1.2 / 6.1 / 6.2 / 6.3 / 6.5 / 7.4 / 8.1 / 8.2_
    - _设计：§组件与接口 5_
    - _前置：4.1 / 7.5 / 7.9_
  - [x] 9.6 `[test]` 单元测试：`EditFileTool`
    - 新建 `epsilon-boot/test/infrastructure/tools/filesystem/test_edit_file_tool_unit.py`
    - 用例：精确匹配成功；模糊匹配成功；未匹配 → 专属错误文案；`old_str=""` 拒绝；越界；**`context["tool_name"] == "edit_file"`**
    - _需求：6.1 / 6.2 / 6.5 / 7.4 / 8.1 / 8.2_
    - _设计：§组件与接口 5_
    - _前置：9.5_
  - [x] 9.7 `[impl]` 改造 `ListDirTool`（调用 Port 时注入 `context`）
    - 修改 `epsilon-boot/src/infrastructure/tools/filesystem/list_dir_tool.py`
    - 构造签名 `__init__(self, workspace: Workspace)`；`description` 动态 property
    - `execute`：`directory_path = kwargs.get("directory_path", "") or "/"` → 空串 / `.` / `/` 统一映射工作区根（需求 6.4 / 7.2）→ 构造 `context` → `resolve_path → list_dir(ws_path, recursive=True, context=context)` → 自行拼装树形输出
    - 返回行文本中的每条路径使用 `WorkspacePath.to_posix()`（需求 7.4）
    - _需求：1.2 / 6.1 / 6.2 / 6.4 / 6.5 / 7.2 / 7.4 / 8.1 / 8.2_
    - _设计：§组件与接口 5_
    - _前置：4.1 / 7.6 / 7.9_
  - [x] 9.8 `[test]` 单元测试：`ListDirTool`
    - 新建 `epsilon-boot/test/infrastructure/tools/filesystem/test_list_dir_tool_unit.py`
    - 用例：空串 / `.` / `/` 均返回根列表；嵌套目录；返回文本中路径以 `/` 起始且不含宿主绝对路径；**`context["tool_name"] == "list_dir"`**
    - _需求：6.4 / 7.2 / 7.4 / 8.1 / 8.2_
    - _设计：§组件与接口 5_
    - _前置：9.7_
  - [x] 9.9 `[test]` 属性测试：工具层源码无后端类型判断
    - 新建 `epsilon-boot/test/infrastructure/tools/filesystem/test_tool_no_backend_branch_property.py`
    - 用 Python `ast` 模块扫描 `read_file_tool.py / write_file_tool.py / edit_file_tool.py / list_dir_tool.py / shell_exec_tool.py / python_exec_tool.py` 的源代码，断言：
      - 不出现 `LocalFilesystemWorkspace` 字面量（可作为 Name / Attribute 访问）
      - 不出现 `isinstance(..., LocalFilesystemWorkspace)` 或等价表达式
      - 允许出现 `LocallyMaterializable` 类型检查
    - _需求：3.5（Property 6）_
    - _设计：§正确性属性 6_
    - _前置：9.1 / 9.3 / 9.5 / 9.7 / 10.1 / 11.1_
  - [x] 9.10 `[checkpoint]` 4 个受控文件工具改造完成度
    - 运行 `uv run pytest test/infrastructure/tools/filesystem/ -q`
    - 运行 `uv run python -m compileall src/infrastructure/tools/filesystem`
    - _需求：6.1 / 6.2 / 6.3 / 6.4 / 6.5 / 7.4_
    - _设计：§架构_
    - _前置：9.2 / 9.4 / 9.6 / 9.8_

- [x] 10. 工具层改造：ShellExecTool
  - [x] 10.1 `[impl]` 改造 `ShellExecTool`（调用 Port 时注入 `context`）
    - 修改 `epsilon-boot/src/infrastructure/tools/shell_exec/shell_exec_tool.py`
    - 构造签名新增 `workspace: Workspace`；保留 `timeout / max_output_size / default_working_dir`
    - `description` 动态 property：文案追加"路径相对于工作区根 `{workspace_root}` 解析"
    - `parameters` schema 中保留 `working_dir` 字段，description 追加"工作区相对路径，必须位于工作区内"
    - `execute` 开头：`caps = self._workspace.capabilities()`；`if not caps.local_materialization: raise ToolExecutionError("当前工作区后端不支持本地命令执行", tool_name=self.name)`
    - 再：`requested = kwargs.get("working_dir") or self._default_working_dir or "/"` → `ws_path = self._workspace.resolve_path(requested)` → `host_cwd = self._workspace.materialize_cwd(ws_path)`（注意：`materialize_cwd` 无 `context` 参数）
    - `WorkspaceConfinementViolation` → `ToolExecutionError(message=f"工作目录 {requested} 超出工作区边界")`
    - 既有的环境变量剥离规则（API_KEY / PASSWORD / SECRET / TOKEN）保持不变（需求 6.12）；subprocess 创建行的 `cwd=host_cwd`
    - _需求：1.2 / 6.1 / 6.6 / 6.7 / 6.9 / 6.11 / 6.12_
    - _设计：§组件与接口 5_
    - _前置：4.1 / 7.8 / 7.9_
  - [x] 10.2 `[test]` 单元测试：`ShellExecTool`
    - 新建 `epsilon-boot/test/infrastructure/tools/shell_exec/test_shell_exec_tool_unit.py`
    - 用例：
      - mock workspace `capabilities.local_materialization=False` → 立即 `ToolExecutionError("当前工作区后端不支持本地命令执行")`
      - `working_dir` 越界 → `ToolExecutionError`
      - `working_dir` 省略 / 空串 → 默认走工作区根
      - 子进程被正确启动且 `cwd=host_cwd`（用 `mock.patch("asyncio.create_subprocess_exec")` 验证传参）
      - 环境变量剥离规则仍生效
    - _需求：6.6 / 6.7 / 6.9 / 6.11 / 6.12_
    - _设计：§组件与接口 5_
    - _前置：10.1_

- [x] 11. 工具层改造：PythonExecTool + DI 注入
  - [x] 11.1 `[impl]` 改造 `PythonExecTool`
    - 修改 `epsilon-boot/src/infrastructure/tools/python_exec/python_exec_tool.py`
    - 构造签名新增 `workspace: Workspace`；`description` 动态 property 追加路径说明
    - `execute` 开头同 10.1：`caps.local_materialization` 守卫
    - 子进程 `cwd` 通过 `self._workspace.resolve_path("/") → materialize_cwd(ws_path)` 取得
    - **不改动** AST 静态分析、`allowed_modules`、内存限制等既有沙箱逻辑（需求 6.10）
    - _需求：1.2 / 6.1 / 6.6 / 6.7 / 6.10 / 6.11_
    - _设计：§组件与接口 5_
    - _前置：4.1 / 7.8 / 7.9_
  - [x] 11.2 `[test]` 单元测试：`PythonExecTool`
    - 新建 `epsilon-boot/test/infrastructure/tools/python_exec/test_python_exec_tool_unit.py`
    - 用例：`local_materialization=False` 拒绝；子进程 `cwd` 等于 `materialize_cwd("/")`；AST 黑名单保持不变
    - _需求：6.7 / 6.10 / 6.11_
    - _设计：§组件与接口 5_
    - _前置：11.1_
  - [x] 11.3 `[impl]` 在 `_create_tool_registry` 注入 `workspace` + 对 exec 配置做二次校验
    - 修改 `epsilon-boot/src/application/container_config.py` 的 `_create_tool_registry()`
    - 函数首行：`ws = await container.resolve(Workspace)`
    - 将 4 个文件工具实例化改为 `ReadFileTool(workspace=ws) / WriteFileTool(workspace=ws) / EditFileTool(workspace=ws) / ListDirTool(workspace=ws)`
    - 构造 `ShellExecTool` / `PythonExecTool` 前：若其配置 `working_dir` 非空，执行 `ws.resolve_path(cfg.working_dir)`；`WorkspaceConfinementViolation` 翻译为 `ConfigurationError("请将 SHELL_EXEC_WORKING_DIR / PYTHON_EXEC_WORKING_DIR 设置到工作区内，或留空使用默认")`
    - 传入工具构造：`ShellExecTool(workspace=ws, timeout=..., default_working_dir=cfg.working_dir or "")`，PythonExecTool 同理
    - _需求：6.11 / 9.3 / 10.3_
    - _设计：§组件与接口 6 / §迁移与兼容性说明_
    - _前置：9.1 / 9.3 / 9.5 / 9.7 / 10.1 / 11.1_
  - [x] 11.4 `[checkpoint]` 全部 6 个受控工具改造完成度
    - 运行 `uv run pytest test/infrastructure/tools/ -q`
    - 运行 `uv run python -m compileall src/infrastructure/tools`
    - 本地起服务 smoke：`uv run python -c "import asyncio; from application.container_config import configure_container; from common.container import container; import os; os.environ['WORKSPACE_ROOT']=os.getcwd(); configure_container(); asyncio.run(container.start()); print('ok'); asyncio.run(container.stop())"`
    - _需求：6.1 / 6.6 / 6.7 / 6.10 / 6.11 / 9.1 / 9.3_
    - _设计：§架构_
    - _前置：9.10 / 10.2 / 11.2 / 11.3_

- [x] 12. ChatConfig.system_prompt 追加（`infrastructure/chat/chat_config.py` + `model_validator(mode="after")` 幂等）
  - [x] 12.1 `[impl]` 在 `infrastructure/chat/chat_config.py` 追加 `_append_workspace_path_guidance` 校验器
    - 修改 `epsilon-boot/src/infrastructure/chat/chat_config.py`（**设计已确认实际路径**：design §组件与接口 8 首段"架构层级澄清"）
    - 模块级新增硬编码字符串常量：
      ```python
      _WORKSPACE_PATH_GUIDANCE: str = (
          "\n\n所有文件路径使用工作区相对的 POSIX 路径，以 / 分隔。"
      )
      ```
    - `ChatConfig` 类内新增 `@model_validator(mode="after")` 方法 `_append_workspace_path_guidance`，**与同文件现有的 `_clamp_max_tool_rounds(mode="before")` 并存**：
      ```python
      @model_validator(mode="after")
      def _append_workspace_path_guidance(self) -> "ChatConfig":
          """在 system_prompt 末尾追加工作区路径规范说明（幂等）。

          无论 system_prompt 来自默认值还是 CHAT_SYSTEM_PROMPT 环境变量覆盖，
          本约束都应守住（需求 7.3）。幂等判断避免多次加载或重复校验时造成
          文案堆叠。
          """
          prompt = self.system_prompt
          if not prompt.rstrip().endswith(_WORKSPACE_PATH_GUIDANCE.strip()):
              self.system_prompt = prompt + _WORKSPACE_PATH_GUIDANCE
          return self
      ```
    - 关键实现约束：
      - 使用 `prompt.rstrip().endswith(_WORKSPACE_PATH_GUIDANCE.strip())` 做**幂等判断**以防重复追加（多次加载 / 重复校验时不堆叠）
      - 不引入新的配置项
      - 位置在任何 `%(...)s` 占位符之后（不影响占位符解析）
      - `system_prompt` 字段类型与签名保持不变
    - 拒绝方案 X（直接改默认值字符串）：设计明确理由是无法覆盖 `CHAT_SYSTEM_PROMPT` 环境变量自定义场景
    - _需求：7.3（决策 4-A）_
    - _设计：§组件与接口 8（架构层级澄清 + 方案 Y 实现 + rejected alternative 方案 X）_
    - _前置：无（本任务独立于 Workspace 链路）_
  - [x] 12.2 `[test]` 单元测试：`ChatConfig.system_prompt` 幂等追加生效 + 环境变量覆盖 + 重复构造不堆叠
    - 新建 `epsilon-boot/test/infrastructure/chat/test_chat_config_system_prompt_unit.py`（与代码 `ChatConfig` 实际位置 `infrastructure/chat/` 镜像）
    - 用例：
      - **默认追加**：默认实例化 → `system_prompt` 以 `"所有文件路径使用工作区相对的 POSIX 路径，以 / 分隔。"` 结尾（容忍前置换行 / 空白）
      - **环境变量覆盖仍追加**：`monkeypatch.setenv("CHAT_SYSTEM_PROMPT", "custom prompt")` → 重新构造 `ChatConfig` → 断言 `system_prompt` 以 `"custom prompt" + "\n\n所有文件路径使用工作区相对的 POSIX 路径，以 / 分隔。"` 形式结尾（环境变量覆盖不影响追加，需求 7.3 拒绝方案 X 的核心理由）
      - **幂等（关键）**：对同一 `ChatConfig` 实例手动再次调用 `_append_workspace_path_guidance`（或通过 `model_validate` 重新校验 `ChatConfig.model_validate(cfg.model_dump())`）→ 断言 `system_prompt` 中该文案**只出现一次**（`cfg.system_prompt.count("所有文件路径使用工作区相对的 POSIX 路径，以 / 分隔。") == 1`）
      - **环境变量已含该文案**：`monkeypatch.setenv("CHAT_SYSTEM_PROMPT", "custom\n\n所有文件路径使用工作区相对的 POSIX 路径，以 / 分隔。")` → 构造后文案不被重复追加（`count == 1`）
    - _需求：7.3_
    - _设计：§组件与接口 8 → 实现方案（方案 Y 幂等判断）_
    - _前置：12.1_

- [x] 13. 观测、薄壳、文档同步
  - [x] 13.1 `[refactor]` 将 `common/tools/common_tools.py` 改为薄壳
    - 修改 `epsilon-boot/src/common/tools/common_tools.py`
    - 在模块 docstring 顶部追加："仅供 `infrastructure.workspace.local_filesystem.LocalFilesystemWorkspace` 内部使用；外部调用方必须经由 `Workspace` 抽象，禁止直接调用"
    - `read_file / write_file / edit_file / tree` 四个公共函数内部改为调用 `infrastructure.workspace.local_filesystem._common_impl` 对应私有函数，保留**现有签名与行为**；每个函数 docstring 首行加上"已内部迁移至 `_common_impl`，本入口后续将被删除"
    - _需求：10.5_
    - _设计：§迁移与兼容性说明_
    - _前置：6.3_
  - [x] 13.2 `[test]` 单元测试：结构化日志字段与敏感信息脱敏
    - 新建 `epsilon-boot/test/infrastructure/workspace/local_filesystem/test_local_workspace_logging_unit.py`
    - 用例（使用 pytest `caplog`）：
      - 触发 `WorkspaceConfinementViolation`（如对 `read` 传入 `context={"tool_name": "read_file", "trace_id": "t1"}` 后触发越界）→ 日志 `extra` 字段含 `workspace_backend_kind="local_filesystem"`、`violation_reason="absolute_outside"`、`tool_name="read_file"`、`trace_id="t1"`（证明 `context` 白名单字段成功透传）
      - 触发 `WorkspaceIoError` → 日志 `extra` 字段含 `tool_name / trace_id`（白名单字段）
      - 含 `token=abcdef` 的 `requested_path` → 日志中 `abcdef` 被替换为 `***`（长度保留）
      - **关键负向断言**：`ToolExecutionError.message` 不含 `workspace_root` 宿主路径、不含 `trace_id` / `agent_id` 任何字段（验证需求 4.4 / 8.6 红线）
      - 当 `context` 含白名单外的键（如 `secret`）→ 日志 `extra` 不包含该键
    - _需求：8.1 / 8.3 / 8.6_
    - _设计：§设计决策表"观测上下文透传"行 / §组件与接口 2 → `context` 透传示意 / §错误处理_
    - _前置：7.2 / 7.3 / 7.4 / 7.5 / 7.6 / 7.7_
  - [x] 13.3 `[test]` 静态检查：6 个工具在调用 Port 时注入合法 `context`（含 `tool_name` 键）【可选 P2】
    - [x]* 13.3 新建 `epsilon-boot/test/infrastructure/tools/test_tool_context_injection_static.py`
    - 用 Python `ast` 扫描 6 个工具 `execute` 方法：
      - 在 `await self._workspace.<io_method>(...)` 调用处，断言 kwargs 中存在 `context=<expr>`
      - 断言 `context` 参数的构造 dict 字面量（或同函数内定义的 `context: dict = {...}`）包含字符串键 `"tool_name"`
      - `<io_method>` 在 `{"exists", "stat", "read", "write", "edit", "list_dir", "delete"}` 中时必须携带 `context`；其他（`resolve_path` / `capabilities` / `display_root_hint` / `materialize_cwd`）不得携带
    - 本任务标注为**可选**（`[ ]*`），主要价值在于维护期防止工具改造漂移；非阻塞主线合并
    - _需求：8.1 / 8.2_
    - _设计：§设计决策表"观测上下文透传"行 / §开放问题（已决策条目）_
    - _前置：9.1 / 9.3 / 9.5 / 9.7 / 10.1 / 11.1_
  - [x] 13.4 `[docs]` 同步 `docs/tools.md` 工具系统说明
    - 修改 `docs/tools.md`
    - 在"文件系统工具（始终注册）"小节追加："本期起所有文件系统工具通过注入的 `Workspace`（`domain.workspace.Workspace`）完成 I/O；`file_path / directory_path / working_dir` 均为工作区相对 POSIX 路径，解析后不得越出 `WORKSPACE_ROOT`"
    - 在"代码执行工具"小节追加"当 `Workspace.capabilities.local_materialization=False` 时 ShellExecTool / PythonExecTool 拒绝执行（本期后端恒为 True）"
    - 追加指向 `docs/spec/workspace/design.md` 的链接（可选）
    - _需求：9.8_
    - _设计：§迁移与兼容性说明_
    - _前置：9.10 / 11.4_
  - [x] 13.5 `[test]` 集成测试：端到端 Workspace 接入
    - 新建 `epsilon-boot/test/application/test_workspace_end_to_end_integration.py`
    - 用例（走完整 `configure_container()` → `container.start()` → 解析 `ToolRegistry`）：
      - `ScopedToolRegistry` 场景下对 `read_file` 传入 `../etc/passwd` → `ToolExecutionError` 以 ToolMessage 形式回传（不终止 Agent Loop，需求 8.5）
      - `write_file` 成功消息使用逻辑路径
      - `list_dir("/")` 返回条目路径为 `/`-起始逻辑路径
      - `ShellExecTool`（若 `SHELL_EXEC_ENABLED=true`）cwd 落在 `WORKSPACE_ROOT` 之内
      - 启动期 `SHELL_EXEC_WORKING_DIR=/etc` → `container.start()` fail-fast
    - _需求：6.1 / 6.11 / 8.5 / 9.1 / 10.1 / 10.3_
    - _设计：§测试策略 → 集成测试_
    - _前置：8.5 / 11.4 / 12.2 / 13.2_
  - [x] 13.6 `[checkpoint]` 全量校验
    - 运行 `uv run pytest -q` 全量通过
    - 运行 `uv run python -m compileall src/`
    - （可选）`uv run pyright src/domain/workspace src/infrastructure/workspace src/infrastructure/tools` 做类型检查
    - 确认 `config.properties` 包含 4 个 `WORKSPACE_*` 键
    - 确认 `infrastructure/workspace/oss/README.md` 存在且无 `__init__.py`
    - 肉眼走查：`grep -nR "from domain.workspace.policy" src/domain/workspace/value_objects.py` 无输出（value_objects 不 import policy）
    - _需求：全部_
    - _设计：全文_
    - _前置：13.2 / 13.4 / 13.5_

## 备注

### 任务排序原则

- 领域层 → 基础设施层 → 配置 / DI → 工具层 → `ChatConfig` → 观测 / 文档，契合仓库 DDD + 六边形架构的依赖方向（`domain/` 不依赖 `infrastructure/`）。
- 每个 `[impl]` 任务后紧随独立的 `[test]` 任务，评审时可单切片 cherry-pick；测试任务不与实现任务合并。
- `[checkpoint]` 放在四处关键里程碑（领域层 / 基础设施适配器 / 文件工具 / 全量），用于验证层间边界与编译通过。

### 粒度上限

- 每个 `[impl]` 子任务均限制在 1-3 个源文件（`LocalFilesystemWorkspace` 拆分为 8 个方法级子任务以保持 <200 行增量）；每个 `[test]` 子任务限制在 1 个测试文件。
- 若 `LocalFilesystemWorkspace.edit`（含 flock）实现超出 200 行阈值，允许在执行阶段进一步拆出 `_edit_with_locking` 私有辅助函数并追加一条 `[test]` 子任务。

### 本次重写反映的三项设计修订

1. **修订 1：ChatConfig 位置闭合（FIXME 已消）**
   - 路径由临时推断的 `domain/chat/config.py` 订正为实际的 `infrastructure/chat/chat_config.py`（infra 层配置对象，不违反领域层纯度）
   - 实现方案采用 `@model_validator(mode="after")` 幂等追加（方案 Y），拒绝"改默认值字符串"（方案 X，无法覆盖 `CHAT_SYSTEM_PROMPT` 环境变量场景）
   - 幂等通过 `rstrip().endswith(...)` 判断；测试新增"重复构造不堆叠"用例
   - 相关任务：12.1 / 12.2

2. **修订 2：Port 方法新增 `context` 参数（观测上下文透传）**
   - 7 个 I/O 方法（`exists` / `stat` / `read` / `write` / `edit` / `list_dir` / `delete`）签名统一追加 keyword-only 末位 `context: dict | None = None`
   - 3 个方法（`resolve_path` / `capabilities` / `display_root_hint`）**不接受** `context`（纯函数或元数据查询）
   - 实现侧新增模块级 `_LOG_CONTEXT_WHITELIST = {"tool_name", "trace_id", "agent_id"}` 和 `_sanitize_context(context)`；日志 `extra` 只透传白名单键
   - 6 个工具改造时每次 I/O 调用构造并传入 `context={"tool_name": ..., "trace_id": ..., "agent_id": ...}`
   - **红线**：`context` 任何字段不得拼入异常 `message`（守住需求 4.4 / 8.6 路径泄露红线）
   - 废弃原"工具层 except 再打一条 warning 靠 trace_id 关联"的妥协方案（被白名单透传替代）
   - 相关任务：4.1 / 4.3 / 7.1 / 7.2-7.7 / 7.12 / 9.1-9.8 / 13.2 / 13.3（可选）

3. **修订 3：`WorkspacePath.join` 解耦 `WorkspacePolicy`（打破循环依赖闭环）**
   - 实现改为纯 `PurePosixPath` 拼接 + 手动 `..` 折叠 + 私有 `_reject_illegal_chars`，**不 import** `WorkspacePolicy`
   - 字符校验常量与 `WorkspacePolicy` 并列维护，本期不共享常量模块
   - 越根时抛 `WorkspaceConfinementViolation(reason=ABSOLUTE_OUTSIDE)`
   - 新增静态检查任务 2.4：通过 `ast` 扫描确保 `value_objects.py` 不 import `policy` 模块（顶层或函数体内）
   - 相关任务：2.1 / 2.3 / 2.4 / 4.5（checkpoint 最终走查）

### 任务总数与阶段分组

- 任务总数：**64 条**（含所有 `- [ ] x.y` 叶子任务，其中 1 条为可选 `[ ]*`）
- 按阶段：
  - 阶段 1 骨架：3 条
  - 阶段 2 值对象 & 错误（含 `join` 自洽 + 静态检查）：7 条
  - 阶段 3 Policy：3 条
  - 阶段 4 Port（含 `context` 签名静态检查 + checkpoint）：5 条
  - 阶段 5 Config：2 条
  - 阶段 6 Guards + _common_impl：4 条
  - 阶段 7 LocalFilesystemWorkspace（含 `_sanitize_context` + context 白名单测试 + checkpoint）：14 条
  - 阶段 8 配置 & DI 装配：6 条
  - 阶段 9 文件工具（4 个工具 × 2 + AST 扫描 + checkpoint）：10 条
  - 阶段 10 ShellExecTool：2 条
  - 阶段 11 PythonExecTool + 注入 + checkpoint：4 条
  - 阶段 12 ChatConfig（`infrastructure/chat` + 幂等 + 重复构造测试）：2 条
  - 阶段 13 观测 & 薄壳 & 文档（含可选静态扫描 + 最终 checkpoint）：6 条

### Checkpoint 清单

- 4.5 领域层完成度（含 `value_objects.py` 无 policy import 的肉眼走查）
- 7.14 基础设施适配器完成度（含 `_LOG_CONTEXT_WHITELIST` 值验证）
- 9.10 文件工具改造完成度
- 11.4 全部 6 个受控工具改造完成度
- 13.6 全量校验（含 `grep` 静态走查补强）

### 开放问题

所有原开放问题在 design.md §开放问题 中已确认为"已决策，原开放问题关闭"，tasks.md 按决策结果落地，无遗留争议。
