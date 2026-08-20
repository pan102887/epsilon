# Requirements Document

## Introduction

本项目当前仅提供一个简单的 `/health.json` 接口（始终返回 `{"status": "UP"}`），用于 Logan 平台的存活检测（Liveness Probe）。该接口不检查任何外部依赖的实际可用性，无法反映应用是否真正具备处理请求的能力。

本需求为项目的健康检查体系增加 Readiness Probe（就绪探针），通过检查关键外部依赖（如 Redis）的连通性，向容器编排平台（如 Kubernetes）报告应用是否已准备好接收流量。当依赖不可用时，就绪探针返回非就绪状态，编排平台将暂停向该实例分发流量，直到依赖恢复。

## Glossary

- **Readiness_Probe**：就绪探针，用于检测应用是否已准备好接收外部流量的 HTTP 端点。
- **Liveness_Probe**：存活探针，用于检测应用进程是否仍在运行的 HTTP 端点（即现有的 `/health.json`）。
- **Health_Router**：健康检查路由模块，位于 `application/routers/health.py`，负责注册所有健康检查相关的 HTTP 端点。
- **Health_Check_Port**：健康检查端口接口，定义在领域层中，声明依赖健康检查的抽象能力。
- **Health_Check_Adapter**：健康检查适配器，位于基础设施层，实现 Health_Check_Port，执行对具体外部依赖的连通性检测。
- **Readiness_Aggregator**：就绪状态聚合器，位于领域层，负责汇总所有 Health_Check_Port 的检查结果并生成最终就绪状态。
- **DI_Container**：依赖注入容器，即项目中的 `Container` 类，管理 Port → Adapter 的绑定和异步资源生命周期。

## Requirements

### Requirement 1: 就绪探针 HTTP 端点

**User Story:** As a 运维工程师, I want 通过 HTTP 端点查询应用的就绪状态, so that 容器编排平台可以根据就绪状态决定是否向该实例分发流量。

#### Acceptance Criteria

1. THE Health_Router SHALL 提供一个 `GET /readiness` HTTP 端点，返回 JSON 格式的就绪状态响应。
2. WHEN 所有依赖检查均通过时, THE Readiness_Probe SHALL 返回 HTTP 200 状态码，响应体包含 `{"status": "UP", "checks": {...}}`，其中 `checks` 包含每个依赖的检查结果。
3. WHEN 任意一个依赖检查未通过时, THE Readiness_Probe SHALL 返回 HTTP 503 状态码，响应体包含 `{"status": "DOWN", "checks": {...}}`，其中 `checks` 包含每个依赖的检查结果（含失败原因）。
4. THE Readiness_Probe SHALL 在每个依赖检查结果的 `checks` 字段中包含该依赖的名称和状态（`UP` 或 `DOWN`）。
5. WHEN 某个依赖检查失败时, THE Readiness_Probe SHALL 在该依赖的检查结果中包含 `reason` 字段，描述失败原因。

### Requirement 2: 健康检查端口接口（领域层）

**User Story:** As a 开发者, I want 在领域层定义健康检查的抽象接口, so that 领域逻辑不依赖具体的基础设施实现，符合六边形架构原则。

#### Acceptance Criteria

1. THE Health_Check_Port SHALL 使用 Python Protocol 定义，声明一个异步方法 `check`，返回包含依赖名称、状态和可选失败原因的检查结果。
2. THE Health_Check_Port SHALL 定义在 `domain` 层中，不引用任何基础设施层的模块。
3. THE Health_Check_Port 的 `check` 方法返回值 SHALL 使用领域层定义的值对象表示检查结果，包含 `name`（str）、`status`（UP 或 DOWN）和可选的 `reason`（str）字段。

### Requirement 3: Redis 健康检查适配器（基础设施层）

**User Story:** As a 开发者, I want 实现 Redis 连通性检查的适配器, so that 就绪探针能够检测 Redis 依赖是否可用。

#### Acceptance Criteria

1. THE Health_Check_Adapter SHALL 实现 Health_Check_Port 接口，通过执行 Redis `PING` 命令检测 Redis 连通性。
2. WHEN Redis `PING` 命令成功返回时, THE Health_Check_Adapter SHALL 返回状态为 `UP` 的检查结果。
3. WHEN Redis `PING` 命令抛出异常时, THE Health_Check_Adapter SHALL 返回状态为 `DOWN` 的检查结果，并在 `reason` 字段中包含异常信息。
4. IF Redis 连接超时, THEN THE Health_Check_Adapter SHALL 在 3 秒内返回 `DOWN` 状态，避免就绪探针长时间阻塞。

### Requirement 4: 就绪状态聚合（领域层）

**User Story:** As a 开发者, I want 在领域层聚合多个依赖的健康检查结果, so that 就绪探针能够综合判断应用的整体就绪状态。

#### Acceptance Criteria

1. THE Readiness_Aggregator SHALL 接收一组 Health_Check_Port 实例，依次执行每个实例的 `check` 方法。
2. WHEN 所有 Health_Check_Port 的检查结果状态均为 `UP` 时, THE Readiness_Aggregator SHALL 返回整体状态为 `UP`。
3. WHEN 任意一个 Health_Check_Port 的检查结果状态为 `DOWN` 时, THE Readiness_Aggregator SHALL 返回整体状态为 `DOWN`。
4. THE Readiness_Aggregator SHALL 在返回结果中包含所有依赖的逐项检查结果，无论整体状态是 `UP` 还是 `DOWN`。

### Requirement 5: 依赖注入集成

**User Story:** As a 开发者, I want 通过 DI 容器管理健康检查组件的绑定, so that 健康检查适配器可以被自动注入到就绪探针中，保持架构一致性。

#### Acceptance Criteria

1. THE DI_Container SHALL 注册 Health_Check_Port 到 Health_Check_Adapter 的绑定，使就绪探针能够通过容器解析获取所有健康检查实例。
2. THE DI_Container SHALL 支持注册多个 Health_Check_Port 实现（如 Redis 检查、未来可能的数据库检查等），就绪探针能获取全部已注册的检查实例。
3. WHEN 应用启动完成后, THE Readiness_Probe SHALL 能够通过 DI_Container 获取所有已注册的 Health_Check_Port 实例并执行检查。

### Requirement 6: 现有存活探针保持不变

**User Story:** As a 运维工程师, I want 现有的 `/health.json` 存活探针保持原有行为不变, so that 已有的监控和部署配置不受影响。

#### Acceptance Criteria

1. THE Health_Router SHALL 保留现有的 `GET /health.json` 端点，其行为和响应格式与当前实现完全一致。
2. THE Liveness_Probe（`/health.json`）SHALL 继续始终返回 `{"status": "UP"}`，不依赖任何外部服务的可用性。
