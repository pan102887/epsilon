# Chat 功能架构全景

## 1. DDD 分层架构总览

```mermaid
graph TB
    subgraph Client["客户端"]
        HTTP["HTTP Client"]
    end

    subgraph Application["应用层 application/"]
        SA["server_app.py<br/>FastAPI 实例 + 路由挂载"]
        CR["routers/chat.py<br/>POST /api/chat<br/>DELETE /api/chat/sessions/{id}"]
        CC["container_config.py<br/>DI 容器配置 & Port→Adapter 绑定"]
    end

    subgraph Domain["领域层 domain/"]
        subgraph DomainChat["domain/chat/"]
            CSP["ChatServicePort<br/>(Protocol)"]
            SCSP["SessionContextStorePort<br/>(Protocol)"]
            VO["ChatRequestVO / ChatResponseVO<br/>值对象"]
            CTX["ConversationContext<br/>对话上下文管理"]
            MSG["Message<br/>消息值对象"]
        end
        subgraph DomainMA["domain/model_access/"]
            MAP["ModelAccessPort<br/>(Protocol)"]
            MAVO["ChatRequest / ChatResponse<br/>StreamingChunk"]
            MRP["ModelRegistryPort<br/>(Protocol)"]
        end
    end

    subgraph Infrastructure["基础设施层 infrastructure/"]
        CSA["chat/ChatServiceAdapter<br/>对话编排服务"]
        RSA["session/RedisSessionContextAdapter<br/>Redis 会话存储"]
        LB["model_access/LoadBalancingModelAdapter<br/>负载均衡模型入口"]
        PR["model_access/ProviderRegistry<br/>供应商注册中心"]
        OCA["model_access/OpenAICompatibleAdapter<br/>OpenAI 协议适配器"]
        CFG["chat/ChatConfig<br/>系统提示词配置"]
    end

    subgraph External["外部依赖"]
        Redis[("Redis<br/>会话持久化")]
        LLM["LLM API<br/>Zhipu / DeepSeek / OpenAI"]
    end

    HTTP -->|HTTP Request| CR
    SA -->|include_router| CR
    SA -->|configure_container| CC
    CR -->|Depends inject| CSP
    CR -->|构造| VO

    CSA -.->|实现| CSP
    RSA -.->|实现| SCSP
    LB -.->|实现| MAP
    PR -.->|实现| MRP

    CSA -->|调用| SCSP
    CSA -->|调用| MAP
    CSA -->|管理| CTX
    CSA -->|读取| CFG
    CTX -->|包含| MSG

    LB -->|路由| PR
    PR -->|持有| OCA
    OCA -->|调用| LLM

    RSA -->|读写| Redis

    CC -->|注册 Port→Adapter| CSP
    CC -->|注册 Port→Adapter| SCSP
    CC -->|注册 Port→Adapter| MAP
```

## 2. 对话请求流程

```mermaid
sequenceDiagram
    participant C as Client
    participant R as Chat Router
    participant S as ChatServiceAdapter
    participant Store as Redis<br/>(SessionContextStore)
    participant LB as LoadBalancingModelAdapter
    participant PR as ProviderRegistry
    participant LLM as LLM API

    C->>R: POST /api/chat<br/>{session_id, message, stream}
    R->>R: 构造 ChatRequestVO
    R->>S: chat / stream_chat

    S->>Store: load(session_id)
    Store-->>S: ConversationContext

    S->>S: _ensure_system_prompt()<br/>注入系统提示词
    S->>S: add_user_message(message)
    S->>S: _compaction.compact()<br/>上下文压缩

    S->>LB: chat / stream (ChatRequest)
    LB->>PR: get_adapter_for_model(model)
    PR->>PR: Round-Robin 负载均衡选择提供商
    PR-->>LB: OpenAICompatibleAdapter

    LB->>LLM: 发起 HTTP 请求 (httpx)
    LLM-->>LB: 响应数据 (JSON / SSE)
    LB-->>S: ChatResponse / StreamingChunk

    S->>S: add_assistant_message(reply)
    S->>Store: save(session_id, context)
    S-->>R: ChatResponseVO / StreamingChunk
    R-->>C: 响应客户端
```

## 3. 依赖注入与生命周期管理

```mermaid
graph LR
    subgraph Startup["FastAPI Lifespan 启动阶段"]
        direction TB
        I1["1. _init_model_client()<br/>初始化提供商注册中心"]
        I2["2. _init_redis()<br/>连接 Redis"]
        I3["3. _init_db()<br/>初始化数据库"]
        I1 --> I2 --> I3
    end

    subgraph Registry["Port → Adapter 绑定"]
        direction TB
        B1["ModelAccessPort → LoadBalancingModelAdapter"]
        B2["SessionContextStorePort → RedisSessionContextAdapter"]
        B3["ChatServicePort → ChatServiceAdapter"]
        B1 --- B2 --- B3
    end

    subgraph Runtime["请求处理阶段"]
        direction TB
        R1["Router: Depends(inject(ChatServicePort))"]
        R2["Container.resolve() → Singleton 实例"]
        R1 --> R2
    end
```

## 4. 负载均衡路由策略

```mermaid
graph TB
    subgraph Request["请求参数"]
        Mo["model: 指定模型"]
    end

    subgraph Registry["ProviderRegistry"]
        direction TB
        R1{"模型是否已注册?"}
        R2["获取该模型的<br/>Round-Robin 迭代器"]
        R3["next(rr_iterator)<br/>选择下一个提供商"]
    end

    subgraph Providers["已注册提供商适配器"]
        ZP["zhipu (Adapter)"]
        DP["deepseek (Adapter)"]
        CP["cliproxy (Adapter)"]
    end

    Mo --> R1
    R1 -->|是| R2
    R2 --> R3
    R3 --> ZP
    R3 --> DP
    R3 --> CP
```

## 5. 上下文压缩策略 (Context Compaction)

```mermaid
graph LR
    subgraph Raw["原始完整消息列表"]
        S1["🔧 system"]
        U1["👤 user 1"]
        A1["🤖 assistant 1"]
        U2["👤 user 2"]
        A2["🤖 assistant 2"]
        UN["👤 user ...N"]
        AN["🤖 assistant ...N"]
    end

    subgraph Compact["压缩策略 (Sliding Window)"]
        direction TB
        C1["保留所有 System 消息"]
        C2["保留最近 K 条上下文"]
    end

    subgraph Result["发送给 LLM 的消息"]
        RS["🔧 system"]
        RU["👤 最近的 user"]
        RA["🤖 最近的 assistant"]
    end

    Raw --> Compact
    Compact --> Result
```

---

以上架构图展示了 Chat 功能的核心实现细节：

- **基于端口/适配器模式**: 确保了领域逻辑与底层 LLM 提供商、存储介质的完全解耦。
- **负载均衡**: 通过 `ProviderRegistry` 实现了生产级的多供应商轮询调度。
- **上下文管理**: 结合滑动窗口压缩与 Redis 持久化，兼顾了 LLM Token 成本与对话历史完整性。
