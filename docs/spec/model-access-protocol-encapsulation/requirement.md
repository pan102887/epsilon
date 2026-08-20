# 需求文档：model_access 协议封装边界归位

## 简介

### 背景与动机

本仓库后端按 DDD 六边形架构组织，`domain/model_access/` 通过 `ModelAccessPort` 端口表达"与 LLM 交互"的业务能力，`infrastructure/model_access/` 提供具体 Provider 适配器（当前仅含 `OpenAICompatibleAdapter`，被 cliproxy / zhipu / deepseek / qwen / openai 五个 Provider 复用）。`docs/steering/ddd-architecture.md` 明确规定：`infrastructure/` 完成技术转换，不得反向要求 `domain/` 感知具体实现细节。

但当前实现存在端口归属错位：

```
ChatService / ReActAgent
    ↓ 已经丢失领域类型
ContextBuilderAdapter.build(messages: list[BaseMessage])
    ↓ infrastructure/chat/message_serialization.py
serialize_messages() → list[dict OpenAI 协议格式]
    ↓
ChatRequest(messages=list[dict[str, Any]])      ← 端口契约已 OpenAI 协议化
    ↓ domain/model_access/value_objects.py:46
OpenAICompatibleAdapter._build_params()
    params["messages"] = request.messages       ← 直接透传，无任何 adapter 内转换
```

具体证据：

- `src/domain/model_access/value_objects.py:46` 把 `ChatRequest.messages` 写为 `list[dict[str, Any]]`，且 `__post_init__` 校验每条消息含 `role` / `content` 键（暗含 OpenAI Chat Completions 形态假设）；
- `src/domain/model_access/value_objects.py:53` 把 `ChatRequest.tools` 写为 `list[dict[str, Any]]`，docstring 显式指明"OpenAI function calling schema"；
- `src/infrastructure/chat/message_serialization.py` 的 `serialize_messages()` 直接产出 OpenAI Chat Completions 协议字典（`tool_calls` 嵌套 `{"id","type":"function","function":{...}}`、`tool_call_id` 等），但所在分层（`infrastructure/chat/`）和服务对象（被多个上游和 token 估算共用）都不属于 model_access 边界；
- `src/infrastructure/model_access/openai_compatible_adapter.py:408` `_build_params` 直接 `params["messages"] = request.messages`，没有任何 adapter 内转换，证明协议化已被"提前完成"在端口外；
- `src/infrastructure/chat/token_counter.py:42` 通过调用 `serialize_messages([message])` 把领域消息先序列化成 OpenAI 字典再交给 tiktoken 估算 token，导致"估算精度"和"OpenAI 协议结构"耦合；
- 所有 5 个已注册 Provider（cliproxy / zhipu / deepseek / qwen / openai）均通过同一个 `OpenAICompatibleAdapter` 适配，未来若要扩展非 OpenAI 协议的 Provider（Anthropic Messages API、AWS Bedrock Converse、Google Gemini 等），当前架构无法以"零侵入领域层"的方式支持，必须改造端口契约才能容纳新协议形态。

### 业务/架构动机

1. **DDD 端口归属正确化**：协议转换属于"对接外部系统、完成技术转换"，按规范属于 `infrastructure/` 内每个具体 adapter 的私有职责，不应在端口契约和上游编排层泄漏；
2. **Provider 扩展零侵入**：未来引入 Anthropic Messages API、Bedrock Converse、Gemini 等非 OpenAI 协议 Provider 时，只需新增 adapter 实现 `ModelAccessPort`，无需触动 `domain/`、`application/` 或其他 adapter；
3. **Tokenizer 与 Provider 绑定**：不同 Provider 用不同 tokenizer（OpenAI 用 tiktoken、Anthropic 有自己的 tokenizer、不同模型 BPE 词表不同），token 计数应由"持有该模型 SDK 的 adapter"提供，不应由上游用一份 tiktoken 估算所有 Provider；
4. **领域信息不丢**：上游编排层（ReActAgent / ChatService / ContextBuilder / Compaction）应当持续持有 `BaseMessage` 类型，让上游需要 inspect 消息结构（如计算消息数、判断 ToolMessage 等）时直接读领域字段，不必反序列化字典。

### In-Scope（本次治理范围）

1. **端口契约去 OpenAI 协议化**：`ChatRequest.messages` 字段类型由 `list[dict[str, Any]]` 改为承载领域消息（`list[BaseMessage]`），`__post_init__` 校验改为校验领域消息子类；
2. **协议转换下沉到具体 adapter**：删除 `infrastructure/chat/message_serialization.py` 的对外暴露，`OpenAICompatibleAdapter` 内部以私有 helper 承担"领域消息 → OpenAI 协议字典"转换，未来新增 adapter 各自承担其协议转换；
3. **Token 计数下沉到端口**：`ModelAccessPort` 新增 token 计数能力（如 `count_tokens(messages: list[BaseMessage]) -> int`），由每个具体 adapter 用对应 Provider 的 tokenizer 实现；上游不再依赖 OpenAI 字典化估算；
4. **Tools schema 治理**：`ChatRequest.tools` 字段去 OpenAI function calling 协议假设。`design.md` 阶段确定具体形态（领域 `ToolSchema` 值对象 vs. 维持 `list[dict]` 但归口由 adapter 内部转换），并允许分阶段实施；
5. **上游 4 个调用点改造**：`ContextBuilderAdapter` / `ReActAgentAdapter` / `LLMSummaryCompactionAdapter` / `TokenCounter` 不再生成或依赖 OpenAI 协议字典；`ContextBuilderResult.serialized_messages` 字段同步去字典化（改为承载领域消息或重命名为反映新语义的字段）；
6. **Provider 不退化**：现有所有已注册 Provider（cliproxy / zhipu / deepseek / qwen / openai）功能与可观测性（错误映射、tool_call.id 校验、流式累积）保持不变；
7. **测试覆盖**：每个 adapter 的协议转换、token 计数行为均有单元/属性测试覆盖。

### Out-of-Scope（不在本次范围）

1. **响应侧值对象**：`LLMResponse` / `StreamingChunk` / `StreamingToolCallDelta` / `ToolCallRequest` 已是领域值对象（参见 `docs/spec/llm-and-tool-resilience`、`docs/spec/structured-agent-trace`），本次不动响应侧；
2. **`ToolCallRequest.id` 校验链路**：commit `040695a` 已加固该链路（参见 `docs/spec/id-validation-analysis`），本次重构是请求侧，不互相影响；
3. **Provider 业务功能扩展**：本次只做"边界归位"，不新增 Anthropic / Bedrock / Gemini 等具体 adapter，但要保证新增这些 adapter 时不再需要触动领域层；
4. **`ModelRegistryPort` / 路由策略**：模型注册中心、负载均衡、热重载逻辑不在本次范围；
5. **环境上下文注入、压缩触发阈值、Prompt 资产注册**：`ContextBuilderAdapter` 的环境上下文注入语义、`LLMSummaryCompactionAdapter` 的触发条件与 prompt 装载行为不在本次范围（仅同步替换内部协议转换调用）；
6. **前端、API、persistence 层**：本次纯后端 model_access / chat / agent 内部边界整理，不触动任何外部接口形态。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 模型接入端口 | `ModelAccessPort` | `domain/model_access/ports.py` 中的 Protocol，表达"与 LLM 交互"的业务能力边界，本次新增 token 计数能力。 |
| 对话请求值对象 | `ChatRequest` | `domain/model_access/value_objects.py` 中的请求值对象，本次将 `messages` 字段从 OpenAI 协议字典列表改为领域消息列表。 |
| 领域消息基类 | `BaseMessage` | `domain/chat/context.py` 中的抽象消息基类，含 `SystemMessage` / `UserMessage` / `AssistantMessage` / `ToolMessage` 四个具体子类，是端口契约承载消息的唯一类型。 |
| OpenAI 兼容协议适配器 | `OpenAICompatibleAdapter` | `infrastructure/model_access/openai_compatible_adapter.py` 中的具体实现，负责把 `BaseMessage` 列表转换为 OpenAI Chat Completions 协议字典并调用 SDK；本次承担新增的 token 计数职责。 |
| 上下文构建适配器 | `ContextBuilderAdapter` | `infrastructure/chat/context_builder_adapter.py` 中的实现，负责压缩与环境上下文注入；本次输出从 OpenAI 字典改为领域消息列表。 |
| ReAct Agent 适配器 | `ReActAgentAdapter` | `infrastructure/agent/react_agent_adapter.py` 中的实现，负责 ReAct Loop 编排；本次取消 `_serialize_messages` 静态方法，改为透传领域消息。 |
| LLM 摘要压缩适配器 | `LLMSummaryCompactionAdapter` | `infrastructure/chat/llm_summary_compaction_adapter.py` 中的实现，负责生成历史摘要；本次构造 `ChatRequest` 时不再调用 `serialize_messages`。 |
| Token 计数器 | `TokenCounter` | `infrastructure/chat/token_counter.py` 中的实现，本次去 OpenAI 字典化路径，改为通过 `ModelAccessPort` 拿到 Provider 自身的 token 计数能力或由 adapter 直接对领域消息计数。 |
| 上下文构建结果 | `ContextBuilderResult` | `domain/chat/value_objects.py` 中的结果值对象，本次 `serialized_messages` 字段去 OpenAI 字典化（类型与/或字段名同步调整以反映新语义）。 |
| 协议转换函数 | `Serialize_Messages_Helper` | 当前位于 `infrastructure/chat/message_serialization.py` 的 `serialize_messages` 顶层函数；本次治理后不再被任何 model_access 之外的模块调用，可整体下沉到 `OpenAICompatibleAdapter` 内部私有 helper 或删除。 |
| 工具 Schema | `Tool_Schema` | 当前以 OpenAI function calling 字典形态由 `ToolRegistry.get_schemas()` 暴露并经 `ChatRequest.tools` 透传；本次治理后端口契约不再硬绑定 OpenAI 形态，由 adapter 内部完成到具体 Provider schema 的转换。 |
| Provider 扩展位 | `Provider_Extension_Slot` | 未来可能新增的非 OpenAI 协议 adapter 槽位（Anthropic Messages API、AWS Bedrock Converse、Google Gemini 等），本次治理的设计目标之一是保证新增此类 adapter 不再需要触动领域层或上游编排层。 |
| 上游编排层 | `Upstream_Orchestrator` | 调用 `ModelAccessPort` 的所有上游模块的统称，包括 `ContextBuilderAdapter`、`ReActAgentAdapter`、`LLMSummaryCompactionAdapter`、`ChatServiceAdapter` 等。 |

## 需求

### 需求 1：端口契约去 OpenAI 协议化

**用户故事：** 作为模型接入层的维护者，我希望 `ChatRequest` 端口契约只承载领域消息类型，以便领域层不再隐含特定 LLM 协议假设，新增 Provider 时无需修改 `domain/`。

#### 验收标准

1. THE `ChatRequest` SHALL 把 `messages` 字段类型声明为 `list[BaseMessage]`（或语义等价的领域消息序列类型），不再使用 `list[dict[str, Any]]`。
2. THE `ChatRequest` SHALL 在 `__post_init__` 中校验 `messages` 列表所有元素均为 `BaseMessage` 子类实例，不再校验是否包含 `role` / `content` 字典键。
3. WHEN `ChatRequest` 的 docstring 描述 `messages` 字段时, THE `ChatRequest` SHALL 不出现 "OpenAI" / "Chat Completions" / 字典示例 / `role` 键名等暗含特定协议的措辞。
4. THE `ChatRequest` SHALL 在 `tools` 字段说明中移除 "OpenAI function calling schema" 假设；具体 Tool_Schema 形态由 `design.md` 阶段决定（领域 `Tool_Schema` 值对象 vs. 维持 `list[dict]` 但语义改为"由各 adapter 内部翻译"），但端口契约文档不得再硬绑定 OpenAI 形态。
5. FOR ALL 现有调用 `ChatRequest(messages=...)` 的位置, THE `Upstream_Orchestrator` SHALL 改为传入 `list[BaseMessage]`，不再先行调用 `Serialize_Messages_Helper`。

### 需求 2：协议转换下沉到具体 adapter

**用户故事：** 作为 `OpenAICompatibleAdapter` 的维护者，我希望"领域消息 → OpenAI Chat Completions 协议字典"的转换完全发生在 adapter 内部，以便协议细节（`tool_calls` 嵌套结构、`tool_call_id` 字段、message role 命名）的演进不再外溢到端口契约或上游模块。

#### 验收标准

1. THE `OpenAICompatibleAdapter` SHALL 在 `_build_params` 调用前，通过 adapter 内部的私有 helper 把 `request.messages: list[BaseMessage]` 转换为 OpenAI Chat Completions API 所需的字典列表。
2. THE `OpenAICompatibleAdapter` SHALL 不直接把 `request.messages` 透传给 OpenAI SDK 的 `messages` 参数。
3. WHEN `Serialize_Messages_Helper` 模块（`infrastructure/chat/message_serialization.py`）保留时, THE `Serialize_Messages_Helper` SHALL 仅作为 `OpenAICompatibleAdapter` 的内部依赖被引用，不得被 `infrastructure/chat/` 或 `infrastructure/agent/` 中任何 model_access 之外的模块导入。
4. IF 设计阶段决定移除 `Serialize_Messages_Helper` 顶层函数, THEN THE `OpenAICompatibleAdapter` SHALL 在 `infrastructure/model_access/` 目录内实现等价私有 helper（如静态方法或同包私有函数），并保证语义与原 `serialize_messages` 完全一致。
5. FOR ALL 现有 OpenAI 兼容 Provider（cliproxy / zhipu / deepseek / qwen / openai）, THE `OpenAICompatibleAdapter` SHALL 在改造后保持原有协议转换语义不变，包括 AssistantMessage 携带 tool_calls 时输出嵌套 `{"id","type":"function","function":{"name","arguments"}}`、ToolMessage 输出 `tool_call_id`、其他消息仅输出 `role` 与 `content`。
6. THE `OpenAICompatibleAdapter` SHALL 保留既有 `tool_call.id` 前置校验语义（commit `040695a` 引入），不因协议转换下沉而退化。

### 需求 3：Token 计数下沉到端口

**用户故事：** 作为上游编排层的维护者，我希望通过 `ModelAccessPort` 获取与具体 Provider tokenizer 一致的 token 计数能力，以便不同 Provider 引入差异化 tokenizer 时上游无需感知，并消除"用 OpenAI tiktoken 估算所有 Provider"的精度偏差。

#### 验收标准

1. THE `ModelAccessPort` SHALL 暴露 token 计数能力（具体方法签名由 `design.md` 决定，候选形态包含 `count_tokens(messages: list[BaseMessage]) -> int`、`count_text(text: str) -> int` 或两者并存）。
2. THE `OpenAICompatibleAdapter` SHALL 实现 `ModelAccessPort` 新增的 token 计数方法，使用 OpenAI 体系下的 tiktoken encoding 完成估算。
3. THE `Upstream_Orchestrator` SHALL 不直接依赖 `tiktoken` 模块完成 `BaseMessage` 列表的 token 估算（`infrastructure/chat/token_counter.py` 等当前持有的字典化估算路径必须改造）。
4. WHEN `LLMSummaryCompactionAdapter` 判定是否触发摘要压缩时, THE `LLMSummaryCompactionAdapter` SHALL 通过 `ModelAccessPort`（或基于其的间接接口）获得 token 估算结果，且估算路径不依赖 OpenAI 字典化中间结构。
5. THE token 计数链路 SHALL 在所有现有 Provider（cliproxy / zhipu / deepseek / qwen / openai）下返回与改造前等价或更精确的估算结果，且对压缩触发阈值（`trigger_tokens`）的判定行为不退化（既有针对压缩触发的属性测试与单元测试均通过）。
6. IF 某个 `Provider_Extension_Slot` 没有合适的本地 tokenizer, THEN THE 对应 adapter SHALL 提供合理回退实现（由 `design.md` 给出策略，例如复用通用 BPE encoding 或基于字符长度近似），并在 docstring 中显式说明。

### 需求 4：上游 4 个调用点不再产生 OpenAI 协议字典

**用户故事：** 作为 `Upstream_Orchestrator` 的维护者，我希望在编排链路中始终持有 `BaseMessage` 领域类型，以便 inspect 消息结构、记录 trace、计算消息数等操作直接读领域字段，不再经手 OpenAI 协议字典。

#### 验收标准

1. THE `ContextBuilderAdapter.build` SHALL 不再调用 `Serialize_Messages_Helper`，其返回的 `ContextBuilderResult` 在结构上承载领域消息列表（字段类型与/或字段名由 `design.md` 决定，但禁止再以 OpenAI Chat Completions 字典作为契约形态）。
2. THE `ContextBuilderResult` SHALL 在 `__post_init__` 中以"是否为 `BaseMessage` 子类"作为校验依据，不再校验字典是否包含 `role` / `content` 键。
3. THE `ReActAgentAdapter` SHALL 移除内部 `_serialize_messages` 静态方法（或仅保留为废弃别名直至其所有调用点迁移完成），并在调用 `ModelAccessPort.chat` / `stream` 时直接传入 `list[BaseMessage]`。
4. THE `LLMSummaryCompactionAdapter._build_summary_request` SHALL 在构造 `ChatRequest` 时不调用 `Serialize_Messages_Helper`；摘要 prompt 的 user 消息正文若仍需历史消息字符串化形式，由 adapter 内部以非 OpenAI 协议结构生成（例如调用领域消息自身 `to_dict()` 或独立的可读化函数）。
5. THE `TokenCounter`（或其继任者）SHALL 不再 import `Serialize_Messages_Helper`，其内部估算路径不依赖任何 OpenAI 协议字典。
6. FOR ALL 上述 4 个调用点, THE `Upstream_Orchestrator` SHALL 在改造后通过现有的相关单元测试与属性测试，必要时调整测试中的辅助 fixture 以传入 `BaseMessage` 列表而非 OpenAI 字典。

### 需求 5：Provider 行为不退化

**用户故事：** 作为系统运维者，我希望本次重构不引入任何运行期行为差异，以便已上线的所有 Provider（cliproxy / zhipu / deepseek / qwen / openai）继续按原契约工作。

#### 验收标准

1. FOR ALL 已注册 Provider（cliproxy / zhipu / deepseek / qwen / openai）, THE `OpenAICompatibleAdapter` SHALL 在 `chat` / `stream` 调用时构造的 OpenAI SDK `messages` 参数与改造前完全等价（同一份 `BaseMessage` 列表输入下，构造出的字典列表 byte-equal 或 dict-equal）。
2. WHEN OpenAI SDK 返回错误（`APITimeoutError` / `RateLimitError` / `APIConnectionError` / `APIError`）时, THE `OpenAICompatibleAdapter` SHALL 维持现有领域异常映射（`ModelTimeoutError` / `ModelRateLimitError` / `ModelConnectionError` / `ModelAccessError`）。
3. WHILE `OpenAICompatibleAdapter` IN 流式工具调用累积态, WHEN 末尾分片 `finished=True` 到来时, THE `OpenAICompatibleAdapter` SHALL 维持 `_materialize_full_tool_calls` 的现有契约（按 index 升序、空串归 None、纯文本流返回 None）。
4. THE retry 装饰器（`build_retry`）SHALL 维持对 `_chat_completion_once` / `_stream_open_once` 的覆盖范围与重试策略不变。
5. THE `ContextBuilderAdapter` 的环境上下文注入语义、`LLMSummaryCompactionAdapter` 的压缩触发阈值与降级策略 SHALL 在改造后保持完全一致。

### 需求 6：测试覆盖每个 adapter 的协议转换与 token 计数

**用户故事：** 作为代码评审者，我希望每个 adapter 的协议转换与 token 计数都有专属测试覆盖，以便未来扩展新 Provider 时可以照同一模板补齐测试。

#### 验收标准

1. THE `OpenAICompatibleAdapter` SHALL 拥有针对其内部"领域消息 → OpenAI 字典"转换的单元测试，覆盖至少：仅含 SystemMessage / UserMessage、含 AssistantMessage 携带 tool_calls、含 ToolMessage 三类典型形态。
2. THE `OpenAICompatibleAdapter` SHALL 拥有针对其 token 计数实现的单元测试，至少覆盖空消息列表、纯文本消息列表、含 tool_calls 的消息列表三类形态。
3. WHEN 既有 `test/infrastructure/chat/test_message_serialization_unit.py` 等针对 `Serialize_Messages_Helper` 的测试在改造后失去顶层入口时, THE 对应测试 SHALL 迁移为针对 `OpenAICompatibleAdapter` 内部 helper 的等价覆盖（直接调用 adapter 内部方法或通过构造 `ChatRequest` + mock SDK 间接断言）。
4. THE 既有所有针对 `ContextBuilderAdapter` / `ReActAgentAdapter` / `LLMSummaryCompactionAdapter` / `TokenCounter` 的单元测试与属性测试 SHALL 在改造后调整为传入 `BaseMessage` 列表，并继续通过。
5. FOR ALL 新引入的 `ModelAccessPort` 抽象方法, THE 端口对应的测试 SHALL 包含至少一个 fake/stub adapter 实现，验证上游编排层在不依赖具体 Provider 的前提下可以独立测试 token 计数与协议无关行为。
6. THE 测试套件 SHALL 在重构完成后整体通过（`uv run pytest` 命令在仓库根目录执行通过），不引入新的 skip / xfail。

### 需求 7：DDD 与文档约束

**用户故事：** 作为 steering 规范的维护者，我希望本次重构严格遵循已有 DDD 与代码文档规范，以便代码库的质量基线不被削弱。

#### 验收标准

1. THE `domain/model_access/` SHALL 不引入对 `infrastructure/`、OpenAI SDK、`tiktoken`、`pydantic_settings` 等基础设施依赖的 import。
2. THE `domain/chat/` SHALL 不引入对 OpenAI SDK / `tiktoken` 的直接依赖（既有 `domain/chat/value_objects.py` 中 `ContextBuilderResult` 现持有的 OpenAI 字典假设须解除）。
3. THE 本次新增或调整的所有模块、公开类、公开函数与方法 SHALL 提供中文 docstring，复杂逻辑（协议转换、tokenizer 选择、回退策略）须在 docstring 中补充背景说明。
4. THE 本次涉及的依赖管理操作 SHALL 仅通过 `uv` 完成，不得引入 `pip` / `poetry` / `pipenv` / `conda` 命令或其产物。
5. THE 任何新增配置键 SHALL 写入 `epsilon-boot/config.properties` 而非 `.env`（若设计阶段决定引入新配置项，例如 Provider 级 tokenizer 名称）。
