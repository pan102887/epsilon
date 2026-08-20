# Review Log：P0 Adapter 瘦身

## 2026-07-09 Wave 1：ReAct 基线与运行时协议

- 范围：任务 1.1-1.4。
- 变更：
  - 新增 `docs/spec/p0-adapter-slimming/react-baseline.md`，记录 `ReActAgentAdapter` 2502 行基线、职责簇、允许/禁止移动边界与既有协作者。
  - 新增 `epsilon-boot/src/infrastructure/agent/react_runtime_protocols.py`，定义 `ToolExecutionRuntime` 与 `ApprovalResumeRuntime` 窄协议。
  - 新增 `epsilon-boot/test/infrastructure/agent/test_react_runtime_protocols_static.py`，用 AST 校验协议模块边界与方法集合。
- 验证：
  - `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent/test_react_runtime_protocols_static.py` -> 3 passed。
  - `UV_CACHE_DIR=../.uv-cache uv run ruff check src/infrastructure/agent/react_runtime_protocols.py test/infrastructure/agent/test_react_runtime_protocols_static.py` -> All checks passed。
- Review：
  - 首次只读 review 因把既有未跟踪 TODO 文件纳入范围而 FAIL；该问题为范围误判，三个 TODO 文件在本 spec 中明确排除且本波未触碰。
  - 第二次 scoped review PASS，结论：无 blocking finding。

## 2026-07-09 Wave 2：ReAct 工具 / 审批 / 最终轮协作者

- 范围：任务 2.1-4.4。
- 变更：
  - 新增 `react_tool_execution_coordinator.py` 与单测，`ReActAgentAdapter` 的工具 dispatch/progress/events 改为委托该协作者。
  - 新增 `react_approval_resume_coordinator.py` 与单测，`_apply_approval_decisions(...)` 改为委托该协作者；adapter 通过 runtime 回调保留 edit 参数 cast/validate、approve/edit 执行与 reject checkpoint 记录。
  - 新增 `react_final_round_streamer.py` 与单测，`_stream_final_round(...)` / `_stream_events_final_round(...)` 改为薄委托；保留 `response_capture` 以维持 max_rounds==1 trace 捕获。
  - `react_agent_adapter.py` 从 2502 行降为 2461 行，净减少 41 行。
- 验证：
  - `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent/test_react_tool_execution_coordinator_unit.py test/infrastructure/agent/test_react_agent_concurrent_tool_calls_unit.py test/infrastructure/agent/test_react_agent_streaming_unit.py test/infrastructure/agent/test_react_agent_events_unit.py test/infrastructure/agent/test_react_approval_resume_coordinator_unit.py test/infrastructure/agent/test_react_agent_hitl_unit.py test/infrastructure/agent/test_react_agent_hitl_checkpoint_recovery_unit.py test/infrastructure/agent/test_react_agent_hitl_resume_timestamp_roundtrip_unit.py test/infrastructure/agent/test_react_final_round_streamer_unit.py test/infrastructure/agent/test_react_agent_tool_arguments_delta_unit.py test/infrastructure/agent/test_react_agent_final_round_helper_unit.py test/infrastructure/agent/test_react_agent_final_round_helper_property.py` -> 63 passed。
  - `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent` -> 365 passed。
  - `UV_CACHE_DIR=../.uv-cache uv run ruff check src/infrastructure/agent/react_agent_adapter.py src/infrastructure/agent/react_runtime_protocols.py src/infrastructure/agent/react_tool_execution_coordinator.py src/infrastructure/agent/react_approval_resume_coordinator.py src/infrastructure/agent/react_final_round_streamer.py test/infrastructure/agent/test_react_runtime_protocols_static.py test/infrastructure/agent/test_react_tool_execution_coordinator_unit.py test/infrastructure/agent/test_react_approval_resume_coordinator_unit.py test/infrastructure/agent/test_react_final_round_streamer_unit.py` -> All checks passed。
- Review：
  - Scoped review PASS，结论：无 blocking finding；adapter 保持门面委托，新增模块未导入 `application`，最终轮 stream/event、审批恢复、工具执行行为边界未发现回归。

## 2026-07-09 Wave 3：Chat 分段用例编排下沉

- 范围：任务 5.1-5.4。
- 并发：
  - Explorer 1 只读分析同步分段迁移边界，建议扩展现有 `ChatApplicationService`，不新增 `ChatSegmentApplicationService`。
  - Explorer 2 只读分析分段流式迁移边界，建议 application 产出业务帧，adapter 保留 `AgentStreamEvent` 线格式与 HTTP/SSE metadata 包装。
- 变更：
  - 扩展 `ChatApplicationService`，新增 `run_segmented_chat_on_context(...)` 与 `stream_segmented_chat_on_context(...)`，承载分段循环、保存时机、风险门、自动续跑决策与 `SegmentRunMetadata` 组合。
  - 新增 `SegmentStreamFrame` 应用层业务帧；`ChatServiceAdapter._stream_segmented_agent_events_on_context(...)` 降为业务帧到既有 `AgentStreamEvent` 格式的翻译层。
  - 新增 `domain.agent.segmented_progress`，把纯进展分析从 infrastructure 下沉到 domain；`infrastructure.agent.segmented_progress` 保留兼容 re-export。
  - `ChatServiceAdapter._run_segmented_agent_on_context(...)` 改为薄委托，模型解析、`AgentConfig` 构造、direct LLM path、`segment_done` 事件格式仍留在 adapter。
  - `chat_service_adapter.py` 当前 982 行；`chat_application_service.py` 当前 547 行。
- 验证：
  - `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest test/application/chat test/infrastructure/chat` -> 183 passed。
  - `UV_CACHE_DIR=../.uv-cache uv run ruff check src/application/chat src/infrastructure/chat src/domain/agent/segmented_progress.py src/infrastructure/agent/segmented_progress.py test/application/chat test/infrastructure/chat` -> All checks passed。
  - `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest test/static/test_architecture_import_boundaries.py` -> 8 passed。
- Review：
  - Scoped review PASS，结论：无 blocking finding；同步/流式分段编排已下沉到 `ChatApplicationService`，adapter 保留模型解析、`AgentConfig` 构造、Agent 回调与 `AgentStreamEvent` 线格式包装，未发现 application/domain 反向依赖 infrastructure。

## 2026-07-09 Wave 4：Task 纯映射与 trace workflow

- 范围：任务 6.1-6.4。
- 并发：
  - Worker 1 实现 `domain.task.result_mapping.TaskResultMapper` 与领域单测。
  - Worker 2 实现 `application.task.TaskTraceWorkflow` 与应用层单测。
- 变更：
  - 新增 `src/domain/task/result_mapping.py`，将 `AgentResult` 到 `TaskStatus` / `TaskResult` 的纯映射收敛到 domain，复用 `TaskContinuationPolicy.should_pause(...)`。
  - 新增 `src/application/task/task_trace_workflow.py` 与 `src/application/task/__init__.py`，提供无 I/O 的 trace shaping workflow。
  - `TaskAgentAdapter._to_task_result(...)` 改为委托 `TaskResultMapper`；`_extract_trace(...)` 暂保留兼容实现，避免 infrastructure 直接 import application 触发静态边界违规，后续 Task 7 由 `TaskApplicationService` 接入 `TaskTraceWorkflow`。
- 验证：
  - `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest test/domain/task test/application/task test/infrastructure/task` -> 150 passed。
  - `UV_CACHE_DIR=../.uv-cache uv run ruff check src/domain/task/result_mapping.py src/application/task src/infrastructure/task/task_agent_adapter.py test/domain/task/test_task_result_mapping_unit.py test/application/task/test_task_trace_workflow_unit.py test/infrastructure/task` -> All checks passed。
  - `UV_CACHE_DIR=../.uv-cache uv run pyright src/domain/task/result_mapping.py src/application/task` -> 0 errors。
  - `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest test/static/test_architecture_import_boundaries.py` -> 8 passed。
- Review：
  - Scoped review PASS，结论：无 blocking finding；`TaskResultMapper` 为纯 domain，`TaskTraceWorkflow` 为无 I/O application workflow；`TaskAgentAdapter` 暂不直接导入 application 以遵守 infrastructure 不得依赖 application 的静态边界，后续 Task 7 由 `TaskApplicationService` 接入 trace workflow。

## 2026-07-09 Wave 5：Task application service 与 adapter 委托

- 范围：任务 7.1-7.4。
- 并发：
  - Explorer 1 只读分析 execute/continue 与分段续跑边界。
  - Explorer 2 只读分析 approval resume 生命周期与 consume 顺序边界。
- 变更：
  - 新增 `TaskApplicationService`，承载 execute/continue/resume 的 session 编排、trace shaping、`TaskResultMapper` 映射、风险门附加、分段续跑聚合与审批 load/check/consume 顺序。
  - `TaskAgentAdapter` 新增结构协议与可选 `task_application_service` 注入；注入时 public `execute(...)`、`continue_task(...)`、`resume_approval(...)` 委托 application service，adapter 通过 prepare/run/resume 回调保留 prompt、tool schema、model registry、`AgentConfig` 与 `AgentPort` 调用边界。
  - `application.container_config._create_task_agent()` 显式构造并注入 `TaskApplicationService` 与 `TaskTraceWorkflow`；`TaskAgentAdapter` 不直接 import application，静态边界保持通过。
  - `task_agent_adapter.py` 当前 958 行；`task_application_service.py` 当前 455 行。
- 验证：
  - `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest test/application/task test/domain/task test/infrastructure/task` -> 156 passed。
  - `UV_CACHE_DIR=../.uv-cache uv run ruff check src/application/task src/domain/task/result_mapping.py src/infrastructure/task/task_agent_adapter.py test/application/task test/domain/task/test_task_result_mapping_unit.py test/infrastructure/task` -> All checks passed。
  - `UV_CACHE_DIR=../.uv-cache uv run pyright src/domain/task/result_mapping.py src/application/task` -> 0 errors。
  - `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest test/application/test_container_config.py::test_create_task_agent_uses_task_agent_config_max_rounds test/application/test_run_container_wiring_unit.py::test_task_agent_port_resolves_with_callable_resume_approval test/static/test_architecture_import_boundaries.py` -> 10 passed。
- Review：
  - 首次 scoped review FAIL：`execute` 自动续跑第二段使用首段 prepare，可能绕过持久化工具边界；组合根重复读取 `task-template` prompt。
  - 已修复：`execute_task(...)` 接收独立 `prepare_resume` 回调并在自动续跑时调用 adapter 的 `_prepare_resume_task`；`TaskAgentAdapter` 支持注入已解析的 `task_template_prompt_id`，组合根只读取一次 prompt。
  - 复审 PASS，结论：无 blocking finding；工具边界恢复、prompt 单次读取、application/infrastructure 静态边界与审批恢复保存语义均已确认。

## 2026-07-09 Wave 6：组合根注册分组拆分

- 范围：任务 8.1-8.5。
- 并发：
  - Explorer 1 只读分析 Agent/Task/Chat 组合根拆分边界。
  - Explorer 2 只读分析 Run/Storage/Tools/Model 组合根拆分边界。
- 变更：
  - 新增 `application/container/` 子包与 `agent.py`、`chat.py`、`task.py`、`run.py`、`tools.py`、`storage.py`，提供 `register_*_components(...)` 分组注册函数。
  - `container_config.configure_container()` 保留唯一对外入口与既有私有 factory facade，但 Port/Adapter 注册改为委托子模块，降低单文件注册区密度并保留测试 monkeypatch 兼容。
  - `test_static/test_architecture_import_boundaries.py` 将 `application/container/*.py` 纳入 composition root 路径；`test_segmented_container_wiring_static.py` 改为扫描 `container_config.py` 与 `application/container/*.py`。
  - `container_config.py` 当前 2032 行；新增组合根子模块合计 198 行。
- 验证：
  - `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest test/static/test_architecture_import_boundaries.py test/application/test_segmented_container_wiring_static.py test/application/test_container_config.py test/application/test_container_config_backend_dispatch.py test/application/test_run_container_wiring_unit.py` -> 48 passed。
  - `UV_CACHE_DIR=../.uv-cache uv run ruff check src/application/container_config.py src/application/container test/static/test_architecture_import_boundaries.py test/application/test_segmented_container_wiring_static.py test/application/test_container_config.py` -> All checks passed。
- Review：
  - Scoped review PASS，结论：无 blocking finding；`configure_container()` 对外入口、Workspace 与 ToolRegistry 解析顺序、delegate async resource 生命周期、`RunWorkerManager` monkeypatch 兼容、组合根子模块静态边界均已确认。
  - 非阻塞观察：delegate async resource 在源码顺序上早于 run component singleton 注册，但 async resource 初始化发生在 `configure_container()` 完成之后，未形成 wiring 回归。

## 2026-07-09 Wave 7：文档同步与 ADR 判断

- 范围：任务 9.1-9.3。
- 并发：
  - Explorer 1 只读对照 `docs/steering/adr.md` 与 ADR 索引，判断 Task application workflow 边界与组合根子包是否触发 ADR。
  - Explorer 2 只读定位 `docs/architecture.md`、`docs/agent.md`、`docs/domain-model.md`、`docs/di-container.md` 的过期段落与同步点。
- 变更：
  - 更新 `docs/architecture.md`，记录 `application/container/*.py` 组合根子模块、ReAct 三类协作者、Chat 分段应用编排下沉、Task application service / trace workflow / result mapper 边界。
  - 更新 `docs/agent.md`，记录 `react_runtime_protocols.py`、`ReactToolExecutionCoordinator`、`ReactApprovalResumeCoordinator`、`ReactFinalRoundStreamer` 的职责，以及 Task adapter 通过结构协议委托 `TaskApplicationService`。
  - 更新 `docs/domain-model.md`，记录 `TaskResultMapper` 纯映射语义与 `TaskTraceWorkflow` 的 application 归属。
  - 更新 `docs/di-container.md`，记录 `configure_container()` 公共入口不变、`application/container/{agent,chat,task,run,tools,storage}.py` 分组注册、Task application service 装配与静态边界。
  - 新增 ADR-0017 `确立 Task application workflow 边界` 与 ADR-0018 `拆分组合根为 application/container 子包`，并更新 `docs/adr/README.md`。
  - 修正 `design.md` 中已过期的 `domain/task/trace_policy.py` 与 `ChatSegmentApplicationService` 预案描述，改为实际落地的 `TaskTraceWorkflow` 与扩展既有 `ChatApplicationService`。
- 验证：
  - 文档切片暂不涉及代码执行路径；后续 Task 10 执行静态边界、聚焦回归、全量测试、ruff 与 pyright。
- Review：
  - Scoped review PASS，结论：无 blocking finding；主题文档描述已实现边界，ADR-0017/0018 触发条件、格式与索引合规，未把 `ChatSegmentApplicationService` 或 `domain/task/trace_policy.py` 写成当前状态，静态边界仍保持普通 application 不得导入 infrastructure 且 `APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS == {}`。

## 2026-07-09 Wave 8：最终验证首轮

- 范围：任务 10.1 与 10.2 首轮。
- 验证：
  - `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest test/static/test_architecture_import_boundaries.py` -> 8 passed。
  - `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent test/infrastructure/chat test/infrastructure/task test/application/chat test/application/task test/application/test_container_config.py test/application/test_container_config_backend_dispatch.py` -> 654 passed。
  - `UV_CACHE_DIR=../.uv-cache uv run ruff check src test` -> All checks passed。
  - `UV_CACHE_DIR=../.uv-cache uv run pyright src/application/task src/application/container src/domain/task/result_mapping.py src/domain/agent/segmented_progress.py` -> 0 errors。
  - `UV_CACHE_DIR=../.uv-cache uv run pyright src/infrastructure/agent/react_runtime_protocols.py src/infrastructure/agent/react_tool_execution_coordinator.py src/infrastructure/agent/react_approval_resume_coordinator.py src/infrastructure/agent/react_final_round_streamer.py` -> 0 errors。
- 未完成：
  - `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest` 在 `test/application/routers/test_chat_continue_router_unit.py::test_continue_chat_json_returns_continuation_fields` 长时间无输出，已手动中断；单独运行该文件也复现卡住。
  - 忽略上述文件后重跑全量，继续在 `test/application/routers/test_chat_router.py` 附近长时间无输出，已手动中断。阻塞集中在既有 router/TestClient 测试族，未在 P0 聚焦回归范围复现。
  - `UV_CACHE_DIR=../.uv-cache uv run pyright src/domain src/application` 当前仍有 53 errors / 6 warnings；本轮新增的结构协议与注册类型问题已修复，剩余为既有基线：FastAPI/Pydantic/Textual/Rich/Uvicorn/Redis 等 import 缺失、旧 `application/run`/`domain/run` 类型问题、`__all__` warning 等。
- 结果：
  - 任务 10.1 已完成。
  - 任务 10.2 未完成：全量 pytest 未能完成，full pyright 仍为既有基线失败；最终 evaluator 与 summary 暂不执行。

## 2026-07-09 Wave 9：full pytest 阻塞修复与最终验证

- 范围：任务 10.2 续跑；修复 full pytest 无法完成的测试环境阻塞点。
- 根因：
  - Router 单元测试通过 `FastAPI/TestClient` 或 ASGI transport 进入 Starlette/httpx 桥接层时会长时间无输出；直接 await endpoint 函数立即返回，说明阻塞不在 router 业务逻辑。
  - 当前 Python/沙箱组合下 `asyncio.to_thread(...)` / `loop.run_in_executor(...)` 表现为线程函数已执行但 await 不返回；影响 artifact/trace 本地 JSONL adapter 测试、run/workspace 并发测试，以及 FastMCP 同步测试工具调用。
  - MCP 持久 session 下 `MCPTool.execute(...)` 对已打开 session 再嵌套 `async with self._client` 会卡住；实现注释中的 fastmcp 引用计数 fast path 假设在当前 in-memory transport 下不成立。
- 修复：
  - `test/application/routers/*` 中仍依赖 TestClient/ASGITransport 的 router 单测改为直接 await endpoint；请求体错误拆为 Pydantic validation 与 endpoint/domain validation 两层断言。
  - `LocalFileArtifactStoreAdapter` 与 `LocalFileTraceStoreAdapter` 去掉 `asyncio.to_thread` 包装，保留 async API、JSONL 读写与 warning 隔离语义。
  - run/workspace 并发测试改用显式 `threading.Thread` + 独立 event loop，保持跨线程锁竞争语义但避开 asyncio executor 完成通知问题。
  - MCP 测试 server 工具改为 async，fixture 自动 `aclose()`；`MCPTool.execute(...)` 在 bridge 已持有 session 时直接 `call_tool`，未持有 session 时才自行 `async with`。
- 验证：
  - `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest test/application/routers -vv` -> 52 passed。
  - `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest test/infrastructure/artifact test/infrastructure/trace -q` -> 20 passed。
  - `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest test/infrastructure/run -q` -> 178 passed。
  - `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest test/infrastructure/tools/mcp -q` -> 14 passed。
  - `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest test/infrastructure/workspace -q` -> 109 passed。
  - `UV_CACHE_DIR=../.uv-cache uv run ruff check src test` -> All checks passed。
  - `UV_CACHE_DIR=../.uv-cache PYTHONPATH=src uv run --frozen pytest -q` -> 3128 passed, 2 skipped。
  - `rg -n "asyncio\.to_thread|run_in_executor" src test -S` -> no matches。
  - `UV_CACHE_DIR=../.uv-cache uv run pyright src/domain src/application` -> 53 errors / 6 warnings，仍为 Wave 8 记录的既有基线：FastAPI/Pydantic/Textual/Rich/Uvicorn/Redis import 缺失、旧 application/run/domain/run typing 问题、`__all__` warnings 等。
- 结果：
  - 任务 10.2 已完成：full pytest 与 full ruff 通过；full pyright 仍为既有基线失败且已记录归属。

## 2026-07-09 Wave 10：最终 evaluator 与 summary

- 范围：任务 10.3-10.4。
- Review：
  - 最终只读 evaluator PASS，结论：无 blocking finding。
  - 审查确认 ReAct 新协作者仍在 infrastructure，未反向依赖 application；Chat/Task 下沉保持 application service 编排、adapter 边界适配；`application/container/*` 仅作为 composition root 例外；full pytest 修复未破坏 adapter 瘦身边界。
- 产物：
  - 新增 `docs/spec/p0-adapter-slimming/summary.md`。
- 结果：
  - 任务 10.3 与 10.4 已完成。

## 2026-07-10 Wave 11：pyright 基线清理

- 范围：P0 收口后继续治理 `src/domain src/application` 的 pyright 既有基线。
- 变更：
  - 新增 `epsilon-boot/pyrightconfig.json`，让 pyright 显式使用项目 `.venv` 与 `src` extra path，消除 FastAPI/Pydantic/Textual/Rich/Uvicorn/Redis 等依赖的 missing import 误报。
  - 修复剩余真实类型问题：任务预算响应体字段显式收窄、MySQL health check session factory 导入指向真实模块、`ToolExecutionKey.arguments_digest` 字段/方法重名改为 `digest_arguments(...)`、JSON-safe dataclass 转换避免 `asdict` narrowing 误报、checkpoint restore maybe-await 显式判断、guardrail cursor/created_at 可选值保护、历史 tool call 恢复时校验 name/arguments 为字符串。
  - `application`、`application.api`、`application.api.presenters` 的 lazy export 增加 `TYPE_CHECKING` 静态声明，保留运行时懒加载。
- 验证：
  - `.venv/bin/ruff check src test` -> All checks passed。
  - `.venv/bin/pyright src/domain src/application` -> 0 errors, 0 warnings。
  - `PYTHONPATH=src .venv/bin/pytest -q` -> 3128 passed, 2 skipped。
  - `UV_CACHE_DIR=../.uv-cache uv run --no-sync pyright src/domain src/application` -> 0 errors, 0 warnings。
- 备注：
  - 常规 `uv run --frozen ...` 在当前 sandbox 中会尝试 build 本地项目并联网解析 `setuptools>=68`；为避免网络变量，本轮验证使用既有 `.venv/bin/*` 与 `uv run --no-sync`。
