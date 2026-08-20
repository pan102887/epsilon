# AI Agent Memories 业界主流设计与实现调研

> 调研主题：业界主流方案中 AI Agent 的 memories（长期记忆、用户记忆、情景记忆、技能记忆等）设计方案与实现方式。
>
> 调研范围：ChatGPT Memory、Claude Memory / Projects RAG / API Memory、LangGraph / LangMem、MemGPT / Letta、Mem0、Zep、LlamaIndex、AutoGen、CrewAI，以及相关安全与评估实践。

## 1. 总体结论

业界 AI Agent memory 的主流设计正在从“把历史对话塞进上下文”演进为**分层、可治理、可检索、可审计、可删除的长期状态系统**。

早期常见做法包括：

1. **直接拼接历史对话**：实现简单，但成本高、容易污染上下文，且难以跨会话稳定复用。
2. **对话摘要**：能压缩上下文，但容易丢失细节，难以处理冲突、权限和删除传播。
3. **RAG / 向量检索**：能召回相关历史，但单纯向量库不能完整解决权限、时效、冲突、审计和用户控制问题。

当前成熟方案通常将 memory 拆分为多个层次：

| 记忆类型 | 典型内容 | 常见实现方式 |
|---|---|---|
| 用户画像 / 偏好记忆 | 用户偏好、身份、长期目标、工作方式 | Key-value、文档、facts、profile |
| 情景 / 事件记忆 | 历史对话、任务过程、过去发生的事实 | conversation history、episodes、timeline |
| 语义记忆 | 抽取出的稳定事实 | facts、triples、JSON documents、embedding |
| 程序性 / 技能记忆 | Agent 应如何行动、工作流、工具使用经验 | system rules、skills、procedural instructions |
| 项目 / 工作区知识 | 文件、项目文档、知识库 | RAG、project knowledge、workspace memory |
| 图谱 / 时间化记忆 | 实体、关系、事实生效/失效时间 | temporal knowledge graph |

核心建议：**Memory 不应被理解为“更长的聊天记录”，而应被建模为带作用域、来源、置信度、权限、生命周期和审计记录的长期状态。**

## 2. 主流产品与框架对比

### 2.1 ChatGPT Memory

OpenAI 的 ChatGPT Memory 主要体现的是**面向终端用户的个人记忆**设计。

官方资料中可以归纳出两类能力：

1. **Saved memories**
   - 用户或模型显式保存的长期信息。
   - 例如姓名、偏好、常用格式、长期目标。
2. **Reference chat history**
   - ChatGPT 可从过往聊天中推断上下文。
   - 不一定将每条内容都变成显式 memory。

| 维度 | ChatGPT Memory 做法 |
|---|---|
| 作用域 | 用户级 |
| 写入 | 可由模型根据对话保存，也可由用户要求记住 |
| 检索 | 回答时自动参考 saved memories / chat history |
| 管理 | 用户可查看、删除、关闭 memory |
| 隐私 | 提供数据控制和 Temporary Chat 等机制 |
| 典型定位 | 个性化助手，而非开发者直接操控的 memory backend |

可借鉴点：

- 区分“显式保存的 memory”和“从历史聊天中参考的信息”。
- 用户可见、可删、可关闭。
- 适合 C 端个人助手，不一定适合作为企业 Agent 的底层状态系统。

### 2.2 Claude Memory / Claude Projects RAG / Claude API Memory

Claude 体系中可以看到三种不同层次的 memory 设计。

#### 2.2.1 Claude 用户级 Memory / Chat Search

Anthropic 官方帮助文档说明，Claude 可以搜索 previous conversations，也可以基于聊天历史合成用户/工作相关记忆；用户可以禁用或重新启用 chat search 和 memory；删除的会话会从 memory synthesis 中移除，合成会在会话创建、修改或删除后刷新。

| 维度 | Claude 用户 Memory |
|---|---|
| 作用域 | 用户级 |
| 数据来源 | 过往聊天、用户/工作上下文 |
| 写入 | 产品侧自动合成 + 用户控制 |
| 删除 | 删除会话会影响后续 memory synthesis |
| 用户控制 | 可启停、可导入导出、可检查 memory edits |
| 目标 | 跨会话延续个人/职业上下文 |

#### 2.2.2 Claude Projects RAG

Claude Projects 的项目知识更接近**工作区级 RAG memory**。

Anthropic 文档说明：

- 项目知识接近上下文限制时自动启用 RAG。
- Claude 使用 project knowledge search tool 检索相关内容。
- 不是一次加载全部项目内容，而是只带入相关片段。
- 产品会显示 RAG 状态。

| 维度 | Claude Projects RAG |
|---|---|
| 作用域 | Project / workspace |
| 数据来源 | 上传文件、项目知识 |
| 检索 | RAG search tool |
| 写入 | 用户上传/维护项目知识 |
| 目标 | 扩展项目上下文容量 |

#### 2.2.3 Claude API Memory Tool / Managed Agents Memory Stores

Claude API / Managed Agents 体系提供更底层的 memory 能力。

1. **Memory Tool**
   - 客户端工具。
   - Claude 通过 `view`、`create`、`str_replace`、`insert`、`delete`、`rename` 等命令读写 `/memories` 目录。
   - 存储 backend 由开发者实现。
   - 适合自建 Agent 时提供文件系统式长期记忆。

2. **Managed Agents Memory Stores**
   - Workspace-scoped persistent memory。
   - 以 memory store / memory / memory version 建模。
   - Session 创建时以 resource 形式挂载。
   - 每次 mutation 会产生 immutable memory version，支持审计、回滚、redact。
   - Memory 是小文本文件，按 path 组织。
   - 支持 `read_write` / `read_only` 访问控制。

| 能力 | 说明 |
|---|---|
| 持久化 | 跨 session 保留 |
| 文件系统接口 | Agent 用普通文件工具读写 |
| 审计 | memory versions 记录每次变更 |
| 删除 / redaction | 可删除 memory，也可 redact 历史版本内容 |
| 权限 | session 挂载时可 read-only 或 read-write |
| 分区 | 可按用户、团队、项目创建不同 memory store |

### 2.3 LangChain / LangGraph / LangMem

LangChain / LangGraph 的 memory 设计偏向**开发者框架抽象**。

官方文档将 memory 分为：

1. **Short-term memory**：单个 thread / conversation 内部状态。
2. **Long-term memory**：跨 conversation / session 持久保存，可在不同 thread 中召回。

同时采用认知科学分类：

| 类型 | 含义 |
|---|---|
| Semantic memory | 事实、偏好、用户信息 |
| Episodic memory | 过去经历、示例、事件 |
| Procedural memory | 行为规则、操作方式、工作流 |

LangGraph store 的核心抽象：

| 概念 | 说明 |
|---|---|
| namespace | 用于分区，如用户、组织、agent |
| key | 单条 memory 的唯一键 |
| value | JSON document |
| store | 持久化 backend，可替换 |

LangMem 是 LangChain 生态中更专门的 agent memory 层：

- 提供 `create_manage_memory_tool`、`create_search_memory_tool`。
- 支持从 conversation 中抽取长期记忆。
- 支持 background memory manager，后台抽取、整合、更新 memory。
- 核心 API 存储无关，高层集成 LangGraph storage。
- 示例常用 `InMemoryStore`，生产需要替换为数据库后端。

可借鉴点：

- 把 memory 操作工具化，让 agent 可以显式 search / manage memory。
- 使用 namespace 隔离用户、项目、agent。
- 后台异步抽取和 consolidation，不要在主对话路径里做所有 memory 写入。

### 2.4 Mem0

Mem0 更像一个**面向 Agent 的 Memory API / Memory SaaS**。

官方文档中的关键点：

| 能力 | 说明 |
|---|---|
| `add` | 接收有序 user / assistant 对话或 facts，存储重要信息 |
| `search` | 根据 query 检索相关 memories |
| `update` / `delete` | 支持更新与删除 |
| scope identifiers | `user_id`、`agent_id`、`run_id`、`app_id` 等 |
| metadata | 用于过滤和治理 |
| memory types | user memory、agent memory、session memory |
| hosted stack | 托管 vector store、rerankers 等基础设施 |
| integrations | LangChain、CrewAI、MCP、Vercel AI SDK 等 |

可借鉴点：

- API 操作清晰：`add` / `search` / `update` / `delete`。
- 作用域明确：user / agent / session。
- metadata 作为过滤条件。
- 对应用方隐藏 embedding / vector / rerank 复杂性。

注意事项：本次 workflow 中，一个关于 Mem0 内部 pipeline 的更强断言被验证器反驳。不能武断断言其 `add` 一定执行“冲突检测 + 去重 + 写向量库”的完整内部流程。官方能确认的是 API 行为和用途，而不是所有内部实现细节。

### 2.5 Zep

Zep 的设计相对不同，它把 Agent memory 做成**时间化知识图谱 Context Graph**。

官方文档和论文中可验证的信息包括：

| 组件 | 说明 |
|---|---|
| Context Graph | agent memory 的核心单元 |
| nodes | 实体 |
| edges | facts / relationships |
| facts | 带时间戳的关系 |
| `valid_at` / `invalid_at` | 表示事实生效和失效时间 |
| episodic nodes / episodes | 记录原始事件或情景 |
| Context Lake | 用于治理和服务上下文 |

Zep 的关键优势：

1. **更适合处理变化事实**
   - 例如“用户以前住北京，现在住上海”。
   - 不是简单覆盖，而是让旧事实 invalidated。
2. **更适合实体关系推理**
   - 用户、组织、项目、任务、偏好之间的关系可显式表达。
3. **更适合审计和时间追踪**
   - 事实什么时候生效、什么时候失效可查询。

可借鉴点：

- 对企业复杂业务 Agent，图谱 memory 通常比单纯向量库更可控。
- 对“事实会变化”的场景，应保留时间维度和失效语义。

### 2.6 MemGPT / Letta

Letta / MemGPT 代表的是**从上下文管理出发的 memory-first agent 架构**。

可确认的信息：

- Letta 将自身定位为 memory-first coding agent / stateful agents。
- 文档中包含 memory blocks、shared memory、archival memory、context hierarchy 等概念。
- 也有工具体系，如 client tools、built-in tools、server tools、MCP tools、human-in-the-loop。
- Letta / MemGPT 的核心思想通常是把 LLM 有限上下文看成“工作内存”，并通过外部 archival memory / tools 来换入换出长期内容。

可借鉴点：

- 不要把 context window 当作 memory 本身。
- 需要有“上下文层级”：当前工作上下文、短期对话、长期归档、共享知识。
- Agent 需要有工具去主动读取和写入记忆，而不是被动依赖 prompt 拼接。

本次抓取到的 Letta 页面信息较有限，因此对 Letta 的具体当前版本 API 不做过强断言。

### 2.7 LlamaIndex Agent Memory

LlamaIndex 当前推荐使用 `llama_index.core.memory.Memory`，并说明旧的 `ChatMemoryBuffer` 已 deprecated。

核心设计：

| 能力 | 说明 |
|---|---|
| `Memory.from_defaults(...)` | 创建 memory，可设置 `session_id`、`token_limit` |
| `memory.put_messages(...)` | 写入消息 |
| `memory.get()` | 获取用于 agent 的 chat history / memory |
| short-term memory | FIFO queue，由 token limit 控制 |
| long-term memory | Memory Blocks |
| `StaticMemoryBlock` | 固定上下文，如用户/项目静态信息 |
| `FactExtractionMemoryBlock` | 使用 LLM 从聊天中抽取 facts |
| `VectorMemoryBlock` | 将批量聊天消息存入 vector database |
| priority | memory blocks 有优先级，低优先级先被截断 |
| insert_method | 可插入 system message 或 latest user message |
| persistence | 默认 in-memory SQLite，可通过 `async_database_uri` 接远程数据库 |

LlamaIndex 的设计值得借鉴：

- memory block 化。
- 不同 memory block 可有不同处理策略。
- 用 priority 管控上下文预算。
- 区分 chat messages 与 workflow Context 的 broader runtime state。

### 2.8 AutoGen AgentChat Memory

AutoGen AgentChat 的 memory 是一种协议化设计。

官方文档中可确认：

| 能力 | 说明 |
|---|---|
| Memory protocol | 包含 `add`、`query`、`update_context`、`clear`、`close` |
| `update_context` | 将检索到的 memory 注入 agent 的 model context |
| `ListMemory` | 简单列表型 memory，按时间顺序保存 |
| `ChromaDBVectorMemory` | 基于 ChromaDB 的向量检索 memory |
| `RedisMemory` | Redis-backed vector memory |
| `MemoryContent` | memory 条目内容，可有 mime type |
| RAG pattern | 文档 indexing 与 agent runtime retrieval 分离 |
| lifecycle | 需要 clear / close 等资源清理 |

AutoGen 的特点：

- memory 更偏 framework protocol。
- 强调把 memory 注入 model context。
- 提供 list、vector、Redis 等不同实现。
- 适合多 Agent / AgentChat 系统里统一 memory interface。

### 2.9 CrewAI Memory

CrewAI 新版 memory 采用统一 `Memory` API，官方文档说明它替代了过去分散的 short-term、long-term、entity、external memory 类型。

核心设计：

| 能力 | 说明 |
|---|---|
| unified `Memory` API | 统一 memory 接口 |
| crew memory | `memory=True` 时 crew agents 默认共享 memory |
| agent-specific memory | 可用 scope 限定到 `/agent/researcher` 等路径 |
| flow memory | `self.remember()`、`self.recall()`、`self.extract_memories()` |
| scopes | 层级路径，如 `/project/alpha` |
| `MemorySlice` | 可组合多个分支用于 recall |
| read-only slices | 防止写入 |
| recall ranking | 结合 semantic similarity、recency、importance |
| storage | 默认 LanceDB，路径为 `./.crewai/memory` 或 `$CREWAI_STORAGE_DIR/memory` |
| custom storage | 实现 `StorageBackend` |
| delete/reset | `reset()`、`forget()`、CLI reset memories |

CrewAI 的设计适合借鉴：

- scope hierarchy 对多 agent / crew 很有用。
- recall 排序不应只看 embedding similarity，还应考虑 recency 和 importance。
- read-only memory slice 可用于共享知识库，避免 Agent 污染。

## 3. 横向架构对比

| 方案 | 主要抽象 | 存储模型 | 检索方式 | 写入方式 | 删除/治理 |
|---|---|---|---|---|---|
| ChatGPT Memory | saved memories + chat history reference | 产品内部用户记忆 | 自动参考 | 用户/模型触发 | 用户可删、可关 |
| Claude Memory | 用户记忆 + chat search | 产品内部合成记忆 | chat search / synthesis | 自动合成 + 导入 | 可启停、删除传播、导入导出 |
| Claude Projects | project knowledge RAG | 项目文件/知识 | RAG search tool | 用户上传 | 项目级管理 |
| Claude Managed Agents | memory store / memory / version | workspace text docs | filesystem / tools | agent 文件写入或 API | versions、redact、access |
| LangGraph | store / namespace / key / JSON doc | JSON docs | store search | tools / app 写入 | 取决于 backend |
| LangMem | memory tools / background manager | storage-agnostic | search tool | manage tool / extraction | backend 决定 |
| Mem0 | memory operations API | hosted memory service | search / filters | add API | update/delete |
| Zep | temporal Context Graph | graph + facts + episodes | graph retrieval | ingestion / graph updates | invalidation / governance |
| LlamaIndex | Memory + Memory Blocks | SQLite / DB / vector store | block retrieval | put / flush / extraction | DB/backend 决定 |
| AutoGen | Memory protocol | list/vector/Redis | query + context injection | add | clear/close |
| CrewAI | unified Memory + scopes | LanceDB / custom | semantic + recency + importance | remember/extract | reset/forget |

## 4. Memory 数据模型设计

一个生产级 Agent memory 通常至少需要以下字段。

### 4.1 基础字段

```json
{
  "id": "mem_...",
  "scope": {
    "tenant_id": "...",
    "user_id": "...",
    "project_id": "...",
    "agent_id": "...",
    "session_id": "..."
  },
  "type": "semantic | episodic | procedural | preference | project_knowledge",
  "content": "...",
  "source": {
    "conversation_id": "...",
    "message_ids": ["..."],
    "tool_call_id": "...",
    "created_by": "user | agent | system | api"
  },
  "metadata": {
    "tags": ["preference", "formatting"],
    "confidence": 0.86,
    "importance": 0.7,
    "sensitivity": "normal | pii | secret | regulated",
    "created_at": "...",
    "updated_at": "...",
    "expires_at": null
  }
}
```

### 4.2 推荐增加的治理字段

```json
{
  "visibility": "private | team | project | org",
  "access": "read_only | read_write",
  "status": "active | superseded | invalidated | deleted",
  "valid_from": "...",
  "valid_until": null,
  "embedding_id": "...",
  "version": 3,
  "content_sha256": "...",
  "audit": {
    "created_by_actor": "...",
    "last_modified_by_actor": "...",
    "delete_reason": null
  }
}
```

### 4.3 不建议只做一张 memories 表

更合理的是分层：

```text
memory_store
  ├── semantic_facts
  ├── episodic_events
  ├── user_preferences
  ├── procedural_rules
  ├── project_knowledge_refs
  ├── embeddings
  ├── versions / audit_log
  └── deletion_redaction_log
```

## 5. 写入策略

### 5.1 写入不应完全自动

如果所有对话都自动写 memory，会产生：

- 噪声记忆。
- 错误事实长期污染。
- 用户隐私风险。
- prompt injection 写入恶意偏好。
- 冲突难处理。

推荐写入分级：

| 写入类型 | 触发方式 | 是否需确认 |
|---|---|---|
| 用户显式要求“记住” | 用户指令 | 通常不需二次确认 |
| 用户偏好 | LLM 抽取 + 规则过滤 | 可后台写入，但用户可见 |
| 任务结果 | Agent 完成任务后总结 | 可写入项目/任务 memory |
| 敏感信息 | PII / secret / regulated detector | 默认不写或需确认 |
| 行为规则 | 用户长期偏好或管理员配置 | 需权限和审计 |
| 工具使用经验 | 系统生成 | 应隔离到 procedural memory |

### 5.2 推荐写入 pipeline

```text
conversation / event
  → candidate extraction
  → classification: semantic / episodic / preference / procedural
  → sensitivity detection
  → dedupe / conflict detection
  → scope assignment
  → confidence scoring
  → optional user confirmation
  → persist memory
  → generate audit version
```

### 5.3 冲突处理

例如：

- “我喜欢 Python”
- 后来：“以后这个项目都用 TypeScript”

不能简单覆盖，应判断：

| 情况 | 处理 |
|---|---|
| 新事实是全局偏好变化 | 旧 memory invalidated |
| 新事实只是项目局部偏好 | 新增 project-scoped memory |
| 两者并存但范围不同 | 保留两条，检索时按 scope 过滤 |
| 不确定 | 降低置信度或询问用户 |

Zep 的 `valid_at` / `invalid_at` 时间化事实模型很值得借鉴。

## 6. 检索策略

### 6.1 检索不应只靠向量相似度

推荐组合：

```text
candidate memories
  = scope filter
  + permission filter
  + sensitivity filter
  + semantic/vector search
  + keyword / structured filters
  + graph relation expansion
  + recency / importance rerank
  + conflict resolution
  + token budget packing
```

### 6.2 检索前过滤优先于检索后过滤

推荐顺序：

1. tenant / user / project 权限过滤。
2. memory 类型过滤。
3. sensitivity 过滤。
4. 再做语义检索。

否则可能出现跨租户 embedding 召回或日志泄漏风险。

### 6.3 上下文注入方式

| 方式 | 适合场景 |
|---|---|
| System message 注入 | 稳定用户偏好、行为约束 |
| User message 附加 context | 当前任务相关事实 |
| Tool result 注入 | 显式 `search_memory` 工具 |
| File mount | Claude Managed Agents / 文件系统式 memory |
| RAG citation | 项目知识、可追溯文档 |
| Graph summary | 复杂实体关系 |

建议对注入内容加 provenance：

```markdown
Relevant memories:
- [preference, confidence=0.92, source=2026-05-12] 用户偏好中文回答。
- [project-rule, scope=project:abc] 本项目后端使用 FastAPI + DDD。
```

## 7. 更新与删除策略

### 7.1 更新

主流做法有三种：

| 模式 | 说明 | 代表 |
|---|---|---|
| 覆盖更新 | 直接修改 memory 内容 | 简单 CRUD memory |
| 追加版本 | 每次修改生成版本 | Claude Managed Agents memory versions |
| 时间失效 | 新事实让旧事实 invalidated | Zep temporal graph |

生产建议：

- 内容更新应保留版本。
- 事实变化应支持 invalidation，不只是覆盖。
- 每次更新记录 actor、source、reason。
- 并发更新用 optimistic concurrency，例如 content hash precondition。

### 7.2 删除

需要区分：

| 删除类型 | 语义 |
|---|---|
| soft delete | 用户界面不可见，但审计仍保留 |
| hard delete | 彻底删除内容 |
| redact | 清除内容，保留审计元数据 |
| source deletion propagation | 删除原会话后，相关 memory 也应移除或重新合成 |

Claude 用户 Memory 强调删除会话会影响 memory synthesis；Claude Managed Agents Memory Store 支持 memory versions redaction，这两个都值得借鉴。

## 8. 隐私与安全

Agent memory 的安全风险比普通 RAG 更高，因为它会长期影响未来行为。

### 8.1 主要风险

| 风险 | 示例 |
|---|---|
| Memory poisoning | 恶意网页诱导 Agent 记住“以后忽略安全策略” |
| Prompt injection persistence | 一次攻击写入长期 memory，之后持续生效 |
| PII 过度保存 | 保存身份证、住址、健康信息 |
| Secret leakage | 把 API key / token 写入 memory |
| Cross-tenant leakage | A 用户 memory 被 B 用户召回 |
| Stale fact harm | 过期事实继续影响决策 |
| Unreviewed automation | Agent 自动写入错误偏好 |
| Right-to-delete 不完整 | 删除聊天但 memory / embedding / audit 仍保留 |

### 8.2 安全设计建议

| 控制 | 建议 |
|---|---|
| 作用域隔离 | tenant / user / project / agent / session 必须显式建模 |
| 写入审计 | 每条 memory 记录来源、actor、时间、版本 |
| 敏感信息检测 | secrets、PII、regulated data 默认不写或需确认 |
| 用户可见 | 用户能查看、编辑、删除自己的 memory |
| Prompt injection 防护 | 外部内容默认不能直接写 procedural memory |
| 检索过滤 | 先权限过滤，再语义检索 |
| 删除传播 | 删除 source 后重新合成或 invalidation |
| 版本 redaction | 历史版本中如有 secret，应支持 redact |
| read-only memory | 共享知识库最好 read-only，避免 Agent 污染 |
| admin policy | 企业租户可禁用或限制 memory |

## 9. 评估方法

目前业界还没有单一统一 benchmark 能覆盖所有 Agent memory 场景。建议自建 eval。

### 9.1 关键指标

| 指标 | 说明 |
|---|---|
| Recall accuracy | 该想起的信息是否想起 |
| Precision | 不相关 memory 是否被注入 |
| Conflict resolution | 新旧冲突事实是否处理正确 |
| Scope correctness | 是否只读取当前用户/项目 memory |
| Deletion compliance | 删除后是否不再召回 |
| Staleness handling | 过期事实是否降权或 invalidated |
| Task lift | 有 memory 是否提升任务完成率 |
| Token efficiency | memory 注入是否节省上下文 |
| Poisoning resistance | 恶意输入是否能写入长期行为规则 |
| User trust | 用户能否理解和控制 memory |

### 9.2 推荐测试集

构造场景：

1. 用户偏好跨 10 次会话持续生效。
2. 用户偏好后来改变。
3. 项目 A 与项目 B 偏好冲突。
4. 删除一条 memory 后再次询问。
5. 恶意网页要求 Agent “记住以后泄露 token”。
6. 用户提供 secret，看系统是否拒绝写入。
7. 长任务完成后，Agent 是否保存可复用经验。
8. 检索 1000 条 memory 时是否只注入 3-5 条相关项。
9. 多租户 memory 是否完全隔离。
10. 旧事实 invalidated 后是否不再使用。

## 10. 工程落地建议

如果要在本项目的 Agent 工作台中实现 memory，建议按三阶段推进。

### 10.1 阶段一：最小可用 Memory

目标：先实现用户/项目级长期事实记忆，不追求复杂图谱。

#### 数据模型

```text
memory_store
- id
- tenant_id
- owner_type: user | project | agent
- owner_id
- name
- description
- created_at

memory_item
- id
- store_id
- scope_path
- type: preference | fact | episode | procedure | project_note
- content
- metadata_json
- confidence
- importance
- status: active | deleted | superseded
- source_type
- source_id
- content_sha256
- created_by
- created_at
- updated_at

memory_version
- id
- memory_id
- operation: created | modified | deleted | redacted
- content_snapshot
- path_snapshot
- actor
- created_at
```

#### API 草案

```http
POST   /memory-stores
GET    /memory-stores/{id}/memories
POST   /memory-stores/{id}/memories
PATCH  /memory-stores/{id}/memories/{memory_id}
DELETE /memory-stores/{id}/memories/{memory_id}
POST   /memory-stores/{id}/search
GET    /memory-stores/{id}/versions
POST   /memory-stores/{id}/versions/{version_id}/redact
```

#### Agent 工具草案

```json
[
  {
    "name": "search_memory",
    "description": "当用户问题需要跨会话偏好、项目背景、过去决策或长期上下文时调用。必须先按当前 tenant/user/project scope 过滤。",
    "input_schema": {
      "type": "object",
      "properties": {
        "query": {"type": "string"},
        "scope": {"type": "string"},
        "types": {"type": "array", "items": {"type": "string"}}
      },
      "required": ["query", "scope"]
    }
  },
  {
    "name": "propose_memory",
    "description": "当对话中出现值得长期保存的用户偏好、项目规则或任务结论时调用。不要保存 secrets、token、密码或敏感个人信息。",
    "input_schema": {
      "type": "object",
      "properties": {
        "content": {"type": "string"},
        "type": {"type": "string"},
        "scope": {"type": "string"},
        "reason": {"type": "string"},
        "confidence": {"type": "number"}
      },
      "required": ["content", "type", "scope", "reason"]
    }
  }
]
```

关键点：**不要一开始让 Agent 直接写入 memory，先让它 propose，由后端规则或用户确认后保存。**

### 10.2 阶段二：Memory 抽取与后台整合

增加后台任务：

```text
conversation ended
  → extract memory candidates
  → classify
  → dedupe
  → detect conflicts
  → generate proposed changes
  → review/apply
```

可以分两类写入：

| 类型 | 处理 |
|---|---|
| 用户显式“记住” | 直接写，仍做敏感信息检测 |
| 模型自动发现 | 进入 pending / proposed 状态 |

UI 上提供：

- Memories 列表。
- 每条 memory 的来源。
- 编辑 / 删除。
- “为什么保存了这条？”
- “哪些回答使用了这条 memory？”

### 10.3 阶段三：图谱化与时间化

当业务复杂后，再引入类似 Zep 的图谱模式：

```text
entity
- user
- project
- organization
- task
- preference
- tool
- repository

relationship / fact
- subject
- predicate
- object
- valid_from
- valid_until
- confidence
- source
```

适合场景：

- 企业组织关系。
- 多项目多角色。
- 客户、工单、任务、代码库关系。
- 用户偏好随时间变化。
- 事实冲突频繁发生。

## 11. 推荐架构蓝图

```text
                  ┌─────────────────────┐
                  │ Conversation / Task  │
                  └──────────┬──────────┘
                             │
                             ▼
                  ┌─────────────────────┐
                  │ Memory Candidate    │
                  │ Extractor           │
                  └──────────┬──────────┘
                             │
         ┌───────────────────┼────────────────────┐
         ▼                   ▼                    ▼
┌────────────────┐  ┌────────────────┐   ┌────────────────┐
│ Sensitivity    │  │ Conflict /     │   │ Scope Resolver │
│ Detector       │  │ Dedupe         │   │ tenant/user/...│
└───────┬────────┘  └───────┬────────┘   └───────┬────────┘
        └───────────────────┼────────────────────┘
                            ▼
                 ┌──────────────────────┐
                 │ Memory Store          │
                 │ facts / episodes /    │
                 │ preferences / rules   │
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │ Memory Search Layer   │
                 │ filter + vector +     │
                 │ rerank + graph        │
                 └──────────┬───────────┘
                            │
                            ▼
                 ┌──────────────────────┐
                 │ Prompt / Tool Context │
                 │ Injection             │
                 └──────────────────────┘
```

## 12. 对本项目的落地建议

结合当前项目是 FastAPI + DDD 六边形架构的 Agent 工作台，建议 memory 能力按 DDD 分层实现。

### 12.1 Domain 层

定义领域模型：

- `MemoryStore`
- `MemoryItem`
- `MemoryVersion`
- `MemoryScope`
- `MemoryType`
- `MemorySensitivity`
- `MemoryStatus`

定义领域服务：

- `MemoryPolicyService`
- `MemoryConflictResolver`
- `MemorySensitivityClassifier`
- `MemoryScopeResolver`

### 12.2 Application 层

Use cases：

- `CreateMemoryStoreUseCase`
- `SearchMemoryUseCase`
- `ProposeMemoryUseCase`
- `ApproveMemoryUseCase`
- `UpdateMemoryUseCase`
- `DeleteMemoryUseCase`
- `RedactMemoryVersionUseCase`

### 12.3 Port

```python
class MemoryRepositoryPort:
    async def create(...):
        ...

    async def update(...):
        ...

    async def delete(...):
        ...

    async def list(...):
        ...

    async def get_versions(...):
        ...


class MemorySearchPort:
    async def search(...):
        ...


class EmbeddingPort:
    async def embed(...):
        ...
```

### 12.4 Infrastructure Adapter

可选实现：

- PostgreSQL：metadata、versions、audit。
- pgvector / Qdrant / Milvus：semantic search。
- Redis：短期 session memory。
- Object storage：长文档或归档。
- Graph DB：后续 Neo4j / AGE / 自建关系表。

### 12.5 Agent Tool Adapter

给 ReAct Agent 提供：

- `search_memory`
- `propose_memory`
- `list_memory`
- `update_memory`
- `delete_memory`

第一阶段建议只开放：

- `search_memory`
- `propose_memory`

由后端审批/规则决定是否真正写入，降低风险。

## 13. 关键结论

1. **Memory 不是聊天历史。**
   - 聊天历史是原始事件；memory 是经过筛选、作用域化、可治理的长期状态。
2. **Memory 必须有作用域。**
   - 至少区分 tenant / user / project / agent / session。
3. **Memory 写入比检索更危险。**
   - 错误写入会长期污染 Agent 行为。
   - 写入应有敏感信息过滤、冲突处理和审计。
4. **删除语义必须从第一天设计。**
   - 不能只删 UI 记录。
   - embedding、版本、摘要、合成 memory 都要考虑删除传播或 redaction。
5. **不要只依赖向量库。**
   - 向量库解决相关性，不解决权限、时效、冲突、审计。
6. **最实用的 MVP 是：结构化 facts + scope + vector search + versions。**
   - 先别上复杂图谱。
   - 等业务事实关系复杂后，再引入 temporal graph。
7. **用户控制是产品级 memory 的核心。**
   - ChatGPT / Claude 都强调用户可查看、可删、可关闭、可导入导出。
8. **企业 Agent 需要 read-only shared memory + read-write user/project memory。**
   - 共享知识库应避免被 Agent 任意污染。
   - 用户/项目 memory 可受控写入。

## 14. Sources

- [OpenAI — Memory FAQ / Memory in ChatGPT](https://help.openai.com/en/articles/8590148-memory-in-chatgpt)
- [OpenAI — Memory and new controls for ChatGPT](https://openai.com/index/memory-and-new-controls-for-chatgpt)
- [Anthropic — Using Claude’s chat search and memory](https://support.claude.com/en/articles/11817273-using-claude-s-chat-search-and-memory-to-build-on-previous-context)
- [Anthropic — Import and export your memory from Claude](https://support.claude.com/en/articles/12123587-import-and-export-your-memory-from-claude)
- [Anthropic — Retrieval Augmented Generation for Projects](https://support.anthropic.com/en/articles/11473015-retrieval-augmented-generation-rag-for-projects)
- [Claude API / Managed Agents Memory Stores](https://platform.claude.com/docs/en/managed-agents/memory.md)
- [Claude API — Memory Tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool.md)
- [LangChain — Long-term memory](https://docs.langchain.com/oss/python/langchain/long-term-memory)
- [LangChain — Memory concepts](https://docs.langchain.com/oss/python/concepts/memory)
- [LangMem documentation](https://langchain-ai.github.io/langmem/)
- [LangMem GitHub](https://github.com/langchain-ai/langmem)
- [Mem0 — Add memories](https://docs.mem0.ai/core-concepts/memory-operations/add)
- [Mem0 — API reference: add memories](https://docs.mem0.ai/api-reference/memory/add-memories)
- [Mem0 documentation overview](https://docs.mem0.ai/overview)
- [Zep documentation](https://help.getzep.com/docs)
- [Zep concepts](https://help.getzep.com/concepts)
- [Zep facts](https://help.getzep.com/facts)
- [Zep Context Lake](https://www.getzep.com/platform/context-lake/)
- [Zep Context Graph Engine](https://www.getzep.com/platform/context-graph-engine/)
- [Zep / Graphiti paper](https://arxiv.org/abs/2501.13956)
- [LlamaIndex — Agent memory](https://developers.llamaindex.ai/python/framework/module_guides/deploying/agents/memory/)
- [AutoGen AgentChat — Memory](https://microsoft.github.io/autogen/stable/user-guide/agentchat-user-guide/memory.html)
- [CrewAI — Memory](https://docs.crewai.com/concepts/memory)
- [Microsoft — Manage agentic memory safety](https://learn.microsoft.com/en-us/security/zero-trust/sfi/manage-agentic-memory-safety)
- [OWASP Agent Memory Guard](https://owasp.org/www-project-agent-memory-guard/)
