# 维度 3：模型与提示工程

## 评估结论

**评分：3 / 5**。多 Provider 注册、Round-Robin 路由、配置热重载、Prompt 资产目录与版本化注册均已落地；但在"提示工程"侧仍缺少 Prompt 评估集 / A/B 对照 / Prompt Caching 复用，路由策略也仅限"模型名映射"，缺少"按任务类型 / 成本"的智能路由。

## 证据与分析

- [`epsilon-boot/src/application/container_config.py:L60-L65`](../../../epsilon-boot/src/application/container_config.py)
  `PROVIDERS = [("cliproxy", "MODEL_CLIPROXY_"), ("zhipu", ...), ("deepseek", ...), ("qwen", ...)]` 显式登记四类 Provider 入口，由 `_init_model_client` 统一装配。新增 Provider 只需追加键值 + `config.properties` 对应键，无需改核心装配代码。
- [`epsilon-boot/src/infrastructure/model_access/provider_registry.py:L146-L200`](../../../epsilon-boot/src/infrastructure/model_access/provider_registry.py)
  `get_adapter_for_model` 以 `itertools.cycle` 维护每个模型独立的 Round-Robin 迭代器，跨 Provider 均摊负载；Provider 被移除后会重建迭代器，避免悬空引用。
- [`epsilon-boot/src/infrastructure/model_access/router_config.py:L17-L43`](../../../epsilon-boot/src/infrastructure/model_access/router_config.py)
  `RouterConfig` 设置 `hot_reload: ClassVar[bool] = True`，通过 `create_config` 工厂返回 `ConfigProxy` 代理；`default_provider` / `routing_strategy` / `default_model` 在配置文件变更后自动刷新，不必重启服务。
- [`epsilon-boot/config.properties:MODEL_CLIPROXY_MODELS`](../../../epsilon-boot/config.properties)
  每个 Provider 都以 `MODEL_<NAME>_MODELS` / `MODEL_<NAME>_API_KEY` / `MODEL_<NAME>_DEFAULT_MODEL` 三类键驱动注册；Provider 模型清单脱离代码，符合 `docs/steering/config-source.md`"配置优先 `config.properties`"的硬约束。
- [`epsilon-boot/src/infrastructure/prompt/prompt_version_config.py`](../../../epsilon-boot/src/infrastructure/prompt/prompt_version_config.py)
  `PromptVersionConfig` 以 `PROMPT_*_VERSION` 选择 `prompts/<name>/v<N>.md`，并在启动期校验版本格式。
- [`epsilon-boot/src/application/container_config.py:_check_legacy_prompt_conflict`](../../../epsilon-boot/src/application/container_config.py)
  历史 `CHAT_SYSTEM_PROMPT` 与 Prompt 版本机制互斥，检测到旧配置时 fail-fast，避免静默覆盖或静默丢弃。

项目当前已有系统化 Prompt 资产与版本选择，但还没有 Prompt Caching 相关的结构化块声明，也没有独立的 Prompt 评估集或 A/B 对照。

## 业界框架对照

- **OpenAI — Function calling best practices**：建议工具描述在 Provider 之间复用且有版本号。项目把工具 schema 由 `Tool.to_schema` 统一生成，已达成复用；但无版本号字段，未满足版本化建议。
- **Anthropic — Prompt caching（Efficient long-context usage）**：要求把系统提示拆成可缓存块，实现长上下文成本控制。项目目前是纯字符串注入，既没有 cached content 请求结构，也没有缓存命中率观测，明显低于 Anthropic 建议。
- **Anthropic — Long context prompting（Context window management）**：强调窗口内信息排序与压缩策略的可测评。项目通过 `SlidingWindowCompactionAdapter` 做基础窗口压缩，但没有针对 Prompt 质量的评估集。

## 改进建议

1. **P1 — 建立 Prompt 评估集与 A/B 对照**：围绕 `prompt_id` 记录样本表现，把 Prompt 版本变化与任务成功率、工具调用成功率、人工反馈关联起来。
2. **P1 — 引入 Prompt Caching 的结构化块**：对 CLIProxy / Qwen 等支持 cache 的兼容端点，将系统提示包装成可缓存块；配套把 `usage` 里的 cache token 指标暴露到 trace。
3. **P1 — 补齐"按任务类型 / 成本"路由**：在 `RouterConfig` 加入 `task_type -> model` 映射（如 `summarize: glm-4.7`, `code: deepseek-coder`），把当前"只按 model 名字"的策略升级为多维路由。引用 **OpenAI — A Practical Guide to Building Agents**"Agent design patterns"对多模型编排的建议。

<!-- AUTO-START: aggregate_scores -->
### 自动生成区块

**评分**：3 / 5，**权重**：0.14，**加权得分**：0.420

**人工打分理由**：多 Provider 注册、Round-Robin 路由、配置热重载、Prompt 资产目录与版本化注册均已落地，满足 OpenAI "A Practical Guide to Building Agents"（Agent design patterns）对 Provider 解耦的要求，也部分对齐 OpenAI "Function calling best practices" 对工具 schema 复用的建议。但 "提示工程"侧仍缺少 Anthropic "Prompt caching — Efficient long-context usage" 推荐的可缓存块声明、Prompt 评估集与 A/B 对照，路由策略也仅限"模型名映射"，距离 Anthropic "Long context prompting — Context window management" 所建议的按任务类型/成本智能路由有明显差距，因此评 3 分。

#### 全局自动化指标（供维度交叉参考）

| 指标 | 分子 | 分母 | 比率 | 样本数 |
| --- | --- | --- | --- | --- |
| `tool_call_success_rate` | 6 | 20 | 0.3000 | 20 |
| `delegation_correctness` | 6 | 15 | 0.4000 | 15 |
| `context_compaction_effectiveness` | 36 | 36 | 1.0000 | 36 |

#### 证据清单（来自 `tests/evaluation/evidence/catalog.py`）

- `epsilon-boot/src/application/container_config.py:60-65`
- `epsilon-boot/src/infrastructure/model_access/provider_registry.py:146-200`
- `epsilon-boot/src/infrastructure/model_access/router_config.py:17-43`
- `epsilon-boot/config.properties:MODEL_CLIPROXY_MODELS`
- `epsilon-boot/src/infrastructure/prompt/prompt_version_config.py`
- `epsilon-boot/src/application/container_config.py:_check_legacy_prompt_conflict`

<!-- AUTO-END: aggregate_scores -->
