# 长任务工作流化与多 Agent 协作阶段六总结

## 状态

- 任务状态：`tasks.md` 全部完成。
- 评审状态：`review-log.md` 已记录每个实现切片与检查点；最终检查点为 `PASS_WITH_CAVEAT`。
- 交付范围：完成领域 workflow 模型、静态 registry/selector、Run 创建选择、phase 编排、协作治理事件、checkpoint/recovery 兼容、容器装配、FastAPI/CLI/TUI/Web 透传展示、集成测试与架构静态测试。

## 主要变更

- `domain/run`：新增 workflow 领域模型、协作上下文、workflow 端口与异常；Run snapshot/request/event 增加 workflow/collaboration 字段。
- `infrastructure/run`：新增 `RunWorkflowConfig`、静态 workflow registry/selector，并扩展本地文件与 Redis Run Store 持久化 workflow 字段。
- `application/run`：`RunApplicationService` 接入 workflow selection；新增 `WorkflowRunOrchestrator`；`RunExecutionCoordinator` 设置 workflow collaboration context；checkpoint sink/recovery 保留 workflow/collaboration metadata。
- `infrastructure/agent`：delegate、parallel delegate、handoff 接入 workflow 协作治理，记录 `COLLABORATION_STEP_RECORDED` 与 `COLLABORATION_LIMIT_HIT`。
- `application/container_config.py`：注册 workflow config、registry、selector、orchestrator，并注入 Run service/coordinator。
- Adapter/UI：FastAPI Run DTO、CLI/TUI、前端 Run View 与事件列表透传和展示 workflow/phase/collaboration 信息，不复制选择器、phase 推进或 limit 判定。

## 验证结果

- 后端收窄最终验证通过：
  - `test/domain/run test/application/run`：146 passed
  - `test/infrastructure/agent` 排除 tiktoken 联网缓存文件：279 passed
  - `test/infrastructure/run` 排除既有 legacy local-file store 卡住文件：125 passed
  - 阶段六 adapter/static/integration 集合：60 passed
- 前端：
  - `npm run lint`：passed
  - `npm run build`：sandbox 外 passed

## 已知 Caveat

- `timeout 180s env PYTHONPATH=src .venv/bin/python -m pytest -q` 全量运行在当前环境超时；已知卡住/环境点包括 legacy local-file run store 测试、router TestClient 用例无输出卡住、以及 tiktoken `cl100k_base` 需要联网缓存的测试文件。
- `npm run build` 在 sandbox 内因 Turbopack 绑定本地端口被拒绝；经审批在 sandbox 外重跑通过。
- `epsilon-boot` 依赖清单中已有 LangGraph 依赖；阶段六未新增 durable workflow runtime，架构测试检查 manifest diff 与 adapter/import 边界。
