# AGENT.md

本文件仅作为项目文档索引。具体内容分散存放在 `docs/` 下，修改项目认知时优先更新对应主题文档。

其中 `docs/steering/` 为项目规范目录，存放强制性约束文档。新增代码、调整配置、引入依赖或补充文档时，必须优先遵循该目录下规范。

## 文档索引

| 文档 | 精炼说明 |
|---|---|
| [docs/project-overview.md](docs/project-overview.md) | 项目定位、前后端边界、主链路和推荐阅读顺序。 |
| [docs/repository-map.md](docs/repository-map.md) | 根目录、后端目录、前端目录和不应作为主要上下文的目录。 |
| [docs/architecture.md](docs/architecture.md) | 后端 DDD + 六边形架构、Port/Adapter、Agent Loop、委派、DI、事件总线。 |
| [docs/frontend.md](docs/frontend.md) | Next.js 控制台结构、API 代理、聊天流式状态和任务工作区。 |
| [docs/api.md](docs/api.md) | 后端 HTTP 端点清单和路由文件位置。 |
| [docs/domain-model.md](docs/domain-model.md) | 后端领域对象、值对象和上下文模型。 |
| [docs/di-container.md](docs/di-container.md) | 自建 DI 容器、生命周期和依赖注册约束。 |
| [docs/model-routing.md](docs/model-routing.md) | 多 Provider 模型注册、路由和负载均衡。 |
| [docs/configuration.md](docs/configuration.md) | `config.properties`、配置系统、Provider、工具开关和部署配置。 |
| [docs/tools.md](docs/tools.md) | Agent 工具清单、注册条件、权限隔离和新增工具步骤。 |
| [docs/development.md](docs/development.md) | 本地开发命令、测试配置、添加 Port/Adapter/工具的基本步骤。 |
| [docs/steering/README.md](docs/steering/README.md) | 项目规范总览，进入具体规范前的首读入口。 |

## 项目规范

`docs/steering/` 下文档为强制性规范，适用于实现、配置、依赖和文档维护。涉及相关变更时，应先阅读总览及对应专题文档。

| 文档 | 精炼说明 |
|---|---|
| [docs/steering/ddd-architecture.md](docs/steering/ddd-architecture.md) | DDD + 六边形分层依赖方向、Port/Adapter 归属、明确禁止与允许的例外。 |
| [docs/steering/ddd-tactical-modeling.md](docs/steering/ddd-tactical-modeling.md) | DDD 战术建模：值对象/实体/领域服务/聚合边界判定/仓储 Port 语义/限界上下文与通用语言；领域层用 dataclass，Pydantic 仅在 API/DTO/配置边界。 |
| [docs/steering/config-source.md](docs/steering/config-source.md) | 配置优先写入 `epsilon-boot/config.properties`，`.env` 仅用于覆盖。 |
| [docs/steering/uv-package-manager.md](docs/steering/uv-package-manager.md) | 依赖管理仅允许 `uv`；禁止 `pip`/`poetry`/`pipenv`/`conda`。 |
| [docs/steering/code-documentation.md](docs/steering/code-documentation.md) | 模块、类、公开函数/方法须有中文 docstring，复杂逻辑补充背景说明。 |
| [docs/steering/adr.md](docs/steering/adr.md) | 架构决策记录（ADR）纪律：架构/方向级决策必写、只增不改；记录在 [docs/adr/](docs/adr/README.md)。 |
| [docs/steering/tool-authoring.md](docs/steering/tool-authoring.md) | 工具开发规范：Tool 契约、安全/恢复语义、面向 LLM 描述、Workspace 边界、注册与测试。 |
| [docs/steering/change-discipline.md](docs/steering/change-discipline.md) | 变更范围纪律：最小改动、按规模选流程门（spec/ADR）、不擅自推翻已定结论。 |
| [docs/steering/doc-sync.md](docs/steering/doc-sync.md) | 文档—代码同步：改代码即同步对应主题文档与索引，防上下文脱节。 |

## 项目摘要

这是一个通用 AI Agent 工作台：后端是 `epsilon-boot` FastAPI 服务，前端是 `epsilon-client` Next.js 控制台。后端以领域 Port 隔离外部依赖，通过 DI 容器装配模型路由、Agent、工具、Redis、MySQL、网关和可观测性；前端通过 rewrites 访问后端，提供聊天和任务执行两个主要工作流。
