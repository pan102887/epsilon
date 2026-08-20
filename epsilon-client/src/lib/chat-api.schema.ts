/**
 * 聊天 API 运行时契约 schema 模块。
 *
 * 负责在前端边界处对高风险响应体做 fail-fast 校验，
 * 当前覆盖后台 Run 快照与任务执行响应，避免仅靠 TypeScript 断言
 * 无法发现的运行时字段漂移进入 UI 状态层。
 */

import { z } from "zod";

const unknownRecordSchema = z.object({}).catchall(z.unknown());

export const terminationReasonSchema = z.enum([
  "completed",
  "max_rounds",
  "token_budget_exceeded",
  "guardrail_blocked",
]);

export const segmentStopReasonSchema = z.enum([
  "completed",
  "auto_disabled",
  "approval_required",
  "max_continuations_reached",
  "total_token_budget_reached",
  "total_duration_budget_reached",
  "consecutive_paused_limit",
  "no_progress",
  "repeated_tool_call",
  "tool_boundary_unavailable",
  "continue_precondition_failed",
  "risk_gate_required",
]);

export const budgetUsageSchema = z.object({
  segment_count: z.number(),
  continuation_count: z.number(),
  total_tokens: z.number(),
  elapsed_ms: z.number(),
  consecutive_paused_count: z.number(),
  no_progress_count: z.number(),
  repeated_tool_call_count: z.number(),
});

export const taskTraceEntrySchema = z.object({
  step: z.number(),
  action: z.string(),
  detail: z.string(),
  timestamp_ms: z.number(),
});

export const taskExecuteStatusSchema = z.enum([
  "success",
  "failed",
  "paused",
  "human_intervention_required",
]);

export const taskExecuteResponseSchema = z.object({
  code: z.number(),
  content: z.string(),
  status: taskExecuteStatusSchema,
  model: z.string(),
  usage: z.record(z.string(), z.number()),
  trace: z.array(taskTraceEntrySchema),
  latency_ms: z.number(),
  prompt_id: z.string(),
  terminated_reason: terminationReasonSchema,
  can_continue: z.boolean(),
  segment_index: z.number(),
  segment_count: z.number(),
  auto_continue_attempted: z.boolean(),
  segment_stop_reason: segmentStopReasonSchema,
  budget_usage: budgetUsageSchema,
  trace_id: z.string().nullable().default(null),
  trace_ref: unknownRecordSchema.nullable().default(null),
  artifact_ids: z.array(z.string()).default([]),
  artifact_ref: unknownRecordSchema.nullable().default(null),
});

export const runStatusSchema = z.enum([
  "queued",
  "running",
  "paused",
  "awaiting_approval",
  "cancel_requested",
  "cancelled",
  "succeeded",
  "failed",
  "lost",
]);

const workflowRunStateSchema = unknownRecordSchema;

const collaborationSummarySchema = z
  .object({
    latest_steps: z.array(unknownRecordSchema).optional(),
    recent_steps: z.array(unknownRecordSchema).optional(),
    child_links: z.array(unknownRecordSchema).optional(),
    delegation_count: z.number().optional(),
    handoff_count: z.number().optional(),
    max_depth_seen: z.number().optional(),
    limit_hit_reason: z.string().nullable().optional(),
  })
  .catchall(z.unknown());

export const runSnapshotSchema = z.object({
  code: z.number(),
  run_id: z.string(),
  kind: z.enum(["chat", "task"]),
  status: runStatusSchema,
  client_request_id: z.string().nullable(),
  payload_hash: z.string().nullable(),
  latest_event_cursor: z.number().nullable(),
  result: unknownRecordSchema.nullable(),
  error: unknownRecordSchema.nullable(),
  approval_id: z.string().nullable(),
  can_continue: z.boolean(),
  terminal_reason: z.string().nullable(),
  segment_metadata: unknownRecordSchema.nullable(),
  latest_checkpoint_id: z.string().nullable(),
  recoverable: z.boolean(),
  recovery_attempt_count: z.number(),
  last_recovery_error: unknownRecordSchema.nullable(),
  task_classification: z.string().nullable(),
  guardrail_summary: unknownRecordSchema.nullable(),
  workflow_name: z.string().nullable(),
  workflow_run_state: workflowRunStateSchema.nullable(),
  collaboration_summary: collaborationSummarySchema.nullable(),
  created_at: z.string(),
  updated_at: z.string(),
  version: z.number(),
});
