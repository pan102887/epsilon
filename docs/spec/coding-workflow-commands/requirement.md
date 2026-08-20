# 需求文档：Coding Workflow 基础命令

## 背景

`TODO.md` P0.4 要求本地 TUI/CLI 补齐 coding workflow 的基础可见性命令：`/status`、`/diff`、`/tests`、`/files` 与 `epsilon exec --json`。现有 runtime 已有会话、审批、Run、Trace、Artifact 和受控 Git 工具，P0.4 不应新增任意 shell 通道，也不应把工具清单重新暴露为用户级能力。

对标主流 coding agent：

- Claude Code 官方命令参考将 `/status`、`/diff`、`/context`、`/code-review` 等作为会话内工作流控制面；命令用于快速查看状态、差异、上下文与交付前检查。
- Claude Code skills 文档强调 `/run`、`/verify` 这类命令应记录项目启动/验证配方，而不是每轮重新猜测。
- Aider 提供 `/diff`、`/test`、`/run`、`/git` 等 in-chat commands，把 Git diff 与测试结果纳入编码会话。

本项目 P0.4 采用保守实现：只读展示已有状态与 trace，不主动执行测试，不新增 commit/reset/push，不绕过 `ToolRegistry` 和 `Workspace`。

## 范围

- TUI slash 命令：
  - `/status`：展示 session、model、workspace、pending approval、最近 trace。
  - `/diff`：展示当前工作区 Git diff 摘要，优先使用已注册 `git_diff` 工具。
  - `/tests`：展示最近 trace 中测试类命令、结果和失败摘要；不主动运行测试。
  - `/files`：展示最近 trace 中本会话读写过的文件清单。
- `epsilon exec --json`：输出结构化 JSON，便于 CI/脚本消费任务状态、usage、trace/artifact 引用。

## 非目标

- 不实现自动测试推荐器、质量门禁、commit/push/reset/rollback。
- 不新增工具、不扩展 Git 工具能力、不执行任意 shell。
- 不改 HTTP API 或前端控制台。
- 不保证 trace 中一定包含所有历史文件；只展示当前 TraceStore 已记录内容。

## 需求

### 需求 1：`/status`

1. THE `/status` 命令 SHALL 返回当前 `session_id`、实际模型、workspace 展示路径。
2. THE `/status` 命令 SHALL 展示当前会话未过期 pending approval 数量。
3. THE `/status` 命令 SHALL 展示当前会话最近 trace 步数与最近一步类型；trace 不存在时给出可读提示。
4. THE `/status` 命令 SHALL 不触发模型调用、工具执行或会话删除。

### 需求 2：`/diff`

1. THE `/diff` 命令 SHALL 通过 `CliRuntime` 调用受控 `git_diff` 工具读取 diff。
2. IF `git_diff` 未注册或执行失败，THE `/diff` 命令 SHALL 返回可读错误，不回退到任意 shell。
3. THE `/diff` 命令 SHALL 输出有界 diff 文本，并包含截断提示或 metadata 摘要。
4. THE `/diff` 命令 SHALL 支持无改动时返回明确提示。

### 需求 3：`/tests`

1. THE `/tests` 命令 SHALL 从当前会话 trace 中提取最近测试类工具调用。
2. 测试类调用判定 SHALL 基于 `shell_exec`/`python_exec` 等 trace metadata 中的命令或代码摘要，匹配 `pytest`、`ruff`、`pyright`、`bun run`、`npm run`、`uv run` 等常见验证命令。
3. THE `/tests` 命令 SHALL 展示命令摘要、成功/失败、退出码、失败摘要。
4. THE `/tests` 命令 SHALL 不主动执行测试；无记录时给出可读提示。

### 需求 4：`/files`

1. THE `/files` 命令 SHALL 从当前会话 trace 中提取工具 metadata 中的工作区逻辑路径。
2. THE `/files` 命令 SHALL 按读、写、执行/其他操作分组展示有界文件清单。
3. THE `/files` 命令 SHALL 不读取文件正文，不泄露宿主绝对路径。
4. 无 trace 或无文件记录时，THE `/files` 命令 SHALL 返回可读提示。

### 需求 5：`epsilon exec --json`

1. `epsilon exec` SHALL 新增 `--json` 开关；未开启时保持现有纯文本输出。
2. WHEN `--json` 开启，THE CLI SHALL 输出合法 JSON 对象，至少包含 `status`、`content`、`model`、`prompt_id`、`usage`、`latency_ms`、`terminated_reason`、`can_continue`、`approval_id`、`trace_ref`、`artifact_ref`。
3. JSON 输出 SHALL 不包含凭证、宿主绝对路径或完整 trace/artifact 正文。
4. 任务失败时仍输出 JSON，并按 `TaskResult.status` 返回既有成功/失败退出码语义。

### 需求 6：帮助、文档和验证

1. `/help` SHALL 展示新增 P0.4 命令。
2. `TODO.md` SHALL 将 P0.4 对应条目标记为已完成。
3. `docs/development.md` SHALL 描述新增命令与 `exec --json` 用法。
4. 新增或更新单元测试覆盖命令路由、runtime facade 和 JSON 输出。
