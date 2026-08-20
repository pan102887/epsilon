# 设计文档：Git Tools

## 概述

新增 `git_status`、`git_diff`、`git_apply_patch` 三个基础设施 Tool，分别位于
`epsilon-boot/src/infrastructure/tools/git_status/`、
`epsilon-boot/src/infrastructure/tools/git_diff/`、
`epsilon-boot/src/infrastructure/tools/git_apply_patch/`。实现使用
`asyncio.create_subprocess_exec()` 直接执行 `git` 可执行文件和固定参数数组，
不经过 shell，不暴露任意命令入口。

Git 需要本地 `.git` 元数据，因此工具要求 Workspace 支持本地物化：
`Workspace.capabilities().local_materialization=True` 且实现 `LocallyMaterializable`。
物化得到的宿主路径只用于子进程 `cwd`。

## 组件

```text
src/infrastructure/tools/
  _git_runner.py
  git_status/
    __init__.py
    git_status_tool.py
  git_diff/
    __init__.py
    git_diff_tool.py
  git_apply_patch/
    __init__.py
    git_apply_patch_tool.py

test/infrastructure/tools/git_status/
  test_git_tools_unit.py
```

### `_git_runner.py`

职责：

- 校验 Workspace 本地物化能力；
- 通过 `workspace.resolve_path("/")` 与 `materialize_cwd()` 获取 Git cwd；
- 执行固定 Git 参数数组；
- 解码 stdout/stderr；
- 按字符上限截断输出；
- 把非零退出码翻译为 `ToolExecutionError`，错误消息不拼宿主路径。

核心数据结构：

```python
@dataclass(frozen=True, slots=True)
class GitCommandResult:
    stdout: str
    stderr: str
    exit_code: int
    stdout_bytes: int
    stderr_bytes: int
    truncated: bool
```

### `GitStatusTool`

固定执行：

```text
git status --short --branch --untracked-files=all
```

metadata：

```python
{
  "operation": "git_status",
  "exit_code": int,
  "stdout_bytes": int,
  "stderr_bytes": int,
  "truncated": bool,
}
```

风险语义：`LOW` / `NONE` / `REPLAY_RESULT`。

### `GitDiffTool`

参数：

- `staged: bool = False`
- `file_paths: list[str] = []`
- `max_chars: int = 60000`

固定执行：

```text
git diff [--cached] [-- <validated pathspecs>]
```

`file_paths` 每项先调用 `Workspace.resolve_path()`，然后用去掉开头 `/` 的
workspace-relative pathspec 传给 Git。

metadata：

```python
{
  "operation": "git_diff",
  "staged": bool,
  "file_count": int,
  "exit_code": int,
  "stdout_bytes": int,
  "stderr_bytes": int,
  "truncated": bool,
}
```

风险语义：`LOW` / `NONE` / `REPLAY_RESULT`。

### `GitApplyPatchTool`

参数：

- `patch: str`
- `check_only: bool = False`
- `max_output_chars: int = 20000`

固定执行：

```text
git apply --check -
git apply -
```

`patch` 通过 stdin 传给 Git，不写入临时文件。

metadata：

```python
{
  "operation": "git_apply_patch",
  "check_only": bool,
  "exit_code": int,
  "patch_bytes": int,
  "stdout_bytes": int,
  "stderr_bytes": int,
  "truncated": bool,
}
```

风险语义：

- `check_only=true` 实际不写，但工具级声明按最危险调用能力处理；
- `risk_level = HIGH`
- `side_effect_level = LOCAL_WRITE`
- `replay_policy = MANUAL_REVIEW`

## 错误处理

- 非本地可物化 Workspace：抛 `ToolExecutionError("当前工作区后端不支持 Git 工具")`。
- pathspec 越界：抛 `ToolExecutionError("路径 ... 超出工作区边界")`。
- Git 非零退出码：抛 `ToolExecutionError("Git 命令执行失败（exit_code=N）...")`，
  可附带截断后的 stderr，但不得包含宿主 cwd。
- 超时：kill 子进程并抛 `ToolExecutionError("Git 命令执行超时...")`。

## 测试策略

使用 mock Workspace 与 mock `asyncio.create_subprocess_exec`，离线验证：

- 三个工具的固定 Git 参数数组；
- 本地物化能力守卫；
- `git_diff` pathspec 校验与 `--` 分隔；
- 输出截断与 metadata；
- `git_apply_patch` 通过 stdin 传 patch；
- 风险、副作用、重放语义；
- AST 静态检查不调用 shell、不开启任意 Git 子命令入口。
