# 开发指南

## 环境

- **Python**：>=3.11（后端 `pyproject.toml`）
- **Node / 前端**：Next.js 16、React 19；包管理器使用 `bun`（`bun.lock` 已入库），脚本位于 `epsilon-client/package.json`
- 开发机可以是 Linux / macOS / Windows；`main.py` 在 Windows 下自动 stub 掉 Prometheus 依赖的 `resource` 模块

## 常用命令

后端命令在 `epsilon-boot/` 下执行：

```bash
uv sync                                                        # 安装依赖
uv run python main.py                                          # 启动服务（默认 0.0.0.0:7777）
uv run pytest                                                  # 运行全部测试
uv run --no-sync pytest                                        # 使用已同步虚拟环境运行全量测试，适合本地验收
uv run pytest test/path/to/test_file.py                        # 运行单个文件
uv run pytest test/path/to/test_file.py::test_function_name    # 运行单个测试
uv run pytest -k "pattern"                                     # 按模式筛选测试
uv run pyright                                                 # Pyright 严格静态类型检查（CI 门禁）
uv add <package>                                               # 添加依赖
uv remove <package>                                            # 移除依赖
```

## 本地 epsilon CLI

`epsilon` 默认进入 Textual TUI。coding workflow 基础命令可在 TUI 输入框中使用：

- `/status`：展示当前 session、model、workspace、pending approval 与最近 trace。
- `/diff`：通过受控 `git_diff` 工具展示当前工作区 diff；不会回退到任意 shell。
- `/tests`：从当前会话 trace 中展示最近测试/验证命令、结果和失败摘要；不会主动执行测试。
- `/files`：从当前会话 trace metadata 中展示读写过的工作区逻辑文件。

一次性任务仍使用 `epsilon exec`；需要脚本/CI 读取结构化结果时加 `--json`：

```bash
uv run epsilon exec "总结当前改动" --json
```

JSON 输出包含 `status`、`content`、`model`、`prompt_id`、`usage`、`latency_ms`、`terminated_reason`、`can_continue`、`approval_id`、`trace_ref` 与 `artifact_ref`，不包含完整 trace/artifact 正文。

**包管理器**：后端仅允许使用 `uv`，禁止使用 `pip`、`poetry`、`pipenv`、`conda`。

> CI 或全新环境可在依赖已可解析、可下载时使用 `uv sync --frozen` 后再执行测试。受限离线环境中直接 `uv run --frozen pytest` 可能触发项目构建隔离依赖解析；本地验收优先使用已同步 `.venv` 的 `uv run --no-sync ...` 或 `.venv/bin/pytest ...`。

前端命令在 `epsilon-client/` 下执行：

```bash
bun install
bun run dev        # 启动 Next.js dev server（默认 3000）
bun run build
bun run start
bun run lint
bun run typecheck  # TypeScript 类型检查
```

> 当前前端也可用 `npm run lint` / `npm run build` 运行 package.json 脚本；仓库仍保留 `bun.lock`。Next/Turbopack build 在受限沙箱中可能因 helper 进程本地端口绑定失败，需要在具备本地端口权限的环境中重跑。

## 评估与回归命令

评测脚本与自测文件位于仓库根目录的 `scripts/evaluation/`、`tests/evaluation/`，但 Python 项目与 `uv.lock` 位于 `epsilon-boot/`。因此评测命令仍然需要在 `epsilon-boot/` 下执行，并额外暴露两段 `PYTHONPATH`：

- `../`：让 `python -m scripts.evaluation.*` 与 `tests.evaluation.*` 能从仓库根解析模块。
- `src`：让评测桩与脚本继续导入后端领域模型与 Adapter。

推荐直接复用 CI 同款命令：

```bash
uv sync --frozen
uv run pytest ../tests/evaluation/self_tests/test_no_external_calls.py ../tests/evaluation/self_tests/test_compare_baseline.py ../tests/evaluation/self_tests/test_end_to_end.py -q --rootdir=..
PYTHONPATH=../:src uv run python -m scripts.evaluation.run_eval --metric=all --baseline=../docs/evaluation/results/2026-06-03_110744_feb5ec6.json --regression-threshold=5.0
PYTHONPATH=../:src uv run python -m scripts.evaluation.run_eval --metric=all --output=../docs/evaluation/results/nightly-local.json --baseline=../docs/evaluation/results/2026-06-03_110744_feb5ec6.json --regression-threshold=5.0
```

补充说明：

- 普通 PR 门禁只要求离线评测与回归阈值检查，不要求任何 Provider 密钥。
- `verify_evidence` 用于维护报告证据行号，不属于当前 PR 的硬门禁；若需要人工复核，可执行 `PYTHONPATH=../:src uv run python -m scripts.evaluation.verify_evidence --repo-root=..`。
- 更新 `docs/evaluation/results/` 下的基线文件时，需要在 PR 描述里说明变更原因与预期指标变化。

## 测试配置

`pyproject.toml` 中：`asyncio_mode = "auto"`，`pythonpath = ["src"]`，`testpaths = ["test"]`。

测试按 DDD 层次镜像组织，现有目录：`test/domain/`、`test/infrastructure/`、`test/application/`、`test/common/`、`test/integration/`、`test/migrations/`。

- **属性测试**（Hypothesis）：文件名以 `_property.py` 或 `_properties.py` 结尾，覆盖 Agent Loop 边界、工具权限不变式、委派深度限制等。
- Port 使用 `Protocol`（结构类型），测试可直接使用 `MagicMock`，无需 `spec=`。
- Run runtime 相关测试分布在 `test/domain/run/`、`test/application/run/`、`test/infrastructure/run/` 与 `test/integration/`。长任务收敛回归重点包括：`test/application/run/test_run_guardrail_recorder.py`、`test/application/run/test_run_approval_resumer.py`、`test/application/run/test_workflow_role_capability.py`、`test/application/run/test_runtime_handoff_persistence_unit.py`、`test/integration/test_long_task_runtime_convergence_p0.py`、`test/integration/test_long_task_runtime_convergence_p1.py`、`test/integration/test_long_task_runtime_convergence_p2.py`、`test/integration/test_run_view_schema_contract.py`、`test/static/test_long_task_runtime_convergence_architecture_boundaries.py`。

## 添加新 Port/Adapter

1. 在 `domain/<context>/ports.py` 中用 `Protocol` 定义 Port 接口
2. 在 `infrastructure/<category>/<adapter_name>.py` 中实现 Adapter
3. 在 `application/container_config.py` 中注册：`container.register(YourPort, _create_your_adapter, Scope.SINGLETON)`
4. 通过 FastAPI `Depends` 注入：`your_svc: YourPort = Depends(inject(YourPort))`

## 添加新工具

1. 在 `infrastructure/tools/<tool_name>/` 中继承 `Tool`（来自 `domain/agent/tools.py`）
2. 实现 `name`、`description`、`parameters`（JSON Schema）和 `async execute(**kwargs) -> str`；文件 I/O 工具须通过构造参数接收 `Workspace`
3. 如有功能开关，通过 `PropertiesBaseSettings` 添加配置类
4. 在 `container_config.py` 的 `_create_tool_registry()` 中按条件注册

## 添加异步资源

在 `container_config.py` 中注册初始化/清理函数：

```python
container.register_async_resource("your_resource", _init_fn, _cleanup_fn)
```

资源按注册顺序初始化，逆序清理。关注 `SESSION_STORE_BACKEND` 分支：`redis` 注册 `redis` 资源、`file` 注册 `local_persistence` 资源。

## 添加 Run adapter 或扩展 Run runtime

- 业务规则优先放在 `domain/run` 状态机、异常和值对象中。
- adapter 只能调用 `RunApplicationService`，不得复制状态机、claim、cancel、continue、approval resume、replay、guardrail 或 workflow 策略规则。
- FastAPI router 只做 DTO、输入校验、`BizException` 映射和 SSE 包装；不得直接导入 `infrastructure.run.*`。
- TUI/agent runtime 不通过 `/api/runs` 自调用，必须直接解析并调用共享应用服务。
- 扩展 worker/store 时同时补本地文件与 Redis 契约测试，确保 claim 原子性、事件 cursor 单调、`RunObservationStorePort` 摘要同步和 replay 过期行为一致。
- 新增 Run snapshot/event 字段时同步更新 API schema、CLI/TUI 渲染、Web `chat-api.ts` 类型、RunView/RunEventList 以及对应 contract/static tests。

## 代码规范

遵循 [docs/steering/](steering/README.md) 下的规范，核心要求：

- **所有模块、类、公开函数/方法的 docstring 必须使用中文**（见 `steering/code-documentation.md`）
- DDD 分层依赖方向不可逆转（`domain` 禁止导入 `infrastructure` 或外部框架；见 `steering/ddd-architecture.md`）
- 配置优先写入 `config.properties`（见 `steering/config-source.md`）
- 包管理器仅允许 `uv`（见 `steering/uv-package-manager.md`）
