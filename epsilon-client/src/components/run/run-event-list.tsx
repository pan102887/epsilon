/**
 * 后台 Run 事件列表组件。
 */

import type { RunEvent } from "@/lib/chat-api";

const EVENT_LABELS: Record<string, string> = {
  run_created: "Created",
  run_queued: "Queued",
  run_claimed: "Claimed",
  run_heartbeat: "Heartbeat",
  segment_started: "Segment started",
  segment_done: "Segment done",
  run_paused: "Paused",
  approval_required: "Approval required",
  cancel_requested: "Cancel requested",
  run_cancelled: "Cancelled",
  run_succeeded: "Succeeded",
  run_failed: "Failed",
  run_lost: "Lost",
  assistant_delta: "Assistant delta",
  assistant_done: "Assistant done",
  tool_start: "Tool start",
  tool_result: "Tool result",
  tool_error: "Tool error",
  replay_expired: "Replay expired",
  checkpoint_saved: "Checkpoint saved",
  run_recovery_queued: "Recovery queued",
  run_recovery_failed: "Recovery failed",
  tool_result_replayed: "Tool replayed",
  guardrail_evaluated: "Guardrail evaluated",
  guardrail_blocked: "Guardrail blocked",
  workflow_selected: "Workflow selected",
  workflow_selection_skipped: "Workflow skipped",
  workflow_phase_started: "Workflow phase started",
  workflow_phase_completed: "Workflow phase completed",
  workflow_phase_failed: "Workflow phase failed",
  workflow_handoff_recorded: "Workflow handoff",
  role_capability_rejected: "Role capability rejected",
  collaboration_step_recorded: "Collaboration step",
  collaboration_limit_hit: "Collaboration limit",
  child_run_linked: "Child run linked",
  child_run_waiting: "Child run waiting",
  child_run_reconciled: "Child run reconciled",
};

const EVENT_SAFE_FIELDS: Record<string, string[]> = {
  guardrail_evaluated: [
    "stage",
    "action",
    "reason",
    "mode",
    "segment_index",
    "round_num",
    "tool_name",
    "tool_risk_level",
    "approval_id",
    "source",
    "stats",
  ],
  guardrail_blocked: [
    "stage",
    "action",
    "reason",
    "mode",
    "segment_index",
    "round_num",
    "tool_name",
    "tool_risk_level",
    "approval_id",
    "source",
    "stats",
  ],
  tool_start: ["round_num", "tool_name", "tool_call_id", "arguments_summary"],
  tool_result: ["round_num", "tool_name", "tool_call_id", "result_summary", "latency_ms"],
  tool_error: ["round_num", "tool_name", "tool_call_id", "error_summary", "latency_ms"],
  workflow_handoff_recorded: [
    "workflow_name",
    "phase",
    "source_role",
    "target_role",
    "target_agent",
    "reason",
    "workflow_run_state",
  ],
  role_capability_rejected: [
    "workflow_name",
    "phase",
    "active_role",
    "action",
    "reason",
    "workflow_run_state",
  ],
  child_run_linked: [
    "parent_run_id",
    "child_run_id",
    "phase",
    "role",
    "reason",
    "ownership_status",
  ],
  child_run_waiting: [
    "parent_run_id",
    "child_run_id",
    "phase",
    "role",
    "reason",
    "ownership_status",
  ],
  child_run_reconciled: [
    "parent_run_id",
    "child_run_id",
    "phase",
    "role",
    "reason",
    "ownership_status",
  ],
};

const GENERIC_BLOCKED_FIELD_FRAGMENTS = [
  "argument",
  "content",
  "input",
  "message",
  "password",
  "prompt",
  "secret",
  "token",
  "api_key",
];

function eventLabel(eventType: string): string {
  return EVENT_LABELS[eventType] ?? eventType;
}

function isPlainRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function compactScalar(value: unknown): string {
  if (value === null || value === undefined || value === "") return "None";
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (typeof value === "string") {
    return value.length > 80 ? `${value.slice(0, 80)}...` : value;
  }
  if (Array.isArray(value)) return `${value.length} items`;
  return "[object]";
}

function runtimeStatsSummary(value: unknown): string {
  if (!isPlainRecord(value)) return compactScalar(value);
  const keys = [
    "total_tokens",
    "elapsed_ms",
    "context_growth_messages",
    "repeated_tool_call_count",
    "consecutive_failure_count",
    "estimated_cost",
    "cost_available",
  ];
  return keys
    .filter((key) => value[key] !== undefined)
    .map((key) => `${key}: ${compactScalar(value[key])}`)
    .join(", ") || "None";
}

function workflowStateSummary(value: unknown): string {
  if (!isPlainRecord(value)) return compactScalar(value);
  const handoffState = isPlainRecord(value.handoff_state) ? value.handoff_state : null;
  return [
    `current_phase: ${compactScalar(value.current_phase)}`,
    handoffState ? `handoff_status: ${compactScalar(handoffState.status)}` : "",
    handoffState ? `active_role: ${compactScalar(value.active_role)}` : "",
  ]
    .filter(Boolean)
    .join(", ");
}

function genericPayloadKeys(payload: Record<string, unknown>): string[] {
  return Object.keys(payload).filter((key) => {
    const normalized = key.toLowerCase();
    return !GENERIC_BLOCKED_FIELD_FRAGMENTS.some((fragment) =>
      normalized.includes(fragment),
    );
  });
}

function safePayloadEntries(event: RunEvent): string[] {
  const payload = event.payload;
  const keys = EVENT_SAFE_FIELDS[event.event_type] ?? genericPayloadKeys(payload).slice(0, 4);
  return keys
    .filter((key) => payload[key] !== undefined)
    .map((key) => {
      if (key === "stats") return `${key}: ${runtimeStatsSummary(payload[key])}`;
      if (key === "workflow_run_state") {
        return `${key}: ${workflowStateSummary(payload[key])}`;
      }
      return `${key}: ${compactScalar(payload[key])}`;
    });
}

function eventSummary(event: RunEvent): string {
  const keys = Object.keys(event.payload);
  if (keys.length === 0) return "No payload";
  const entries = safePayloadEntries(event);
  return entries.length > 0 ? entries.join(" · ") : "No safe payload summary";
}

export function RunEventList({ events }: { events: RunEvent[] }) {
  if (events.length === 0) {
    return (
      <div className="rounded-[1.2rem] border border-dashed border-[color:var(--color-line-strong)] px-4 py-5 text-sm text-[color:var(--color-ink-soft)]">
        暂无事件，Run 创建后会从最新 cursor 开始订阅。
      </div>
    );
  }

  return (
    <ol className="flex max-h-80 flex-col gap-2 overflow-y-auto pr-1">
      {events.map((event) => (
        <li
          key={`${event.run_id}-${event.cursor}`}
          className="rounded-[1.15rem] border border-[color:var(--color-line)] bg-white/70 px-3 py-3"
        >
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="text-xs font-semibold uppercase tracking-[0.18em] text-[color:var(--color-ink-muted)]">
              {eventLabel(event.event_type)}
            </span>
            <span className="font-[family:var(--font-display)] text-xs text-[color:var(--color-ink-soft)]">
              #{event.cursor}
            </span>
          </div>
          <p className="mt-2 break-words text-xs leading-5 text-[color:var(--color-ink-soft)]">
            {eventSummary(event)}
          </p>
        </li>
      ))}
    </ol>
  );
}
