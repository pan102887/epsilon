# 实现计划：Code Search Tools

## 概述

本计划按 `design.md` 落地三个只读代码检索工具：`glob`、`grep`、`read_many_files`。实现不涉及数据库 DDL、数据迁移、API DTO 或前端变更；主要修改后端工具实现、组合根注册、工具文档与离线单元测试。

## Tasks

- [x] 1. 实现 Workspace 搜索共享 helper
  - [x] 1.1 编写 helper 单元测试
    - 创建 `epsilon-boot/test/infrastructure/tools/test_workspace_search_helpers_unit.py`
    - 覆盖 `validate_workspace_pattern(pattern: str, *, field_name: str) -> None`：正常 pattern、`..`、反斜杠、NUL、Windows 盘符
    - 覆盖 `pattern_matches(posix_path: str, pattern: str) -> bool`：`**/*.py`、精确路径、无匹配、大小写敏感
    - 覆盖 `clamp_text(text: str, *, max_chars: int) -> BoundedText`：未截断、截断、边界值
    - _需求: 需求 1.2, 需求 1.3, 需求 2.6, 需求 3.5, 需求 4.1-4.6, 需求 5.5_
  - [x] 1.2 实现 `infrastructure.tools._workspace_search`
    - 创建 `epsilon-boot/src/infrastructure/tools/_workspace_search.py`
    - 定义 `SearchMode(StrEnum)`、`SearchFileCandidate`、`BoundedText`
    - 实现 `validate_workspace_pattern`、`pattern_matches`、`list_file_candidates`、`clamp_text`、`render_file_header`
    - `list_file_candidates(workspace: Workspace, *, directory_path: str, include_pattern: str, max_files: int, context: dict[str, object]) -> tuple[list[SearchFileCandidate], bool]` 只调用 `Workspace.resolve_path()` 与 `Workspace.list_dir()`
    - 不导入 `os` / `pathlib` / `open` / `common.tools.common_tools`
    - _需求: 需求 1.2, 需求 1.3, 需求 2.5, 需求 3.4, 需求 4.1-4.7_
  - [x] 1.3 运行 helper 验证
    - 执行 `cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/tools/test_workspace_search_helpers_unit.py -q`
    - _需求: 需求 6.6_

- [x] 2. 实现 `glob` 工具
  - [x] 2.1 编写 `GlobTool` 单元测试
    - 创建 `epsilon-boot/test/infrastructure/tools/glob/__init__.py`
    - 创建 `epsilon-boot/test/infrastructure/tools/glob/test_glob_tool_unit.py`
    - 使用 mock `Workspace` 覆盖正常匹配、空结果、稳定排序、`max_results` 截断、pattern 越界拒绝
    - 断言 `ToolExecutionResult.metadata` keys 为 `operation`、`pattern`、`directory_path`、`match_count`、`truncated`
    - 断言 `description` 为英文且包含 `workspace.display_root_hint()`，参数 schema 含 `pattern`、`directory_path`、`max_results`
    - 增加 AST 静态测试，禁止工具源码导入 `os` / `pathlib` / `common.tools.common_tools`
    - _需求: 需求 1.1-1.5, 需求 4.1, 需求 4.4, 需求 5.1, 需求 5.4, 需求 6.2_
  - [x] 2.2 实现 `GlobTool`
    - 创建 `epsilon-boot/src/infrastructure/tools/glob/__init__.py`
    - 创建 `epsilon-boot/src/infrastructure/tools/glob/glob_tool.py`
    - 实现 `class GlobTool(Tool)`，构造签名 `def __init__(self, workspace: Workspace) -> None`
    - 实现 `name == "glob"`、`risk_level == ToolRiskLevel.LOW`、`side_effect_level == ToolSideEffectLevel.NONE`、`replay_policy == ToolReplayPolicy.REPLAY_RESULT`
    - 实现 `async def execute(self, **kwargs: Any) -> ToolExecutionResult`
    - 输出每行一个 Workspace POSIX 文件路径；空结果返回空串；超过上限追加 `[truncated: more paths not shown]`
    - _需求: 需求 1.1-1.5, 需求 4.1, 需求 4.4, 需求 5.1, 需求 5.5_
  - [x] 2.3 运行 `glob` 工具验证
    - 执行 `cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/tools/glob -q`
    - _需求: 需求 6.2, 需求 6.6_

- [x] 3. 实现 `grep` 工具
  - [x] 3.1 编写 `GrepTool` 单元测试
    - 创建 `epsilon-boot/test/infrastructure/tools/grep/__init__.py`
    - 创建 `epsilon-boot/test/infrastructure/tools/grep/test_grep_tool_unit.py`
    - 使用 mock `Workspace` 覆盖 literal 搜索、regex 搜索、`case_sensitive=False`、非法 regex、`include_pattern` 过滤
    - 覆盖 `Workspace.read()` 抛 `WorkspaceIoError` 或返回不可解码内容时跳过文件并递增 `files_skipped`
    - 覆盖 `max_matches` 与 `max_line_chars` 截断
    - 断言 `metadata` keys 为 `operation`、`query`、`mode`、`directory_path`、`include_pattern`、`files_scanned`、`files_skipped`、`matches_returned`、`truncated`
    - 增加 AST 静态测试，禁止工具源码导入宿主文件系统 API
    - _需求: 需求 2.1-2.7, 需求 4.2, 需求 4.5, 需求 5.2, 需求 5.4, 需求 6.3_
  - [x] 3.2 实现 `GrepTool`
    - 创建 `epsilon-boot/src/infrastructure/tools/grep/__init__.py`
    - 创建 `epsilon-boot/src/infrastructure/tools/grep/grep_tool.py`
    - 实现 `class GrepTool(Tool)`，构造签名 `def __init__(self, workspace: Workspace) -> None`
    - 实现 `name == "grep"`、`risk_level == ToolRiskLevel.LOW`、`side_effect_level == ToolSideEffectLevel.NONE`、`replay_policy == ToolReplayPolicy.REPLAY_RESULT`
    - 实现参数 schema：`query`、`mode` enum、`directory_path`、`include_pattern`、`case_sensitive`、`max_matches`、`max_files`、`max_line_chars`
    - 实现 `async def execute(self, **kwargs: Any) -> ToolExecutionResult`
    - regex 模式在扫描前 `re.compile()`；非法 regex 抛 `ToolExecutionError`
    - 输出格式为 `path:line: preview`；截断时追加 `[truncated: more matches not shown]`
    - _需求: 需求 2.1-2.7, 需求 4.2, 需求 4.5, 需求 5.2, 需求 5.5_
  - [x] 3.3 运行 `grep` 工具验证
    - 执行 `cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/tools/grep -q`
    - _需求: 需求 6.3, 需求 6.6_

- [x] 4. 实现 `read_many_files` 工具
  - [x] 4.1 编写 `ReadManyFilesTool` 单元测试
    - 创建 `epsilon-boot/test/infrastructure/tools/read_many_files/__init__.py`
    - 创建 `epsilon-boot/test/infrastructure/tools/read_many_files/test_read_many_files_tool_unit.py`
    - 使用 mock `Workspace` 覆盖多文件成功、单文件不存在、单文件 I/O 失败、单文件越界、`offset` / `limit` 参数、`max_total_chars` 截断
    - 断言 per-file error entry 不阻断后续文件
    - 断言 `metadata` keys 为 `operation`、`requested_file_count`、`files_read`、`files_failed`、`total_lines_returned`、`truncated`
    - 增加 AST 静态测试，禁止工具源码导入宿主文件系统 API
    - _需求: 需求 3.1-3.6, 需求 4.3, 需求 4.6, 需求 5.3, 需求 5.4, 需求 6.4_
  - [x] 4.2 实现 `ReadManyFilesTool`
    - 创建 `epsilon-boot/src/infrastructure/tools/read_many_files/__init__.py`
    - 创建 `epsilon-boot/src/infrastructure/tools/read_many_files/read_many_files_tool.py`
    - 实现 `class ReadManyFilesTool(Tool)`，构造签名 `def __init__(self, workspace: Workspace) -> None`
    - 实现 `name == "read_many_files"`、`risk_level == ToolRiskLevel.LOW`、`side_effect_level == ToolSideEffectLevel.NONE`、`replay_policy == ToolReplayPolicy.REPLAY_RESULT`
    - 实现参数 schema：`file_paths`、`offset`、`limit`、`max_total_chars`
    - 实现 `async def execute(self, **kwargs: Any) -> ToolExecutionResult`
    - 每个文件通过 `Workspace.resolve_path()` + `Workspace.read(start_line=offset, end_line=offset+limit-1)` 读取
    - 输出使用 `===== /path =====` 文件头与既有行号渲染风格；单文件错误输出 `[error] ...`
    - _需求: 需求 3.1-3.6, 需求 4.3, 需求 4.6, 需求 5.3, 需求 5.5_
  - [x] 4.3 运行 `read_many_files` 工具验证
    - 执行 `cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/tools/read_many_files -q`
    - _需求: 需求 6.4, 需求 6.6_

- [x] 5. 注册工具、补齐风险测试与文档
  - [x] 5.1 在组合根注册三个工具
    - 修改 `epsilon-boot/src/application/container_config.py`
    - 在 `_create_tool_registry()` 中解析 `Workspace` 后注册 `GlobTool(workspace=ws)`、`GrepTool(workspace=ws)`、`ReadManyFilesTool(workspace=ws)`
    - 保持注册失败 fail-soft 风格与日志习惯，不新增配置键
    - _需求: 需求 4.7, 需求 6.1_
  - [x] 5.2 更新内置工具风险等级测试
    - 修改 `epsilon-boot/test/infrastructure/tools/test_builtin_tool_risk_levels_unit.py`
    - 增加 `GlobTool`、`GrepTool`、`ReadManyFilesTool` 断言为 `ToolRiskLevel.LOW`
    - 增加 side effect / replay policy 断言，确保三者为 `ToolSideEffectLevel.NONE` 与 `ToolReplayPolicy.REPLAY_RESULT`
    - _需求: 需求 1.5, 需求 2.7, 需求 3.6, 需求 6.2-6.4_
  - [x] 5.3 更新工具文档
    - 修改 `docs/tools.md`
    - 在 metadata 表、文件/代码检索工具清单、HITL 默认低风险工具说明、新增工具列表中加入 `glob`、`grep`、`read_many_files`
    - 记录参数、metadata keys、风险等级、副作用等级、重放策略与注册行为
    - _需求: 需求 5.1-5.4, 需求 6.5_
  - [x] 5.4 运行注册与文档相关验证
    - 执行 `cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/tools/test_builtin_tool_risk_levels_unit.py test/application/test_workspace_container_integration.py -q`
    - _需求: 需求 6.1, 需求 6.5, 需求 6.6_

- [x] 6. 最终质量门
  - [x] 6.1 运行后端相关单元测试集合
    - 执行 `cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/tools/test_workspace_search_helpers_unit.py test/infrastructure/tools/glob test/infrastructure/tools/grep test/infrastructure/tools/read_many_files test/infrastructure/tools/test_builtin_tool_risk_levels_unit.py test/application/test_workspace_container_integration.py -q`
    - _需求: 需求 6.6_
  - [x] 6.2 运行 lint 与类型检查
    - 执行 `cd epsilon-boot && uv run ruff check src/infrastructure/tools test/infrastructure/tools`
    - 执行 `cd epsilon-boot && uv run pyright`
    - _需求: 需求 6.6_
  - [x] 6.3 检查文档与规格同步
    - 检查 `docs/spec/code-search-tools/requirement.md`、`design.md`、`tasks.md` 与 `docs/tools.md` 对工具名称、参数、metadata 和风险语义描述一致
    - _需求: 需求 6.5_

## 备注

- 本特性不新增 DDL、数据迁移、配置项、API 端点或前端改动。
- 用户已确认：`glob` / `grep` 使用 Python `fnmatch.fnmatchcase()` 做 POSIX 路径匹配，不新增依赖，也不默认排除 `.git`、`.venv` 等目录。
- 性能边界通过 `directory_path`、`include_pattern`、`max_results`、`max_files`、`max_matches`、`max_total_chars` 控制；默认不承诺无上限全仓扫描。
