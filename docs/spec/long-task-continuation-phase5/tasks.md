# 实现计划：长任务智能调度与护栏阶段五

## 概述

本计划按阶段五收敛版 v1 修订：只承诺确定性任务分类、guardrail 领域模型、静态策略、配置读取、工具风险分级、Run/API/CLI/TUI/Web 字段透传、`TASK_CLASSIFIED` 写入、`GUARDRAIL_EVALUATED`/`GUARDRAIL_BLOCKED` 枚举预留，以及 ReAct 工具真实执行前的 critical enforce 阻断。计划不承诺完整运行时 guardrail 事件闭环、`guardrail_summary` 动态累计更新、模型完成后或工具执行后运行时评估接入、guardrail `require_approval` 接入 HITL、checkpoint recovery guardrail 累计状态恢复。

## Tasks

- [x] 1.1 创建 guardrail 领域值对象与策略端口
  - 在 `epsilon-boot/src/domain/agent/guardrails.py` 中创建 `TaskExecutionClass`、`GuardrailMode`、`GuardrailAction`、`GuardrailReason`、`ToolRiskLevel`、`GuardrailPolicy`、`GuardrailDecision`、`GuardrailSummary`、`GuardrailEvaluationContext`
  - 在 `epsilon-boot/src/domain/agent/ports.py` 中声明 `AgentGuardrailPolicyPort.classify_payload()`、`evaluate_run_start()`、`evaluate_model_completed()`、`evaluate_tool_before_execution()`、`evaluate_tool_after_execution()`；端口保留后续扩展能力，但 v1 运行时只要求分类与工具执行前评估接入
  - 在 `epsilon-boot/src/domain/agent/__init__.py` 中导出新增领域类型
  - _需求: 1.1, 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 4.1, 5.1_

- [x] 1.2 编写 guardrail 领域值对象测试
  - 在 `epsilon-boot/test/domain/agent/test_guardrail_value_objects_unit.py` 中覆盖枚举稳定性、默认 `observe`、`guardrail_blocked` 主终止原因、`GuardrailSummary.to_dict()` JSON-safe 转换
  - 在 `epsilon-boot/test/domain/agent/test_tool_risk_level_unit.py` 中覆盖未覆盖工具默认 `ToolRiskLevel.HIGH`
  - **验证: 需求 1.1, 2.1, 2.2, 2.3, 2.4, 2.5, 3.1, 4.1, 4.2, 7.2, 7.4**

- [x] 1.3 实现 guardrail 配置读取
  - 在 `epsilon-boot/src/infrastructure/agent/guardrail_config.py` 中创建 `AgentGuardrailConfig`，读取 `AGENT_GUARDRAILS_*` 配置并转换为 `GuardrailPolicy`
  - 在 `epsilon-boot/config.properties` 中新增默认 `observe` 配置、critical/high enforce 开关、阈值配置和 `AGENT_GUARDRAILS_MODEL_PRICING` 入口；`0` 阈值转换为 `None`
  - 在 `epsilon-boot/src/application/container_config.py` 中注册 `AgentGuardrailPolicyPort -> StaticAgentGuardrailPolicy`
  - _需求: 3.1, 4.9, 5.7, 7.3_

- [x] 1.4 补足 guardrail 配置校验测试
  - 在 `epsilon-boot/test/infrastructure/agent/test_guardrail_config_unit.py` 中补充 `AGENT_GUARDRAILS_MODEL_PRICING` 合法 JSON object、非法 JSON、非 object、非法模型名、负数或非数字价格的 fail-fast 覆盖
  - 当前测试已覆盖默认 `observe`、properties 加载、非法模式、负数阈值、非正重复/失败阈值；模型单价格式校验仍缺少明确用例
  - **验证: 需求 5.7, 7.3**

- [x] 2.1 实现静态 guardrail 策略
  - 在 `epsilon-boot/src/infrastructure/agent/static_guardrail_policy.py` 中实现 `StaticAgentGuardrailPolicy.classify_payload()`、`classify_run()`、`evaluate_run_start()`、`evaluate_model_completed()`、`evaluate_tool_before_execution()`、`evaluate_tool_after_execution()`
  - 分类只依赖 Run kind、工具可用性、checkpoint/segment/can_continue 元数据和批量输入字段；不调用 LLM 或外部服务
  - 策略可根据 token、耗时、上下文增长、重复工具调用、连续失败阈值返回 `observe` 或 `stop`；这些阈值在 v1 不接入模型后/工具后运行时累计闭环
  - _需求: 1.1, 1.2, 1.3, 2.6, 3.2, 4.7, 4.8, 4.9, 4.10, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7_

- [x] 2.2 编写静态策略测试
  - 在 `epsilon-boot/test/infrastructure/agent/test_static_guardrail_policy_unit.py` 中覆盖 checkpoint Run 归类为 `long_task`、批量输入优先归类为 `batch_task`、`observe` critical 不阻断、`enforce` critical 返回 `stop`、high 默认不阻断且显式开启才 `require_approval`、阈值命中返回 `stop`
  - **验证: 需求 1.1, 1.2, 1.3, 3.2, 4.7, 4.9, 5.1, 5.2, 5.3, 5.4, 7.1, 7.5, 7.6, 7.7**

- [x] 3.1 实现工具风险分级
  - 在 `epsilon-boot/src/domain/agent/tools.py` 中为 `Tool.risk_level` 提供默认 `ToolRiskLevel.HIGH`
  - 在 `epsilon-boot/src/infrastructure/tools/filesystem/read_file_tool.py`、`list_dir_tool.py`、`epsilon-boot/src/infrastructure/tools/web_fetch/web_fetch_tool.py`、`web_search_tool.py` 中标记读类工具为 `low`
  - 在 `epsilon-boot/src/infrastructure/agent/delegate_to_agent_tool.py` 中标记委派工具为 `medium`
  - 在 `epsilon-boot/src/infrastructure/tools/filesystem/write_file_tool.py`、`edit_file_tool.py`、`epsilon-boot/src/infrastructure/tools/http_request/http_request_tool.py` 中标记写入/HTTP 工具为 `high`
  - 在 `epsilon-boot/src/infrastructure/tools/shell_exec/shell_exec_tool.py`、`python_exec_tool.py` 中标记 Shell/Python 工具为 `critical`
  - _需求: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6_

- [x] 3.2 编写工具风险分级测试
  - 在 `epsilon-boot/test/infrastructure/tools/test_builtin_tool_risk_levels_unit.py` 中覆盖内置工具风险映射
  - 在 `epsilon-boot/test/domain/agent/test_tool_risk_level_unit.py` 中覆盖未知或未覆盖工具默认 `high`
  - **验证: 需求 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 7.4**

- [x] 4.1 接入 ReAct 工具真实执行前 critical 阻断
  - 在 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` 的 `_execute_tool_call()` 中，在 checkpoint ledger 与 `ToolRegistry.execute()` 前调用 `_evaluate_tool_guardrail()`
  - 仅当策略返回 `GuardrailAction.REQUIRE_APPROVAL` 或 `GuardrailAction.STOP` 时跳过真实工具执行，并向 `ConversationContext` 追加 `ToolMessage`，metadata 写入 `error=True`、`guardrail_blocked=True`、`guardrail_reason`
  - v1 不写入 `GUARDRAIL_EVALUATED`/`GUARDRAIL_BLOCKED` 运行时事件，不把 `require_approval` 转换为既有 `ApprovalInterrupt`
  - _需求: 3.2, 3.3, 4.7, 4.8, 4.9, 4.10, 5.5, 5.6_

- [x] 4.2 编写 ReAct 前置阻断测试
  - 在 `epsilon-boot/test/infrastructure/agent/test_react_agent_guardrail_unit.py` 中覆盖 `observe` critical 仍执行真实工具、`enforce` critical 在真实执行前阻断且回灌错误工具消息
  - **验证: 需求 3.2, 3.3, 4.7, 4.8, 7.5, 7.6**

- [x] 5.1 扩展 Run 值对象、存储与创建分类事件
  - 在 `epsilon-boot/src/domain/run/value_objects.py` 中新增 `RunEventType.TASK_CLASSIFIED`、`GUARDRAIL_EVALUATED`、`GUARDRAIL_BLOCKED`，并为 `RunCreateRequest`、`RunSnapshot` 增加 `task_classification` 与 `guardrail_summary`
  - 在 `epsilon-boot/src/infrastructure/run/local_file_run_store_adapter.py`、`redis_run_store_adapter.py` 中保持新增字段 JSON 序列化与恢复兼容
  - 在 `epsilon-boot/src/application/run/run_application_service.py` 中于 Run 创建时调用 `_with_task_classification()`，分类成功后写入 `TASK_CLASSIFIED` 事件；不维护 `guardrail_summary` 动态状态
  - _需求: 1.4, 1.5, 3.4, 6.1, 6.2, 6.3, 6.4, 6.9_

- [x] 5.2 编写 Run 值对象与创建分类测试
  - 在 `epsilon-boot/test/domain/run/test_run_guardrail_value_objects_unit.py`、`epsilon-boot/test/domain/run/test_run_value_objects_unit.py` 中覆盖事件枚举和快照默认字段
  - 在 `epsilon-boot/test/application/run/test_run_application_service_unit.py` 中覆盖创建 Run 持久化 `task_classification` 并写入 `TASK_CLASSIFIED` 事件
  - **验证: 需求 1.4, 1.5, 3.4, 6.1, 6.2, 6.3, 6.4, 6.9, 7.8**

- [x] 5. 检查点 — 后端领域、策略、工具、Run 接入
  - 使用项目自身的编译/测试命令验证；如有问题请向用户确认
  - 运行项目中的全部测试用例，并要求全部通过：`cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest`

- [x] 6.1 实现 API/CLI/TUI/Web 字段透传展示
  - 在 `epsilon-boot/src/application/api/routers/runs.py` 的 `RunSnapshotBody` 与 `_snapshot_body()` 中透传 `task_classification`、`guardrail_summary`
  - 在 `epsilon-boot/src/application/cli/commands.py`、`epsilon-boot/src/application/cli/tui.py` 的 Run 快照渲染中展示 `task_classification`，并在 `guardrail_summary` 非空时展示摘要
  - 在 `epsilon-client/src/lib/chat-api.ts` 的 `RunSnapshot` 类型中增加 `task_classification`、`guardrail_summary`，并在 `epsilon-client/src/components/run/run-view.tsx` 中展示字段
  - Adapter 与 Web 只做 DTO 字段透传/展示，不复制 `GuardrailDecision` 策略判断
  - _需求: 6.1, 6.2, 6.5, 6.6, 6.7, 6.8_

- [x] 6.2 补足 Adapter/Web 字段透传测试
  - 在 `epsilon-boot/test/application/routers/test_runs_router_unit.py` 中补充 `/api/runs*` 响应包含 `task_classification` 与 `guardrail_summary` 的断言
  - 在 `epsilon-boot/test/application/cli/test_commands.py`、`epsilon-boot/test/application/cli/test_tui_run_view.py` 中补充 CLI/TUI 快照渲染新增字段的断言
  - 增加或扩展前端静态/组件测试，覆盖 `epsilon-client/src/lib/chat-api.ts` 类型和 `epsilon-client/src/components/run/run-view.tsx` 字段展示，不在前端复制策略判断
  - 当前代码已实现字段透传展示，但现有测试检索未发现这些 adapter/Web 字段的明确断言
  - **验证: 需求 6.5, 6.6, 6.7, 6.8, 7.8**

- [x] 7.1 执行阶段五回归验证
  - 后端全量命令：`cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest`
  - 前端命令：在 `epsilon-client` 执行 `npm run lint` 与 `npm run build`
  - 验证结果已在 `docs/spec/long-task-continuation-phase5/summary.md` 记录：后端 `2186 passed, 2 skipped`；前端 lint/build 通过，build 仅有 Next.js workspace root 推断警告
  - **验证: 需求 7.9, 7.10**

- [x] 7. 检查点 — 补齐未完成验证项后全量回归
  - 使用项目自身的编译/测试命令验证；如有问题请向用户确认
  - 已在 1.4 与 6.2 的测试补齐后重新运行项目中的全部测试用例并通过：`cd epsilon-boot && env PYTHONPATH=src uv run --frozen pytest`，结果为 `2186 passed, 2 skipped`
  - 已重新运行前端 `npm run lint` 与 `npm run build` 并通过；build 仅有 Next.js workspace root 推断警告

## 备注

- `GUARDRAIL_EVALUATED` 与 `GUARDRAIL_BLOCKED` 在阶段五 v1 只是 `RunEventType` 枚举预留；除 `TASK_CLASSIFIED` 外，本计划不要求新增 guardrail 运行时事件写入闭环。
- `guardrail_summary` 在阶段五 v1 只要求 `RunSnapshot`、API、CLI/TUI、Web 字段可透传；本计划不要求模型调用后或工具执行后动态累计更新摘要。
- `StaticAgentGuardrailPolicy.evaluate_model_completed()` 与 `evaluate_tool_after_execution()` 是策略端口/静态策略能力，不代表 ReAct runtime 已在模型完成后或工具执行后接入运行时 guardrail 评估。
- guardrail `require_approval` 不接入既有 HITL approval recovery；checkpoint recovery 不保存、恢复或累计 guardrail 状态。
