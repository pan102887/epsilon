---
inclusion: always
---

# DDD 架构规范

本项目的代码必须遵循 DDD（Domain-Driven Design）设计范式。

## 分层结构

- **domain/**：领域层，包含业务逻辑、实体、值对象、领域事件和端口（Port）接口（使用 Python Protocol 定义）。领域层不依赖任何外部框架或基础设施。
- **infrastructure/**：基础设施层，提供端口的具体实现（Adapter），如 Redis、文件系统、外部 API 等。
- **application/**：应用层，负责编排领域逻辑，包含 FastAPI 路由、lifespan 管理、异常处理等。
- **common/**：公共模块，提供跨层共享的工具类、配置机制、DI 容器等。

## 核心原则

- 领域层通过 Protocol 定义端口接口，基础设施层提供适配器实现（六边形架构）
- 依赖方向：application → domain ← infrastructure，领域层不依赖基础设施层
- 新增功能时，先在 domain 层定义 Port 接口，再在 infrastructure 层实现 Adapter
- 使用依赖注入容器管理 Port → Adapter 的绑定，避免在领域层直接引用具体实现

## 依赖方向细化规则

- `domain/` 只允许依赖 Python 标准库、`common/` 中与业务无关的共享抽象，以及同层其他领域模块的稳定公开模型；禁止依赖 `application/`、`infrastructure/`、Web 框架、ORM、HTTP SDK、Redis/MySQL 客户端等技术实现。
- `application/` 允许依赖 `domain/` 与 `common/`；默认不应直接依赖 `infrastructure/` 的具体 Adapter 类型，只有组合根与启动装配代码可以引用基础设施实现完成注册。
- `infrastructure/` 允许依赖 `domain/` 与 `common/`；其职责是实现 Port、对接外部系统、完成技术转换，不得反向要求 `domain/` 感知具体实现细节。
- `common/` 是共享内核，不承载具体业务编排；`common/` 不得依赖 `application/`、`infrastructure/` 或某个特定业务子域的实现，避免公共模块演化为隐式上层。

## 组件级声明

- 领域 Port 应定义在 `src/domain/*/ports.py`，用于表达业务所需能力边界；只有当抽象确属跨领域、非业务特定的共享机制时，才可放入 `common/`。
- Adapter 必须位于 `src/infrastructure/`，命名与职责应明确映射到所实现的 Port；禁止在 `domain/` 或 `application/` 中混入外部系统访问逻辑。
- FastAPI 路由、中间件、异常处理与应用服务属于 `application/`；它们可以编排领域对象和 Port，但不得在请求处理流程中直接 new 基础设施实现替代容器装配。
- 依赖注入容器、启动入口、资源生命周期管理属于组合根职责；当前仓库中此职责默认落在 `src/application/container_config.py`、`src/application/server_app.py` 及相关启动代码。

## 明确禁止的依赖

- 禁止 `domain/` 导入任何 `src/infrastructure/*` 模块。
- 禁止 `domain/` 导入 FastAPI、Pydantic Settings、SQLAlchemy、Redis 客户端、OpenAI SDK、HTTP 客户端等基础设施或框架 API；纯数据校验模型如需使用，应证明其不引入框架耦合，并优先使用项目既有领域模型表达。
- 禁止 `application/` 在业务逻辑中绕过 Port 直接调用数据库、缓存、外部模型或文件系统客户端。
- 禁止 `common/` 反向依赖某个具体 Agent、Chat、Task、ModelAccess 等业务域实现。

## 允许的例外

- 应用启动阶段的组合根代码可以同时引用 `domain/` 中的 Port 与 `infrastructure/` 中的 Adapter，用于完成绑定、注册和资源初始化。
- 测试代码可根据测试目标跨层导入，但生产代码的依赖方向约束不得因测试便利而放宽。
- 迁移历史或过渡重构期间如暂存例外，必须在变更说明中记录原因、范围与清理计划，不得作为长期结构默认值。
