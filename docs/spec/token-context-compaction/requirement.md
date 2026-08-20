# 需求文档：Token 语义摘要上下文压缩

## 简介

当前后端已通过 `ContextCompactionPort` 与 `SlidingWindowCompactionAdapter` 实现滑动窗口式上下文压缩：保留所有 system 消息和最近 N 条非 system 消息。这种方式能限制发送给模型的历史长度，但只能裁剪消息，无法把较早上下文中的目标、约束、已做操作、错误、文件路径、命令结果、用户偏好等高价值信息浓缩后继续提供给模型。

本特性引入基于 token 触发的 LLM 语义摘要压缩能力：当待发送上下文达到配置的 token 触发阈值时，系统将较早上下文压缩成结构化摘要，再与最近消息一起发送给模型。压缩的目的不是无损保存所有细节，而是在有限上下文中保留继续工作所需的高价值信息。完整会话历史仍保存到原有会话存储中，压缩结果只用于模型调用输入。

本期范围包括：

- 为上下文压缩引入异步 LLM 摘要策略，并默认启用；
- 通过 token 触发阈值决定是否执行摘要压缩；
- 通过独立 Prompt 资产 `prompts/context-summary/v1.md` 管理摘要提示词，禁止在代码中硬编码摘要提示词正文；
- 摘要模型调用复用当前请求解析出的同一个模型访问适配器和模型名称，不新增独立摘要模型路由配置；
- 保留现有滑动窗口策略作为降级路径和独立可测策略；
- 将摘要模型调用产生的 token 用量计入聊天与 Agent 响应的 usage；
- 保持完整会话历史不被摘要覆盖或写回。

本期不包括：

- 不引入 `budget` / “预算”概念，不新增以预算为语义的配置、字段或值对象；
- 不实现摘要缓存、摘要持久化或把摘要写回会话历史；
- 不提供前端展示摘要内容的 UI；
- 不实现运行期热切换摘要 Prompt 版本；
- 不在本期承诺对摘要 Prompt 文案做质量迭代，后续可通过新增 `context-summary` Prompt 版本单独优化。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| Token 语义摘要压缩 | `Token_Semantic_Summary_Compaction` | 当待发送上下文达到 token 触发阈值时，调用 LLM 将较早上下文压缩为结构化摘要，并与最近消息组合后发送给后续模型调用的压缩策略。 |
| 上下文压缩端口 | `Context_Compaction_Port` | 定义模型调用前压缩消息列表能力的领域端口，当前位于 `domain/chat/ports.py`。本特性中该端口需要支持异步摘要调用。 |
| 上下文压缩结果 | `Context_Compaction_Result` | 压缩端口返回的结构化结果，包含压缩后的消息列表、摘要调用 token 用量，以及是否生成摘要的标记。 |
| LLM 摘要压缩适配器 | `LLM_Summary_Compaction_Adapter` | `Context_Compaction_Port` 的基础设施实现，负责 token 计数、摘要触发、调用模型生成摘要、组装压缩后的消息列表和失败降级。 |
| 滑动窗口压缩适配器 | `Sliding_Window_Compaction_Adapter` | 现有滑动窗口压缩策略，保留所有 system 消息和最近 N 条非 system 消息。本特性中保留为降级策略和独立测试对象。 |
| 压缩触发 token 数 | `Compaction_Trigger_Tokens` | 配置项 `CHAT_COMPACTION_TRIGGER_TOKENS`，表示待发送上下文达到或超过该 token 数时触发摘要压缩。它是触发阈值，不是预算。 |
| 最近消息保留数 | `Compaction_Keep_Recent_Messages` | 配置项 `CHAT_COMPACTION_KEEP_RECENT_MESSAGES`，表示摘要压缩后无条件保留的最近非 system 消息数量。 |
| Token 编码名称 | `Compaction_Encoding_Name` | 配置项 `CHAT_COMPACTION_ENCODING`，表示 token 计数使用的编码名称，默认 `cl100k_base`。 |
| 摘要 Prompt | `Context_Summary_Prompt` | 用于指导 LLM 生成结构化上下文摘要的 Prompt 资产，位于 `prompts/context-summary/v<N>.md`，通过 `PROMPT_CONTEXT_SUMMARY_VERSION` 选择版本。 |
| Prompt 注册表端口 | `Prompt_Registry_Port` | 现有 Prompt 资产访问端口，提供 `get(name)` 获取 `LoadedPrompt` 的能力。`LLM_Summary_Compaction_Adapter` 必须通过它加载摘要 Prompt。 |
| 模型访问端口 | `Model_Access_Port` | 统一模型接入端口，提供 `chat` 和 `stream` 能力。摘要压缩通过该端口执行摘要模型调用。 |
| 当前请求模型 | `Current_Request_Model` | 聊天或 Agent 本轮请求已经解析出的模型访问适配器和模型名称；摘要压缩复用该模型，不新增摘要专用路由。 |
| 完整会话历史 | `Full_Conversation_History` | `SessionContextStorePort` 保存的未压缩对话上下文，包含用户消息、助手消息、工具调用消息等完整历史。 |
| 压缩后模型输入 | `Compacted_Model_Input` | 本次模型调用实际发送的消息列表，可能包含结构化摘要消息和最近消息，不等同于完整会话历史。 |
| 摘要 token 用量 | `Summary_Token_Usage` | 摘要模型调用返回的 usage 信息，需要合并到聊天或 Agent 最终 usage 中，避免压缩成本不可见。 |

## 需求

### 需求 1：默认启用 LLM 语义摘要压缩

**用户故事：** 作为长对话用户，我希望旧上下文在过长时被总结为短摘要，以便模型在后续轮次仍能理解目标、约束和关键进展。

#### 验收标准

1. THE `LLM_Summary_Compaction_Adapter` SHALL 作为默认 `Context_Compaction_Port` 实现注册到容器中。
2. THE `Sliding_Window_Compaction_Adapter` SHALL 保留为可直接实例化和测试的独立策略。
3. WHEN `Compacted_Model_Input` 的估算 token 数小于 `Compaction_Trigger_Tokens`, THE `LLM_Summary_Compaction_Adapter` SHALL 返回原消息列表且不调用 `Model_Access_Port.chat` 生成摘要。
4. WHEN `Compacted_Model_Input` 的估算 token 数达到或超过 `Compaction_Trigger_Tokens`, THE `LLM_Summary_Compaction_Adapter` SHALL 调用 `Model_Access_Port.chat` 生成结构化摘要。
5. THE `LLM_Summary_Compaction_Adapter` SHALL 使用 `Current_Request_Model` 生成摘要，不新增摘要专用模型配置或模型注册路由。
6. THE `LLM_Summary_Compaction_Adapter` SHALL NOT 使用 `budget` 或“预算”作为配置、字段、方法参数或值对象语义。

### 需求 2：优先保留高价值信息并丢弃低价值信息

**用户故事：** 作为继续执行任务的模型调用方，我希望压缩摘要优先保留关键工作上下文，以便后续推理不会被重复日志和低价值过程细节占用上下文。

#### 验收标准

1. THE `Context_Summary_Prompt` SHALL 要求摘要优先保留目标、约束、已做操作、错误、文件路径、命令结果、用户偏好。
2. THE `Context_Summary_Prompt` SHALL 要求摘要弱化或丢弃重复日志、无关寒暄、中间过程细节、已经失效的假设。
3. THE `Context_Summary_Prompt` SHALL 要求摘要输出采用固定结构，至少包含 `当前目标`、`已完成`、`关键文件`、`关键命令与结果`、`约束与偏好`、`错误与阻塞`、`下一步`。
4. THE `Context_Summary_Prompt` SHALL NOT 直接硬编码用户要求避免的提示词句子；它应以结构化压缩指令表达摘要规则。
5. WHEN 需要调整摘要质量或栏目措辞, THE 变更 SHALL 通过新增 `prompts/context-summary/v<N+1>.md` 和更新 `PROMPT_CONTEXT_SUMMARY_VERSION` 完成，而不是修改压缩适配器代码中的字符串。

### 需求 3：摘要 Prompt 文件化和版本化

**用户故事：** 作为 Prompt 运维者，我希望摘要提示词作为独立 Prompt 资产管理，以便后续可以审计、回滚和迭代摘要策略。

#### 验收标准

1. THE `Context_Summary_Prompt` SHALL 存放在 `epsilon-boot/prompts/context-summary/v1.md`。
2. THE `Prompt_Version_Config` SHALL 新增 `context_summary_version: str = "v1"` 字段。
3. THE `config.properties` SHALL 新增 `PROMPT_CONTEXT_SUMMARY_VERSION=v1` 配置键及中文注释。
4. THE `LLM_Summary_Compaction_Adapter` SHALL 在构造期通过 `Prompt_Registry_Port.get("context-summary")` 加载 `Context_Summary_Prompt`。
5. THE `LLM_Summary_Compaction_Adapter` SHALL NOT 在生产代码中硬编码 `Context_Summary_Prompt` 的完整正文。
6. IF `PROMPT_CONTEXT_SUMMARY_VERSION` 指向不存在、为空或不可解码的 Prompt 文件, THEN THE Prompt 注册表启动校验 SHALL 按现有 Prompt 资产失败语义拒绝启动。

### 需求 4：压缩后消息结构

**用户故事：** 作为模型调用链路维护者，我希望压缩后的消息列表结构稳定，以便直接聊天、流式聊天和 Agent Loop 都能一致消费压缩结果。

#### 验收标准

1. FOR ALL `Full_Conversation_History`, THE `LLM_Summary_Compaction_Adapter` SHALL 在压缩后保留所有 system 消息。
2. WHEN 生成摘要, THE `LLM_Summary_Compaction_Adapter` SHALL 将摘要内容作为新的 `SystemMessage` 插入所有原 system 消息之后。
3. WHEN 生成摘要, THE `LLM_Summary_Compaction_Adapter` SHALL 保留最近 `Compaction_Keep_Recent_Messages` 条非 system 消息。
4. WHEN 较早非 system 消息为空, THE `LLM_Summary_Compaction_Adapter` SHALL 不调用摘要模型。
5. THE `Context_Compaction_Result.messages` SHALL 是可被现有模型序列化逻辑处理的 `BaseMessage` 列表。
6. THE `LLM_Summary_Compaction_Adapter` SHALL NOT 修改传入的原始消息对象列表。

### 需求 5：完整会话历史保持不变

**用户故事：** 作为会话恢复用户，我希望压缩不会破坏历史记录，以便后续仍可基于完整历史进行回放、审计或重新压缩。

#### 验收标准

1. THE `ChatServiceAdapter` SHALL 仅将 `Compacted_Model_Input` 发送给模型，不得将压缩摘要写回 `Full_Conversation_History`。
2. THE `ChatServiceAdapter` SHALL 保存包含最新用户消息和助手回复的完整未压缩上下文。
3. THE `ReActAgentAdapter` SHALL 在 Agent Loop 中仅使用压缩结果构造模型请求，不得用摘要替换 `ConversationContext` 中已有消息。
4. THE `TaskAgentAdapter` SHALL 继续保存任务会话的完整上下文，不得保存压缩摘要替代原始消息。
5. WHEN 用户清除会话, THE 既有 `clear_session` 行为 SHALL 删除完整会话历史和审批状态，不需要额外删除摘要缓存，因为本期不持久化摘要。

### 需求 6：摘要调用 token 用量可见

**用户故事：** 作为运维者，我希望摘要模型调用的 token 用量计入最终响应，以便成本统计不会漏掉压缩消耗。

#### 验收标准

1. THE `Context_Compaction_Result` SHALL 包含 `Summary_Token_Usage`。
2. WHEN `ChatServiceAdapter.chat` 执行摘要压缩, THE `ChatResponseVO.usage` SHALL 合并摘要调用 usage 和主模型调用 usage。
3. WHEN `ChatServiceAdapter.stream_chat` 执行摘要压缩, THE 最终 `StreamingChunk.usage` SHALL 合并摘要调用 usage 和主模型流式 usage。
4. WHEN `ChatServiceAdapter.stream_chat_events` 执行摘要压缩, THE `assistant_done` 事件 usage SHALL 合并摘要调用 usage 和主模型 usage。
5. WHEN `ReActAgentAdapter` 在多轮 Agent Loop 中多次执行摘要压缩, THE `AgentResult.usage` SHALL 累加所有摘要调用 usage 和所有主模型调用 usage。
6. FOR ALL usage 合并, THE 合并逻辑 SHALL 对缺失键按 0 处理，并保留已有 usage 键名。

### 需求 7：失败降级与可用性

**用户故事：** 作为聊天用户，我希望摘要压缩失败时主请求仍尽量可用，以便一次摘要模型错误不会阻断整个对话。

#### 验收标准

1. IF `Model_Access_Port.chat` 在摘要调用中抛出模型访问异常, THEN THE `LLM_Summary_Compaction_Adapter` SHALL 记录 warning 日志并降级到 `Sliding_Window_Compaction_Adapter`。
2. IF 摘要模型返回空白内容, THEN THE `LLM_Summary_Compaction_Adapter` SHALL 记录 warning 日志并降级到 `Sliding_Window_Compaction_Adapter`。
3. WHEN 降级发生, THE `Context_Compaction_Result.summary_created` SHALL 为 `False`。
4. WHEN 降级发生, THE 主聊天或 Agent 请求 SHALL 继续执行，不因摘要失败直接返回错误。
5. THE 降级日志 SHALL 包含是否发生摘要失败和消息数量信息，但不得包含完整消息正文。

### 需求 8：配置来源与校验

**用户故事：** 作为运维者，我希望通过 `config.properties` 调整压缩触发阈值和最近消息保留数，以便在不同模型上下文能力下控制压缩时机。

#### 验收标准

1. THE `ChatConfig` SHALL 新增 `compaction_trigger_tokens: int` 字段，对应 `CHAT_COMPACTION_TRIGGER_TOKENS`。
2. THE `ChatConfig` SHALL 新增 `compaction_keep_recent_messages: int` 字段，对应 `CHAT_COMPACTION_KEEP_RECENT_MESSAGES`。
3. THE `ChatConfig` SHALL 新增 `compaction_encoding: str` 字段，对应 `CHAT_COMPACTION_ENCODING`。
4. THE `config.properties` SHALL 写入上述 `CHAT_` 配置键及中文注释。
5. IF `CHAT_COMPACTION_TRIGGER_TOKENS` 小于等于 0, THEN THE `ChatConfig` SHALL 触发配置校验失败并拒绝启动。
6. IF `CHAT_COMPACTION_KEEP_RECENT_MESSAGES` 小于等于 0, THEN THE `ChatConfig` SHALL 触发配置校验失败并拒绝启动。
7. THE 配置字段命名 SHALL NOT 使用 `budget`。

### 需求 9：调用链异步化兼容

**用户故事：** 作为后端开发者，我希望摘要压缩可在现有聊天和 Agent 链路中一致运行，以便所有模型调用入口都获得相同压缩能力。

#### 验收标准

1. THE `Context_Compaction_Port.compact` SHALL 支持异步调用。
2. THE `ChatServiceAdapter.chat` SHALL await `Context_Compaction_Port.compact` 后再构造直接 LLM 调用请求。
3. THE `ChatServiceAdapter.stream_chat` SHALL await `Context_Compaction_Port.compact` 后再构造直接 LLM 流式调用请求。
4. THE `ChatServiceAdapter.stream_chat_events` SHALL await `Context_Compaction_Port.compact` 后再构造直接 LLM 事件流请求。
5. THE `ReActAgentAdapter.run` SHALL 在每轮模型调用前 await `Context_Compaction_Port.compact`。
6. THE `ReActAgentAdapter.run_streaming` SHALL 在每轮模型调用前 await `Context_Compaction_Port.compact`。
7. THE `ReActAgentAdapter.run_events` SHALL 在每轮模型调用前 await `Context_Compaction_Port.compact`。
8. THE `ReActAgentAdapter.resume` 恢复后继续执行模型调用时 SHALL 使用异步压缩链路。
9. FOR ALL 测试 mock, THE 压缩端口 mock SHALL 使用异步返回语义，避免生产代码和测试替身签名漂移。

### 需求 10：测试与评测迁移

**用户故事：** 作为维护者，我希望新增摘要压缩能力后既有滑动窗口测试仍然有效，同时新增摘要策略测试覆盖关键行为，以便后续重构不会破坏压缩链路。

#### 验收标准

1. THE `Sliding_Window_Compaction_Adapter` 既有边界测试 SHALL 保留，并迁移到异步调用形式或独立同步 helper 的明确测试形式。
2. THE `LLM_Summary_Compaction_Adapter` SHALL 有单元测试覆盖未触发、触发摘要、摘要失败降级、空摘要降级、usage 合并。
3. THE `Context_Summary_Prompt` SHALL 有测试验证通过 `Prompt_Registry_Port` 加载，而不是从适配器代码常量加载。
4. THE 评测指标 `Context_Compaction_Effectiveness` SHALL 继续覆盖滑动窗口策略自身，不因默认策略切换而失效。
5. THE 聊天与 Agent 相关测试 SHALL 覆盖摘要 usage 合并到最终 usage 的行为。
6. THE 依赖管理 SHALL 使用 `uv`，不得使用 `pip` 或其他包管理器。
