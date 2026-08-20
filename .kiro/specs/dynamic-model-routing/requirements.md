# 需求文档

## 简介

当前 ChatServiceAdapter 在初始化时通过 `_provider_registry.get_default_model()` 获取默认模型，
再通过 `_provider_registry.get_adapter_for_model(default_model)` 获取固定的 ModelAccessPort 实例。
该实例在整个生命周期内不变，导致用户在对话过程中无法通过请求中的 `model` 参数动态选择不同的模型。

本需求旨在修改 ChatServiceAdapter 的模型获取机制，使其在每次对话请求时根据请求中的 `model` 参数
动态路由到对应的模型适配器，而非始终使用初始化时绑定的固定适配器。当请求未指定 `model` 时，
回退到系统默认模型。

## 术语表

- **Chat_Service_Adapter**: 聊天服务适配器，实现 ChatServicePort，编排对话的完整生命周期。
- **Provider_Registry**: 统一供应商注册中心，实现 ModelRegistryPort，管理模型提供商的注册与路由。
- **Model_Access_Port**: 统一模型接入端口协议，定义与 LLM 交互的标准操作（chat / stream）。
- **Model_Registry_Port**: 模型注册中心端口协议，定义提供商注册、模型列表查询和适配器获取接口。
- **Chat_Request_VO**: 聊天请求值对象，包含 session_id、message、stream 和可选的 model 字段。
- **Model_Router**: 模型路由组件，负责根据请求中的 model 参数解析并返回对应的 Model_Access_Port 实例。

## 需求

### 需求 1：ChatServiceAdapter 依赖 ModelRegistryPort 而非固定 ModelAccessPort

**用户故事：** 作为系统架构师，我希望 Chat_Service_Adapter 持有 Model_Registry_Port 而非固定的 Model_Access_Port 实例，以便在每次请求时动态解析模型适配器。

#### 验收标准

1. THE Chat_Service_Adapter SHALL 在构造函数中接受 Model_Registry_Port 实例替代当前的 Model_Access_Port 实例。
2. THE Chat_Service_Adapter SHALL 在每次对话请求处理时，通过 Model_Registry_Port 动态获取对应的 Model_Access_Port 实例。
3. THE Chat_Service_Adapter SHALL 不再在初始化阶段绑定固定的 Model_Access_Port 实例。

### 需求 2：根据请求中的 model 参数动态路由

**用户故事：** 作为 API 用户，我希望在对话请求中通过 model 参数指定使用的模型，以便在不同对话轮次中灵活切换模型。

#### 验收标准

1. WHEN Chat_Request_VO 的 model 字段包含有效的模型名称时，THE Chat_Service_Adapter SHALL 使用该模型名称通过 Model_Registry_Port 获取对应的 Model_Access_Port 实例。
2. WHEN Chat_Request_VO 的 model 字段为 None 时，THE Chat_Service_Adapter SHALL 通过 Model_Registry_Port 获取默认模型名称，再获取对应的 Model_Access_Port 实例。
3. WHEN Chat_Request_VO 的 model 字段指定的模型未在 Provider_Registry 中注册时，THE Chat_Service_Adapter SHALL 向上传播 ModelAccessError 异常，异常详情中包含请求的模型名称和可用模型列表。

### 需求 3：同步对话模式支持动态模型路由

**用户故事：** 作为 API 用户，我希望在同步对话模式下通过 model 参数选择模型，以便获取指定模型的完整回复。

#### 验收标准

1. WHEN 同步对话请求包含 model 参数时，THE Chat_Service_Adapter 的 chat 方法 SHALL 使用指定模型的 Model_Access_Port 实例执行对话。
2. WHEN 同步对话请求未包含 model 参数时，THE Chat_Service_Adapter 的 chat 方法 SHALL 使用默认模型的 Model_Access_Port 实例执行对话。
3. THE Chat_Response_VO 的 model 字段 SHALL 反映实际使用的模型名称。

### 需求 4：流式对话模式支持动态模型路由

**用户故事：** 作为 API 用户，我希望在流式对话模式下通过 model 参数选择模型，以便实时接收指定模型的流式回复。

#### 验收标准

1. WHEN 流式对话请求包含 model 参数时，THE Chat_Service_Adapter 的 stream_chat 方法 SHALL 使用指定模型的 Model_Access_Port 实例执行流式对话。
2. WHEN 流式对话请求未包含 model 参数时，THE Chat_Service_Adapter 的 stream_chat 方法 SHALL 使用默认模型的 Model_Access_Port 实例执行流式对话。

### 需求 5：Agent Loop 支持动态模型路由

**用户故事：** 作为 API 用户，我希望在启用 function calling 的 Agent Loop 模式下，所有轮次的 LLM 调用均使用请求指定的模型。

#### 验收标准

1. WHEN Agent Loop 同步模式执行时，THE Chat_Service_Adapter SHALL 在每一轮迭代中使用请求指定的模型对应的 Model_Access_Port 实例调用 LLM。
2. WHEN Agent Loop 流式模式执行时，THE Chat_Service_Adapter SHALL 在中间轮次的同步调用和最终轮次的流式调用中均使用请求指定的模型对应的 Model_Access_Port 实例。

### 需求 6：DI 容器配置适配

**用户故事：** 作为系统架构师，我希望 DI 容器的配置能正确反映新的依赖关系，以便 Chat_Service_Adapter 能通过容器获取 Model_Registry_Port 实例。

#### 验收标准

1. THE 容器配置 SHALL 将 Model_Registry_Port 注入到 Chat_Service_Adapter 的构造函数中，替代当前注入的 Model_Access_Port。
2. THE 容器配置 SHALL 保留 Model_Access_Port 的注册（供其他可能的消费者使用），其行为保持不变。
3. WHEN 容器初始化完成后，THE Chat_Service_Adapter SHALL 能通过注入的 Model_Registry_Port 访问所有已注册的模型。

### 需求 7：模型路由的负载均衡

**用户故事：** 作为系统运维人员，我希望动态模型路由保留现有的 Round-Robin 负载均衡能力，以便同一模型由多个提供商提供时请求能均匀分布。

#### 验收标准

1. WHEN 同一模型由多个提供商注册时，THE Chat_Service_Adapter 通过 Model_Registry_Port 获取的 Model_Access_Port 实例 SHALL 遵循 Provider_Registry 的 Round-Robin 负载均衡策略。
2. THE 动态模型路由 SHALL 不引入额外的负载均衡逻辑，完全复用 Provider_Registry 现有的 Round-Robin 机制。
