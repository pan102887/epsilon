# 需求文档：Context Engineering

## 简介

当前模型调用链路已经具备多项上下文相关能力：`ConversationContext` 保存完整会话历史，`ContextCompactionPort` 在模型调用前压缩消息列表，`serialize_messages` 负责把领域消息转换为模型消息格式，`ChatServiceAdapter` 与 `ReActAgentAdapter` 分别作为直接聊天与 Agent Loop 的模型调用入口。

这些能力仍分散在不同入口中重复编排：调用方各自读取 `ConversationContext.get_messages()`、调用 `ContextCompactionPort.compact()`、合并摘要 usage、调用 `serialize_messages()` 并组装 `ChatRequest`。随着上下文工程需求继续增长，例如注入 Codex 风格的环境上下文，这种分散编排会增加 Chat 与 Agent 行为漂移的风险，也会让“哪些内容进入模型输入、哪些内容写入历史”这一边界不够集中。

本特性引入上下文工程装配层：通过 `ContextBuilderPort` 统一构建模型输入，把 instructions/tools/input/environment/history/compaction 视为模型输入的组合部分。其中，V1 重点落地环境上下文注入与现有上下文能力编排复用：

- 保留 `ConversationContext` 作为完整历史容器，不把运行期环境上下文写入历史；
- 复用 `ContextCompactionPort` 负责历史压缩，不替换已有摘要压缩逻辑；
- 复用 `serialize_messages` 负责模型消息格式转换，不在 builder 中重写序列化规则；
- 让 `ChatServiceAdapter` 与 `ReActAgentAdapter` 通过统一 builder 获取模型请求消息，减少重复逻辑；
- 在模型输入中注入 Codex 风格环境上下文，但不得泄露宿主绝对路径。

本期不包括：

- 不实现 `AGENTS.md`、项目指令文件或仓库级指令发现；
- 不修改 session 持久化格式；
- 不新增数据库、Redis 或其他持久化结构；
- 不替换、重写或扩展现有 `ContextCompactionPort` 的摘要压缩策略；
- 不改变工具注册、工具 schema 生成或模型 provider 路由逻辑；
- 不引入新的外部依赖。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 上下文工程 | `Context_Engineering` | 面向模型调用输入的装配过程，把 instructions、tools、input、environment、history、compaction 等内容组合成一次模型请求可消费的上下文。 |
| 上下文构建端口 | `Context_Builder_Port` | 领域层 Port，定义从完整会话历史、模型访问端口、模型名称和调用选项构建模型输入的能力。 |
| 上下文构建结果 | `Context_Builder_Result` | `Context_Builder_Port` 返回的领域值对象，包含可直接发送给模型的序列化消息、压缩 usage、压缩摘要标记和可观测元数据。 |
| 上下文构建适配器 | `Context_Builder_Adapter` | 基础设施层 Adapter，实现 `Context_Builder_Port`，负责调用 `ContextCompactionPort`、插入环境上下文并复用 `serialize_messages`。 |
| 对话上下文 | `ConversationContext` | 现有完整会话历史容器，负责保存和序列化消息，不包含裁剪、压缩或环境上下文注入逻辑。 |
| 上下文压缩端口 | `ContextCompactionPort` | 现有领域 Port，负责把完整消息列表压缩为适合发送给模型的消息列表，并返回摘要 usage 与摘要生成标记。 |
| 消息序列化函数 | `serialize_messages` | 现有基础设施函数，负责把 `BaseMessage` 列表转换为 OpenAI Chat Completions 兼容的字典列表。 |
| 聊天服务适配器 | `ChatServiceAdapter` | 现有聊天编排入口，负责加载 session、注入系统提示词、追加用户消息、选择直接模型调用或 Agent 委托并保存完整历史。 |
| ReAct Agent 适配器 | `ReActAgentAdapter` | 现有 Agent Loop 入口，负责按 ReAct 流程执行模型调用、工具调用、审批中断和恢复。 |
| 模型访问端口 | `ModelAccessPort` | 现有模型访问 Port，提供 `chat` 与 `stream` 能力，Chat 与 Agent 的主模型调用和摘要压缩模型调用均通过该端口执行。 |
| Agent 配置 | `AgentConfig` | 现有 Agent 运行配置值对象，包含 system prompt、工具 schema、模型、最大轮次、prompt_id 和允许工具集合等信息。 |
| 完整会话历史 | `Full_Conversation_History` | `ConversationContext` 中保存的完整消息列表，包含 system、user、assistant、tool 等所有已持久化消息。 |
| 模型输入 | `Model_Input` | 单次发送给模型的消息列表及工具 schema 等请求数据，允许包含环境上下文和压缩摘要，不等同于 `Full_Conversation_History`。 |
| 环境上下文 | `Environment_Context` | 当前运行环境中允许提供给模型的非会话信息，例如安全处理后的工作区提示、当前日期、运行模式或宿主环境摘要。V1 中必须避免泄露宿主绝对路径。 |
| 环境上下文提供器 | `Environment_Context_Provider` | 基础设施层 provider，生成 `Environment_Context` 文本或消息；它可以读取运行环境与 Workspace 抽象，但不得把真实宿主绝对路径直接暴露给模型。 |
| 压缩后历史输入 | `Compacted_History_Input` | `ContextCompactionPort.compact()` 返回的压缩后领域消息列表，是 `Model_Input` 的组成部分之一。 |
| 消息序列化器 | `Message_Serializer` | `serialize_messages` 在上下文构建层中的职责名称，表示模型消息格式转换能力。 |
| 聊天模型入口 | `Chat_Model_Entry` | `ChatServiceAdapter` 中未启用工具调用时直接调用模型的同步、流式和事件流入口。 |
| Agent 模型入口 | `Agent_Model_Entry` | `ReActAgentAdapter` 中 `run`、`run_streaming`、`run_events`、`resume` 等 Agent Loop 内部模型调用入口。 |
| 用量合并 | `Usage_Merge` | 将上下文构建阶段产生的压缩 usage 与主模型调用 usage 通过现有 `merge_usage` 语义累加。 |
| DDD 依赖方向 | `DDD_Dependency_Direction` | 项目 steering 规定的依赖方向：领域层只定义 Port/VO，基础设施层实现 Adapter/Provider，应用组合根负责装配。 |

## 需求

### 需求 1：定义统一的上下文构建能力

**用户故事：** 作为模型调用链路维护者，我希望通过统一的上下文构建能力装配模型输入，以便 Chat 与 Agent 不再重复实现压缩、环境注入和序列化逻辑。

#### 验收标准

1. THE `Context_Builder_Port` SHALL 定义在领域层，作为模型输入装配的业务能力边界。
2. THE `Context_Builder_Result` SHALL 定义在领域层，包含 `Model_Input` 的序列化消息、上下文构建阶段 usage、是否生成摘要的标记和环境上下文是否注入的标记。
3. THE `Context_Builder_Adapter` SHALL 实现 `Context_Builder_Port`，并位于基础设施层。
4. THE `Context_Builder_Adapter` SHALL 复用 `ContextCompactionPort` 生成 `Compacted_History_Input`，不得复制或重写压缩策略。
5. THE `Context_Builder_Adapter` SHALL 复用 `Message_Serializer` 生成模型消息字典，禁止在 builder 中维护第二套 `BaseMessage` 到模型消息的转换规则。
6. THE `Chat_Model_Entry` 和 `Agent_Model_Entry` SHALL 使用 `Context_Builder_Port` 获取模型请求消息，而不是各自直接组合 `context.get_messages()`、`ContextCompactionPort.compact()` 与 `serialize_messages()`。

### 需求 2：注入 Codex 风格环境上下文

**用户故事：** 作为使用代码工作区的用户，我希望模型能获得必要的运行环境摘要，以便它在执行任务时理解当前环境而不依赖会话历史里重复说明。

#### 验收标准

1. THE `Environment_Context` SHALL 作为 `Model_Input` 的一部分注入模型消息。
2. WHEN `Context_Builder_Adapter` 构建 `Model_Input`, THE `Environment_Context` SHALL 插入在已有 system 指令之后、历史 user/assistant/tool 消息之前。
3. THE `Environment_Context` SHALL 表达为可被 `Message_Serializer` 处理的领域消息，或在序列化前与领域消息列表组合后统一序列化。
4. THE `Environment_Context` SHALL 至少包含当前运行日期、工作区的显示级提示和模型应遵守的路径披露边界。
5. THE `Environment_Context` SHALL NOT 包含宿主机器真实绝对路径，例如 `/mnt/c/...`、`C:\...`、`/home/...` 这类物理路径。
6. IF `Environment_Context_Provider` 无法生成安全环境上下文, THEN THE `Context_Builder_Adapter` SHALL 拒绝构建 `Model_Input` 并抛出内部环境上下文错误，且错误消息与日志不得包含敏感路径。

### 需求 3：环境上下文不污染完整会话历史

**用户故事：** 作为会话恢复用户，我希望环境上下文只是本次模型调用输入的一部分，而不是永久写入历史，以便历史存储仍保持用户与助手真实交互记录。

#### 验收标准

1. THE `Environment_Context` SHALL NOT 写入 `Full_Conversation_History`。
2. THE `Environment_Context` SHALL NOT 通过 `ConversationContext.add_system_message`、`add_user_message`、`add_assistant_message` 或 `add_tool_result` 持久化。
3. WHEN `ChatServiceAdapter.chat` 保存 session, THE `Full_Conversation_History` SHALL 只包含既有 system prompt、用户消息、助手回复和 Agent/tool 交互消息，不包含 `Environment_Context`。
4. WHEN `ChatServiceAdapter.stream_chat` 或 `stream_chat_events` 保存 session, THE `Full_Conversation_History` SHALL 不包含 `Environment_Context`。
5. WHEN `ReActAgentAdapter` 在 Agent Loop 中追加 assistant tool_calls 或 tool 结果, THE `Full_Conversation_History` SHALL 不包含 `Environment_Context`。
6. THE session 持久化序列化格式 SHALL 保持与 `ConversationContext.to_dict()` 当前格式兼容。

### 需求 4：保持 Chat 与 Agent 运行行为不变

**用户故事：** 作为现有 API 调用方，我希望引入上下文构建层后直接聊天、流式聊天和 Agent 工具调用行为保持兼容，以便升级不会改变业务结果结构。

#### 验收标准

1. WHEN `Chat_Model_Entry` 未启用工具调用, THE 主模型调用 SHALL 继续使用请求解析出的 `ModelAccessPort` 和模型名称。
2. WHEN `Chat_Model_Entry` 启用工具调用且存在工具 schema, THE `ChatServiceAdapter` SHALL 继续委托 `Agent_Model_Entry` 执行 Agent Loop。
3. FOR ALL `Agent_Model_Entry`, THE 工具 schema、最大轮次、审批中断、审批恢复和工具授权行为 SHALL 保持现有语义。
4. THE `Context_Builder_Port` SHALL NOT 改变 `AgentConfig.tool_schemas` 的来源和内容。
5. THE `Context_Builder_Port` SHALL NOT 改变模型 provider 注册、默认模型解析或请求中 `model` 字段的路由语义。
6. FOR ALL direct chat and Agent paths, THE assistant 最终回复写回 `Full_Conversation_History` 的行为 SHALL 保持现有语义。

### 需求 5：usage 合并继续正确

**用户故事：** 作为运维者，我希望上下文构建层接管压缩调用后 token usage 统计仍然准确，以便成本统计不因编排迁移丢失摘要压缩消耗。

#### 验收标准

1. THE `Context_Builder_Result` SHALL 暴露来自 `ContextCompactionPort` 的 usage。
2. WHEN `ChatServiceAdapter.chat` 调用主模型完成, THE `Usage_Merge` SHALL 合并 `Context_Builder_Result` usage 与主模型 response usage。
3. WHEN `ChatServiceAdapter.stream_chat` 收到最终 streaming chunk, THE `Usage_Merge` SHALL 合并 `Context_Builder_Result` usage 与最终 chunk usage。
4. WHEN `ChatServiceAdapter.stream_chat_events` 发出 `assistant_done`, THE `Usage_Merge` SHALL 合并 `Context_Builder_Result` usage 与主模型 usage。
5. WHEN `ReActAgentAdapter` 多轮调用模型, THE `AgentResult.usage` SHALL 累加每轮 `Context_Builder_Result` usage 与每轮主模型 usage。
6. FOR ALL `Usage_Merge`, THE 合并逻辑 SHALL 继续使用现有 `merge_usage` 语义，对缺失键按 0 处理并保留既有 usage 键名。

### 需求 6：明确上下文组件顺序与职责边界

**用户故事：** 作为后端开发者，我希望模型输入中 instructions、environment、history、compaction 的顺序和职责清晰，以便新增上下文来源时不会破坏既有 prompt 或压缩行为。

#### 验收标准

1. THE `Context_Builder_Adapter` SHALL 把 `ContextCompactionPort` 的输出视为 history/compaction 组件，不得把 `Environment_Context` 交给压缩策略写回或总结。
2. WHEN `Compacted_History_Input` 包含 system 消息, THE `Environment_Context` SHALL 位于所有原 system 消息之后。
3. WHEN `Compacted_History_Input` 包含非 system 历史消息, THE `Environment_Context` SHALL 位于第一条非 system 历史消息之前。
4. WHEN `Compacted_History_Input` 因压缩生成摘要 system 消息, THE `Environment_Context` SHALL 位于摘要 system 消息之后。
5. THE `Context_Builder_Adapter` SHALL NOT 修改传入的 `Full_Conversation_History` 消息对象列表。
6. THE `Context_Builder_Adapter` SHALL NOT 调整 `ContextCompactionPort` 对 system 消息和最近消息的保留策略。

### 需求 7：遵守 DDD 依赖方向和应用装配职责

**用户故事：** 作为代码评审者，我希望上下文构建层符合项目 DDD 规范，以便新增能力不会让领域层依赖基础设施实现。

#### 验收标准

1. THE `Context_Builder_Port` 和 `Context_Builder_Result` SHALL 只依赖标准库、领域层稳定模型或 `common` 中与业务无关的共享抽象。
2. THE 领域层 SHALL NOT 导入 `infrastructure.chat.message_serialization`、Workspace 具体实现、配置类、模型 SDK 或文件系统 API。
3. THE `Environment_Context_Provider` SHALL 位于基础设施层，允许依赖 Workspace 抽象或配置，但不得反向要求领域层感知具体实现。
4. THE `Context_Builder_Adapter` SHALL 位于基础设施层，允许依赖 `ContextCompactionPort`、`Environment_Context_Provider` 和 `Message_Serializer`。
5. THE 应用容器 SHALL 负责把 `Context_Builder_Port` 绑定到 `Context_Builder_Adapter`。
6. THE `ChatServiceAdapter` 与 `ReActAgentAdapter` SHALL 通过构造参数接收 `Context_Builder_Port`，不得在运行期自行 new `Context_Builder_Adapter` 或 `Environment_Context_Provider`。

### 需求 8：配置与路径披露安全

**用户故事：** 作为运维者，我希望环境上下文注入可控且不会泄露宿主路径，以便模型获得有用环境信息同时满足安全边界。

#### 验收标准

1. THE V1 默认 SHALL 启用 `Environment_Context` 注入。
2. IF 需要新增环境上下文开关或格式配置, THEN THE 配置 SHALL 优先写入 `epsilon-boot/config.properties` 并遵守现有配置来源规范。
3. THE `Environment_Context_Provider` SHALL 使用 display-safe 工作区提示，不得使用 `Path.resolve()` 后的宿主绝对路径作为模型可见内容。
4. FOR ALL `Environment_Context` 文本, THE 生成逻辑 SHALL 有测试覆盖宿主绝对路径脱敏或排除行为。
5. THE `Environment_Context_Provider` 日志 SHALL NOT 输出完整环境上下文正文。
6. THE `Environment_Context` SHALL NOT 包含环境变量值、密钥、访问令牌或完整配置文件内容。

### 需求 9：测试覆盖与回归保护

**用户故事：** 作为维护者，我希望新增上下文构建层有针对性测试覆盖，以便后续迁移 Chat 与 Agent 入口时能发现行为漂移。

#### 验收标准

1. THE `Context_Builder_Adapter` SHALL 有单元测试覆盖压缩调用、环境上下文插入顺序、序列化复用和原消息列表不变。
2. THE `Environment_Context_Provider` SHALL 有单元测试覆盖不泄露宿主绝对路径。
3. THE `ChatServiceAdapter` 相关测试 SHALL 覆盖直接聊天路径通过 `Context_Builder_Port` 构建 `Model_Input`。
4. THE `ReActAgentAdapter` 相关测试 SHALL 覆盖同步、流式、事件流和恢复路径通过 `Context_Builder_Port` 构建每轮 `Model_Input`。
5. THE 历史保存相关测试 SHALL 断言 `Environment_Context` 不出现在 `ConversationContext.to_dict()` 结果中。
6. THE usage 相关测试 SHALL 覆盖 Chat 与 Agent 的 `Usage_Merge` 在引入 `Context_Builder_Result` 后继续正确。
7. THE 架构边界测试或静态检查 SHALL 覆盖领域层不导入基础设施层模块。
8. THE 依赖管理 SHALL 使用 `uv`，不得使用 `pip` 或其他包管理器。
