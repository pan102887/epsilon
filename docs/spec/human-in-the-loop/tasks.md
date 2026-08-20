# 实现计划：Human-in-the-loop 工具审批

## 概述

本计划从领域契约开始，依次实现审批配置与状态存储、ReAct Agent 中断/恢复、Chat/API/TUI 编排和文档。所有任务遵循现有 DDD 分层：`domain/` 只放值对象与 Port，`infrastructure/` 实现策略、存储和 Agent Loop，`application/` 负责容器、HTTP 与 TUI adapter；验证命令在 `epsilon-boot/` 下使用 `uv run --frozen pytest test`。

## Tasks

### 1. 领域契约与错误模型

- [x] 1.1 修改 Agent 审批值对象
  - 在 `epsilon-boot/src/domain/agent/value_objects.py` 中新增 `ApprovalDecisionType = Literal["approve", "edit", "reject", "respond"]`、`AgentRunStatus = Literal["completed", "approval_required"]`。
  - 新增 frozen dataclass：`ApprovalPolicy(tool_name: str, interrupt: bool, allowed_decisions: frozenset[ApprovalDecisionType], risk_label: str = "")`、`PendingActionRequest(tool_call_id: str, tool_name: str, arguments: str, allowed_decisions: frozenset[ApprovalDecisionType], reason: str = "")`、`EditedAction(name: str, arguments: str)`、`ApprovalDecision(type: ApprovalDecisionType, tool_call_id: str, edited_action: EditedAction | None = None, message: str = "")`。
  - 新增 `ApprovalInterrupt(session_id, approval_id, actions, context_snapshot, round_num, model, usage_so_far, created_at_epoch, expires_at_epoch, metadata)`，并实现 `is_expired(self, now_epoch: float) -> bool`。
  - 新增 `ApprovalRequiredPayload(session_id, approval_id, actions, prompt_id, metadata)` 与 `ApprovalResume(session_id, approval_id, decisions, model=None)`。
  - 扩展 `AgentResult` 默认字段 `status: AgentRunStatus = "completed"`、`approval: ApprovalRequiredPayload | None = None`，保持现有构造兼容。
  - 扩展 `AgentStreamEventKind`，新增 `"approval_required"`。
  - _需求: 1.1, 2.1, 4.5_

- [x] 1.2 编写 Agent 审批值对象测试
  - 在 `epsilon-boot/test/domain/agent/test_approval_value_objects_unit.py` 中覆盖默认 `AgentResult` 仍为 `completed`、`approval_required` payload 构造、`ApprovalInterrupt.is_expired(...)` 边界。
  - 在 `epsilon-boot/test/domain/agent/test_approval_value_objects_property.py` 中使用 Hypothesis 验证 `PendingActionRequest` 顺序可稳定保留、`ApprovalInterrupt` 的 `actions` tuple 不被变异。
  - 覆盖 Property 4、Property 14。
  - **验证: 需求 2.1, 2.3, 2.4, 4.5, 9.8**

- [x] 1.3 新增审批异常类型
  - 在 `epsilon-boot/src/domain/agent/exceptions.py` 中新增继承 `BizException` 的审批异常，错误码使用 60020-60029。
  - 类型包含：`ApprovalNotFoundError`、`ApprovalExpiredError`、`ApprovalConsumedError`、`ApprovalDecisionCountMismatchError`、`ApprovalDecisionOrderMismatchError`、`ApprovalDecisionNotAllowedError`、`ApprovalEditToolNameMismatchError`、`ApprovalEditInvalidArgumentsError`、`ApprovalRespondNotAllowedError`、`HitlConfigInvalidError`。
  - 每个异常提供中文 message，不包含存储物理路径、内部堆栈或密钥。
  - _需求: 2.6, 5.3, 5.4, 5.5, 5.6, 5.7, 8.4, 8.5_

- [x] 1.4 编写审批异常测试
  - 在 `epsilon-boot/test/domain/agent/test_approval_exceptions_unit.py` 中断言每个异常的 `code`、中文 message 与关键属性。
  - 在 `epsilon-boot/test/domain/agent/test_approval_exceptions_properties.py` 中用 Hypothesis 验证异常 message 不包含模拟路径片段、`token/password/secret` 原始值。
  - 覆盖 Property 10。
  - **验证: 需求 2.6, 5.3, 5.4, 5.5, 5.6, 5.7, 8.4, 8.5**

- [x] 1.5 扩展 Agent 端口
  - 在 `epsilon-boot/src/domain/agent/ports.py` 中为 `AgentPort` 新增：
    `async def resume(self, context: ConversationContext, config: AgentConfig, model_access: ModelAccessPort, interrupt: ApprovalInterrupt, decisions: tuple[ApprovalDecision, ...]) -> AgentResult`。
  - 新增 `ApprovalPolicyPort.policy_for(self, tool_name: str) -> ApprovalPolicy`。
  - 新增 `ApprovalStateStorePort.save/load/consume/delete/delete_session` Protocol，返回类型与 `design.md` 保持一致。
  - 使用 `TYPE_CHECKING` 导入新增值对象，保持领域层无基础设施依赖。
  - _需求: 2.2, 4.7_

- [x] 1.6 编写 Agent 端口静态契约测试
  - 在 `epsilon-boot/test/domain/agent/test_approval_ports_unit.py` 中用 dummy class 验证 `AgentPort.resume(...)`、`ApprovalPolicyPort.policy_for(...)`、`ApprovalStateStorePort.consume(...)` 的签名和返回注解。
  - 覆盖 Property 14。
  - **验证: 需求 2.1, 2.2, 4.7**

- [x] 1.7 扩展聊天值对象
  - 在 `epsilon-boot/src/domain/chat/value_objects.py` 中新增 `ChatResponseStatus = Literal["completed", "approval_required"]`。
  - 为 `ChatResponseVO` 增加默认字段：`status: ChatResponseStatus = "completed"`、`approval_id: str | None = None`、`action_requests: tuple[PendingActionRequest, ...] = field(default_factory=tuple)`。
  - 新增 `ApprovalResumeRequestVO(session_id: str, approval_id: str, decisions: tuple[ApprovalDecision, ...], model: str | None = None)`，校验 `session_id`、`approval_id` 非空。
  - 保持 `prompt_id` 校验与现有 `_PROMPT_ID_PATTERN` 一致。
  - _需求: 4.1, 4.2, 4.3, 5.2_

- [x] 1.8 编写聊天值对象状态联合测试
  - 在 `epsilon-boot/test/domain/chat/test_hitl_chat_value_objects_unit.py` 中覆盖 `ChatResponseVO` completed 默认兼容、approval_required 字段、非法 `ApprovalResumeRequestVO`。
  - 在 `epsilon-boot/test/domain/chat/test_hitl_chat_value_objects_property.py` 中验证任意 action tuple 顺序在 `ChatResponseVO.action_requests` 中保持。
  - 覆盖 Property 7。
  - **验证: 需求 4.1, 4.2, 4.3, 5.2, 9.9**

- [x] 1.9 扩展聊天 Port 与流式分片元数据
  - 在 `epsilon-boot/src/domain/chat/ports.py` 中为 `ChatServicePort` 新增 `async def resume_approval(self, request: ApprovalResumeRequestVO) -> ChatResponseVO`。
  - 在 `epsilon-boot/src/domain/model_access/value_objects.py` 中为 `StreamingChunk` 新增 `metadata: dict[str, Any] = field(default_factory=dict)`。
  - 确保旧代码只传 `delta_content/finished/usage` 时仍可构造。
  - _需求: 4.6, 4.7, 5.1_

- [x] 1.10 编写聊天 Port 与 StreamingChunk 兼容测试
  - 在 `epsilon-boot/test/domain/chat/test_hitl_chat_ports_unit.py` 中验证 `ChatServicePort.resume_approval(...)` 协议签名。
  - 在 `epsilon-boot/test/domain/model_access/test_streaming_chunk_metadata_unit.py` 中验证 `StreamingChunk.metadata` 默认空 dict、旧构造方式兼容。
  - 覆盖 Property 7、Property 8。
  - **验证: 需求 4.6, 4.7, 5.1**

- [x] 2. 检查点 — 领域契约验证
  - 在 `epsilon-boot/` 目录运行 `uv run --frozen pytest test`。
  - 全部测试必须通过；如新增 Protocol 签名导致既有测试 dummy 不兼容，先在对应测试中补齐 dummy 方法，不改变领域设计。

### 3. 审批配置、策略与状态存储

- [x] 3.1 创建 HITL 配置模块
  - 在 `epsilon-boot/src/infrastructure/agent/hitl_config.py` 中创建模块级中文 docstring。
  - 实现 `DEFAULT_HITL_STATE_TTL_SECONDS = 3600`。
  - 实现 `class HitlConfig(PropertiesBaseSettings)`，`model_config = SettingsConfigDict(env_prefix="HITL_")`，字段 `enabled: bool = False`、`interrupt_on: str = ""`、`state_ttl_seconds: int = DEFAULT_HITL_STATE_TTL_SECONDS`。
  - 用 `@model_validator(mode="before")` 实现 TTL 小于等于 0 时回退 3600。
  - 暴露 `hitl_config = create_config(HitlConfig)`。
  - _需求: 1.3, 1.4, 10.4_

- [x] 3.2 编写 HITL 配置测试
  - 在 `epsilon-boot/test/infrastructure/agent/test_hitl_config_unit.py` 中覆盖默认关闭、TTL 回退、`HITL_` 前缀字段。
  - 在 `epsilon-boot/test/infrastructure/agent/test_hitl_config_properties.py` 中用 Hypothesis 覆盖正负 TTL 输入。
  - 覆盖 Property 1、Property 13。
  - **验证: 需求 1.3, 1.4, 10.4**

- [x] 3.3 创建审批策略提供器
  - 在 `epsilon-boot/src/infrastructure/agent/approval_policy_provider.py` 中实现 `StaticApprovalPolicyProvider(ApprovalPolicyPort)`。
  - 构造签名：`def __init__(self, enabled: bool, interrupt_on: str) -> None`。
  - 实现 `policy_for(self, tool_name: str) -> ApprovalPolicy`。
  - 默认策略：`write_file/edit_file/shell_exec/python_exec/delegate_to_agent` 允许 `approve/reject`；`http_request` 允许 `approve/edit/reject`；`read_file/list_dir/web_fetch/web_search` 不审批；现有工具默认不开放 `respond`。
  - 解析 `HITL_INTERRUPT_ON` JSON，支持 `true`、`false`、决策数组、`{"allowed_decisions": [...], "risk_label": "..."}`；非法 JSON 或非法决策抛 `HitlConfigInvalidError`。
  - _需求: 1.1, 1.2, 1.6, 1.7, 1.8, 1.9, 1.10_

- [x] 3.4 编写审批策略提供器测试
  - 在 `epsilon-boot/test/infrastructure/agent/test_approval_policy_provider_unit.py` 中覆盖默认敏感工具、低风险工具、`http_request`、默认关闭、用户覆盖。
  - 在 `epsilon-boot/test/infrastructure/agent/test_approval_policy_provider_property.py` 中用 Hypothesis 验证合法决策集合顺序不影响生成策略，非法决策 fail-fast。
  - 覆盖 Property 13。
  - **验证: 需求 1.1, 1.2, 1.4, 1.7, 1.8, 1.9, 1.10, 9.5**

- [x] 3.5 创建审批状态序列化与本地文件存储
  - 在 `epsilon-boot/src/infrastructure/agent/approval_state_store.py` 中实现模块级序列化 helper：`approval_interrupt_to_dict(interrupt: ApprovalInterrupt) -> dict[str, Any]`、`approval_interrupt_from_dict(data: dict[str, Any]) -> ApprovalInterrupt`。
  - 在同文件中实现 `LocalFileApprovalStateStore(ApprovalStateStorePort)`，构造签名与 `design.md` 一致：`root: Path`、`lock_factory`、`path_policy`、`atomic_writer`、`ttl_seconds: int`。
  - 文件布局为 `<root>/approvals/<session_bucket>/<session_stem>/<approval_id>.json` 与 `.json.lock`。
  - `save(...)` 使用独占锁和 `TempFileAtomicWriter.write_bytes_atomic(...)`；`load(...)` 返回不存在/过期时 `None`；`consume(...)` 在独占锁内读、过期校验、删除并返回状态；`delete(...)` 幂等删除；`delete_session(...)` 删除该 session bucket 下审批目录。
  - _需求: 2.2, 2.3, 2.5, 2.6, 2.7, 2.8_

- [x] 3.6 编写本地文件审批状态存储测试
  - 在 `epsilon-boot/test/infrastructure/agent/test_local_file_approval_state_store_unit.py` 中覆盖 save/load/consume/delete/delete_session、TTL 过期、重复 consume。
  - 在 `epsilon-boot/test/infrastructure/agent/test_approval_state_store_serialization_property.py` 中用 Hypothesis 验证 `ApprovalInterrupt` 序列化往返保持 action 顺序和 `context_snapshot`。
  - 覆盖 Property 4、Property 5、Property 14。
  - **验证: 需求 2.2, 2.3, 2.4, 2.5, 2.6, 2.7, 2.8, 9.6**

- [x] 3.7 实现 Redis 审批状态存储
  - 在 `epsilon-boot/src/infrastructure/agent/approval_state_store.py` 中实现 `RedisApprovalStateStore(ApprovalStateStorePort)`。
  - 构造签名：`def __init__(self, redis_client: aioredis.Redis, key_prefix: str = "agent:approval:", ttl_seconds: int = 3600) -> None`。
  - key 格式为 `agent:approval:<session_id>:<approval_id>`；`save(...)` 使用 `set(..., ex=ttl_seconds)`；`load(...)` 反序列化 JSON；`consume(...)` 优先使用 `GETDEL`，不支持时用 `WATCH/MULTI/EXEC`；`delete_session(...)` 使用前缀扫描或已知 session 前缀清理。
  - _需求: 2.2, 2.5, 2.6, 2.7, 2.8, 5.7_

- [x] 3.8 编写 Redis 审批状态存储测试
  - 在 `epsilon-boot/test/infrastructure/agent/test_redis_approval_state_store_unit.py` 中使用 fake redis 或 `AsyncMock` 覆盖 key、TTL、load、consume 成功、consume 缺失、delete。
  - 覆盖并发消费的最多一次成功语义；不依赖真实 Redis 服务。
  - 覆盖 Property 5、Property 14。
  - **验证: 需求 2.5, 2.6, 2.7, 2.8, 5.7, 9.6**

- [x] 3.9 创建审批日志脱敏工具
  - 在 `epsilon-boot/src/infrastructure/agent/approval_logging.py` 中实现 `SENSITIVE_KEYS = frozenset({"api_key", "password", "secret", "token", "authorization"})`。
  - 实现 `redact_approval_value(value: Any, *, max_length: int = 1200) -> str`，支持 dict/list/str，大小写不敏感匹配敏感键，输出截断。
  - 实现 `approval_log_extra(session_id: str, approval_id: str, tool_names: list[str], action_count: int, round_num: int | None = None, decision_types: list[str] | None = None) -> dict[str, Any]`。
  - _需求: 8.1, 8.2, 8.3, 8.4_

- [x] 3.10 编写审批日志脱敏测试
  - 在 `epsilon-boot/test/infrastructure/agent/test_approval_logging_property.py` 中用 Hypothesis 生成大小写变体敏感键，验证输出不包含原始 secret/token/password/authorization 值。
  - 在 `epsilon-boot/test/infrastructure/agent/test_approval_logging_unit.py` 中覆盖长度截断和 `approval_log_extra(...)` 字段。
  - 覆盖 Property 10。
  - **验证: 需求 8.1, 8.2, 8.3, 8.4, 8.5**

- [x] 4. 检查点 — 配置、策略与状态存储验证
  - 在 `epsilon-boot/` 目录运行 `uv run --frozen pytest test`。
  - 全部测试必须通过；如 fake redis 语义不足以验证 `GETDEL` 分支，补充 mock 分支测试，不引入真实外部服务依赖。

### 5. Agent Loop 中断与恢复

- [x] 5.1 修改 ReActAgentAdapter 构造与授权优先级
  - 在 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` 中扩展 `ReActAgentAdapter.__init__(self, tool_registry: ToolRegistry, compaction: ContextCompactionPort, approval_policy: ApprovalPolicyPort, approval_store: ApprovalStateStorePort) -> None`。
  - 将工具执行前的授权校验抽成共享逻辑，保证 `ToolPermissionDeniedError` / `ToolNotFoundError` 优先于审批策略，不允许通过审批绕过 `AgentConfig.allowed_tool_names`。
  - 更新现有测试 helper `_make_adapter(...)`，为新构造参数注入默认关闭策略与 mock store。
  - _需求: 1.5, 1.11, 3.1, 3.12_

- [x] 5.2 编写 HITL 关闭兼容与授权优先级测试
  - 在 `epsilon-boot/test/infrastructure/agent/test_react_agent_hitl_compat_unit.py` 中覆盖 `HITL_ENABLED=false` 等价行为、低风险工具不审批、未授权工具不创建 `ApprovalInterrupt`。
  - 在 `epsilon-boot/test/infrastructure/agent/test_react_agent_permission_properties.py` 中补充敏感但未授权工具优先产生权限错误的属性测试。
  - 覆盖 Property 1、Property 13。
  - **验证: 需求 1.4, 1.5, 1.11, 3.1, 3.12, 9.1**

- [x] 5.3 实现同步 Agent 审批中断创建
  - 在 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` 中实现 `_collect_pending_actions(...)`、`_save_interrupt(...)`。
  - `run(...)` 在模型返回 `tool_calls` 后先追加 `AssistantMessage(tool_calls=...)`，再按策略筛选待审批动作；命中时创建单个 `ApprovalInterrupt`，保存 `context.to_dict()`，返回 `AgentResult(status="approval_required", approval=ApprovalRequiredPayload(...), content="", model=response.model, usage=total_usage)`。
  - 同一轮多个敏感工具按模型 `tool_calls` 顺序进入同一个中断；中断前不执行任何待审批工具，也不追加待审批 `ToolMessage`。
  - 创建中断时记录结构化日志，字段包含 `session_id`、`approval_id`、工具名列表、动作数量、round。
  - _需求: 1.6, 2.3, 2.4, 3.2, 3.3, 8.1_

- [x] 5.4 编写同步 Agent 审批中断测试
  - 在 `epsilon-boot/test/infrastructure/agent/test_react_agent_hitl_interrupt_unit.py` 中覆盖单敏感工具中断、批量敏感工具顺序、同轮中断时工具未执行、快照包含 assistant tool_calls 且不含 pending ToolMessage。
  - 在 `epsilon-boot/test/infrastructure/agent/test_react_agent_hitl_interrupt_property.py` 中用 Hypothesis 验证任意待审批 tool_call 列表顺序被保留。
  - 覆盖 Property 2、Property 3、Property 4。
  - **验证: 需求 1.6, 2.3, 2.4, 3.2, 3.3, 9.2, 9.3**

- [x] 5.5 实现审批恢复决策应用
  - 在 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` 中实现 `_apply_approval_decisions(...)`。
  - `approve` 使用原始 `ToolCallRequest.name/arguments` 执行；`edit` 要求 `EditedAction.name == 原工具名`，用 `EditedAction.arguments` 构造新 `ToolCallRequest` 并重新走 `ToolRegistry.execute(...)`；`reject` 跳过执行并追加中文拒绝 `ToolMessage`；`respond` 仅在 allowed decisions 中允许时把人工回复作为 `ToolMessage`。
  - 校验决策数量、顺序、决策类型、`edit` 参数 JSON/schema、`respond` message；非法时抛对应审批异常，不执行工具。
  - _需求: 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 5.3, 5.4, 5.5, 5.6_

- [x] 5.6 编写审批恢复决策测试
  - 在 `epsilon-boot/test/infrastructure/agent/test_react_agent_hitl_resume_unit.py` 中覆盖 `approve`、`edit`、`reject`、允许的 `respond`、非法数量、非法顺序、非法类型、修改工具名、非法 JSON 参数。
  - 在 `epsilon-boot/test/infrastructure/agent/test_react_agent_hitl_resume_property.py` 中用 Hypothesis 验证任意顺序错位必拒绝且工具未执行。
  - 覆盖 Property 3、Property 6。
  - **验证: 需求 3.4, 3.5, 3.6, 3.7, 3.8, 3.9, 5.3, 5.4, 5.5, 5.6, 9.4, 9.5, 9.6**

- [x] 5.7 实现 Agent resume 继续 ReAct Loop
  - 在 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` 中实现 `async def resume(...) -> AgentResult` 和 `_continue_after_tools(...)`。
  - `resume(...)` 从 `interrupt.round_num + 1` 继续，复用 `interrupt.usage_so_far` 累计 token；所有恢复工具处理完成后继续模型调用，直到最终文本、再次中断或达到 `max_rounds`。
  - 再次命中敏感工具时创建新的 `approval_id` 与新的 `ApprovalInterrupt`。
  - _需求: 3.10, 3.11, 5.8, 5.9_

- [x] 5.8 编写 Agent resume 循环测试
  - 在 `epsilon-boot/test/infrastructure/agent/test_react_agent_hitl_continue_unit.py` 中覆盖恢复后继续一轮得到最终回复、恢复后再次触发审批、达到最大轮次。
  - 验证 usage 累计、context 消息顺序、第二次中断使用新 `approval_id`。
  - 覆盖 Property 6、Property 7。
  - **验证: 需求 3.10, 3.11, 5.8, 5.9, 9.4**

- [x] 5.9 实现流式与事件流审批行为
  - 在 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` 中为 `run_streaming(...)` 和 `run_events(...)` 接入相同审批筛选与中断创建逻辑。
  - `run_streaming(...)` 命中审批时 yield 单个 `StreamingChunk(delta_content="当前会话等待人工审批，approval_id=...", finished=True, metadata={"status": "approval_required", ...})` 并返回。
  - `run_events(...)` 命中审批时 yield `AgentStreamEvent(kind="approval_required", content="当前请求等待人工审批，请通过审批恢复接口提交决策。", metadata={session_id, approval_id, actions})` 并返回，不发送 `assistant_done`。
  - _需求: 4.4, 4.5, 4.6, 6.1, 6.3_

- [x] 5.10 编写流式与事件流审批测试
  - 在 `epsilon-boot/test/infrastructure/agent/test_react_agent_hitl_streaming_unit.py` 中覆盖 `run_streaming(...)` 审批提示 chunk、metadata、未执行工具。
  - 在 `epsilon-boot/test/infrastructure/agent/test_react_agent_hitl_events_unit.py` 中覆盖 `approval_required` 事件字段完整性、无 `assistant_done`。
  - 覆盖 Property 8、Property 11。
  - **验证: 需求 4.4, 4.5, 4.6, 6.1, 6.3, 9.8**

- [x] 6. 检查点 — Agent Loop 审批语义验证
  - 在 `epsilon-boot/` 目录运行 `uv run --frozen pytest test`。
  - 全部测试必须通过；如现有 Agent 单元测试构造器失败，应按新构造签名补齐 mock 依赖，不放宽设计语义。

### 7. Chat 编排、HTTP/SSE、TUI、容器与文档

- [x] 7.1 修改 ChatServiceAdapter 编排
  - 在 `epsilon-boot/src/infrastructure/chat/chat_service_adapter.py` 中扩展构造签名，新增 `approval_store: ApprovalStateStorePort`。
  - `chat(...)` 将 `AgentResult(status="approval_required")` 转换为 `ChatResponseVO(status="approval_required", approval_id=..., action_requests=...)`，不追加最终 assistant 回复，不保存普通 session context。
  - 新增 `async def resume_approval(self, request: ApprovalResumeRequestVO) -> ChatResponseVO`：load 状态、校验未过期和决策、consume 状态、从 `ConversationContext.from_dict(...)` 恢复、调用 `agent.resume(...)`、完成时保存 session、再次中断时返回新 approval。
  - `clear_session(...)` 同时调用 `approval_store.delete_session(session_id)`。
  - `stream_chat(...)` 遇到 approval metadata 时不把中文提示保存为最终 assistant 回复；`stream_chat_events(...)` 遇到 `approval_required` 时不保存 session。
  - _需求: 2.5, 2.6, 2.7, 4.1, 4.3, 4.7, 5.7, 5.8, 5.9_

- [x] 7.2 编写 ChatServiceAdapter HITL 测试
  - 在 `epsilon-boot/test/infrastructure/chat/test_chat_service_hitl_unit.py` 中覆盖 chat 审批中断不保存 session、completed 保存 session、resume 成功保存 session、resume 再次中断返回新 approval、clear_session 清理审批。
  - 在 `epsilon-boot/test/infrastructure/chat/test_chat_service_hitl_errors_unit.py` 中覆盖状态不存在、过期、重复消费、非法决策映射。
  - 覆盖 Property 5、Property 7。
  - **验证: 需求 2.5, 2.6, 2.7, 4.1, 4.3, 4.7, 5.7, 5.8, 5.9, 9.7, 9.9**

- [x] 7.3 修改聊天 HTTP 路由与响应模型
  - 在 `epsilon-boot/src/application/api/routers/chat.py` 中新增 Pydantic 模型：`ApprovalActionBody`、`EditedActionBody`、`ApprovalDecisionBody`、`ApprovalResumeRequestBody`、`ChatCompletedResponseBody`、`ChatApprovalRequiredResponseBody`。
  - 修改 `POST /api/chat` 同步分支：completed 返回 `status="completed"` 和现有字段；approval 返回 `status="approval_required"`、`session_id`、`approval_id`、`action_requests`、`prompt_id`、`model`、`usage`。
  - 新增 `POST /api/chat/sessions/{session_id}/approvals/{approval_id}/resume`，把请求体转换为 `ApprovalResumeRequestVO`，调用 `service.resume_approval(...)`。
  - 审批业务异常映射 400/404/409 中文 JSON；保留普通 ValueError 400。
  - _需求: 4.2, 4.3, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9_

- [x] 7.4 编写聊天 HTTP 路由测试
  - 在 `epsilon-boot/test/application/routers/test_chat_router_hitl_unit.py` 中覆盖同步 completed、同步 approval_required、resume completed、resume approval_required。
  - 在 `epsilon-boot/test/application/routers/test_chat_router_hitl_errors_unit.py` 中覆盖数量不匹配、非法决策、edit 缺字段/改工具名、respond 不允许、状态不存在/过期/已消费的 HTTP 400/404/409。
  - 覆盖 Property 7。
  - **验证: 需求 4.2, 4.3, 5.1, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.9, 9.7, 9.9**

- [x] 7.5 修改 SSE 审批事件输出
  - 在 `epsilon-boot/src/application/api/routers/chat.py` 中让 `stream=true` 优先消费 `service.stream_chat_events(...)`。
  - 普通文本增量继续输出兼容 JSON data；`approval_required` 输出包含 `status/session_id/approval_id/action_requests` 的 SSE 事件后发送 `[DONE]`。
  - 审批中断时不得发送 `assistant_done` 或 prompt_id completion 事件。
  - _需求: 4.4, 4.5, 4.6_

- [x] 7.6 编写 SSE 审批事件测试
  - 在 `epsilon-boot/test/application/routers/test_chat_router_hitl_sse_unit.py` 中使用 fake `ChatServicePort.stream_chat_events(...)` 产出 `approval_required`，断言 SSE data 包含审批字段且以 `[DONE]` 结束。
  - 验证无 `assistant_done` 和无误导性最终回复。
  - 覆盖 Property 8。
  - **验证: 需求 4.4, 4.5, 4.6, 9.8**

- [x] 7.7 修改 TUI 审批提示
  - 在 `epsilon-boot/src/application/cli/tui.py` 中为 `_EpsilonTextualApp._handle_event(...)` 新增 `event.kind == "approval_required"` 分支。
  - 新增 `_append_approval_required(self, event: AgentStreamEvent) -> None`，展示工具名、`_compact(arguments)`、允许决策、`session_id`、`approval_id` 和中文恢复接口提示。
  - 不实现 approve/edit/reject/respond 表单，不展示存储路径、Redis key、锁文件路径或异常堆栈。
  - _需求: 6.1, 6.2, 6.3, 6.4_

- [x] 7.8 编写 TUI 审批提示测试
  - 在 `epsilon-boot/test/application/cli/test_tui_hitl_approval.py` 中使用 fake runtime 产出 `AgentStreamEvent(kind="approval_required", metadata={...})`。
  - 断言 Textual UI 渲染工具名、允许决策、`session_id`、`approval_id` 和中文提示；断言不出现模拟物理路径或 Redis key。
  - 覆盖 Property 11。
  - **验证: 需求 6.1, 6.2, 6.3, 6.4**

- [x] 7.9 修改容器装配与配置文件
  - 在 `epsilon-boot/src/application/container_config.py` 中注册 `ApprovalPolicyPort`、`ApprovalStateStorePort`。
  - 实现 `_create_approval_policy() -> ApprovalPolicyPort`，使用 `hitl_config.enabled` 与 `hitl_config.interrupt_on`。
  - 实现 `_create_approval_state_store() -> ApprovalStateStorePort`，复用 `SESSION_STORE_BACKEND`：redis 后端创建 `RedisApprovalStateStore`，file 后端创建 `LocalFileApprovalStateStore` 并复用 `_local_persistence_root/_lock_factory/_path_policy/_atomic_writer`。
  - 修改 `_create_agent()` 注入 `approval_policy` 与 `approval_store`；修改 `_create_chat_service()` 注入 `approval_store`。
  - 在 `epsilon-boot/config.properties` 中新增 `HITL_ENABLED=false`、`HITL_INTERRUPT_ON=`、`HITL_STATE_TTL_SECONDS=3600` 及中文注释。
  - _需求: 1.3, 1.4, 2.8, 10.4_

- [x] 7.10 编写容器装配与配置测试
  - 在 `epsilon-boot/test/application/test_hitl_container_config_unit.py` 中覆盖 `SESSION_STORE_BACKEND=file` 创建 `LocalFileApprovalStateStore`、`SESSION_STORE_BACKEND=redis` 创建 `RedisApprovalStateStore`、`ReActAgentAdapter` 和 `ChatServiceAdapter` 依赖可解析。
  - 在 `epsilon-boot/test/application/test_hitl_config_properties_file_unit.py` 中验证 `config.properties` 包含新增 HITL 配置键和中文注释。
  - 覆盖 Property 1、Property 14。
  - **验证: 需求 1.3, 1.4, 2.8, 10.4**

- [x] 8. 检查点 — 应用层接口验证
  - 在 `epsilon-boot/` 目录运行 `uv run --frozen pytest test`。
  - 全部测试必须通过；如 HTTP 路由测试发现兼容导入路径问题，优先修正测试加载当前 `application/api/routers/chat.py` 路径，不新增旧 router。

### 9. 文档、全量验证与实施交接

- [x] 9.1 更新 Agent、API 与工具文档
  - 在 `docs/agent.md` 中说明 HITL 位于 ReAct Loop 的 assistant `tool_calls` 之后、工具执行之前；说明中断/恢复上下文快照规则。
  - 在 `docs/api.md` 中说明 `/api/chat` 的 `completed/approval_required` 状态联合、SSE `approval_required` 事件、`POST /api/chat/sessions/{session_id}/approvals/{approval_id}/resume` 请求/响应和 400/404/409 错误。
  - 在 `docs/tools.md` 中列出默认敏感工具、低风险工具、`http_request` edit 策略、`respond` 默认关闭。
  - 文档明确本项目借鉴 LangChain Deep Agents 的 `interrupt_on` / decision / checkpointer 语义，但不依赖 Deep Agents 执行图；明确 v1/v2 边界和安全边界。
  - _需求: 8.6, 10.1, 10.2, 10.3, 10.5, 10.6, 10.7_

- [x] 9.2 编写文档静态检查测试
  - 在 `epsilon-boot/test/application/test_hitl_docs_static.py` 中读取 `../docs/agent.md`、`../docs/api.md`、`../docs/tools.md` 和 `config.properties`。
  - 断言文档包含 `HITL_ENABLED`、`approval_required`、resume 路径、默认敏感工具列表、LangChain Deep Agents 关系、v1/v2 边界、安全边界。
  - 覆盖 Property 12。
  - **验证: 需求 8.6, 10.1, 10.2, 10.3, 10.4, 10.5, 10.6, 10.7**

- [x] 10. 检查点 — 最终全量验证
  - 在 `epsilon-boot/` 目录运行 `uv run --frozen pytest test`。
  - 若失败，先定位是新功能缺陷、测试假设不匹配还是环境依赖问题；不得把任务标记完成直到全量测试通过或明确记录无法运行的外部阻塞。
  - 确认 `docs/spec/human-in-the-loop/tasks.md` 中所有已实现任务仍由 evaluator 审核后再勾选；未通过 evaluator 前不得自行把实现任务标记为完成。

## 备注

- 本功能不需要 DDL、ORM PO、数据迁移或 backfill 脚本；审批状态使用 file/redis 键值持久化。
- 不创建 `manifest.json`，除非用户后续显式要求 manifest 模式。
- 任务 5.x 涉及 `ReActAgentAdapter` 核心循环，实施时应优先保持现有非 HITL 测试通过，再逐步打开审批分支。
