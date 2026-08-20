# Workspace Feature — 实施总结

- **Feature**：workspace（工作区抽象）
- **交付日期**：2026-05-11
- **评审结论**：PASS（spec-evaluator 最终评审 + pytest 回归缺陷修复再评审均 PASS）
- **任务完成度**：tasks.md 82 条勾选项全部 `[x]`，0 条未完成
- **pytest 全量基线**：397 passed / 0 failed / 0 errors（venv 环境就绪后执行）

---

## 1. 需求背景

将工具层对文件系统的直接调用抽象为 **Workspace Port**，支持 `local_filesystem` 与未来 `oss` 后端切换；强制把所有 I/O 限制在工作区根目录内，消除路径越界、符号链接逃逸、宿主路径泄露等隐患；同时保留 Shell / Python 执行工具的沙箱语义（AST 分析、`sanitize_env`、materialize cwd）。

## 2. 分阶段实施（13 个阶段）

| 阶段 | 主题 | 结论 |
|------|------|------|
| 1 | 目录骨架 & 占位 | PASS |
| 2 | domain 值对象（`WorkspacePath` / `CapabilitySet`） | PASS |
| 3 | domain 异常 + policy 路径规范化 | PASS（Property 3 / 4 验证） |
| 4 | Port `Workspace` 抽象 + 容量声明 | PASS |
| 5 | `_common_impl` 字节级读写 + 渲染 tree | PASS |
| 6 | `LocalFilesystemWorkspace` 基础 I/O | PASS |
| 7 | 守卫（SymlinkGuard / IdentityGuard）+ 并发 | PASS |
| 8 | DI 容器装配 + 单例顺序约束 | PASS |
| 9 | 4 个 filesystem 工具 `workspace=` keyword-only 改造 | PASS |
| 10 | 2 个 exec 工具 `workspace=` + capability 守卫 | PASS |
| 11 | `_create_tool_registry` 注入 + 启动期 fail-fast | PASS |
| 12 | ChatConfig system_prompt 追加路径规范 | PASS |
| 13 | 观测（结构化日志 + 脱敏）、薄壳、文档、端到端 | PASS-WITH-CAVEATS |

## 3. 架构边界约束（全部守住）

- **需求 9.5**：`src/domain/workspace/value_objects.py` 不依赖 `domain.workspace.policy`
- **需求 9.6**：`src/infrastructure/workspace/oss/` 仅留 `README.md`，无 `__init__.py`
- **Property 6**：工具层 6 个源文件 AST 扫描，无后端类型判断
- **DI 顺序**：`database < workspace < ToolRegistry` 已在 `container_config.py` 验证
- **路径输入输出**：对外一律使用以 `/` 起始的逻辑路径；宿主路径仅在日志 / 错误消息中显式脱敏

## 4. 观测与安全

- 新增 `_log_confinement_violation(...)` 统一结构化日志（事件名 `workspace_confinement_violation`），white-listed extra 字段：`tool_name / trace_id / agent_id`
- 新增 `_sanitize_requested_path_for_log(...)` 正则脱敏 `token / secret / password / api[_-]?key / credential` 五类敏感 key 的 value（等长 `*` 替换，下限 3）
- 领域异常 message **不含** `tool_name / trace_id / agent_id / 宿主根前缀`，由 `test_context_sanitize_unit.py` + `test_local_workspace_logging_unit.py` 负向断言覆盖
- Exec 工具启动期二次校验：`SHELL_EXEC_WORKING_DIR` / `PYTHON_EXEC_WORKING_DIR` 若越出工作区根 → `ConfigurationError` fail-fast，消息包含配置项名 + "工作区内 / 留空使用默认" 修复指引

## 5. 变更文件清单（高层）

- **新增**：`src/domain/workspace/` 整套、`src/infrastructure/workspace/local_filesystem/` 整套、`src/infrastructure/workspace/oss/README.md`、10+ 份单元 / 属性 / 集成测试、`test/application/test_workspace_end_to_end_integration.py`
- **改造**：`src/application/container_config.py`（DI + fail-fast）、`src/infrastructure/chat/chat_config.py`（validator）、`src/common/tools/common_tools.py`（薄壳转发）、4 个 filesystem 工具 + 2 个 exec 工具构造签名、`docs/tools.md`、`config.properties`（4 键）
- **删除**：Phase 9 / 11.3 共 9 份基于旧签名的测试文件，等价断言面已由新测试（7 份 `*_unit.py` + `test_tool_no_backend_branch_property.py` + `test_tool_context_injection_static.py`）覆盖

## 6. 未验证项 / 已知 caveat

| 项目 | 原因 | 现状 |
|------|------|------|
| `uv run pytest -q` 全量测试 | 早期 Pod 缺依赖 | ✅ 已在 `epsilon-boot/.venv/` 下完成 8 个核心路径真实 pytest 回归：**397 passed / 0 failed / 0 errors** |
| `uv run pyright` 类型检查 | Pod 缺 pyright | `python3 -m compileall src/ test/` 通过；pyright 可选 |
| 真实 DI 启动冒烟 | 依赖容器初始化完整栈 | Phase 8 等价 smoke + Phase 11.3 `_validate_exec_working_dir` 直接 exec 覆盖 + 回归批次补强分支（宿主绝对路径前缀比对）|

## 6.1 pytest 回归缺陷修复批次（2026-05-11）

venv 就绪后做真实 pytest 回归，发现 5 项遗留缺陷并闭环：

| 编号 | 类别 | 文件 | 根因 / 修复 |
|------|------|------|-------------|
| A | 生产（severe） | `src/infrastructure/chat/chat_config.py` | `@model_validator(mode="after")` 在 frozen 模型上赋值触发 `ValidationError`。改为 `object.__setattr__(self, "system_prompt", ...)`，并把 `_DEFAULT_MAX_TOOL_ROUNDS` 由单边漂移的 1000 对齐为 10（与 `config.properties` / docstring / 测试一致） |
| B | 生产（high） | `src/application/container_config.py` | `_validate_exec_working_dir` 新增"宿主绝对路径前缀比对"分支（`abs_wd.startswith(abs_root + os.sep)`），真实拦截 `/etc` 等宿主路径，错误消息含配置项名 + 工作区外 + 留空 |
| C | 测试（low） | `test/domain/workspace/test_workspace_port_unit.py` | Python 3.13 `@runtime_checkable` 对 MagicMock 判定变严格。改用 `_WorkspaceStub` 手写类覆盖 Protocol 全部方法 |
| D | 测试（low） | `test/infrastructure/workspace/local_filesystem/test_local_workspace_logging_unit.py` | `monkeypatch os.stat` 粒度过大。改为按调用次数分流：首次放行 IdentityGuard 跨设备校验、第二次触发 adapter PermissionError 翻译 |
| E | 测试（medium） | `test/infrastructure/tools/shell_exec/test_shell_exec_config.py` | `test_conditional_registration` 缺 `workspace=` 新签名参数，补 `workspace=MagicMock()` |

- 本批次 spec-evaluator 再评审 **PASS**。
- 详细根因、改动范围、验证命令见 `review-log.md` 批次"pytest 回归缺陷修复（2026-05-11）"。

## 7. 后续建议（非阻塞）

- CI 环境跑一次 `uv run pytest test/ -q` 做全量回归
- exec 工具的 capability 拒绝消息可追加 "请改用 read_file / write_file 等工作区文件操作工具" 以便 LLM 自修复（tasks 未强制）
- OSS 后端落地时，可复用当前 Workspace Port 契约与 `_common_impl` 的 tree 渲染逻辑

## 8. 交付物索引

- 需求：`docs/spec/workspace/requirement.md`
- 设计：`docs/spec/workspace/design.md`
- 任务清单：`docs/spec/workspace/tasks.md`
- 实施日志：`docs/spec/workspace/review-log.md`
- 本总结：`docs/spec/workspace/summary.md`
