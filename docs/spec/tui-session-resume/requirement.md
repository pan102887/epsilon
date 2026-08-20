# 需求文档：TUI Session Resume

## 简介

当前 TUI `/new` 命令会在生成新 `session_id` 后调用 `CliRuntime.clear_session(old_session_id)`，最终由 `ChatServiceAdapter.clear_session()` 删除旧 `Session_Context` 并清理对应 `Approval_State`。该行为会把“开启新会话”误实现为“销毁旧会话”，与主流 AI 产品和 CLI 的会话语义不一致：New Chat / 新会话应只开启新线程，删除、清空、临时聊天应由单独且显式的能力承载。

本需求覆盖 `docs/spec/epsilon-cli-runtime/requirement.md` 第 3 节中“新会话命令应生成新的 `session_id` 并清理旧会话上下文”的旧语义。历史 spec 不改写；后续设计、任务与实现以本 spec 对 TUI 会话保留、恢复、发现和显式删除的定义为准。

本期范围包括：

- `/new` 只切换当前 TUI 到新的 `Session_Id`，不得删除旧 `Session_Context` 或 `Approval_State`。
- 新增 `/resume <session_id>`，从已存在且未过期的会话恢复当前 TUI 会话。
- 新增 `/sessions` 或等价命令，列出足够识别的可恢复会话，避免用户只能记住 ID。
- 新增显式删除命令，例如 `/delete <session_id>` 或 `/clear <session_id>`，替代 `/new` 的破坏性行为，并提供确认或明确不可逆语义。
- 新增会话索引 / 注册能力，区分“不存在会话”和“存在但为空的会话”，并为本地文件与 Redis 后端提供一致的恢复边界。

本期不引入云端同步、跨用户共享、模型长期记忆系统、复杂全文检索召回、Temporary Chat / 临时聊天模式，也不要求在 `/sessions` 中列出后台 Run；后台 Run 的发现与跨会话管理可作为后续需求推进。恢复会话时不得破坏既有 Run，且与会话相关的 paused / approval 状态应可继续或给出可读状态提示。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| TUI 会话 | `TUI_Session` | 一个 TUI 进程当前绑定的交互状态，包含当前 `Session_Id`、模型选择、approval 模式与退出状态。 |
| 会话 ID | `Session_Id` | 标识一条聊天会话线程的字符串，当前 TUI 默认形如 `tui-<uuid>`。 |
| 会话上下文 | `Session_Context` | 持久化的对话历史，类型为 `ConversationContext`，由 `Session_Context_Store` 保存、加载和删除。 |
| 会话上下文存储 | `Session_Context_Store` | `SessionContextStorePort` 及其 Adapter，用于保存、加载、删除 `Session_Context`。 |
| 会话元数据 | `Session_Metadata` | 用于发现和恢复会话的轻量索引信息，至少包含 `Session_Id`、更新时间、消息数和摘要或最后一条消息预览。 |
| 会话索引 | `Session_Index` | 记录可恢复会话集合及其 `Session_Metadata` 的领域能力，支持注册、查询、列出和删除索引项。 |
| 新会话命令 | `New_Command` | TUI slash 命令 `/new`，仅用于让当前 `TUI_Session` 切换到新的 `Session_Id`。 |
| 恢复命令 | `Resume_Command` | TUI slash 命令 `/resume <session_id>`，用于将当前 `TUI_Session` 切换到已存在的 `Session_Id`。 |
| 会话列表命令 | `Sessions_Command` | TUI slash 命令 `/sessions` 或等价命令，用于展示可恢复会话列表。 |
| 显式删除命令 | `Delete_Command` | TUI slash 命令 `/delete <session_id>`、`/clear <session_id>` 或等价命令，用于不可逆删除指定会话。 |
| 帮助命令 | `Help_Command` | TUI slash 命令 `/help`，用于展示当前可用命令及其用法。 |
| 审批状态 | `Approval_State` | HITL approval 中断状态，由 `ApprovalStateStorePort` 及其 Adapter 保存，可按 `Session_Id` 清理。 |
| 后台 Run | `Background_Run` | 通过 `/run ...` 创建的后台执行任务，具有独立 `run_id`、状态、事件和 checkpoint。 |
| 本地文件后端 | `Local_File_Backend` | 基于本地文件系统的 `Session_Context_Store` Adapter；当前无 TTL，仅显式删除会移除会话。 |
| Redis 后端 | `Redis_Backend` | 基于 Redis 的 `Session_Context_Store` Adapter；当前会话 key 存在 TTL 过期语义。 |
| 不存在会话 | `Missing_Session` | `Session_Index` 中不存在、`Session_Context_Store` 中不存在，或因 TTL 已过期不再可恢复的 `Session_Id`。 |
| 空会话 | `Empty_Session` | 已被 `Session_Index` 注册但尚无消息或消息数为 0 的 `Session_Context`，不同于 `Missing_Session`。 |
| 组合根 | `Composition_Root` | `application/container_config.py` 等启动装配代码，负责 Port 到 Adapter 的绑定。 |
| 实现 | `Implementation` | 为本需求新增或修改的生产代码、配置和公开模块。 |
| 测试套件 | `Test_Suite` | 为本需求新增或修改的单元测试、Adapter 测试和必要的 CLI runtime facade 测试。 |

## 需求

### 需求 1：`/new` 只开启新会话

**用户故事：** 作为 TUI 用户，我希望 `/new` 只切换到一个新的聊天线程，以便保留旧对话并可稍后恢复。

#### 验收标准

1. WHEN `New_Command` 被执行, THE `TUI_Session` SHALL 生成新的 `Session_Id` 并将其设为当前会话。
2. WHEN `New_Command` 被执行, THE `New_Command` SHALL NOT 调用 `Session_Context_Store` 的删除能力。
3. WHEN `New_Command` 被执行, THE `New_Command` SHALL NOT 清理旧 `Session_Id` 对应的 `Approval_State`。
4. WHEN `New_Command` 成功执行, THE `New_Command` SHALL 返回包含新 `Session_Id` 的可读提示。
5. THE `New_Command` SHALL 覆盖 `docs/spec/epsilon-cli-runtime/requirement.md` 中“新会话命令清理旧会话上下文”的旧语义。

### 需求 2：恢复已存在会话

**用户故事：** 作为 TUI 用户，我希望通过 `/resume <session_id>` 恢复其他对话，以便继续旧会话上下文而不是重新开始。

#### 验收标准

1. WHEN `Resume_Command` 携带已存在且未过期的 `Session_Id` 被执行, THE `TUI_Session` SHALL 将当前 `Session_Id` 切换为目标 `Session_Id`。
2. WHEN `Resume_Command` 成功执行, THE `Resume_Command` SHALL 返回包含目标 `Session_Id`、消息数和最近更新时间的可读提示。
3. WHEN `Resume_Command` 携带 `Missing_Session` 被执行, THE `Resume_Command` SHALL 返回可读错误并保持当前 `TUI_Session` 的 `Session_Id` 不变。
4. WHEN `Resume_Command` 携带 `Missing_Session` 被执行, THE `Resume_Command` SHALL NOT 通过 `Session_Context_Store.load()` 的空上下文返回值静默创建 `Empty_Session`。
5. IF `Session_Id` 对应 `Approval_State` 存在且可用, THEN THE `Resume_Command` SHALL 在恢复提示中展示会话存在待处理 approval 或 paused 状态。
6. IF `Session_Id` 对应 `Approval_State` 已过期或不可用, THEN THE `Resume_Command` SHALL 返回可读状态提示，且不得误报为可继续审批。

### 需求 3：发现可恢复会话

**用户故事：** 作为 TUI 用户，我希望看到可恢复会话列表，以便不依赖手工记忆 `session_id`。

#### 验收标准

1. WHEN `Sessions_Command` 被执行, THE `Sessions_Command` SHALL 返回按最近更新时间倒序排列的 `Session_Metadata` 列表。
2. FOR ALL `Session_Metadata` in `Session_Index`, THE `Sessions_Command` SHALL 至少展示 `Session_Id`、更新时间、消息数和摘要或最后一条消息预览。
3. WHEN `Session_Index` 为空, THE `Sessions_Command` SHALL 返回可读的空列表提示。
4. THE `Sessions_Command` SHALL NOT 为了展示列表加载完整 `Session_Context` 正文作为主要路径。
5. THE `Sessions_Command` SHALL NOT 要求本期支持全文检索、语义搜索或跨用户共享。

### 需求 4：显式删除替代隐式清理

**用户故事：** 作为 TUI 用户，我希望通过明确的删除命令清理不需要的会话，以便理解该操作不可逆且不会误删历史。

#### 验收标准

1. WHEN `Delete_Command` 携带有效 `Session_Id` 被执行, THE `Delete_Command` SHALL 要求确认或在命令语义中显式表达不可逆删除。
2. WHEN `Delete_Command` 被确认执行, THE `Delete_Command` SHALL 调用 `Session_Context_Store` 的删除能力删除目标 `Session_Context`。
3. WHEN `Delete_Command` 被确认执行, THE `Delete_Command` SHALL 删除目标 `Session_Id` 对应的 `Approval_State`。
4. WHEN `Delete_Command` 被确认执行, THE `Delete_Command` SHALL 从 `Session_Index` 移除目标 `Session_Metadata`。
5. WHEN `Delete_Command` 删除当前 `TUI_Session` 的 `Session_Id`, THE `TUI_Session` SHALL 切换到新的 `Session_Id` 或给出当前会话已被删除的可读提示。
6. WHEN `Delete_Command` 携带 `Missing_Session` 被执行, THE `Delete_Command` SHALL 返回可读错误或幂等成功提示，但不得创建新的 `Empty_Session`。

### 需求 5：会话索引与存在性判定

**用户故事：** 作为开发者，我希望会话发现和恢复通过明确的索引能力实现，以便区分不存在会话与空会话，并保持后端实现可测试。

#### 验收标准

1. THE `Session_Index` SHALL 先在 `domain/*/ports.py` 中定义 Port，再由 infrastructure 提供 `Local_File_Backend` 和 `Redis_Backend` Adapter。
2. THE `Composition_Root` SHALL 在 `application/container_config.py` 中装配 `Session_Index` 的 Port 到 Adapter 绑定。
3. WHEN `Session_Context` 首次保存或被 `TUI_Session` 创建为可恢复会话, THE `Session_Index` SHALL 注册或更新对应 `Session_Metadata`。
4. WHEN `Session_Context` 更新, THE `Session_Index` SHALL 更新对应 `Session_Metadata` 的更新时间、消息数和摘要或最后一条消息预览。
5. WHEN `Resume_Command` 校验目标 `Session_Id`, THE `Resume_Command` SHALL 通过 `Session_Index` 或等价存在性能力区分 `Missing_Session` 与 `Empty_Session`。
6. IF `Session_Index` 中存在 `Empty_Session`, THEN THE `Resume_Command` SHALL 允许恢复该 `Empty_Session` 并提示消息数为 0。

### 需求 6：后端保留边界与 TTL 策略

**用户故事：** 作为运维或开发者，我希望会话恢复的持久化边界清晰，以便本地文件和 Redis 后端行为可预期。

#### 验收标准

1. WHILE `Local_File_Backend` IN use, THE `Session_Context` SHALL 仅在 `Delete_Command` 或其他显式删除能力执行时被删除。
2. WHILE `Redis_Backend` IN use, THE `Session_Context` SHALL 明确受 Redis TTL 影响，过期后按 `Missing_Session` 处理。
3. THE `Redis_Backend` SHALL 提供面向会话恢复的 TTL 配置策略或文档化默认值，配置来源优先写入 `epsilon-boot/config.properties`。
4. WHEN `Redis_Backend` 的 `Session_Context` 因 TTL 过期但 `Session_Index` 尚未同步清理, THE `Resume_Command` SHALL 返回“会话不存在或已过期”的可读错误并避免静默创建空上下文。
5. THE `Session_Index` SHALL 让 `Local_File_Backend` 和 `Redis_Backend` 的列表、恢复、删除语义在应用层保持一致。

### 需求 7：Approval 与后台 Run 兼容

**用户故事：** 作为 TUI 用户，我希望切换或恢复会话不破坏审批和后台任务状态，以便长期任务和人工确认流程可以继续。

#### 验收标准

1. WHEN `New_Command` 被执行, THE `New_Command` SHALL NOT 删除任何旧 `Session_Id` 的 `Approval_State`。
2. WHEN `Delete_Command` 被确认执行, THE `Delete_Command` SHALL 删除目标 `Session_Id` 的 `Approval_State`。
3. WHEN `Resume_Command` 恢复包含 paused 或 approval 状态的 `Session_Id`, THE `Resume_Command` SHALL 给出可读状态提示或允许后续继续操作。
4. WHEN `Resume_Command` 被执行, THE `Resume_Command` SHALL NOT 取消、删除或重建任何 `Background_Run`。
5. THE `Sessions_Command` SHALL NOT 要求本期列出 `Background_Run`，但不得阻碍后续增加 Run 摘要展示。

### 需求 8：CLI 集成与帮助文本

**用户故事：** 作为 TUI 用户，我希望 slash 命令自解释且错误清晰，以便知道如何创建、恢复、列出和删除会话。

#### 验收标准

1. THE `Help_Command` SHALL 展示 `Sessions_Command`、`Resume_Command` 和 `Delete_Command` 的用法。
2. WHEN `Resume_Command` 缺少 `Session_Id`, THE `Resume_Command` SHALL 返回包含正确用法的可读错误。
3. WHEN `Delete_Command` 缺少 `Session_Id`, THE `Delete_Command` SHALL 返回包含正确用法的可读错误。
4. WHEN 未知 slash 命令被执行, THE `TUI_Session` SHALL 保持现有未知命令错误语义，不进入模型调用。
5. THE `TUI_Session` SHALL 保留现有 `/model`、`/config doctor`、`/run ...`、`/runs`、`/quit` 行为。

### 需求 9：架构与代码规范

**用户故事：** 作为维护者，我希望新增会话恢复能力符合现有 DDD 和项目规范，以便后续扩展和审计成本可控。

#### 验收标准

1. THE `Session_Index` SHALL 遵循 DDD 约束：domain 定义 Port，application 编排 CLI 行为，infrastructure 实现文件和 Redis Adapter。
2. THE `Composition_Root` SHALL 负责新增 Adapter 的实例化、生命周期和依赖注入绑定。
3. THE `TUI_Session` SHALL NOT 直接访问文件系统、Redis 客户端或基础设施实现。
4. THE `Session_Context_Store` SHALL 继续保留显式 delete 能力，但该能力只能由 `Delete_Command` 或其他显式删除入口触发。
5. THE `Implementation` SHALL 为 `Session_Index` 及相关公开模块、类、方法提供中文 docstring。
6. THE `Implementation` SHALL 使用 `uv` 运行依赖与测试命令。

### 需求 10：验证要求

**用户故事：** 作为维护者，我希望有针对性的自动化测试覆盖新语义，以便避免 `/new` 再次误删历史。

#### 验收标准

1. THE `Test_Suite` SHALL 覆盖 `New_Command` 成功后不调用 `CliRuntime.clear_session()` 或等价删除入口。
2. THE `Test_Suite` SHALL 覆盖 `Resume_Command` 对已存在 `Session_Id` 的成功恢复。
3. THE `Test_Suite` SHALL 覆盖 `Resume_Command` 对 `Missing_Session` 的失败提示和当前 `Session_Id` 不变。
4. THE `Test_Suite` SHALL 覆盖 `Sessions_Command` 返回 `Session_Metadata` 列表。
5. THE `Test_Suite` SHALL 覆盖 `Delete_Command` 调用显式删除入口并清理 `Session_Index`。
6. THE `Test_Suite` SHALL 覆盖 `Local_File_Backend` 的 `Session_Metadata` 注册、更新、列表和删除。
7. THE `Test_Suite` SHALL 覆盖 `Redis_Backend` 的 `Session_Metadata` 注册、更新、列表、删除和 TTL 过期边界，若 Redis 集成环境不可用则提供隔离单元测试。
8. THE `Test_Suite` SHALL 覆盖必要的 `CliRuntime` facade 行为，确保 TUI 命令不直接依赖 infrastructure Adapter。
