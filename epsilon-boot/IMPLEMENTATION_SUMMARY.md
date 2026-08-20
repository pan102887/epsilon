# 多模型路由与负载均衡 - 实现总结

## 📋 实施概览

成功为 epsilon 项目实现了**统一模型接入与负载均衡系统**，支持在 OpenAI、Zhipu AI、DeepSeek 等多个兼容 OpenAI 协议的 LLM 提供商之间进行智能路由和负载均衡。

**架构模式**: Port-Adapter Pattern + Registry with Round-Robin Load Balancing
**核心组件**: 
- `ModelAccessPort`: 统一模型接入协议
- `OpenAICompatibleAdapter`: 通用 OpenAI 协议适配器
- `ProviderRegistry`: 提供商注册与路由中心
- `LoadBalancingModelAdapter`: 具备负载均衡能力的统一入口

---

## ✅ 已完成功能

### 1. 领域层抽象

#### **值对象 (Value Objects)**
- `ChatRequest`: 统一请求参数，支持 `system`, `temperature`, `max_tokens`, `extra_params` 等。
- `ChatResponse`: 统一响应格式，包含内容、模型名、Token 用量和耗时。
- `StreamingChunk`: 流式响应分片，支持增量内容和最终用量统计。
- `ThinkingConfig`: 支持扩展推理配置（预留给支持推理的模型）。
- `ModelInfo`: 模型元数据，包含支持该模型的提供商列表。

#### **端口 (Ports)**
- `ModelAccessPort`: 定义了 `chat` 和 `stream` 两个核心异步接口。
- `ModelRegistryPort`: 定义了提供商注册、模型列表查询、适配器路由等管理接口。

---

### 2. 基础设施层实现

#### **OpenAICompatibleAdapter**
- **通用性**: 支持所有遵循 OpenAI Chat Completion API 协议的提供商（如 Zhipu, DeepSeek, OpenAI 官方）。
- **健壮性**: 完整的错误映射（超时、速率限制、业务错误）。
- **可观测性**: 详细记录请求参数、响应耗时和 Token 用量。
- **流式支持**: 完善的 SSE (Server-Sent Events) 解析逻辑。

#### **ProviderRegistry**
- **配置驱动**: 启动时从配置文件读取每个提供商支持的模型列表并注册。
- **路由逻辑**: 维护 `model -> providers` 的反向索引。
- **负载均衡**: 对每个模型实现 **Round-Robin (轮询)** 算法，确保请求在支持该模型的多个提供商间均匀分布。
- **自动降级**: 当指定模型在某个提供商处未注册时，提供清晰的错误提示。

#### **LoadBalancingModelAdapter**
- **统一入口**: 实现 `ModelAccessPort`，作为应用层唯一依赖的 LLM 接口。
- **自动路由**: 根据请求中的 `model` 自动从注册中心选择最优适配器。
- **默认模型**: 支持配置全局默认模型。

---

### 3. 配置管理

使用 `provider_config.py` 和 `router_config.py` 进行精细化管理：
- 支持多提供商独立配置（API Base, API Key, Timeout, Max Connections）。
- 环境变量前缀支持：`MODEL_ZHIPU_`, `MODEL_DEEPSEEK_`, `MODEL_CLIPROXY_` 等。
- 支持通过 `models` 属性配置每个提供商实际可用的模型列表。

---

## 📊 测试结果

### 单元测试
- `test/domain/model_access/`: 验证值对象合法性和端口定义。
- `test/infrastructure/model_access/`: 
  - 验证 `OpenAICompatibleAdapter` 的协议转换和错误处理。
  - 验证 `ProviderRegistry` 的注册逻辑和 Round-Robin 算法。
  - 验证负载均衡适配器的路由正确性。

### 完整集成
- `ChatServiceAdapter` 已成功集成 `ModelAccessPort`，实现带上下文压缩的对话流。

---

## 🚀 使用指南

### 场景 1: 使用默认模型
```python
request = ChatRequest(messages=[{"role": "user", "content": "你好"}])
response = await model_access.chat(request)
```

### 场景 2: 指定模型（自动路由与负载均衡）
```python
request = ChatRequest(
    messages=[{"role": "user", "content": "分析这段代码"}],
    model="glm-4"  # 自动选择支持 glm-4 的提供商并轮询
)
```

### 场景 3: 流式对话
```python
async for chunk in model_access.stream(request):
    print(chunk.delta_content, end="")
```

---

## 📚 相关文件

### 领域层 (`src/domain/model_access/`)
- `ports.py`: 接口协议
- `value_objects.py`: 数据模型
- `exceptions.py`: 异常定义

### 基础设施层 (`src/infrastructure/model_access/`)
- `openai_compatible_adapter.py`: 核心适配器
- `provider_registry.py`: 注册与路由中心
- `load_balancing_adapter.py`: 负载均衡入口
- `provider_config.py`: 提供商配置
- `router_config.py`: 路由器配置

### 应用集成 (`src/application/`)
- `container_config.py`: 异步资源初始化与 DI 绑定

---

## 🎯 架构优势

1. **解耦**: 业务代码只依赖 `ModelAccessPort`，不感知底层是哪个提供商。
2. **高可用**: 某个模型有多个提供商时，自动实现负载均衡。
3. **易扩展**: 接入新提供商只需在配置文件添加条目，无需修改核心代码。
4. **一致性**: 统一的错误处理和日志格式，极大降低了运维难度。
