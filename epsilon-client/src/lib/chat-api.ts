/**
 * 聊天 API 请求封装模块。
 *
 * 提供与 FastAPI 后端 /api/chat、/api/task 端点的通信能力，
 * 包括同步请求、SSE 流式请求、继续请求和会话清除。
 */

import {
  runSnapshotSchema,
  taskExecuteResponseSchema,
} from "./chat-api.schema";

/** 聊天请求参数 */
export interface ChatRequest {
  session_id: string;
  message: string;
  stream?: boolean;
  model?: string;
}

/** Agent 终止原因 */
export type TerminationReason =
  | "completed"
  | "max_rounds"
  | "token_budget_exceeded"
  | "guardrail_blocked";

/** 分段停止原因 */
export type SegmentStopReason =
  | "completed"
  | "auto_disabled"
  | "approval_required"
  | "max_continuations_reached"
  | "total_token_budget_reached"
  | "total_duration_budget_reached"
  | "consecutive_paused_limit"
  | "no_progress"
  | "repeated_tool_call"
  | "tool_boundary_unavailable"
  | "continue_precondition_failed"
  | "risk_gate_required";

/** 分段预算用量 */
export interface BudgetUsage {
  segment_count: number;
  continuation_count: number;
  total_tokens: number;
  elapsed_ms: number;
  consecutive_paused_count: number;
  no_progress_count: number;
  repeated_tool_call_count: number;
}

/** 分段运行元数据 */
export interface SegmentMetadata {
  segment_index: number;
  segment_count: number;
  auto_continue_attempted: boolean;
  segment_stop_reason: SegmentStopReason;
  budget_usage: BudgetUsage;
}

/** 聊天响应状态 */
export type ChatResponseStatus = "completed" | "paused" | "approval_required";

/** 模型信息 */
export interface ModelInfo {
  id: string;
  object: string;
  created: number;
  owned_by: string;
  providers: string[];
}

/** 同步聊天响应 */
export interface ChatResponse {
  code: number;
  session_id: string;
  reply: string;
  model: string;
  usage: Record<string, number>;
  prompt_id?: string;
  status?: ChatResponseStatus;
  terminated_reason?: TerminationReason;
  can_continue?: boolean;
  segment_index?: number;
  segment_count?: number;
  auto_continue_attempted?: boolean;
  segment_stop_reason?: SegmentStopReason;
  budget_usage?: BudgetUsage;
  trace_id?: string;
  trace_ref?: Record<string, unknown>;
  artifact_ids?: string[];
  artifact_ref?: Record<string, unknown>;
}

/** 任务执行请求参数 */
export interface TaskExecuteRequest {
  goal: string;
  input_data?: Record<string, unknown>;
  constraints?: string[];
  output_format?: string;
  model?: string;
  session_id?: string;
}

/** 任务执行轨迹条目 */
export interface TaskTraceEntry {
  step: number;
  action: string;
  detail: string;
  timestamp_ms: number;
}

/** 任务执行状态 */
export type TaskExecuteStatus =
  | "success"
  | "failed"
  | "paused"
  | "human_intervention_required";

/** 任务执行响应 */
export interface TaskExecuteResponse {
  code: number;
  content: string;
  status: TaskExecuteStatus;
  model: string;
  usage: Record<string, number>;
  trace: TaskTraceEntry[];
  latency_ms: number;
  prompt_id: string;
  terminated_reason: TerminationReason;
  can_continue: boolean;
  segment_index: number;
  segment_count: number;
  auto_continue_attempted: boolean;
  segment_stop_reason: SegmentStopReason;
  budget_usage: BudgetUsage;
  trace_id: string | null;
  trace_ref: Record<string, unknown> | null;
  artifact_ids: string[];
  artifact_ref: Record<string, unknown> | null;
}

/** 后台 Run 状态 */
export type RunStatus =
  | "queued"
  | "running"
  | "paused"
  | "awaiting_approval"
  | "cancel_requested"
  | "cancelled"
  | "succeeded"
  | "failed"
  | "lost";

/** 后台 Run 类型 */
export type RunKind = "chat" | "task";

/** 工作流运行状态摘要 */
export interface WorkflowRunState {
  workflow_name?: string;
  current_phase?: string;
  active_role?: string | null;
  handoff_state?: Record<string, unknown> | null;
  phase_history?: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

/** 多 Agent 协作摘要 */
export interface CollaborationSummary {
  latest_steps?: Array<Record<string, unknown>>;
  /** 历史快照兼容字段，仅展示层 fallback 读取，不再作为规范写路径。 */
  recent_steps?: Array<Record<string, unknown>>;
  child_links?: Array<Record<string, unknown>>;
  delegation_count?: number;
  handoff_count?: number;
  max_depth_seen?: number;
  limit_hit_reason?: string | null;
  [key: string]: unknown;
}

/** 后台 Run 快照 */
export interface RunSnapshot {
  code: number;
  run_id: string;
  kind: RunKind;
  status: RunStatus;
  client_request_id: string | null;
  payload_hash: string | null;
  latest_event_cursor: number | null;
  result: Record<string, unknown> | null;
  error: Record<string, unknown> | null;
  approval_id: string | null;
  can_continue: boolean;
  terminal_reason: string | null;
  segment_metadata: Record<string, unknown> | null;
  latest_checkpoint_id: string | null;
  recoverable: boolean;
  recovery_attempt_count: number;
  last_recovery_error: Record<string, unknown> | null;
  task_classification: string | null;
  guardrail_summary: Record<string, unknown> | null;
  workflow_name: string | null;
  workflow_run_state: WorkflowRunState | null;
  collaboration_summary: CollaborationSummary | null;
  created_at: string;
  updated_at: string;
  version: number;
}

/** 后台 Run 事件 */
export interface RunEvent {
  run_id: string;
  cursor: number;
  event_type: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export type ApprovalDecisionType = "approve" | "edit" | "reject";

export interface ApprovalActionRequest {
  tool_call_id: string;
  tool_name: string;
  arguments: string;
  allowed_decisions: ApprovalDecisionType[];
  reason?: string;
}

export interface ApprovalDecisionRequest {
  type: ApprovalDecisionType;
  tool_call_id: string;
  edited_action?: {
    name: string;
    arguments: string;
  };
  message?: string;
}

/** 后台 Run 创建请求 */
export interface RunCreateRequest {
  kind: RunKind;
  client_request_id?: string;
  workflow_name?: string;
  chat?: {
    session_id: string;
    message: string;
    model?: string;
  };
  task?: {
    goal: string;
    input_data?: Record<string, unknown>;
    constraints?: string[];
    output_format?: string;
    model?: string;
    session_id?: string;
  };
  model?: string;
  created_by?: string;
}

/** Run 事件轮询响应 */
export interface RunEventsResponse {
  code: number;
  events: RunEvent[];
  latest_cursor: number | null;
}

/** Run 事件流控制事件 */
export interface RunReplayExpiredEvent {
  run_id: string;
  cursor: number | null;
  after_cursor: number | null;
  message: string;
  fallback: "polling";
}

/** SSE 流式分片数据 */
export interface StreamChunk {
  event_type?:
    | "assistant_delta"
    | "assistant_done"
    | "segment_done"
    | "tool_start"
    | "tool_result"
    | "tool_error";
  delta_content: string;
  finished: boolean;
  status?: ChatResponseStatus;
  terminated_reason?: TerminationReason;
  can_continue?: boolean;
  segment_index?: number;
  segment_count?: number;
  auto_continue_attempted?: boolean;
  segment_stop_reason?: SegmentStopReason;
  budget_usage?: BudgetUsage;
}

async function readJsonOrThrow<T>(
  res: Response,
  fallbackMessage: string,
  parse?: (json: unknown) => T,
): Promise<T> {
  if (!res.ok) {
    let message = `${fallbackMessage}: ${res.status} ${res.statusText}`;
    try {
      const json = await res.json();
      if (json?.message) {
        message = json.message as string;
      }
    } catch {
      // 保持默认错误信息
    }
    throw new Error(message);
  }

  const json = await res.json();
  return parse ? parse(json) : (json as T);
}

async function readStream(
  res: Response,
  onChunk: (chunk: StreamChunk) => void,
  onDone: () => void,
): Promise<void> {
  if (!res.ok) {
    throw new Error(`请求失败: ${res.status} ${res.statusText}`);
  }

  const reader = res.body?.getReader();
  if (!reader) {
    throw new Error("无法获取响应流");
  }

  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    buffer = lines.pop() || "";

    for (const line of lines) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith(":")) continue;

      if (trimmed.startsWith("data:")) {
        const data = trimmed.slice(5).trim();
        if (data === "[DONE]") {
          onDone();
          return;
        }
        try {
          const parsed = JSON.parse(data) as Partial<StreamChunk>;
          if (typeof parsed.finished === "boolean") {
            onChunk({
              delta_content: parsed.delta_content ?? "",
              finished: parsed.finished,
              status: parsed.status,
              terminated_reason: parsed.terminated_reason,
              can_continue: parsed.can_continue,
              event_type: parsed.event_type,
              segment_index: parsed.segment_index,
              segment_count: parsed.segment_count,
              auto_continue_attempted: parsed.auto_continue_attempted,
              segment_stop_reason: parsed.segment_stop_reason,
              budget_usage: parsed.budget_usage,
            });
          }
        } catch {
          // 忽略无法解析的行
        }
      }
    }
  }

  onDone();
}

/** 发送流式聊天请求，通过 SSE 接收增量响应。 */
export function streamChat(
  request: ChatRequest,
  onChunk: (chunk: StreamChunk) => void,
  onDone: () => void,
  onError: (error: Error) => void,
): AbortController {
  const controller = new AbortController();

  void (async () => {
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ...request, stream: true }),
        signal: controller.signal,
      });
      await readStream(res, onChunk, onDone);
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        onError(err as Error);
      }
    }
  })();

  return controller;
}

/** 基于已有会话继续流式聊天。 */
export function streamContinueChat(
  sessionId: string,
  model: string | undefined,
  onChunk: (chunk: StreamChunk) => void,
  onDone: () => void,
  onError: (error: Error) => void,
): AbortController {
  const controller = new AbortController();

  void (async () => {
    try {
      const res = await fetch(`/api/chat/sessions/${sessionId}/continue`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ stream: true, model }),
        signal: controller.signal,
      });
      await readStream(res, onChunk, onDone);
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        onError(err as Error);
      }
    }
  })();

  return controller;
}

/** 清除指定会话的对话历史。 */
export async function clearSession(sessionId: string): Promise<void> {
  const res = await fetch(`/api/chat/sessions/${sessionId}`, {
    method: "DELETE",
  });
  if (!res.ok) {
    throw new Error(`清除会话失败: ${res.status}`);
  }
}

/** 获取可用模型列表。 */
export async function fetchModels(): Promise<ModelInfo[]> {
  const res = await fetch("/v1/models");
  if (!res.ok) {
    throw new Error(`获取模型列表失败: ${res.status}`);
  }
  const json = await res.json();
  return json.data ?? [];
}

/** 执行结构化任务。 */
export async function executeTask(
  request: TaskExecuteRequest,
): Promise<TaskExecuteResponse> {
  const res = await fetch("/api/task/execute", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });

  return readJsonOrThrow(
    res,
    "任务执行失败",
    (json) => taskExecuteResponseSchema.parse(json),
  );
}

/** 创建后台 Run。 */
export async function createRun(
  request: RunCreateRequest,
): Promise<RunSnapshot> {
  const res = await fetch("/api/runs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request),
  });

  return readJsonOrThrow(res, "创建后台运行失败", (json) => runSnapshotSchema.parse(json));
}

/** 查询后台 Run 快照。 */
export async function fetchRun(runId: string): Promise<RunSnapshot> {
  const res = await fetch(`/api/runs/${encodeURIComponent(runId)}`);
  return readJsonOrThrow(res, "查询后台运行失败", (json) => runSnapshotSchema.parse(json));
}

/** 轮询查询后台 Run 事件。 */
export async function fetchRunEvents(
  runId: string,
  afterCursor?: number | null,
  limit = 100,
): Promise<RunEventsResponse> {
  const params = new URLSearchParams({ limit: String(limit) });
  if (typeof afterCursor === "number") {
    params.set("after_cursor", String(afterCursor));
  }
  const res = await fetch(
    `/api/runs/${encodeURIComponent(runId)}/events?${params.toString()}`,
  );
  return readJsonOrThrow<RunEventsResponse>(res, "查询后台运行事件失败");
}

/** 订阅后台 Run 事件流。 */
export function streamRunEvents(
  runId: string,
  afterCursor: number | null | undefined,
  onEvent: (event: RunEvent) => void,
  onReplayExpired: (event: RunReplayExpiredEvent) => void,
  onDone: () => void,
  onError: (error: Error) => void,
): AbortController {
  const controller = new AbortController();

  void (async () => {
    const params = new URLSearchParams();
    if (typeof afterCursor === "number") {
      params.set("after_cursor", String(afterCursor));
    }
    const suffix = params.toString() ? `?${params.toString()}` : "";
    try {
      const res = await fetch(
        `/api/runs/${encodeURIComponent(runId)}/events/stream${suffix}`,
        { signal: controller.signal },
      );
      if (!res.ok) {
        throw new Error(`后台运行事件流失败: ${res.status} ${res.statusText}`);
      }

      const reader = res.body?.getReader();
      if (!reader) {
        throw new Error("无法获取后台运行事件流");
      }
      const decoder = new TextDecoder();
      let buffer = "";
      let currentEvent = "message";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const rawLine of lines) {
          const line = rawLine.trim();
          if (!line || line.startsWith(":")) continue;
          if (line.startsWith("event:")) {
            currentEvent = line.slice(6).trim();
            continue;
          }
          if (!line.startsWith("data:")) continue;

          const data = line.slice(5).trim();
          try {
            const parsed = JSON.parse(data);
            if (currentEvent === "replay_expired") {
              onReplayExpired(parsed as RunReplayExpiredEvent);
            } else if (currentEvent === "error") {
              throw new Error(String(parsed?.message ?? "后台运行事件流错误"));
            } else {
              onEvent(parsed as RunEvent);
            }
          } finally {
            currentEvent = "message";
          }
        }
      }
      onDone();
    } catch (err) {
      if ((err as Error).name !== "AbortError") {
        onError(err as Error);
      }
    }
  })();

  return controller;
}

/** 请求取消后台 Run。 */
export async function cancelRun(runId: string): Promise<RunSnapshot> {
  const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
  });
  return readJsonOrThrow(res, "取消后台运行失败", (json) => runSnapshotSchema.parse(json));
}

/** 继续 paused 后台 Run。 */
export async function continueRun(
  runId: string,
  model?: string,
): Promise<RunSnapshot> {
  const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/continue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
  return readJsonOrThrow(res, "继续后台运行失败", (json) => runSnapshotSchema.parse(json));
}

/** 提交后台 Run HITL 审批决策。 */
export async function approveRun(
  runId: string,
  decisions: ApprovalDecisionRequest[],
  model?: string,
): Promise<RunSnapshot> {
  const res = await fetch(`/api/runs/${encodeURIComponent(runId)}/approve`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decisions, model }),
  });
  return readJsonOrThrow(res, "提交后台运行审批失败", (json) =>
    runSnapshotSchema.parse(json),
  );
}

/** 基于已有任务会话继续执行。 */
export async function continueTask(
  sessionId: string,
  model?: string,
): Promise<TaskExecuteResponse> {
  const res = await fetch(`/api/task/sessions/${sessionId}/continue`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });

  return readJsonOrThrow(
    res,
    "任务继续失败",
    (json) => taskExecuteResponseSchema.parse(json),
  );
}
