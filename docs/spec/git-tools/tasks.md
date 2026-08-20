# 实现计划：Git Tools

## Tasks

- [x] 1. 实现 Git 工具共享 runner
  - [x] 1.1 创建 `infrastructure/tools/_git_runner.py`
  - [x] 1.2 覆盖本地物化守卫、stdout/stderr 截断、非零退出码和超时

- [x] 2. 实现 `git_status`
  - [x] 2.1 创建 `infrastructure/tools/git_status/git_status_tool.py`
  - [x] 2.2 单测固定参数、metadata、风险语义

- [x] 3. 实现 `git_diff`
  - [x] 3.1 创建 `infrastructure/tools/git_diff/git_diff_tool.py`
  - [x] 3.2 单测 staged、pathspec 校验、截断、metadata、风险语义

- [x] 4. 实现 `git_apply_patch`
  - [x] 4.1 创建 `infrastructure/tools/git_apply_patch/git_apply_patch_tool.py`
  - [x] 4.2 单测 check-only、真实 apply 参数、stdin patch、metadata、风险语义

- [x] 5. 默认注册与文档同步
  - [x] 5.1 在 `_create_tool_registry()` 默认注册三项 Git 工具
  - [x] 5.2 更新 `test_builtin_tool_risk_levels_unit.py`
  - [x] 5.3 更新 `docs/tools.md`

- [x] 6. 质量门
  - [x] 6.1 运行 Git 工具单测和注册相关测试
  - [x] 6.2 运行 `ruff check src/infrastructure/tools test/infrastructure/tools`
  - [x] 6.3 运行本次新增文件的定向 `pyright`
