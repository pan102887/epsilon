# 需求文档：ToolCallRequest id 校验失败链路分析与加固

## 简介

### 背景

当前线上频繁出现 `ValueError: id 不能为空` 报错，影响 AI Agent 工作台的对话与任务执行体验。经静态代码扫描，该文案在仓库中**唯一**抛出位置为 `epsilon-boot/src/domain/model_access/value_objects.py` 内 `ToolCallRequest.__post_init__`（约第 92-93 行）：当 `ToolCallRequest.id` 为 `None` 或空字符串 `""` 时，直接抛出裸 `ValueError`，且不携带任何上下文（Provider 名、模型、tool_name、原始 SDK payload、所在调用链路），运维与开发拿到的日志/前端错误只看到这 7 个字，无法定位故障源头，因此问题既"频繁"又"难以分析"。

### 触发链路概览

经源码梳理，构造 `ToolCallRequest` 的全部生产路径共 4 条，其中 4 条均可能触发空 `id`：

1. **同步对话链路**：`infrastructure/model_access/openai_compatible_adapter.py` 的 `chat()` 在解析 OpenAI 兼容 Provider（DeepSeek / 智谱 / 月之暗面 / 本地 Ollama / 第三方网关等）的 `tool_calls[i].id` 时，若该字段为 `None` 或 `""`，会直接构造 `ToolCallRequest(id=tc.id, ...)`。
2. **流式 finished=True 重组链路**：`infrastructure/agent/round_stream_accumulator.py` 在 `chunk.finished=True` 分支仅以 `is None` 判定 id/name/arguments 是否齐全，**空字符串会绕过回退保护**直接进入 `ToolCallRequest(id="", ...)`。
3. **历史会话恢复链路**：`domain/chat/context.py` 的 `BaseMessage.from_dict` 反序列化历史会话快照时，若 `tool_calls[*].id` 为 `""`，逐项构造 `ToolCallRequest` 即抛错，导致历史会话无法加载。
4. **审批恢复链路**：`infrastructure/agent/react_agent_adapter.py` 的审批恢复流程在调用 `ToolCallRequest(id=action.tool_call_id, ...)` 之前，对 `PendingActionRequest.tool_call_id` 没有非空校验（参见 `domain/agent/value_objects.py` 中 `PendingActionRequest`），错误被延迟到适配器构造点才暴露，定位困难。

### 目标

- **可观测**：让任一链路触发空 `id` 时，错误信息携带充分上下文，至少包含来源链路、Provider/模型、tool_name 与原始 payload 摘要，方便运维与开发定位。
- **可分类**：把"id 不合法"统一为领域级异常（继承自 `ModelAccessError` 体系或新增明确的领域异常类），不再裸抛 `ValueError`，便于 application 层做差异化错误处理。
- **可前置**：把校验前移到第一手入口（值对象构造、反序列化入口、审批入参校验），避免错误延迟暴露。
- **不破坏既有契约**：保留 `ToolCallRequest` 的 frozen dataclass 形态与 `(id, name, arguments)` 三字段非空语义。

### 范围（In-Scope）

仅围绕 `ToolCallRequest.id` 校验抛错的四条链路进行**异常分类、上下文增强、流式契约对齐、审批前置校验、历史快照兼容策略**五类加固。

### 不在范围内（Out-of-Scope）

- 不修改前端代码（`epsilon-client/`）。
- 不重写 `ToolCallRequest` 数据结构，保持其 `@dataclass(frozen=True)` 形态与三字段必填语义。
- 不修改 `ToolCallRequest.name` / `ToolCallRequest.arguments` 的非空校验语义（两者的同质问题可在同一加固中顺带受益，但本需求的验收以 `id` 为主）。
- 不引入新的 LLM Provider，不修改任何 Provider 的鉴权与路由逻辑。
- 不扩展到其他值对象（如 `ChatRequest`、`StreamingChunk`）的校验问题。
- 不引入新的可观测性后端（仍复用现有日志体系）。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 工具调用请求值对象 | ToolCallRequest | `domain/model_access/value_objects.py` 中的 frozen dataclass，三字段 `id` / `name` / `arguments` 必填非空，是本次故障的唯一抛错点。 |
| 流式工具调用增量切片 | StreamingToolCallDelta | `domain/model_access/value_objects.py` 中表示一次工具调用在某个流式分片上的增量信息，承载 `index` / `id` / `name` / `arguments_delta` 四字段。 |
| 流式分片 | StreamingChunk | LLM 流式接口的单次输出分片，`finished=True` 表示末尾分片并承诺携带"按 index 升序的完整 tool_calls 列表"。 |
| 单轮流式累积器 | Round_Stream_Accumulator | `infrastructure/agent/round_stream_accumulator.py` 中的内部组件，将多个 StreamingChunk 聚合为等价于一次 chat 返回的 LLMResponse。 |
| OpenAI 兼容适配器 | OpenAI_Compatible_Adapter | `infrastructure/model_access/openai_compatible_adapter.py`，对接所有遵循 OpenAI Chat Completions 协议的 Provider（DeepSeek、智谱、月之暗面、本地 Ollama、第三方网关等）。 |
| 提供商 | Provider | LLM 模型供应方的逻辑标识（如 `deepseek` / `zhipu` / `moonshot` / `ollama`），由模型路由层管理。 |
| 待审批工具动作值对象 | PendingActionRequest | `domain/agent/value_objects.py` 中的值对象，承载需要人工审批的工具调用，关键字段 `tool_call_id`。 |
| 审批决策值对象 | ApprovalDecision | `domain/agent/value_objects.py` 中表示一次人工审批决策的值对象，含 `tool_call_id` 与决策类型。 |
| 审批恢复请求 | ApprovalResumeRequest | `domain/chat/value_objects.py` 中表示从中断点恢复审批的请求，承载 `ApprovalDecision` 列表。 |
| 会话上下文 | ConversationContext | `domain/chat/context.py` 中聚合历史消息的会话上下文对象，提供 `get_messages` 等接口。 |
| 助手消息 | AssistantMessage | `domain/chat/context.py` 中代表 LLM 回复的消息，可携带 `tool_calls: list[ToolCallRequest]`。 |
| 基础消息反序列化入口 | BaseMessage_From_Dict | `domain/chat/context.py` 的 `BaseMessage.from_dict` 类方法，是历史会话快照恢复的唯一入口。 |
| 模型接入领域异常 | ModelAccessError | `domain/model_access/exceptions.py` 中的领域基础异常，承载 `code` / `message` / `details`，是本需求加固后异常的归属基类。 |
| 流式 finished 分片 | Finished_Stream_Chunk | `StreamingChunk.finished=True` 的末尾分片，按契约承诺 `tool_calls[*]` 的 `id` / `name` / `arguments_delta` 三字段非 `None`，本次故障表明实际可能为 `""`。 |
| 流式增量分片 | Incremental_Stream_Chunk | `StreamingChunk.finished=False` 的过程分片，`tool_calls[*]` 仅携带局部增量字段。 |
| 业务日志器 | Domain_Logger | 仓库通用日志组件（`common/` 下提供），负责承载告警、警告与诊断日志。 |

## 需求

### 需求 1：同步对话链路在解析 Provider tool_calls 时，对空 id 抛出携带上下文的领域异常

**用户故事：** 作为后端开发者，我希望在 OpenAI 兼容适配器同步链路上，当某个 Provider 返回的 `tool_calls[i].id` 为空时，能直接通过领域异常拿到 Provider、模型、tool_name 等上下文，而不是只看到一行 `ValueError: id 不能为空`，以便快速定位是哪家 Provider 的兼容性问题。

#### 验收标准

1. WHEN OpenAI_Compatible_Adapter 在同步 chat 链路解析到 `tool_calls[i].id` 为 `None` 或空字符串，THE OpenAI_Compatible_Adapter SHALL 抛出继承自 ModelAccessError 的领域异常，而不抛出裸 `ValueError`。
2. WHEN 上述领域异常被抛出，THE OpenAI_Compatible_Adapter SHALL 在异常的 `details` 中至少包含：Provider 名称、目标模型名称、`tool_calls[i].function.name`（若存在）、`tool_calls[i].index`（若存在）、原始 `id` 字段值（`None` 或 `""`），用于排障。
3. THE OpenAI_Compatible_Adapter SHALL 在抛出该异常前以 WARN 级别通过 Domain_Logger 输出一条结构化日志，字段集合与异常 `details` 对齐。
4. FOR ALL OpenAI 兼容 Provider（包括但不限于 DeepSeek、智谱、月之暗面、本地 Ollama 与任意第三方 OpenAI 兼容网关），THE OpenAI_Compatible_Adapter SHALL 应用同一套校验与异常分类逻辑，不得为不同 Provider 走不同分支。

### 需求 2：流式 finished 分片重组对空字符串 id/name/arguments 与 None 同等回退

**用户故事：** 作为后端开发者，我希望流式工具调用的 finished 分片重组阶段把"空字符串"和"None"当作同样的违约信号，让 Round_Stream_Accumulator 的现有回退保护真正生效，避免空 id 绕过保护直达 ToolCallRequest。

#### 验收标准

1. WHEN Round_Stream_Accumulator 处理 Finished_Stream_Chunk 中的 StreamingToolCallDelta 时，IF 任一 `id` / `name` / `arguments_delta` 为 `None` 或为空字符串，THEN THE Round_Stream_Accumulator SHALL 视该 Finished_Stream_Chunk 不满足完整性契约并回退到增量累积结果，不构造任何 `ToolCallRequest(id="", ...)`。
2. WHEN Round_Stream_Accumulator 因上述违约触发回退，THE Round_Stream_Accumulator SHALL 通过 Domain_Logger 输出一条 WARN 级别日志，至少包含模型名、违约的 `index`、违约字段名、违约字段实际值（如 `""`）。
3. WHILE Round_Stream_Accumulator 处理 Incremental_Stream_Chunk 时，THE Round_Stream_Accumulator SHALL 维持现有"三字段缺一即跳过"的累积语义不变（保持已有行为，避免回归）。
4. FOR ALL 通过 OpenAI_Compatible_Adapter 流式产出的 Finished_Stream_Chunk，THE OpenAI_Compatible_Adapter SHALL 在生成 `StreamingToolCallDelta` 时，对从 SDK 拿到的占位空字符串显式归一化（要么填充为有意义值后产出，要么不再放入 finished 分片），保证下游契约可被严格依赖。

### 需求 3：历史会话恢复链路对缺失/空 tool_call id 提供明确的兼容策略

**用户故事：** 作为终端用户，我希望恢复历史会话时不会因为某条历史 `assistant` 消息的 `tool_calls[*].id` 为空而整段崩溃，让我能继续与 Agent 对话，并由系统通过日志告知运维存在脏数据。

#### 验收标准

1. WHEN BaseMessage_From_Dict 反序列化 `role=assistant` 的字典且 `tool_calls` 列表中存在 `id` 为空（缺失键、`None` 或空字符串）的项，THE BaseMessage_From_Dict SHALL 按设计阶段确定的兼容策略处理（要么过滤该项并通过 Domain_Logger 输出 WARN 日志，要么以专用领域异常包装抛出，二选一在 design 阶段决策），不得直接抛出裸 `ValueError("id 不能为空")`。
2. IF 设计阶段选择"过滤策略"，THEN THE BaseMessage_From_Dict SHALL 在过滤后的 AssistantMessage 中保留剩余合法 `tool_calls`，不影响其它字段（`content`、`metadata`）的反序列化。
3. IF 设计阶段选择"专用异常策略"，THEN THE BaseMessage_From_Dict SHALL 抛出携带 `session_id`（若上下文提供）、消息序号、违约 `tool_calls[*]` 内容摘要的领域异常，由 application 层决定如何降级。
4. WHEN BaseMessage_From_Dict 触发上述任一策略，THE BaseMessage_From_Dict SHALL 通过 Domain_Logger 输出一条 WARN 级别日志，字段至少包含消息 role、违约 `tool_calls` 项数量、首个违约项的 name 摘要。
5. THE BaseMessage_From_Dict SHALL 维持对合法历史会话快照的反序列化结果与现有行为一致（不引入回归）。

### 需求 4：审批恢复链路在领域值对象层前置校验 tool_call_id 非空

**用户故事：** 作为后端开发者，我希望 `PendingActionRequest` / `ApprovalDecision` 在构造时就拒绝空 `tool_call_id`，让审批恢复路径的错误在进入 React Agent 适配器之前就暴露，方便测试用例与上游路由层捕获。

#### 验收标准

1. WHEN PendingActionRequest 构造时 `tool_call_id` 为 `None` 或空字符串，THE PendingActionRequest SHALL 在 `__post_init__` 中以领域异常（不可为裸 `ValueError("id 不能为空")`）拒绝构造，错误消息中明确指向 `tool_call_id` 字段与所在值对象名称。
2. WHEN ApprovalDecision 构造时 `tool_call_id` 为 `None` 或空字符串，THE ApprovalDecision SHALL 同样在 `__post_init__` 中以领域异常拒绝构造。
3. WHEN ApprovalResumeRequest 经 application 层入口收到一组 `ApprovalDecision`，IF 任一项 `tool_call_id` 缺失或为空，THEN THE ApprovalResumeRequest SHALL 在抵达 React Agent 适配器之前被领域层异常阻断，不允许走到 `infrastructure/agent/react_agent_adapter.py` 中重新构造 `ToolCallRequest` 的位置。
4. THE PendingActionRequest 与 ApprovalDecision 的非空校验异常 SHALL 归属于 `domain/agent` 子域的现有异常体系（沿用或扩展，不在 `domain/model_access` 下新增重复类型）。

### 需求 5：所有空 id 抛错路径携带统一的诊断上下文字段集

**用户故事：** 作为运维，我希望任何 "id 不能为空" 类故障的日志/异常都遵循统一字段集（来源链路、Provider、模型、tool_name、原始 payload 摘要），让我在 ELK / 日志平台上能以同一查询命中所有相关故障样本。

#### 验收标准

1. FOR ALL 上述四条链路（同步对话、流式 finished 重组、历史会话恢复、审批恢复）抛出的 id 校验领域异常，THE 抛出方 SHALL 在异常 `details` 中提供以下字段子集：`source`（链路标识，如 `chat_sync` / `stream_finished` / `history_restore` / `approval_resume`）、`provider`（如适用）、`model`（如适用）、`tool_name`（如适用）、`tool_call_index`（如适用）、`raw_id_value`（原始 `id` 字段实际值）。
2. WHEN 任一链路抛出 id 校验领域异常，THE 抛出方 SHALL 通过 Domain_Logger 输出一条 WARN 级别日志，日志字段集合与异常 `details` 对齐，便于以同一查询语句聚合。
3. THE 异常类型 SHALL 满足 application 层可基于 `isinstance` 单独捕获并转换为面向用户的友好错误响应，不与既有 `ModelTimeoutError` / `ModelRateLimitError` 等异常共享类型。
4. THE 异常 `details` SHALL 不包含敏感信息（API 密钥、完整 system prompt、用户原文消息），仅携带定位故障所需的最小字段集，符合 `ModelAccessError.__init__` 的现有约定。

### 需求 6：加固方案符合仓库分层规范与文档规范

**用户故事：** 作为代码评审者，我希望本次加固严格遵守仓库已有的 DDD 分层与文档规范，不引入新的方向性破坏。

#### 验收标准

1. THE 加固方案 SHALL 把 id 校验领域异常类型定义在 `domain/` 层（`domain/model_access/exceptions.py` 或 `domain/agent/exceptions.py`，按归属拆分），不得在 `infrastructure/` 层新增领域异常类型。
2. THE 加固方案 SHALL 不让 `domain/` 层导入任何 `infrastructure/` 模块，所有 SDK / Provider 相关上下文字段由 `infrastructure/` 层在抛出异常时通过参数注入。
3. THE 加固方案 SHALL 为新增或修改的所有公开类、方法、模块补充中文 docstring，遵循 `docs/steering/code-documentation.md` 的要求。
4. IF 本次加固需要新增任何配置开关（如"历史会话恢复时 tool_call id 缺失的兼容策略选择"），THEN THE 配置开关 SHALL 写入 `epsilon-boot/config.properties`，遵循 `docs/steering/config-source.md`。
5. IF 本次加固涉及依赖管理变更，THEN THE 依赖管理操作 SHALL 仅通过 `uv` 命令完成，遵循 `docs/steering/uv-package-manager.md`。
6. THE `ToolCallRequest` 数据结构 SHALL 保持 `@dataclass(frozen=True)` 与 `(id, name, arguments)` 三字段必填语义不变，本次加固不得修改其字段集与不可变性。
