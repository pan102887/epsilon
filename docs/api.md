# API 接口

## 端点列表

| 方法 | 路径 | 用途 |
|---|---|---|
| `POST` | `/api/chat` | 与 Agent 对话（`stream=true` 使用 SSE，否则返回 JSON） |
| `POST` | `/api/chat/sessions/{session_id}/approvals/{approval_id}/resume` | 提交 HITL 审批决策并恢复 Agent 执行 |
| `POST` | `/api/chat/sessions/{session_id}/continue` | 基于已有聊天会话继续 paused/可继续的分段对话 |
| `DELETE` | `/api/chat/sessions/{session_id}` | 清除指定会话的对话历史 |
| `POST` | `/api/task/execute` | 通过任务型 Agent 执行任务，返回最终结果、trace 与 token 用量 |
| `POST` | `/api/task/sessions/{session_id}/continue` | 基于已有任务会话继续 paused task |
| `POST` | `/api/runs` | 创建后台 Run，支持 `kind=chat` 或 `kind=task` |
| `GET` | `/api/runs/{run_id}` | 查询后台 Run 快照 |
| `GET` | `/api/runs/{run_id}/events` | 轮询查询后台 Run 事件，支持 `after_cursor` 与 `limit` |
| `GET` | `/api/runs/{run_id}/events/stream` | SSE 订阅后台 Run 事件；replay 过期时发送 `replay_expired` 控制事件 |
| `POST` | `/api/runs/{run_id}/cancel` | 请求取消后台 Run |
| `POST` | `/api/runs/{run_id}/continue` | 继续 paused 且 `can_continue=true` 的后台 Run |
| `POST` | `/api/runs/{run_id}/approve` | 提交 awaiting_approval Run 的审批决策并恢复 |
| `GET` | `/api/traces` | 按时间倒序列出最近的结构化 Agent trace 摘要 |
| `GET` | `/api/traces/{session_id}` | 查询指定 session 的完整结构化 Agent trace |
| `GET` | `/api/artifacts/{session_id}` | 查询指定 session 的 artifact 摘要列表 |
| `GET` | `/v1/models` | 列出已注册可用模型（OpenAI `/v1/models` 兼容 + `providers` 扩展字段） |
| `GET` | `/health.json` | 存活探针，恒为 `{"status": "UP"}` |
| `GET` | `/readiness` | 就绪探针，按实际装配的异步资源动态聚合（默认 `file` 后端检查 `local_persistence`；显式 `redis` 后端检查 Redis；MySQL 仅未来恢复 `database` 资源时出现） |
| `GET` | `/prometheus` | Prometheus 指标 |
| `GET` | `/favicon.ico` | 测试/辅助：返回 `resource/images/icon.jpg` |
| `GET` | `/api/test/get` | 测试/辅助：返回 `{"message": "hello fastapi!!"}` |
| `GET` | `/resource/*` | 测试/辅助：静态文件挂载点，来源为后端根目录下的 `resource/` |

## 路由文件位置

真实 HTTP adapter 位于 `epsilon-boot/src/application/api/routers/`，兼容导出位于 `epsilon-boot/src/application/routers/`。当前包括：`artifacts.py`、`chat.py`、`health.py`、`models.py`、`runs.py`、`task.py`、`test_router.py`（辅助/静态挂载）和 `traces.py`。

## API presenter 边界

HTTP 请求/响应 DTO 仍由 `application/api/routers/` 内的 Pydantic 模型表达；领域层不感知 Pydantic 或 HTTP body。轻量响应映射归属 `application/api/presenters/`：

- `health.py` 使用 `application.api.presenters.health_presenter.readiness_result_to_response_body(...)` 生成 `/readiness` JSON body，不再导入 `infrastructure.health.health_serialization`。
- `task.py` 使用 `application.api.presenters.task_presenter.segment_budget_usage_to_response_body(...)` 生成任务分段预算字段，不再导入 `infrastructure.agent.segment_serialization`。

`application/run/*` 曾存在的 application→infrastructure serializer 受控迁移例外已清理完成：Run 应用层现在依赖 `application/run/serialization_ports.py` 的序列化 Protocol，由组合根注入 `infrastructure/run/run_serialization_adapters.py`。`test/static/test_architecture_import_boundaries.py` 中 `APPLICATION_INFRASTRUCTURE_IMPORT_EXCEPTIONS == {}`，新增普通 application→infrastructure 导入会失败。

## Trace / Artifact 引用

`POST /api/chat` 与 `POST /api/task/execute` 的同步响应除最终文本外，还返回工作台资源引用：

- `trace_id` / `trace_ref`：以 session 为稳定 ID，指向 `GET /api/traces/{session_id}`。
- `artifact_ids` / `artifact_ref`：来自 `ArtifactStorePort.list_artifacts(session_id)` 的轻量引用；当前 artifact port 没有单条强 ID，列表项使用 `logical_path` 暴露。
- 任务请求未传 `session_id` 时，`trace_id`、`trace_ref`、`artifact_ref` 为 `null`，`artifact_ids=[]`。

`GET /api/artifacts/{session_id}` 返回：

```json
{
  "object": "list",
  "session_id": "session-1",
  "data": [
    {
      "kind": "artifact",
      "session_id": "session-1",
      "logical_path": "reports/result.md",
      "artifact_type": "file",
      "timestamp_epoch": 1780000000.0,
      "size_bytes": 123,
      "content_summary": "生成报告摘要",
      "source_tool": "write_file"
    }
  ]
}
```

Artifact 存储关闭时返回 `{"object":"list","data":[]}`。

## HITL 审批协议

`POST /api/chat` 同步响应使用状态联合：

- `status="completed"`：保留 `session_id`、`reply`、`model`、`usage`、`prompt_id`。
- `status="approval_required"`：返回 `session_id`、`approval_id`、`action_requests`、`prompt_id`、`model`、`usage`，`reply` 为空。

SSE `stream=true` 会在兼容旧 `data:` 分片的同时新增显式事件类型：

- `assistant_delta`：data 包含 `event_type="assistant_delta"`、`delta_content`、`finished=false`。
- `tool_start` / `tool_result` / `tool_error`：使用同名 SSE event，data 包含 `event_type`、`content` 与安全 metadata。
- `approval_required`：data 包含 `event_type="approval_required"`、`status/session_id/approval_id/action_requests`，随后发送 `[DONE]`，不会发送误导性的最终 assistant 完成事件。
- `assistant_done`：data 包含 `event_type="assistant_done"`、`finished=true` 与分段状态字段。

恢复端点：

```http
POST /api/chat/sessions/{session_id}/approvals/{approval_id}/resume
```

请求体：

```json
{
  "decisions": [
    {"type": "approve", "tool_call_id": "call-1"},
    {
      "type": "edit",
      "tool_call_id": "call-2",
      "edited_action": {"name": "http_request", "arguments": "{\"url\":\"https://example.com\"}"}
    },
    {"type": "reject", "tool_call_id": "call-3", "message": "不要执行"}
  ],
  "model": null
}
```

决策必须与 `action_requests` 顺序一一对应。数量不匹配、非法决策、`edit` 改工具名或参数非法返回 400；审批状态不存在返回 404；状态过期或已消费返回 409。

## 后台 Run 协议

创建 chat run：

```http
POST /api/runs
```

```json
{
  "kind": "chat",
  "client_request_id": "session-1:chat:001",
  "workflow_name": null,
  "created_by": "web",
  "chat": {
    "session_id": "session-1",
    "message": "整理当前会话并给出下一步计划",
    "model": "glm-4.7"
  }
}
```

创建 task run：

```json
{
  "kind": "task",
  "client_request_id": "session-1:task:001",
  "workflow_name": "code_change",
  "created_by": "web",
  "task": {
    "session_id": "session-1",
    "goal": "检查 docs 是否与当前实现一致",
    "input_data": {},
    "constraints": [],
    "model": "glm-4.7"
  }
}
```

创建请求支持的顶层字段包括 `kind`、`client_request_id`、`workflow_name`、`chat`、`task`、`model`、`created_by`。`workflow_name` 为空时由 `StaticWorkflowSelector` 按配置、payload 关键词和 task classification 选择；显式指定时要求命中已启用的静态 workflow，否则返回业务错误。顶层 `model` 会覆盖 `chat.model` 或 `task.model`，并写入 `RunPayload.model`。

响应主体是 `RunSnapshot`。除基础状态外，后端会直接透传 checkpoint、guardrail、workflow 与 collaboration 的 canonical 摘要字段；HTTP adapter 不重算策略判断：

```json
{
  "code": 0,
  "run_id": "run-...",
  "kind": "task",
  "status": "queued",
  "latest_event_cursor": 2,
  "can_continue": false,
  "approval_id": null,
  "segment_metadata": null,
  "result": null,
  "error": null,
  "latest_checkpoint_id": null,
  "recoverable": false,
  "recovery_attempt_count": 0,
  "last_recovery_error": null,
  "task_classification": null,
  "workflow_name": null,
  "workflow_run_state": null,
  "collaboration_summary": {"latest_steps": []},
  "guardrail_summary": null
}
```

状态取值：`queued`、`running`、`paused`、`awaiting_approval`、`cancel_requested`、`cancelled`、`succeeded`、`failed`、`lost`。

事件轮询：

```http
GET /api/runs/{run_id}/events?after_cursor=2&limit=100
```

SSE 事件：

```http
GET /api/runs/{run_id}/events/stream?after_cursor=2
```

每条 SSE `data` 是 JSON，包含 `cursor`。当客户端 cursor 早于事件保留窗口时，SSE 发送 `event: replay_expired`，客户端应改用 `GET /api/runs/{run_id}` 和事件轮询补快照。HTTP polling 场景下 replay 过期返回 409。

事件流除生命周期事件外，还会透传以下运行时事实：

- guardrail：`guardrail_evaluated`、`guardrail_blocked`，payload 包含 stage/action/reason/message/stats 等安全摘要。
- workflow：`workflow_selected`、`workflow_selection_skipped`、`workflow_phase_started`、`workflow_phase_completed`、`workflow_phase_failed`、`workflow_handoff_recorded`、`role_capability_rejected`。
- collaboration：`collaboration_step_recorded`、`collaboration_limit_hit`，摘要字段以 `latest_steps` 为规范 schema。
- child run：`child_run_linked`、`child_run_waiting`、`child_run_reconciled`。
- checkpoint/recovery：`checkpoint_saved`、`run_recovery_queued`、`run_recovery_failed`、`tool_result_replayed` 等事件用于解释恢复过程。

`POST /api/runs/{run_id}/approve` 复用 Run 级审批恢复：Chat Run 和 Task Run 均由 `RunApprovalResumer` 分派到对应 `resume_approval(...)`，guardrail `require_approval` 也走同一 HITL 链路。恢复后若再次命中审批，同一 Run 会重新进入 `awaiting_approval` 并生成新的 `approval_id`。

错误码映射：

- 400：payload 校验失败。
- 404：Run 不存在。
- 409：幂等冲突、不可继续、不可取消、事件 replay 过期。
- 429：队列或运行容量达到上限。
