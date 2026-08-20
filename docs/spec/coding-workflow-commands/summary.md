# Summary：Coding Workflow 基础命令

P0.4 已落地：

- TUI slash commands 新增 `/status`、`/diff`、`/tests`、`/files`。
- `/diff` 通过已注册 `git_diff` 工具读取差异，不回退任意 shell。
- `/tests` 与 `/files` 只读当前会话 `TraceStorePort` 中的结构化 trace。
- `epsilon exec --json` 输出脚本友好的结构化结果，保留默认纯文本输出。
- `TODO.md` 与 `docs/development.md` 已同步当前行为。

验证：

- `uv run --frozen pytest test/application/cli/test_commands.py test/application/cli/test_runtime.py test/application/cli/test_main.py`：54 passed。
- `uv run --frozen ruff check src/application/cli test/application/cli`：passed。
- `uv run --frozen pyright src/application/cli`：0 errors。
- `git diff --check`：passed。

后续可在 P1 单独推进测试推荐器、质量门禁、commit/rollback 辅助和更完整 artifact 写入侧接入。
