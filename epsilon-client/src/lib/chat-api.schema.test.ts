/**
 * 聊天 API 运行时契约校验测试。
 *
 * 覆盖 RunSnapshot 与 TaskExecuteResponse 在边界处的关键 schema 行为，
 * 确保合法响应可通过，非法枚举值会被快速拒绝。
 */

import { describe, expect, it } from "vitest";
import {
  runSnapshotSchema,
  taskExecuteResponseSchema,
} from "./chat-api.schema";

describe("runSnapshotSchema", () => {
  it("accepts a valid run snapshot", () => {
    const parsed = runSnapshotSchema.parse({
      code: 0,
      run_id: "run-1",
      kind: "task",
      status: "running",
      client_request_id: null,
      payload_hash: null,
      latest_event_cursor: 1,
      result: null,
      error: null,
      approval_id: null,
      can_continue: false,
      terminal_reason: null,
      segment_metadata: null,
      latest_checkpoint_id: null,
      recoverable: false,
      recovery_attempt_count: 0,
      last_recovery_error: null,
      task_classification: null,
      guardrail_summary: null,
      workflow_name: "research",
      workflow_run_state: {
        workflow_name: "research",
        current_phase: "analysis",
        active_role: "planner",
        handoff_state: null,
        phase_history: [{ phase: "analysis" }],
      },
      collaboration_summary: {
        latest_steps: [{ role: "planner", action: "outline" }],
        recent_steps: [{ role: "planner", action: "outline" }],
        child_links: [{ run_id: "child-1" }],
        delegation_count: 1,
        handoff_count: 1,
        max_depth_seen: 2,
        limit_hit_reason: null,
      },
      created_at: "2026-06-16T00:00:00Z",
      updated_at: "2026-06-16T00:00:00Z",
      version: 1,
    });

    expect(parsed.run_id).toBe("run-1");
  });

  it("accepts opaque workflow run state records with producer-specific keys", () => {
    const parsed = runSnapshotSchema.parse({
      code: 0,
      run_id: "run-1",
      kind: "task",
      status: "running",
      client_request_id: null,
      payload_hash: null,
      latest_event_cursor: null,
      result: null,
      error: null,
      approval_id: null,
      can_continue: false,
      terminal_reason: null,
      segment_metadata: null,
      latest_checkpoint_id: null,
      recoverable: false,
      recovery_attempt_count: 0,
      last_recovery_error: null,
      task_classification: null,
      guardrail_summary: null,
      workflow_name: "research",
      workflow_run_state: {
        workflow_name: "research",
        current_phase: "analysis",
        phase_started_at: "2026-06-16T00:00:00Z",
        phase_result_summary: { content: "done" },
        phase_error_summary: null,
        revise_counts: { revise: 1 },
        phase_history: "legacy-redacted-history",
      },
      collaboration_summary: null,
      created_at: "2026-06-16T00:00:00Z",
      updated_at: "2026-06-16T00:00:00Z",
      version: 1,
    });

    expect(parsed.workflow_run_state?.phase_history).toBe(
      "legacy-redacted-history",
    );
  });

  it("rejects unknown run status", () => {
    expect(() =>
      runSnapshotSchema.parse({
        code: 0,
        run_id: "run-1",
        kind: "task",
        status: "unknown",
        client_request_id: null,
        payload_hash: null,
        latest_event_cursor: null,
        result: null,
        error: null,
        approval_id: null,
        can_continue: false,
        terminal_reason: null,
        segment_metadata: null,
        latest_checkpoint_id: null,
        recoverable: false,
        recovery_attempt_count: 0,
        last_recovery_error: null,
        task_classification: null,
        guardrail_summary: null,
        workflow_name: null,
        workflow_run_state: null,
        collaboration_summary: null,
        created_at: "2026-06-16T00:00:00Z",
        updated_at: "2026-06-16T00:00:00Z",
        version: 1,
      }),
    ).toThrow();
  });
});

describe("taskExecuteResponseSchema", () => {
  it("accepts a valid task execute response", () => {
    const parsed = taskExecuteResponseSchema.parse({
      code: 0,
      content: "done",
      status: "success",
      model: "gpt-4.1",
      usage: { prompt_tokens: 10, completion_tokens: 20 },
      trace: [
        {
          step: 1,
          action: "plan",
          detail: "created plan",
          timestamp_ms: 100,
        },
      ],
      latency_ms: 320,
      prompt_id: "prompt-1",
      terminated_reason: "completed",
      can_continue: false,
      segment_index: 1,
      segment_count: 1,
      auto_continue_attempted: false,
      segment_stop_reason: "completed",
      budget_usage: {
        segment_count: 1,
        continuation_count: 0,
        total_tokens: 30,
        elapsed_ms: 320,
        consecutive_paused_count: 0,
        no_progress_count: 0,
        repeated_tool_call_count: 0,
      },
      trace_id: "task-session-1",
      trace_ref: { available: true, trace_id: "task-session-1" },
      artifact_ids: ["reports/result.md"],
      artifact_ref: { available: true, session_id: "task-session-1" },
    });

    expect(parsed.status).toBe("success");
    expect(parsed.trace_id).toBe("task-session-1");
  });

  it("rejects unknown task response status", () => {
    expect(() =>
      taskExecuteResponseSchema.parse({
        code: 0,
        content: "done",
        status: "mystery",
        model: "gpt-4.1",
        usage: { prompt_tokens: 10, completion_tokens: 20 },
        trace: [],
        latency_ms: 320,
        prompt_id: "prompt-1",
        terminated_reason: "completed",
        can_continue: false,
        segment_index: 1,
        segment_count: 1,
        auto_continue_attempted: false,
        segment_stop_reason: "completed",
        budget_usage: {
          segment_count: 1,
          continuation_count: 0,
          total_tokens: 30,
          elapsed_ms: 320,
          consecutive_paused_count: 0,
          no_progress_count: 0,
          repeated_tool_call_count: 0,
        },
      }),
    ).toThrow();
  });
});
