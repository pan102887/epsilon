# 模型路由

## 多 Provider 注册

每个 Provider 在 `config.properties` 中以 `MODEL_<PREFIX>_*` 键组配置：

```properties
MODEL_CLIPROXY_ENABLED=true
MODEL_CLIPROXY_PROVIDER_NAME=cliproxy
MODEL_CLIPROXY_API_BASE=http://localhost:8317/v1
MODEL_CLIPROXY_API_KEY=...
MODEL_CLIPROXY_DEFAULT_MODEL=glm-4.7
MODEL_CLIPROXY_MODELS=glm-4.7
# 其他通用字段：TEMPERATURE / MAX_TOKENS / TIMEOUT / MAX_RETRIES /
#              MAX_CONNECTIONS / MAX_KEEPALIVE_CONNECTIONS
```

`container_config.PROVIDERS` 中候选 env_prefix：`cliproxy` / `zhipu` / `deepseek` / `qwen` / `openai`。每个 Provider 在以下条件全部满足时才会被注册到 `ProviderRegistry`：

1. `MODEL_<PREFIX>_ENABLED=true`；
2. `MODEL_<PREFIX>_API_KEY` 非空；
3. `MODEL_<PREFIX>_PROVIDER_NAME` 非空；
4. `get_model_list()` 结果非空（由 `MODEL_<PREFIX>_MODELS` 或 `DEFAULT_MODEL` 决定）。

当前 `config.properties` 默认启用 `cliproxy`、`zhipu`、`qwen`、`openai` 配置组；API key 为空的 Provider 会在启动时跳过注册，`deepseek` 作为候选保留，按需补齐配置键即可上线。

## 路由策略

`ProviderRegistry`（实现 `ModelRegistryPort`）在查询模型时对所有提供该模型的 Provider 维护 `itertools.cycle` 做 Round-Robin 分发。`MODEL_ROUTER_ROUTING_STRATEGY` 目前支持：

| 策略 | 说明 |
|---|---|
| `model_prefix`（默认） | 按模型名解析对应 Provider 集合后轮询 |
| `explicit` | 仅使用显式指定的 Provider |

## 路由入口

`ChatServiceAdapter._resolve_model_access(model: str | None) -> tuple[ModelAccessPort, str]`：

- 指定 model → `model_registry.get_adapter_for_model(model)`
- 未指定 → `model_registry.get_default_model()` → `get_adapter_for_model()`
- 未知 model → 抛出 `ModelAccessError`

`/v1/models` 端点直接返回 `ProviderRegistry.list_models()` 的结果，兼容 OpenAI `/v1/models` 格式并附加 `providers` 扩展字段。

## 热重载

`ProviderConfig` / `RouterConfig` 均声明 `hot_reload: ClassVar[bool] = True`；配置文件变更后 `create_config` 创建的 ConfigProxy 会感知变化自动重新加载，无需重启服务。

## 配置驱动模型列表

可用模型列表从 `config.properties` 的 `MODEL_<PREFIX>_MODELS`（逗号分隔）或 `MODEL_<PREFIX>_DEFAULT_MODEL` 字段读取，不依赖 Provider API 的 `/models` 端点（避免网络依赖和 API 差异）。
