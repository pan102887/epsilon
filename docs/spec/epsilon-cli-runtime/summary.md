# epsilon CLI Runtime Summary

## 完成内容

- 已评估 `docs/suggestions/epsilon-tui-cli-cloud-evolution.md`，将本期范围收敛为阶段 1：TUI/CLI adapter 骨架。
- 已新增 `application.cli`：
  - `CliRuntime` 复用 `configure_container()`、`container.start()`、`container.stop()` 和现有领域 Port。
  - `TuiApp` 基于 `prompt-toolkit` 进入持续主 Agent 会话，并通过 `CliRuntime.stream_main_agent()` 流式调用既有 Chat/Agent 编排层。
  - `SlashCommandRouter` 支持 `/help`、`/new`、`/model`、`/config doctor`、`/quit`。
  - TUI 不直接暴露工具列表；工具选择由 Agent Loop 内部决定。
  - `main.py` 支持 `epsilon`、`epsilon exec "..."`、`epsilon serve`、`--help`、`--version`。
- 已在 `pyproject.toml` 声明 `epsilon` console script，并补齐 `src` 布局的 setuptools 包发现配置。
- 已增加 `test/application/cli` 单元测试覆盖命令路由和 runtime facade。

## 验证

- `uv run --frozen epsilon --help`：通过。
- `env PYTHONPATH=src uv run --frozen pytest -q test/application/cli`：9 passed。

## 后续范围

- ApprovalPort 与 shell/python 高风险工具确认流留到阶段 2。
- SkillRegistryPort、MCP registry 与 slash 命令留到阶段 3。
- 云端强隔离 SandboxPort 另立 spec 处理。
