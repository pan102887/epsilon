# Config-Driven Model List Bugfix Design

## Overview

`ProviderRegistry.register_provider()` 当前通过 HTTP 请求 `/v1/models` 端点来发现提供商支持的模型列表。这是一个设计反模式：并非所有提供商都支持该端点（如 Anthropic/Claude），且网络不可用时也会导致注册失败。实际上 `LangChainProviderConfig` 已提供 `models` 配置字段和 `get_model_list()` 方法，`LangChainModelRegistry` 也已在使用配置驱动方式。修复方案是将 `ProviderRegistry` 重构为接受 `models: list[str]` 参数，移除 HTTP 发现逻辑，与 `LangChainModelRegistry` 保持一致。

## Glossary

- **Bug_Condition (C)**: `ProviderRegistry.register_provider()` 依赖 HTTP `/v1/models` 端点发现模型列表，当提供商不支持该端点或端点不可用时触发注册失败
- **Property (P)**: `register_provider()` 应接受显式的模型列表参数 `models: list[str]`，直接使用该列表完成注册，不依赖任何 HTTP 请求
- **Preservation**: 注册中心的核心功能（模型→提供商映射、Round-Robin 负载均衡、模型列表查询、适配器路由）必须保持不变
- **ProviderRegistry**: `src/infrastructure/model_access/provider_registry.py` 中的统一供应商注册中心，实现 `ModelRegistryPort` 协议
- **ModelRegistryPort**: `src/domain/model_access/ports.py` 中定义的模型注册中心端口协议
- **LangChainProviderConfig**: `src/infrastructure/model_access/langchain_provider_config.py` 中的提供商配置类，提供 `get_model_list()` 方法从 `config.properties` 读取模型列表
- **_discover_models()**: `ProviderRegistry` 中通过 HTTP 请求 `/v1/models` 发现模型的私有方法，需要被移除
- **RouterConfig**: `src/infrastructure/model_access/router_config.py` 中的路由配置类，其 `discovery_timeout` 和 `discovery_max_retries` 字段将被移除

## Bug Details

### Bug Condition

当 `ProviderRegistry.register_provider()` 被调用时，系统通过 HTTP 请求 `{api_base}/v1/models` 发现模型列表。如果提供商不支持该端点（如 Anthropic/Claude）或端点暂时不可用，`_discover_models()` 重试耗尽后返回 `None`，导致注册失败。同时，`register_provider()` 的签名包含仅用于 HTTP 发现的参数（`api_base`、`api_key`、`timeout`、`max_retries`），造成接口污染。

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type RegisterProviderCall
  OUTPUT: boolean

  // 当前实现中，任何调用 register_provider 的场景都受此 bug 影响：
  // 1. 提供商不支持 /v1/models → _discover_models 返回 None → 注册失败
  // 2. 提供商支持但端点暂时不可用 → 同上
  // 3. 即使成功，也依赖了不必要的 HTTP 请求
  RETURN input.uses_http_discovery == True
         AND (NOT provider_supports_v1_models(input.provider_name)
              OR NOT endpoint_available(input.api_base))
END FUNCTION
```

### Examples

- **Claude 提供商注册失败**: 调用 `register_provider(provider_name="claude", ..., api_base="https://api.anthropic.com")` 时，Anthropic 不提供 `/v1/models` 端点，`_discover_models()` 重试 3 次后返回 `None`，`register_provider()` 返回 `False`，Claude 提供商无法使用
- **CLIProxy 网络故障**: 调用 `register_provider(provider_name="cliproxy", ..., api_base="http://localhost:8317/v1")` 时，CLIProxy 服务暂时不可用，即使 `config.properties` 中已配置 `MODEL_CLIPROXY_DEFAULT_MODEL=glm-4.7`，注册仍然失败
- **接口污染**: `container_config.py` 中调用 `register_provider()` 时需要传入 `api_base=config.api_base, api_key=config.api_key, timeout=router_config.discovery_timeout, max_retries=router_config.discovery_max_retries`，这些参数与提供商注册的核心职责无关
- **正常场景（非 bug）**: 当提供商支持 `/v1/models` 且端点可用时，注册成功，但仍然执行了不必要的 HTTP 请求

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- `model_name → Set[provider_name]` 映射关系的维护逻辑不变，同一模型可由多个提供商提供
- `get_adapter_for_model()` 的 Round-Robin 负载均衡算法不变
- `list_models()` 返回 `ModelInfo` 列表的格式和排序逻辑不变
- `get_default_model()` 的默认模型选择逻辑不变（优先使用配置值，否则使用首个注册模型）
- `LangChainModelRegistry` 的注册和路由逻辑完全不受影响
- `LoadBalancingModelAdapter` 作为 `ModelAccessPort` 统一入口的行为不变

**Scope:**
所有不涉及 `register_provider()` 签名和 `_discover_models()` 的代码路径不受影响。具体包括：
- `list_models()`、`get_adapter_for_model()`、`get_default_model()` 的内部实现
- `LangChainModelRegistry` 的全部功能
- `LoadBalancingModelAdapter` 的全部功能
- 所有下游消费者（ChatService、路由 API 等）

## Hypothesized Root Cause

基于代码分析，根本原因是 `ProviderRegistry` 的设计选择了错误的模型发现策略：

1. **HTTP 发现作为唯一模型来源**: `register_provider()` 将 HTTP `/v1/models` 作为获取模型列表的唯一途径，忽略了 `config.properties` 中已有的模型配置。这导致不支持该端点的提供商（如 Anthropic/Claude）无法注册。

2. **与 LangChainModelRegistry 的不一致**: `LangChainModelRegistry.register_provider()` 已经通过 `config.get_model_list()` 从配置文件获取模型列表，而 `ProviderRegistry` 却使用 HTTP 发现，两个注册中心的模型来源不一致。

3. **不必要的 HTTP 依赖**: `ProviderRegistry.__init__` 接受 `http_client` 参数，`register_provider()` 接受 `api_base`、`api_key`、`timeout`、`max_retries` 参数，这些都仅服务于 HTTP 发现逻辑，造成了不必要的依赖和接口污染。

4. **RouterConfig 中的冗余配置**: `RouterConfig` 包含 `discovery_timeout` 和 `discovery_max_retries` 字段，这些配置仅用于 HTTP 模型发现，移除 HTTP 发现后不再需要。

## Correctness Properties

Property 1: Bug Condition - 配置驱动的模型列表注册

_For any_ 调用 `register_provider(provider_name, adapter, models)` 且 `models` 为非空列表时，修复后的函数 SHALL 直接使用传入的 `models` 列表完成注册，不发起任何 HTTP 请求，返回 `True`。所有传入的模型名称都应出现在 `_model_providers` 映射中，且关联到对应的 `provider_name`。

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5**

Property 2: Preservation - 注册中心核心功能不变

_For any_ 通过新签名 `register_provider(provider_name, adapter, models)` 成功注册的提供商集合，修复后的 `list_models()`、`get_adapter_for_model()`、`get_default_model()` SHALL 产生与原始实现在相同模型数据下完全一致的结果，保持 `model_name → Set[provider_name]` 映射、Round-Robin 负载均衡、`ModelInfo` 列表格式和默认模型选择逻辑不变。

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**

## Fix Implementation

### Changes Required

假设根因分析正确，需要修改以下文件：

**File**: `src/domain/model_access/ports.py`

**Interface**: `ModelRegistryPort.register_provider`

**Specific Changes**:
1. **更新方法签名**: 移除 `api_base: str`、`api_key: str`、`timeout: float`、`max_retries: int` 参数，新增 `models: list[str]` 参数
2. **更新 docstring**: 反映新的配置驱动注册流程，移除 HTTP 发现相关描述
3. **方法改为同步**: 不再需要 `async`，因为没有 HTTP I/O 操作

---

**File**: `src/infrastructure/model_access/provider_registry.py`

**Class**: `ProviderRegistry`

**Specific Changes**:
1. **移除 `http_client` 构造参数**: `__init__` 不再接受 `http_client: httpx.AsyncClient | None` 参数，移除 `self._http_client` 属性
2. **重写 `register_provider` 方法**: 改为同步方法，接受 `models: list[str]` 参数，直接使用该列表注册，不再调用 `_discover_models()`
3. **删除 `_discover_models` 方法**: 整个方法及其 HTTP 请求逻辑全部移除
4. **移除 `httpx` 导入**: 不再需要 `import httpx`

---

**File**: `src/infrastructure/model_access/router_config.py`

**Class**: `RouterConfig`

**Specific Changes**:
1. **移除 `discovery_timeout` 字段**: 不再需要模型发现超时配置
2. **移除 `discovery_max_retries` 字段**: 不再需要模型发现重试配置
3. **更新 docstring**: 移除模型发现参数的描述

---

**File**: `src/application/container_config.py`

**Function**: `_init_model_client`

**Specific Changes**:
1. **更新 `register_provider` 调用**: 传入 `models=config.get_model_list()` 替代 `api_base`、`api_key`、`timeout`、`max_retries`
2. **调用改为同步**: `await _provider_registry.register_provider(...)` 改为 `_provider_registry.register_provider(...)`
3. **更新模块 docstring**: 移除 "自动调用 /v1/models 发现模型列表" 的描述

---

**File**: `epsilon-boot/config.properties`

**Specific Changes**:
1. **移除 `MODEL_ROUTER_DISCOVERY_TIMEOUT`**: 不再需要
2. **移除 `MODEL_ROUTER_DISCOVERY_MAX_RETRIES`**: 不再需要
3. **确保各提供商的 `MODELS` 配置存在**: 如 `MODEL_CLIPROXY_MODELS=glm-4.7`、`MODEL_CLAUDE_MODELS=claude-3-5-sonnet-20241022` 等，使 `get_model_list()` 能返回正确的模型列表

## Testing Strategy

### Validation Approach

测试策略分两阶段：首先在未修复代码上验证 bug 的存在（探索性测试），然后验证修复的正确性和行为保持。

### Exploratory Bug Condition Checking

**Goal**: 在实施修复前，通过测试用例展示 bug 的存在，确认或否定根因分析。如果否定，需要重新假设根因。

**Test Plan**: 编写测试模拟不支持 `/v1/models` 的提供商注册场景，在未修复代码上运行以观察失败。

**Test Cases**:
1. **Claude 注册失败测试**: 模拟 Anthropic API 不提供 `/v1/models` 端点，验证 `register_provider()` 返回 `False`（在未修复代码上会失败）
2. **网络不可用测试**: 模拟 HTTP 请求超时，验证即使配置文件有模型列表，注册仍然失败（在未修复代码上会失败）
3. **接口签名测试**: 验证当前 `register_provider()` 需要 `api_base`、`api_key` 等 HTTP 发现参数（在未修复代码上会通过，证明接口污染存在）

**Expected Counterexamples**:
- `register_provider()` 在提供商不支持 `/v1/models` 时返回 `False`
- 可能原因：`_discover_models()` 的 HTTP 请求失败，重试耗尽后返回 `None`

### Fix Checking

**Goal**: 验证对于所有满足 bug 条件的输入，修复后的函数产生期望行为。

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := register_provider_fixed(
    provider_name=input.provider_name,
    adapter=input.adapter,
    models=input.models_from_config
  )
  ASSERT result == True
  ASSERT all models in input.models_from_config are registered
  ASSERT provider_name appears in model_providers mapping for each model
END FOR
```

### Preservation Checking

**Goal**: 验证对于所有不满足 bug 条件的输入，修复后的函数与原始函数产生相同结果。

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  // 使用相同的模型列表数据，验证注册后的查询行为一致
  ASSERT list_models_original(input) == list_models_fixed(input)
  ASSERT get_adapter_for_model_original(input) == get_adapter_for_model_fixed(input)
  ASSERT get_default_model_original(input) == get_default_model_fixed(input)
END FOR
```

**Testing Approach**: 推荐使用基于属性的测试（Property-Based Testing）进行保持性验证，因为：
- 可自动生成大量测试用例覆盖输入域
- 能捕获手动单元测试可能遗漏的边界情况
- 对非 bug 输入的行为不变性提供强保证

**Test Plan**: 先在未修复代码上观察正常注册后的查询行为，然后编写基于属性的测试验证修复后行为一致。

**Test Cases**:
1. **模型映射保持测试**: 注册多个提供商后，验证 `list_models()` 返回的 `ModelInfo` 列表与预期一致
2. **Round-Robin 保持测试**: 注册同一模型的多个提供商后，验证 `get_adapter_for_model()` 按轮询顺序返回
3. **默认模型保持测试**: 验证 `get_default_model()` 在有/无配置默认模型时的行为一致
4. **未注册模型异常保持测试**: 验证查询未注册模型时仍抛出 `ModelAccessError`

### Unit Tests

- 测试 `register_provider()` 接受 `models: list[str]` 参数并正确注册
- 测试空模型列表时 `register_provider()` 返回 `False`
- 测试重复注册同一提供商时的覆盖行为
- 测试 `ProviderRegistry.__init__` 不再接受 `http_client` 参数

### Property-Based Tests

- 生成随机提供商名称和模型列表，验证注册后 `list_models()` 包含所有模型
- 生成随机多提供商注册序列，验证 `model_providers` 映射的正确性
- 生成随机模型查询序列，验证 Round-Robin 负载均衡的轮询行为

### Integration Tests

- 测试完整的提供商注册流程：从 `LangChainProviderConfig.get_model_list()` 获取模型列表到 `ProviderRegistry.register_provider()` 注册
- 测试 `container_config._init_model_client()` 使用新签名正确初始化注册中心
- 测试 `LoadBalancingModelAdapter` 通过修复后的 `ProviderRegistry` 正确路由请求
