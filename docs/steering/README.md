# 项目规范（Steering）

本目录存放项目强制性规范文档，约束代码结构、配置来源、依赖管理与文档风格。所有规范在新增代码、修改配置或引入依赖时必须优先遵循。

| 文档 | 精炼说明 |
|---|---|
| [ddd-architecture.md](ddd-architecture.md) | DDD + 六边形分层依赖方向、Port/Adapter 归属、明确禁止与允许的例外。 |
| [ddd-tactical-modeling.md](ddd-tactical-modeling.md) | DDD 战术建模：值对象/实体/领域服务/聚合边界判定/仓储 Port 语义/限界上下文=子域目录/通用语言；领域层用 dataclass 不用 Pydantic。 |
| [config-source.md](config-source.md) | 配置优先写入 `epsilon-boot/config.properties`，`.env` 仅用于覆盖。 |
| [uv-package-manager.md](uv-package-manager.md) | 依赖管理仅允许 `uv`；禁止 `pip`/`poetry`/`pipenv`/`conda`。 |
| [code-documentation.md](code-documentation.md) | 模块、类、公开函数/方法须有中文 docstring，复杂逻辑补充背景说明。 |
| [srp-principle.md](srp-principle.md) | 单一职责原则：每个模块/类/函数只承担一项职责，职责混杂时应拆分。 |
| [python-typing-lint.md](python-typing-lint.md) | Python 类型注解与 `ruff`/`pyright` 基线：全量类型标注、禁裸 `Any`、统一 lint。 |
| [typescript-strict.md](typescript-strict.md) | 前端 TypeScript 严格模式：禁 `any`/`@ts-ignore`、API 类型集中且与后端对齐。 |
| [pydantic-model.md](pydantic-model.md) | Pydantic 2 数据建模：接口用模型不用裸 dict、校验前置、DTO 与领域模型分离。 |
| [adr.md](adr.md) | 架构决策记录（ADR）纪律：何时写、只增不改、状态机、supersede 链接；记录在 [../adr/](../adr/README.md)。 |
| [tool-authoring.md](tool-authoring.md) | 工具开发规范：Tool 契约、安全/恢复语义、面向 LLM 的描述、Workspace 边界、权限审批、注册与测试。 |
| [change-discipline.md](change-discipline.md) | 变更范围纪律：最小改动、按规模选流程门（spec/ADR）、可追溯、不擅自推翻已定结论。 |
| [doc-sync.md](doc-sync.md) | 文档—代码同步：改代码即同步对应主题文档与索引，防上下文脱节导致 agent 跑偏。 |
