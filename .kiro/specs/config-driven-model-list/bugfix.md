# Bugfix Requirements Document

## Introduction

`ProviderRegistry.register_provider()` 方法在注册模型提供商时，通过 HTTP 请求 `{api_base}/v1/models` 接口来发现可用模型列表。这是一个设计反模式（anti-pattern），因为并非所有模型提供商都支持该接口（如 Anthropic/Claude 不提供 `/v1/models` 端点）。当提供商不支持该接口时，注册会因重试耗尽而失败，导致该提供商无法使用。

实际上，`LangChainProviderConfig` 已经提供了 `models` 配置字段和 `get_model_list()` 方法，可以从 `config.properties` 中读取每个提供商的可用模型列表。`LangChainModelRegistry` 已经在使用这种配置驱动的方式。但 `ProviderRegistry` 忽略了这些配置，仍然依赖 HTTP 发现，造成了不一致和注册失败。

## Bug Analysis

### Current Behavior (Defect)

1.1 WHEN 提供商不支持 `/v1/models` 接口（如 Anthropic/Claude）THEN 系统在 `_discover_models()` 中重试耗尽后返回 `None`，`register_provider()` 返回 `False`，该提供商注册失败，无法使用

1.2 WHEN 提供商支持 `/v1/models` 接口但该接口暂时不可用（网络故障、服务降级等）THEN 系统因模型发现失败而拒绝注册该提供商，即使配置文件中已明确声明了可用模型列表

1.3 WHEN 调用 `register_provider()` 时 THEN 系统要求传入 `api_base`、`api_key`、`timeout`、`max_retries` 等仅用于 HTTP 模型发现的参数，这些参数与提供商注册的核心职责无关，造成接口污染

1.4 WHEN `ProviderRegistry` 初始化时 THEN 系统接受 `http_client` 参数用于模型发现 HTTP 请求，该依赖仅服务于反模式的 HTTP 发现逻辑

1.5 WHEN `config.properties` 中已通过 `MODEL_CLAUDE_MODELS` 等字段配置了提供商的可用模型列表 THEN 系统忽略这些配置，仍然尝试通过 HTTP 接口发现模型

### Expected Behavior (Correct)

2.1 WHEN 提供商不支持 `/v1/models` 接口（如 Anthropic/Claude）THEN 系统 SHALL 从配置文件中读取该提供商的可用模型列表，成功完成注册

2.2 WHEN 提供商的 `/v1/models` 接口暂时不可用 THEN 系统 SHALL 不受影响，因为模型列表来自配置文件而非 HTTP 发现

2.3 WHEN 调用 `register_provider()` 时 THEN 系统 SHALL 接受模型列表（`list[str]`）作为参数，不再要求传入 `api_base`、`api_key`、`timeout`、`max_retries` 等 HTTP 发现相关参数

2.4 WHEN `ProviderRegistry` 初始化时 THEN 系统 SHALL 不再依赖 `http_client`，因为不再需要进行 HTTP 模型发现

2.5 WHEN `config.properties` 中配置了提供商的可用模型列表 THEN 系统 SHALL 使用该配置列表作为提供商的可用模型，通过 `LangChainProviderConfig.get_model_list()` 获取

### Unchanged Behavior (Regression Prevention)

3.1 WHEN 提供商注册成功后 THEN 系统 SHALL CONTINUE TO 维护 `model_name → Set[provider_name]` 的映射关系，支持同一模型由多个提供商提供

3.2 WHEN 请求指定模型名称时 THEN 系统 SHALL CONTINUE TO 通过 Round-Robin 负载均衡算法在支持该模型的多个提供商之间轮询选择

3.3 WHEN 调用 `list_models()` 时 THEN 系统 SHALL CONTINUE TO 返回所有已注册提供商支持的模型信息列表，格式与现有 `ModelInfo` 值对象一致

3.4 WHEN 调用 `get_adapter_for_model()` 时 THEN 系统 SHALL CONTINUE TO 根据模型名称返回对应的提供商适配器实例，未注册模型抛出 `ModelAccessError`

3.5 WHEN 调用 `get_default_model()` 时 THEN 系统 SHALL CONTINUE TO 返回默认模型名称，无可用模型时抛出 `NoAvailableModelError`

3.6 WHEN `LangChainModelRegistry` 注册提供商时 THEN 系统 SHALL CONTINUE TO 使用 `config.get_model_list()` 获取模型列表并通过 `base_llm.bind(model=model_name)` 绑定模型
