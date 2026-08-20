# 需求文档

## 简介

当前 `model_access` 模块中的 `OpenAIProviderConfig` 通过固定的 `MODEL_OPENAI_` 环境变量前缀创建为模块级单例，导致系统只能注册一个 OpenAI 兼容提供商实例。实际业务场景中，需要同时接入多个 OpenAI 兼容提供商（如智谱 AI、OpenAI 官方、DeepSeek 等），每个提供商拥有独立的 API 端点、密钥和模型配置。

本特性采用"方案3"：保留 `OpenAIProviderConfig` 作为模板类，移除模块级单例，在 `container_config.py` 中根据配置动态创建多个实例，每个实例使用不同的 `env_prefix`（如 `MODEL_ZHIPU_`、`MODEL_DEEPSEEK_`），从而支持同时注册多个 OpenAI 兼容提供商。

## 术语表

- **OpenAIProviderConfig**: OpenAI 兼容提供商的 pydantic-settings 配置类，继承自 `PropertiesBaseSettings`，通过 `env_prefix` 从环境变量和配置文件加载参数。
- **Container_Config**: 应用层容器配置模块（`container_config.py`），负责依赖注入编排，将 Port 绑定到 Adapter 实现。
- **Router_Adapter**: 模型路由适配器（`ModelRouterAdapter`），根据路由策略将请求委托给具体的提供商适配器。
- **OpenAI_Compatible_Adapter**: OpenAI 兼容模型适配器（`OpenAICompatibleAdapter`），实现与 OpenAI API 协议兼容的模型服务交互。
- **Router_Config**: 模型路由配置（`RouterConfig`），管理默认提供商和路由策略等全局路由参数。
- **Provider_Registry**: 提供商注册表，在 Container_Config 中维护的提供商名称到适配器实例的映射字典。
- **env_prefix**: pydantic-settings 的环境变量前缀配置项，决定配置类从哪些环境变量/配置属性中读取值。
- **config.properties**: Java Properties 格式的配置文件，作为配置源之一，键名通过 `.` 分隔并转换为大写环境变量风格后与 `env_prefix` 匹配。

## 需求

### 需求 1：OpenAIProviderConfig 模板化

**用户故事：** 作为开发者，我希望 OpenAIProviderConfig 作为可复用的模板类存在，以便通过不同的 env_prefix 创建多个独立的配置实例。

#### 验收标准

1. THE OpenAIProviderConfig SHALL 保留所有现有配置字段（enabled、provider_name、api_base、api_key、default_model、temperature、max_tokens、timeout、max_retries、max_connections、max_keepalive_connections）及其默认值不变。
2. THE openai_config.py 模块 SHALL 移除模块级单例 `openai_config = create_config(OpenAIProviderConfig)` 的创建语句。
3. THE OpenAIProviderConfig SHALL 移除硬编码的 `model_config = SettingsConfigDict(env_prefix="MODEL_OPENAI_")`，改为支持在实例化时通过参数指定 env_prefix。
4. WHEN 使用不同的 env_prefix 创建多个 OpenAIProviderConfig 实例时，THE OpenAIProviderConfig SHALL 确保每个实例独立读取对应前缀的配置值，互不干扰。

### 需求 2：多提供商动态注册

**用户故事：** 作为开发者，我希望在 container_config.py 中能够动态创建和注册多个 OpenAI 兼容提供商，以便同时使用智谱 AI、OpenAI 官方、DeepSeek 等多个模型服务。

#### 验收标准

1. THE Container_Config SHALL 定义一个提供商注册列表，声明需要注册的 OpenAI 兼容提供商及其对应的 env_prefix（如 `MODEL_ZHIPU_`、`MODEL_OPENAI_`、`MODEL_DEEPSEEK_`）。
2. WHEN Container_Config 初始化模型客户端时，THE Container_Config SHALL 遍历提供商注册列表，为每个提供商使用对应的 env_prefix 创建独立的 OpenAIProviderConfig 实例。
3. WHEN 某个提供商的 enabled 字段为 true 且 api_key 非空时，THE Container_Config SHALL 为该提供商创建独立的 httpx.AsyncClient 和 OpenAI_Compatible_Adapter 实例，并以 provider_name 为 key 注册到 Provider_Registry 中。
4. WHEN 某个提供商的 enabled 字段为 false 时，THE Container_Config SHALL 跳过该提供商的初始化，不创建 HTTP 客户端和适配器实例。
5. IF 某个提供商的 enabled 为 true 但 api_key 为空，THEN THE Container_Config SHALL 记录警告日志并跳过该提供商的初始化。

### 需求 3：配置文件格式支持

**用户故事：** 作为运维人员，我希望通过 config.properties 和环境变量为每个 OpenAI 兼容提供商配置独立的参数，以便灵活管理多个模型服务的接入信息。

#### 验收标准

1. THE config.properties SHALL 支持以不同前缀区分各提供商的配置项（如 `MODEL_ZHIPU_API_BASE`、`MODEL_DEEPSEEK_API_BASE`）。
2. WHEN 配置文件中存在某个提供商前缀的配置项时，THE OpenAIProviderConfig SHALL 正确解析并加载对应的值。
3. THE config.properties SHALL 为每个提供商提供完整的配置段，包含 ENABLED、PROVIDER_NAME、API_BASE、API_KEY、DEFAULT_MODEL、TEMPERATURE、MAX_TOKENS、TIMEOUT 等字段。

### 需求 4：路由器适配

**用户故事：** 作为开发者，我希望路由器能够感知所有已注册的 OpenAI 兼容提供商，以便根据请求参数正确路由到目标提供商。

#### 验收标准

1. THE Router_Adapter SHALL 接收包含所有已注册提供商（多个 OpenAI 兼容提供商 + Claude 提供商）的 Provider_Registry。
2. WHEN 请求中显式指定 provider 字段时，THE Router_Adapter SHALL 将请求路由到对应名称的提供商适配器。
3. WHEN 请求中未指定 provider 且路由策略为 model_prefix 时，THE Router_Adapter SHALL 根据模型名称前缀推断目标提供商（如 `glm-` 前缀路由到 zhipu、`gpt-` 前缀路由到 openai、`deepseek-` 前缀路由到 deepseek）。
4. THE Router_Config SHALL 保持现有的 default_provider 和 routing_strategy 配置不变，无需修改。

### 需求 5：HTTP 客户端生命周期管理

**用户故事：** 作为开发者，我希望每个 OpenAI 兼容提供商拥有独立的 HTTP 客户端，以便各提供商的连接池配置和生命周期互不影响。

#### 验收标准

1. WHEN Container_Config 初始化时，THE Container_Config SHALL 为每个启用的 OpenAI 兼容提供商创建独立的 httpx.AsyncClient 实例，使用该提供商配置中的 max_connections 和 max_keepalive_connections 参数。
2. WHEN 应用关闭时，THE Container_Config SHALL 关闭所有已创建的 OpenAI 兼容提供商 HTTP 客户端，释放连接池资源。
3. IF 某个提供商的 HTTP 客户端创建失败，THEN THE Container_Config SHALL 记录错误日志并继续初始化其他提供商，保证部分可用。

### 需求 6：向后兼容

**用户故事：** 作为开发者，我希望现有的单提供商配置能够无缝迁移到多提供商模式，以便不影响已有的部署环境。

#### 验收标准

1. WHEN 仅配置了一个 OpenAI 兼容提供商时，THE 系统 SHALL 保持与重构前完全一致的行为，包括路由逻辑和默认提供商选择。
2. THE 系统 SHALL 支持现有的 `MODEL_OPENAI_` 前缀配置项继续生效，作为提供商注册列表中的一个条目。
3. WHEN 使用现有 config.properties 配置文件（仅含 `MODEL_OPENAI_` 前缀）启动时，THE 系统 SHALL 正常启动并注册对应的提供商，无需修改配置文件。

### 需求 7：热更新支持

**用户故事：** 作为开发者，我希望多实例配置仍然支持热更新机制，以便运行时修改配置文件后各提供商能自动感知变更。

#### 验收标准

1. THE OpenAIProviderConfig SHALL 保留 `hot_reload: ClassVar[bool] = True` 声明，支持通过 `create_config` 工厂函数创建带热更新能力的代理实例。
2. WHEN config.properties 或 .env 文件中某个提供商的配置值发生变更时，THE 对应的 ConfigProxy 实例 SHALL 在下次属性访问时检测到文件 mtime 变化并重新加载配置。
