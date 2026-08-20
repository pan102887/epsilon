# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

本文件仅作为文档索引，所有主题内容按独立文档拆分存放在 `docs/` 下；修改项目认知或规范时，优先更新对应主题文档，不在本文件中铺开细节。

## 项目摘要

本仓库包含通用 AI Agent 工作台的前后端：后端 `epsilon-boot` 为 FastAPI + DDD 六边形架构的 Python 服务，提供聊天、任务执行、ReAct Agent Loop、多模型路由、工具调用、会话存储、健康检查与可观测性；前端 `epsilon-client` 为 Next.js 控制台，通过 rewrites 代理到后端。后端通过自建 DI 容器装配 Port→Adapter，模型/工具/会话后端均由配置驱动。

## 常用命令（非默认，易用错）

后端（在 `epsilon-boot/` 下，依赖管理仅用 `uv`）：

```bash
uv run python main.py                    # 启动服务，默认监听 0.0.0.0:7777（非常规端口）
PYTHONPATH=src uv run --frozen pytest    # CI/验收全量测试（必须带 PYTHONPATH=src）
uv run pytest test/path::test_name       # 单个测试
```

前端（在 `epsilon-client/` 下，包管理器用 `bun` 而非 npm）：

```bash
bun install && bun run dev   # dev server 默认 3000
bun run build                # 构建
bun run typecheck            # TypeScript 类型检查
```

> 更多命令与评测流程见 [docs/development.md](docs/development.md)。

## 安全红线

- 🚫 禁止读取、输出或提交任何凭证/密钥：`config.properties`、`.env` 中的 API Key、内网 Nexus/仓库地址等敏感明文不得外泄或写入文档、注释、日志。
- 🚫 本文件与 `docs/` 会进入上下文/system prompt，当作可公开文档对待，不写入任何机密。

## 项目规范（强制性）

**Required Reading — 非平凡改动前先读**：@docs/steering/ddd-architecture.md、@docs/steering/config-source.md、@docs/steering/uv-package-manager.md

所有改动必须先阅读并遵循 [docs/steering/](docs/steering/README.md) 下的规范。核心约束：

| 文档 | 精炼说明 |
|---|---|
| [docs/steering/ddd-architecture.md](docs/steering/ddd-architecture.md) | DDD 分层依赖方向、Port/Adapter 归属、明确禁止与允许的例外。 |
| [docs/steering/ddd-tactical-modeling.md](docs/steering/ddd-tactical-modeling.md) | DDD 战术建模：值对象/实体/领域服务/聚合边界判定/仓储 Port 语义/限界上下文与通用语言；领域层用 dataclass，Pydantic 仅在 API/DTO/配置边界。 |
| [docs/steering/config-source.md](docs/steering/config-source.md) | 配置优先写入 `epsilon-boot/config.properties`，`.env` 仅用于本地覆盖。 |
| [docs/steering/uv-package-manager.md](docs/steering/uv-package-manager.md) | 后端依赖管理仅允许 `uv`；禁止 `pip`/`poetry`/`pipenv`/`conda`。 |
| [docs/steering/code-documentation.md](docs/steering/code-documentation.md) | 模块、类、公开函数/方法须有中文 docstring，复杂逻辑补充背景说明。 |
| [docs/steering/srp-principle.md](docs/steering/srp-principle.md) | 单一职责原则：每个模块/类/函数只承担一项职责，职责混杂时应拆分。 |
| [docs/steering/python-typing-lint.md](docs/steering/python-typing-lint.md) | Python 类型注解与 `ruff`/`pyright` 基线：全量类型标注、禁裸 `Any`、统一 lint。 |
| [docs/steering/typescript-strict.md](docs/steering/typescript-strict.md) | 前端 TypeScript 严格模式：禁 `any`/`@ts-ignore`、API 类型集中且与后端对齐。 |
| [docs/steering/pydantic-model.md](docs/steering/pydantic-model.md) | Pydantic 2 数据建模：接口用模型不用裸 dict、校验前置、DTO 与领域模型分离。 |
| [docs/steering/adr.md](docs/steering/adr.md) | 架构决策记录（ADR）纪律：架构/方向级决策必写、只增不改、supersede 链接；记录在 [docs/adr/](docs/adr/README.md)。 |
| [docs/steering/tool-authoring.md](docs/steering/tool-authoring.md) | 工具开发规范：Tool 契约、安全/恢复语义、面向 LLM 的描述、Workspace 边界、权限审批、注册与测试。 |
| [docs/steering/change-discipline.md](docs/steering/change-discipline.md) | 变更范围纪律：最小改动、按规模选流程门（spec/ADR）、可追溯、不擅自推翻已定结论。 |
| [docs/steering/doc-sync.md](docs/steering/doc-sync.md) | 文档—代码同步：改代码即同步对应主题文档与索引，防上下文脱节导致 agent 跑偏。 |

## 主题文档索引

| 文档 | 精炼说明 |
|---|---|
| [docs/project-overview.md](docs/project-overview.md) | 项目定位、前后端边界、运行时主链路和推荐阅读顺序。 |
| [docs/repository-map.md](docs/repository-map.md) | 根目录、后端目录、前端目录和不作为主要上下文的目录。 |
| [docs/architecture.md](docs/architecture.md) | 后端 DDD + 六边形架构、Port/Adapter 映射、Agent Loop、委派、DI、Workspace 边界与会话后端。 |
| [docs/agent.md](docs/agent.md) | ReAct Agent Loop、任务型 Agent、多 Agent 委派、上下文压缩、顶层聊天编排。 |
| [docs/domain-model.md](docs/domain-model.md) | 消息层次、会话上下文、任务模型、Agent 配置、工具抽象与工具异常。 |
| [docs/di-container.md](docs/di-container.md) | 自建 DI 容器、Scope、异步资源生命周期、绑定位置和错误类型。 |
| [docs/model-routing.md](docs/model-routing.md) | 多 Provider 注册、路由策略、热重载和配置驱动的模型列表。 |
| [docs/tools.md](docs/tools.md) | 工具清单、注册条件、Workspace 边界、权限隔离（ScopedToolRegistry）和新增工具步骤。 |
| [docs/api.md](docs/api.md) | 后端 HTTP 端点清单与路由文件位置。 |
| [docs/frontend.md](docs/frontend.md) | Next.js 控制台页面结构、API 代理、聊天流式状态与任务工作区。 |
| [docs/configuration.md](docs/configuration.md) | `config.properties` 主配置源、Provider 配置键组、工具开关与部署配置。 |
| [docs/development.md](docs/development.md) | 本地开发命令、测试配置、添加 Port/Adapter/工具/异步资源的步骤。 |
| [docs/prompts.md](docs/prompts.md) | Prompt 资产目录与版本化注册：资产布局、DDD 分层、配置键、消费方集成与可观测性。 |
| [docs/operations/runtime-backends.md](docs/operations/runtime-backends.md) | 后端会话存储后端、健康检查差异、单主机约束与升级指南。 |

## 快速导航

- 第一次阅读：从 [docs/project-overview.md](docs/project-overview.md) 入手，再按其"初始化后的阅读顺序"推进。
- 跑起来：后端见 [docs/development.md](docs/development.md)，前端见 [docs/frontend.md](docs/frontend.md) 的"本地开发"章节。
- 改代码：修改前先核对 [docs/steering/](docs/steering/README.md) 下的全部规范是否被满足。
