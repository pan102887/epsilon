# 设计文档：Coding Workflow 基础命令

## 设计取舍

P0.4 只补齐本地 coding workflow 的可见性命令，不把 CLI 变成新的工具执行入口。TUI 命令通过 `CliRuntime` 读取已有 Port：

- `ApprovalStateStorePort`：pending approval 概览。
- `TraceStorePort`：最近 trace、测试命令、文件清单。
- `ArtifactStorePort`：`exec --json` 输出 artifact 引用。
- `ToolRegistry`：仅调用已注册 `git_diff` 工具；未注册时失败，不回退 shell。

该方案对齐 Claude Code/Aider 的命令工作流表面，但保持本仓库安全边界：Git diff 走固定 Git 工具，测试视图只读 trace，不执行新命令，JSON 输出只给引用与摘要。

## 模块变更

- `application.cli.workflow`：新增只读快照值对象与提取/格式化辅助函数。
- `application.cli.runtime.CliRuntime`：
  - 解析 `TraceStorePort | None`、`ArtifactStorePort | None`、`ToolRegistry`。
  - 新增 `coding_status()`、`coding_diff()`、`coding_tests()`、`coding_files()`、`execute_once_json()`.
- `application.cli.commands.SlashCommandRouter`：
  - 新增 `/status`、`/diff`、`/tests`、`/files` 路由。
  - `/help` 同步新增命令。
- `application.cli.main`：
  - `epsilon exec` 新增 `--json`。
  - JSON 模式只输出结构化结果，不改变退出码规则。

## Trace 提取规则

`/tests` 读取当前 session 的 `SessionTrace.steps`，筛选 `kind == "tool_call"` 的步骤。命令摘要优先取 metadata：

- `command_summary`
- `code_summary`
- 兜底 `arguments_summary`

匹配关键词包括 `pytest`、`ruff`、`pyright`、`mypy`、`bun run`、`npm run`、`pnpm`、`yarn`、`uv run`、`cargo test`、`go test`。输出最多展示最近 5 条。

`/files` 从 tool metadata 读取：

- `logical_path`
- `file_path`
- `file_paths`
- `path`
- `paths`
- `working_dir`

仅保留相对逻辑路径，忽略以 `/` 开头或含 Windows drive 前缀的宿主路径。按工具名和 metadata operation 粗分 `read`、`write`、`execute`、`other`。

## `/diff`

`CliRuntime.coding_diff()` 构造 `ToolCallRequest(id="cli-diff", name="git_diff", arguments='{"max_chars": 60000}')` 交给 `ToolRegistry.execute()`。如果工具不存在或失败，返回结构化错误；不直接导入 `infrastructure.tools.git_diff`，不调用 `git` 子进程。

## `exec --json`

`execute_once_json()` 将 `TaskResult` 映射为脚本友好的 JSON 字典：

- `status` 使用 `TaskStatus.value`。
- `trace_ref` 固定为当前任务 session 粒度不可用时的轻量引用；P0.4 不强制 TaskAgent 返回 session_id，因此先用 `available`/`step_count` 表达当前结果自带 trace。
- `artifact_ref` 只给 `available` 与 `count`，不输出 artifact 正文。

后续若 Task 执行引入稳定 session_id，可在不破坏字段的情况下补充 `session_id`。

## 验证

聚焦测试：

```bash
cd epsilon-boot
PYTHONPATH=src uv run --frozen pytest test/application/cli/test_commands.py test/application/cli/test_runtime.py test/application/cli/test_main.py
```

如环境已同步，也可用 `.venv/bin/pytest` 替代。
