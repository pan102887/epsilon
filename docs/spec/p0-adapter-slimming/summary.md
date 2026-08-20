# Summary：P0 Adapter 瘦身

## Feature

- Slug：`p0-adapter-slimming`
- 状态：完成
- 最终审查：PASS，无 blocking finding

## 最终产物

- Spec 文档：`requirement.md`、`design.md`、`tasks.md`、`review-log.md`、`summary.md`
- ReAct 协作者：`react_runtime_protocols.py`、`react_tool_execution_coordinator.py`、`react_approval_resume_coordinator.py`、`react_final_round_streamer.py`
- Chat 应用编排：扩展 `ChatApplicationService`，下沉分段同步与流式业务编排
- Task 应用编排：新增 `application/task/`、`TaskApplicationService`、`TaskTraceWorkflow`
- 领域纯映射：新增 `domain/task/result_mapping.py` 与 `domain/agent/segmented_progress.py`
- 组合根拆分：新增 `application/container/{agent,chat,task,run,tools,storage}.py`
- ADR：新增 ADR-0017 `确立 Task application workflow 边界` 与 ADR-0018 `拆分组合根为 application/container 子包`
- 类型检查配置：新增 `epsilon-boot/pyrightconfig.json`

## 关键设计决策

- ReAct adapter 保持 infrastructure 门面，工具执行、审批恢复、最终轮流式输出拆为 infrastructure 协作者，副作用通过窄 runtime protocol 回到 adapter。
- Chat/Task 的用例编排下沉到 application service；adapter 保留模型解析、`AgentConfig`、tool schema、Agent 调用、事件/DTO 翻译与持久化边界。
- `domain/task/result_mapping.py` 只承载纯映射，不导入 application / infrastructure。
- `application/container/*` 是 composition root 子模块，可导入 concrete infrastructure adapter；普通 application 文件仍不得导入 infrastructure，静态例外表保持为空。
- full pytest 阻塞修复限定在测试稳定性和运行时阻塞点：router 单测直接 await endpoint，本地 JSONL adapter 去掉问题 executor 包装，MCP 持久 session 避免嵌套 client enter。

## 验证

- `UV_CACHE_DIR=../.uv-cache uv run ruff check src test` -> All checks passed。
- `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest -q` -> 3128 passed, 2 skipped。
- `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest test/application/routers -vv` -> 52 passed。
- `.venv/bin/pyright src/domain src/application` -> 0 errors, 0 warnings。
- `UV_CACHE_DIR=../.uv-cache uv run --no-sync pyright src/domain src/application` -> 0 errors, 0 warnings。

## Follow-ups

- 三个未跟踪 TODO 文件不属于本 spec 范围：`src/domain/TODO.md`、`src/domain/agent/TODO.md`、`src/infrastructure/tools/mcp/TODO.md`。
