# 需求文档：流式工具调用 ID 兼容恢复

## 简介

当前后端在 ReAct Agent 流式工具调用链路中出现 `工具调用 id 不合法(source=stream_finished, raw_id_value=None)` 报错。现有 `id-validation-analysis` 已经把 Provider 返回空 `tool_call.id` 的问题显式暴露为 `InvalidToolCallIdError`，但线上运行目标需要在 OpenAI-compatible Provider 或中间代理偶发缺失 `id` 时继续完成工具调用，而不是中断整轮 Agent。

本特性在不降低领域模型约束的前提下，为流式工具调用提供 Provider 兼容恢复能力：官方协议仍以 Provider 返回的 `tool_call.id` 为优先；当流式分片可完整重组出工具名称和参数但缺失 id 时，由模型接入适配器生成稳定、本地唯一、可追踪的合成 id，并通过日志/metadata 暴露兼容事件。

范围内：

- 修复 `OpenAICompatibleAdapter.stream(...)` 的流式 `tool_calls` 收尾校验路径。
- 保持 `ToolCallRequest.id`、`PendingActionRequest.tool_call_id`、`ToolMessage.tool_call_id` 非空约束。
- 对缺失 id 的流式工具调用进行合成 id 恢复，并确保同一工具调用请求、执行结果、审批动作、会话历史之间 id 一致。
- 提供配置开关与结构化日志，支持严格模式回滚。
- 覆盖同步、流式、usage-only 末尾分片、多工具并行、Agent 累积器、历史恢复不回归等测试。

范围外：

- 不修改 Provider 或外部代理服务。
- 不修改工具 schema、工具执行权限、HITL 审批语义。
- 不改变非流式 `chat()` 对 Provider 空 id 的 fail-fast 语义，除非后续另起需求。
- 不迁移到 OpenAI Responses API 或 Anthropic 原生 Messages API。

参考资料：

- OpenAI 官方 Function Calling 文档说明：流式工具调用按 `index` 聚合，`id`、`function.name`、`type` 通常只出现在首个 delta，后续 delta 只追加参数片段。https://developers.openai.com/api/docs/guides/function-calling
- OpenAI API Reference 建议生产排障记录请求 id，并说明输出对象标识符属于 opaque string，不应依赖格式长度。https://developers.openai.com/api/reference/overview
- Anthropic fine-grained tool streaming 文档强调流式工具参数可能是不完整或无效 JSON，调用方需要显式处理边界。https://platform.claude.com/docs/en/agents-and-tools/tool-use/fine-grained-tool-streaming

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 流式工具调用 ID | Streaming_Tool_Call_Id | OpenAI-compatible 流式 `delta.tool_calls[i].id` 字段，用于把 assistant 工具请求与后续 tool 结果关联。 |
| 合成工具调用 ID | Synthetic_Tool_Call_Id | 当 Provider 缺失 `Streaming_Tool_Call_Id` 时，适配器为单个工具调用生成的本地稳定 id。 |
| 工具调用槽位 | Tool_Call_Slot | 按 `delta.tool_calls[i].index` 聚合出来的一次工具调用累积状态。 |
| 严格 ID 模式 | Strict_Id_Mode | 缺失 `Streaming_Tool_Call_Id` 时继续抛出 `InvalidToolCallIdError` 的运行模式。 |
| 兼容恢复模式 | Recovery_Mode | 缺失 `Streaming_Tool_Call_Id` 时生成 `Synthetic_Tool_Call_Id` 并继续执行的运行模式。 |
| 兼容恢复事件 | Recovery_Event | 适配器发现并修复缺失 id 时输出的结构化日志或 metadata。 |

## 需求

### 需求 1：流式缺失 ID 自动恢复

**用户故事：** 作为 Agent 使用者，我希望兼容 Provider 在流式工具调用中缺失 id 时系统仍能执行工具，以便避免整轮对话因第三方协议偏差中断。

#### 验收标准

1. WHEN `Tool_Call_Slot` 已累积出非空工具名称和非空参数但 `Streaming_Tool_Call_Id` 缺失, THE `Recovery_Mode` SHALL 为该 `Tool_Call_Slot` 生成一个非空 `Synthetic_Tool_Call_Id`。
2. WHEN `Tool_Call_Slot` 已存在非空 `Streaming_Tool_Call_Id`, THE `Recovery_Mode` SHALL 保留 Provider 原始 id，不生成 `Synthetic_Tool_Call_Id`。
3. FOR ALL 同一轮内的 `Tool_Call_Slot`, THE `Synthetic_Tool_Call_Id` SHALL 在本轮模型响应内唯一且可按 `index` 稳定复现。
4. WHEN `Tool_Call_Slot` 缺失工具名称或参数, THE `Recovery_Mode` SHALL NOT 生成可执行 `ToolCallRequest`。

### 需求 2：领域非空 ID 契约保持不变

**用户故事：** 作为维护者，我希望兼容逻辑只存在于基础设施适配层，以便领域层、Agent、审批和历史上下文继续依赖非空 id 契约。

#### 验收标准

1. THE `ToolCallRequest` SHALL 继续拒绝空 `id`。
2. THE `PendingActionRequest` SHALL 继续拒绝空 `tool_call_id`。
3. THE `ToolMessage` SHALL 在 Agent 工具结果回写时使用对应的非空 `Synthetic_Tool_Call_Id` 或原始 `Streaming_Tool_Call_Id`。
4. FOR ALL 已恢复的流式工具调用, THE `AssistantMessage.tool_calls[].id` AND 对应 `ToolMessage.tool_call_id` SHALL 相等。

### 需求 3：可配置严格模式与回滚

**用户故事：** 作为运维人员，我希望能通过配置在兼容恢复和严格失败之间切换，以便不同环境按风险偏好运行。

#### 验收标准

1. THE `OpenAICompatibleAdapter` SHALL 支持配置项控制缺失 `Streaming_Tool_Call_Id` 的处理策略。
2. WHEN 策略为 `recover`, THE `OpenAICompatibleAdapter` SHALL 使用 `Recovery_Mode`。
3. WHEN 策略为 `raise`, THE `OpenAICompatibleAdapter` SHALL 使用 `Strict_Id_Mode` 并抛出 `InvalidToolCallIdError`。
4. THE 默认策略 SHALL 为 `recover`，以修复当前运行故障。

### 需求 4：可观测性与排障

**用户故事：** 作为维护者，我希望每次合成 id 都有结构化日志，以便统计 Provider 兼容问题并定位来源。

#### 验收标准

1. WHEN `Synthetic_Tool_Call_Id` 被生成, THE `OpenAICompatibleAdapter` SHALL 输出 `Recovery_Event`。
2. THE `Recovery_Event` SHALL 包含 `source`、`provider`、`model`、`tool_name`、`tool_call_index`、`raw_id_value`、`synthetic_id`、`recovery_strategy` 字段。
3. THE `Recovery_Event` SHALL NOT 包含 API key、完整用户消息、完整 system prompt 或完整工具参数。
4. THE 流式 finished `StreamingChunk.metadata` SHOULD 标记本轮是否发生过 id 恢复，以便上层事件流保留轻量诊断信息。

### 需求 5：主流流式聚合语义兼容

**用户故事：** 作为模型接入维护者，我希望实现方式符合主流模型 API 的流式工具调用聚合习惯，以便减少未来接入其他 Provider 的适配成本。

#### 验收标准

1. THE `OpenAICompatibleAdapter` SHALL 继续按 `Tool_Call_Slot.index` 聚合同一工具调用的分片。
2. WHEN 后续分片的 `Streaming_Tool_Call_Id` 为 `None`, THE `OpenAICompatibleAdapter` SHALL NOT 覆盖已存在的原始 id 或 `Synthetic_Tool_Call_Id`。
3. WHEN usage-only 末尾分片出现, THE `OpenAICompatibleAdapter` SHALL 使用同一套恢复结果产出 finished chunk。
4. THE 实现 SHALL 不依赖 Provider id 的具体前缀、长度或字符集格式，只要求最终 id 是非空 ASCII 安全字符串。

### 需求 6：回归测试覆盖

**用户故事：** 作为开发者，我希望通过单元测试、属性测试和 Agent 集成测试覆盖修复路径，以便避免后续重构重新引入流式 id 缺失中断。

#### 验收标准

1. THE 测试 SHALL 覆盖流式 finished 分支缺失 id 的恢复路径。
2. THE 测试 SHALL 覆盖 usage-only 末尾分片缺失 id 的恢复路径。
3. THE 测试 SHALL 覆盖多工具并行时合成 id 唯一且按 index 稳定。
4. THE 测试 SHALL 覆盖配置为 `raise` 时继续抛出 `InvalidToolCallIdError`。
5. THE 测试 SHALL 覆盖 `ReActAgentAdapter` 能消费恢复后的流式工具调用并写回匹配的 `ToolMessage.tool_call_id`。
