# 需求文档：Git Tools

## 简介

当前 Agent 若要查看 Git 状态、查看 diff 或应用补丁，通常需要调用高风险
`shell_exec`。这会把一个狭窄的版本控制操作升级为任意命令执行，扩大审批和误用面。

本特性新增三个内置 Git 工具：`git_status`、`git_diff`、`git_apply_patch`。
三者只允许执行固定 Git 子命令，不接受任意 shell 字符串；通过注入的 `Workspace`
解析并物化工作区根作为 Git 子进程 `cwd`，不向 LLM 或 metadata 泄露宿主绝对路径。

范围不包含 commit、push、checkout、reset、branch 管理、remote 操作或交互式 Git 操作。

## 需求

### 需求 1：提供 Git 状态工具

**用户故事：** 作为 coding agent，我希望用低风险工具读取 `git status --short --branch`，
以便在修改前后判断工作区变化，而不是调用 `shell_exec`。

#### 验收标准

1. THE Git_Status_Tool SHALL expose tool name `git_status` with English description and JSON Schema parameters.
2. WHEN executed in a local materializable Workspace, THE Git_Status_Tool SHALL run only fixed `git status --short --branch --untracked-files=all`.
3. THE Git_Status_Tool SHALL return bounded stdout content and structured metadata.
4. THE Git_Status_Tool SHALL declare low-risk, no-side-effect, replayable semantics.

### 需求 2：提供 Git Diff 工具

**用户故事：** 作为 coding agent，我希望用低风险工具读取当前 diff 或 staged diff，
以便审查修改内容，而不是调用 `shell_exec`。

#### 验收标准

1. THE Git_Diff_Tool SHALL expose tool name `git_diff` with parameters for `staged`, `file_paths`, and output limit.
2. WHEN `file_paths` are provided, THE Git_Diff_Tool SHALL validate each path through Workspace before passing workspace-relative pathspecs to Git.
3. THE Git_Diff_Tool SHALL run only fixed `git diff` / `git diff --cached` with optional `-- <pathspecs>`.
4. THE Git_Diff_Tool SHALL return bounded diff text and metadata including staged state, file count, and truncation.
5. THE Git_Diff_Tool SHALL declare low-risk, no-side-effect, replayable semantics.

### 需求 3：提供 Git Patch 应用工具

**用户故事：** 作为 coding agent，我希望通过受控工具应用 unified diff，
以便减少使用任意 shell 执行 `git apply`。

#### 验收标准

1. THE Git_Apply_Patch_Tool SHALL expose tool name `git_apply_patch` with parameters for patch text, `check_only`, and output limit.
2. THE Git_Apply_Patch_Tool SHALL run only fixed `git apply --check -` or `git apply -` and pass patch text through stdin.
3. WHEN `check_only=true`, THE Git_Apply_Patch_Tool SHALL perform no write.
4. WHEN `check_only=false`, THE Git_Apply_Patch_Tool SHALL declare local-write side effects and non-replayable/manual-review recovery semantics.
5. THE Git_Apply_Patch_Tool SHALL return bounded stdout/stderr summary and metadata including `check_only`, exit code, patch size, and truncation.

### 需求 4：保持安全边界和文档同步

1. THE Git tools SHALL require `Workspace.capabilities().local_materialization=True` and `LocallyMaterializable`.
2. THE Git tools SHALL NOT execute through shell strings and SHALL NOT accept arbitrary Git subcommands.
3. THE Git tools SHALL NOT expose host absolute paths in success content or metadata.
4. THE Tool_Registry SHALL default-register the Git tools without new configuration keys.
5. THE tool documentation SHALL list parameters, metadata, risk level, side-effect level, replay policy, and default registration behavior.
