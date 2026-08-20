# Pydantic 数据模型规范

后端使用 Pydantic 2（`pydantic>=2.12`、`pydantic-settings`）作为**API/DTO 与配置边界**的数据校验与序列化方案。API 请求/响应契约、跨进程/跨层的数据传输对象（DTO）、应用配置优先使用 Pydantic 模型；**领域层（`domain/`）不使用 Pydantic**，领域值对象/实体一律用 Python 原生类型与 `@dataclass(frozen=True)`（见 [ddd-architecture.md](ddd-architecture.md)「明确禁止的依赖」与 [ddd-tactical-modeling.md](ddd-tactical-modeling.md)）。

## 建模原则

- FastAPI 的请求体、响应体、查询参数必须使用 Pydantic 模型或显式类型，禁止用裸 `dict` 在接口间传递结构化数据
- 数据校验前置：在模型层完成字段校验（类型、范围、必填），不把校验逻辑散落到业务代码中
- **领域值对象不使用 Pydantic**，用 `@dataclass(frozen=True)` 表达不可变性（依据：`domain/` 下 19 个文件用 dataclass、0 个用 Pydantic `BaseModel`）；`ConfigDict(frozen=True)` 仅用于 API/DTO 边界确需不可变的 Pydantic 模型
- 模型字段必须有明确类型标注；可选字段显式使用 `X | None` 并给出默认值

## Pydantic 2 用法

- 使用 Pydantic 2 API：`model_config = ConfigDict(...)`、`model_validate`、`model_dump`，禁止沿用 v1 的 `class Config`、`.dict()`、`.parse_obj()`
- 自定义校验使用 `@field_validator` / `@model_validator`
- 应用配置使用 `pydantic-settings` 的 `BaseSettings`，并遵循 [config-source.md](config-source.md)：配置源优先 `config.properties`
- 字段约束优先用 `Field(...)`（如 `Field(gt=0)`、`Field(max_length=...)`）表达，而非在业务逻辑中手工判断

## 分层与职责

- API 层的请求/响应模型（DTO，Pydantic）与领域模型（`domain/`，dataclass）分离：DTO↔领域对象的转换在应用层/基础设施层完成，避免直接把领域对象暴露到 HTTP 边界，也避免把 Pydantic 反向引入领域层，遵循 [ddd-architecture.md](ddd-architecture.md) 与 [ddd-tactical-modeling.md](ddd-tactical-modeling.md)
- 模型只承担数据结构与校验职责，不掺入业务编排逻辑，遵循 [srp-principle.md](srp-principle.md)
- 公开模型与字段须遵循 [code-documentation.md](code-documentation.md)：模型类与非直观字段应有中文说明（docstring 或 `Field(description=...)`）
