# 仓库地图

## 根目录

| 路径 | 说明 |
|---|---|
| `epsilon-boot/` | Python FastAPI 后端服务。 |
| `epsilon-client/` | Next.js 前端控制台。 |
| `docs/` | 项目说明文档，作为 Agent 和开发者的主入口。 |
| `docs/steering/` | 强制性规范（分层、配置源、包管理、文档约束）。 |
| `docs/spec/` | 按 Feature 组织的需求/设计/任务文档，重点包括 workspace、本地持久化、HITL、长任务 phase1-6、runtime convergence、MCP、prompt registry、评估体系等。 |
| `docs/design/` | 架构、工作流、Skill 系统等设计材料。 |
| `docs/research/` | 调研和研究报告。 |
| `docs/evaluation/` | 评估维度、得分与报告。 |
| `docs/operations/` | 运行、部署、SLO、smoke test 等运维文档。 |
| `docs/archive/` | 从旧目录归档的历史文档副本，不作为当前主入口。 |
| `agents/`、`.agents/` | Agent 角色说明。 |
| `.codex/skills/` | 本仓库内的 Codex skill 定义，例如 `spec-dev`、`long-running-app-harness`。 |
| `lib/` | 离线依赖或辅助资源。 |
| `scripts/`、`tests/` | 根级辅助脚本与测试资料（与后端 `epsilon-boot/test/` 不同）。 |

## 后端目录

| 路径 | 说明 |
|---|---|
| `epsilon-boot/main.py` | 后端启动入口，配置日志并启动 uvicorn。 |
| `epsilon-boot/config.properties` | 主配置文件，包含服务、数据库、Redis、模型、工具、Workspace、本地持久化和可观测性配置。 |
| `epsilon-boot/src/application/` | FastAPI app、router、异常处理、中间件、DI 装配、CLI/TUI、Run 应用服务、审批恢复、checkpoint recovery、guardrail recorder 和 workflow orchestrator。 |
| `epsilon-boot/src/domain/` | 领域对象、Port、领域异常和核心规则（`agent/`、`chat/`、`health/`、`model_access/`、`prompt/`、`run/`、`storage/`、`task/`、`workspace/`）。 |
| `epsilon-boot/src/infrastructure/` | Port 的具体 Adapter：`agent/`、`artifact/`、`chat/`、`database/`（默认不装配）、`gateway/`、`health/`、`model_access/`、`persistence/local_file/`、`prompt/`、`redis/`、`run/`、`session/`、`storage/`、`task/`、`telemetry/`、`tools/`、`trace/`、`workspace/`。 |
| `epsilon-boot/src/common/` | DI 容器、配置工具、通用工具和基础异常。 |
| `epsilon-boot/test/` | pytest 测试，按 DDD 层次组织（`domain/`、`infrastructure/`、`application/`、`common/`、`integration/`、`migrations/`）。 |
| `epsilon-boot/migrations/` | 数据库迁移 SQL（默认未装配 MySQL，保留备用）。 |

## 前端目录

| 路径 | 说明 |
|---|---|
| `epsilon-client/src/app/` | App Router 页面、布局和全局样式。 |
| `epsilon-client/src/components/chat/` | 聊天面板组件（`chat-panel`、`chat-header`、`chat-input`、`message-list`、`message-bubble`、`model-selector`）。 |
| `epsilon-client/src/components/run/` | 后台 Run 面板组件（`run-view`、`run-event-list`）。 |
| `epsilon-client/src/components/task/` | 任务执行工作区组件（`task-workspace`）。 |
| `epsilon-client/src/hooks/` | 前端状态 Hook（`use-chat.ts`、`use-run.ts`）。 |
| `epsilon-client/src/lib/` | 后端 API 请求封装（`chat-api.ts`，包含 Chat/Task/Run/Models）。 |
| `epsilon-client/next.config.ts` | Next.js 配置，包含后端 API rewrite 与 React 编译器启用。 |
| `epsilon-client/package.json` | 前端脚本和依赖。 |

## 不应作为主要上下文的目录

- `node_modules/`：前端依赖目录，不用于代码理解。
- `__pycache__/`、`.pytest_cache/`、构建产物：运行时缓存，不应纳入文档或设计判断。
- `archive_docs/`：若存在，只在追溯历史决策时阅读，不作为当前行为准则。
- `epsilon-boot/src/infrastructure/database/`：本期默认不装配，作为未来新增 MySQL 消费者时的死代码备用。
- `epsilon-client/.next/`、`epsilon-client/tsconfig.tsbuildinfo`：前端构建产物和增量缓存。
