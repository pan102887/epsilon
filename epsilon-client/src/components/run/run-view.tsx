/**
 * 后台 Run 面板组件。
 */

"use client";

import { useMemo, useState } from "react";
import type {
  ApprovalActionRequest,
  ApprovalDecisionRequest,
  ApprovalDecisionType,
  RunEvent,
  RunSnapshot,
  RunStatus,
} from "@/lib/chat-api";
import { useRun } from "@/hooks/use-run";
import { RunEventList } from "./run-event-list";

interface RunViewProps {
  runId: string | null;
  selectedModel: string;
}

const STATUS_LABELS: Record<RunStatus, string> = {
  queued: "Queued",
  running: "Running",
  paused: "Paused",
  awaiting_approval: "Awaiting approval",
  cancel_requested: "Cancel requested",
  cancelled: "Cancelled",
  succeeded: "Succeeded",
  failed: "Failed",
  lost: "Lost",
};

function statusTone(status: RunStatus): string {
  if (status === "succeeded") return "bg-emerald-100 text-emerald-900";
  if (status === "failed" || status === "lost") return "bg-red-100 text-red-900";
  if (status === "cancelled" || status === "cancel_requested") {
    return "bg-orange-100 text-orange-900";
  }
  if (status === "paused" || status === "awaiting_approval") {
    return "bg-amber-100 text-amber-900";
  }
  return "bg-sky-100 text-sky-900";
}

function jsonSummary(value: Record<string, unknown> | null): string {
  if (!value) return "None";
  const text = JSON.stringify(value);
  return text.length > 280 ? `${text.slice(0, 280)}...` : text;
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function workflowName(snapshot: RunSnapshot): string {
  return snapshot.workflow_name ?? stringValue(snapshot.workflow_run_state?.workflow_name);
}

function workflowPhase(snapshot: RunSnapshot): string {
  return stringValue(snapshot.workflow_run_state?.current_phase);
}

function collaborationSteps(snapshot: RunSnapshot): Array<Record<string, unknown>> {
  const summary = snapshot.collaboration_summary;
  if (!summary) return [];
  if (Array.isArray(summary.latest_steps)) return summary.latest_steps;
  if (Array.isArray(summary.recent_steps)) return summary.recent_steps;
  return [];
}

function collaborationText(snapshot: RunSnapshot): string {
  const summary = snapshot.collaboration_summary;
  if (!summary) return "No collaboration summary";
  const steps = collaborationSteps(snapshot);
  if (steps.length > 0) {
    return steps
      .slice(-5)
      .map((item) => {
        const action = stringValue(item.action);
        const target = stringValue(item.target_agent);
        const result = stringValue(item.result_summary ?? item.task_summary);
        return [action, target, result].filter(Boolean).join(" / ");
      })
      .filter(Boolean)
      .join("\n");
  }
  return ["delegation_count", "handoff_count", "max_depth_seen", "limit_hit_reason"]
    .filter((key) => summary[key] !== undefined)
    .map((key) => `${key}: ${String(summary[key])}`)
    .join("\n") || "No collaboration summary";
}

function resultText(snapshot: RunSnapshot): string {
  if (snapshot.result) return jsonSummary(snapshot.result);
  if (snapshot.error) return jsonSummary(snapshot.error);
  return "Run 尚未产生终态结果。";
}

function recoveryText(snapshot: RunSnapshot): string {
  if (snapshot.last_recovery_error) return jsonSummary(snapshot.last_recovery_error);
  if (snapshot.recoverable) return "自动恢复条件已满足。";
  return "暂无恢复失败摘要。";
}

function compactStats(value: unknown): string {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return "None";
  }
  const stats = value as Record<string, unknown>;
  const keys = [
    "total_tokens",
    "prompt_tokens",
    "completion_tokens",
    "elapsed_ms",
    "context_growth_messages",
    "repeated_tool_call_count",
    "consecutive_failure_count",
    "total_model_calls",
    "total_tool_calls",
    "estimated_cost",
    "cost_available",
    "last_tool_name",
    "last_tool_risk_level",
    "last_tool_error",
  ];
  return keys
    .filter((key) => stats[key] !== undefined)
    .map((key) => `${key}: ${String(stats[key])}`)
    .join("\n") || "None";
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}

function normalizeActionRequest(value: unknown): ApprovalActionRequest | null {
  if (!isRecord(value)) return null;
  const toolCallId = stringValue(value.tool_call_id);
  const toolName = stringValue(value.tool_name);
  if (!toolCallId || !toolName) return null;
  const rawAllowed = Array.isArray(value.allowed_decisions)
    ? value.allowed_decisions
    : ["approve", "edit", "reject"];
  const allowed = rawAllowed.filter((item): item is ApprovalDecisionType =>
    item === "approve" || item === "edit" || item === "reject",
  );
  const args =
    typeof value.arguments === "string"
      ? value.arguments
      : JSON.stringify(value.arguments ?? {}, null, 2);
  return {
    tool_call_id: toolCallId,
    tool_name: toolName,
    arguments: args,
    allowed_decisions: allowed.length > 0 ? allowed : ["approve", "reject"],
    reason: stringValue(value.reason),
  };
}

function actionsFromPayload(payload: Record<string, unknown>): ApprovalActionRequest[] {
  const direct = Array.isArray(payload.action_requests) ? payload.action_requests : null;
  const nested = isRecord(payload.result) && Array.isArray(payload.result.action_requests)
    ? payload.result.action_requests
    : null;
  return (direct ?? nested ?? [])
    .map(normalizeActionRequest)
    .filter((item): item is ApprovalActionRequest => item !== null);
}

function pendingApprovalActions(
  snapshot: RunSnapshot,
  events: RunEvent[],
): ApprovalActionRequest[] {
  const event = [...events].reverse().find((item) => item.event_type === "approval_required");
  const fromEvent = event ? actionsFromPayload(event.payload) : [];
  if (fromEvent.length > 0) return fromEvent;
  return snapshot.result ? actionsFromPayload(snapshot.result) : [];
}

function guardrailText(snapshot: RunSnapshot): string {
  const summary = snapshot.guardrail_summary;
  if (!summary) return "No guardrail summary";
  return [
    `action: ${String(summary.action ?? "None")}`,
    `reason: ${String(summary.reason ?? "None")}`,
    `runtime_stats:\n${compactStats(summary.runtime_stats)}`,
  ].join("\n");
}

function workflowHandoffText(state: RunSnapshot["workflow_run_state"]): string {
  if (!state?.handoff_state || typeof state.handoff_state !== "object") return "None";
  const handoff = state.handoff_state as Record<string, unknown>;
  return [
    `status: ${String(handoff.status ?? "None")}`,
    `source_role: ${String(handoff.source_role ?? "None")}`,
    `target_role: ${String(handoff.target_role ?? "None")}`,
    `target_agent: ${String(handoff.target_agent ?? "None")}`,
    `reason: ${String(handoff.reason ?? "None")}`,
  ].join("\n");
}

function workflowStateText(snapshot: RunSnapshot): string {
  const state = snapshot.workflow_run_state;
  if (!state) return "No workflow state";
  return [
    `current_phase: ${String(state.current_phase ?? "None")}`,
    `workflow_name: ${String(state.workflow_name ?? "None")}`,
    `active_role: ${String(state.active_role ?? "None")}`,
    `handoff_state:\n${workflowHandoffText(state)}`,
    `state: ${jsonSummary(state)}`,
  ].join("\n");
}

export function RunView({ runId, selectedModel }: RunViewProps) {
  const {
    snapshot,
    events,
    isLoading,
    isWatching,
    error,
    replayExpired,
    refresh,
    continueRun,
    approveRun,
    cancelRun,
  } = useRun(runId);
  const [decisionDrafts, setDecisionDrafts] = useState<
    Record<string, { type: ApprovalDecisionType; arguments: string }>
  >({});

  const active = snapshot?.status === "queued" || snapshot?.status === "running";
  const terminal =
    snapshot?.status === "cancelled" ||
    snapshot?.status === "succeeded" ||
    snapshot?.status === "failed" ||
    snapshot?.status === "lost";
  const approvalActions = useMemo(
    () => (snapshot ? pendingApprovalActions(snapshot, events) : []),
    [events, snapshot],
  );

  function draftFor(action: ApprovalActionRequest) {
    return (
      decisionDrafts[action.tool_call_id] ?? {
        type: action.allowed_decisions[0] ?? "approve",
        arguments: action.arguments,
      }
    );
  }

  function updateDecision(
    toolCallId: string,
    patch: Partial<{ type: ApprovalDecisionType; arguments: string }>,
  ) {
    setDecisionDrafts((prev) => ({
      ...prev,
      [toolCallId]: {
        type: patch.type ?? prev[toolCallId]?.type ?? "approve",
        arguments: patch.arguments ?? prev[toolCallId]?.arguments ?? "{}",
      },
    }));
  }

  async function submitApproval() {
    const decisions: ApprovalDecisionRequest[] = approvalActions.map((action) => {
      const draft = draftFor(action);
      if (draft.type === "edit") {
        return {
          type: "edit",
          tool_call_id: action.tool_call_id,
          edited_action: {
            name: action.tool_name,
            arguments: draft.arguments,
          },
        };
      }
      return {
        type: draft.type,
        tool_call_id: action.tool_call_id,
      };
    });
    await approveRun(decisions, selectedModel || undefined);
  }

  return (
    <section className="panel-shell flex min-h-[420px] flex-col overflow-hidden rounded-[1.5rem]">
      <header className="border-b border-[color:var(--color-line)] px-5 py-5">
        <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
          <div>
            <p className="eyebrow">Background run</p>
            <h2 className="mt-2 font-[family:var(--font-display)] text-2xl leading-tight text-[var(--color-ink-strong)]">
              Run monitor
            </h2>
          </div>
          {snapshot && (
            <span
              className={`inline-flex w-fit rounded-full px-3 py-1 text-xs font-semibold ${statusTone(snapshot.status)}`}
            >
              {STATUS_LABELS[snapshot.status]}
            </span>
          )}
        </div>
      </header>

      <div className="flex flex-1 flex-col gap-4 px-5 py-5">
        {!runId && (
          <div className="glass-panel rounded-[1.4rem] px-4 py-6 text-sm leading-6 text-[color:var(--color-ink-soft)]">
            从聊天或任务区点击“后台运行”后，这里会显示 Run_ID、状态、事件和结果。
          </div>
        )}

        {runId && !snapshot && (
          <div className="glass-panel rounded-[1.4rem] px-4 py-6 text-sm text-[color:var(--color-ink-soft)]">
            {isLoading ? "正在加载后台运行..." : "未获取到 Run 快照。"}
          </div>
        )}

        {error && (
          <div
            className="rounded-[1.2rem] border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"
            role="alert"
          >
            {error}
          </div>
        )}

        {snapshot && (
          <>
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
              <div className="stat-card">
                <span className="stat-label">Run_ID</span>
                <span className="stat-value break-all text-sm">{snapshot.run_id}</span>
              </div>
              <div className="stat-card">
                <span className="stat-label">Run_Status</span>
                <span className="stat-value text-sm">{STATUS_LABELS[snapshot.status]}</span>
              </div>
              <div className="stat-card">
                <span className="stat-label">Kind</span>
                <span className="stat-value text-sm">{snapshot.kind}</span>
              </div>
              <div className="stat-card">
                <span className="stat-label">Cursor</span>
                <span className="stat-value text-sm">
                  {snapshot.latest_event_cursor ?? "None"}
                </span>
              </div>
              <div className="stat-card">
                <span className="stat-label">Checkpoint</span>
                <span className="stat-value break-all text-sm">
                  {snapshot.latest_checkpoint_id ?? "None"}
                </span>
              </div>
              <div className="stat-card">
                <span className="stat-label">Recoverable</span>
                <span className="stat-value text-sm">
                  {snapshot.recoverable ? "Yes" : "No"}
                </span>
              </div>
              <div className="stat-card">
                <span className="stat-label">Recovery_Attempts</span>
                <span className="stat-value text-sm">
                  {snapshot.recovery_attempt_count}
                </span>
              </div>
              <div className="stat-card">
                <span className="stat-label">Task_Class</span>
                <span className="stat-value text-sm">
                  {snapshot.task_classification ?? "None"}
                </span>
              </div>
              <div className="stat-card">
                <span className="stat-label">Workflow</span>
                <span className="stat-value break-all text-sm">
                  {workflowName(snapshot) || "None"}
                </span>
              </div>
              <div className="stat-card">
                <span className="stat-label">Workflow_Phase</span>
                <span className="stat-value text-sm">
                  {workflowPhase(snapshot) || "None"}
                </span>
              </div>
              <div className="stat-card">
                <span className="stat-label">Guardrail</span>
                <span className="stat-value text-sm">
                  {snapshot.guardrail_summary
                    ? `${String(snapshot.guardrail_summary.action ?? "observe")}:${String(snapshot.guardrail_summary.reason ?? "none")}`
                    : "None"}
                </span>
              </div>
              <div className="stat-card">
                <span className="stat-label">Updated</span>
                <span className="stat-value break-all text-xs">{snapshot.updated_at}</span>
              </div>
            </div>

            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => void refresh()}
                disabled={isLoading}
                className="inline-flex h-10 items-center justify-center rounded-full border border-[color:var(--color-line-strong)] bg-white/80 px-4 text-sm font-semibold text-[var(--color-ink-strong)] disabled:cursor-not-allowed disabled:opacity-50"
              >
                刷新
              </button>
              {snapshot.can_continue && snapshot.status === "paused" && (
                <button
                  type="button"
                  onClick={() => void continueRun(selectedModel || undefined)}
                  disabled={isLoading}
                  className="inline-flex h-10 items-center justify-center rounded-full bg-[linear-gradient(135deg,#0f766e,#1d4ed8)] px-4 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
                >
                  继续 Run
                </button>
              )}
              {active && (
                <button
                  type="button"
                  onClick={() => void cancelRun()}
                  disabled={isLoading}
                  className="inline-flex h-10 items-center justify-center rounded-full bg-orange-600 px-4 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
                >
                  取消 Run
                </button>
              )}
              {terminal && (
                <span className="inline-flex h-10 items-center rounded-full border border-[color:var(--color-line)] px-4 text-sm text-[color:var(--color-ink-soft)]">
                  终态操作已禁用
                </span>
              )}
              {isWatching && (
                <span className="inline-flex h-10 items-center rounded-full bg-sky-100 px-4 text-sm font-semibold text-sky-900">
                  SSE watching
                </span>
              )}
            </div>

            {snapshot.status === "awaiting_approval" && (
              <div className="rounded-[1.2rem] border border-amber-200 bg-amber-50 px-4 py-4 text-sm leading-6 text-amber-900">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="font-semibold">HITL approval</p>
                    <p className="text-xs text-amber-800">
                      {snapshot.approval_id ?? "pending approval"}
                    </p>
                  </div>
                  <button
                    type="button"
                    onClick={() => void submitApproval()}
                    disabled={isLoading || approvalActions.length === 0}
                    className="inline-flex h-9 items-center justify-center rounded-full bg-amber-700 px-4 text-xs font-semibold text-white disabled:cursor-not-allowed disabled:opacity-50"
                  >
                    提交审批
                  </button>
                </div>
                {approvalActions.length === 0 ? (
                  <p className="mt-3 text-xs text-amber-800">
                    当前事件中没有可提交的工具动作摘要，请刷新事件后重试。
                  </p>
                ) : (
                  <div className="mt-3 flex flex-col gap-3">
                    {approvalActions.map((action) => {
                      const draft = draftFor(action);
                      return (
                        <div
                          key={action.tool_call_id}
                          className="rounded-[1rem] border border-amber-200 bg-white/75 p-3"
                        >
                          <div className="flex flex-wrap items-start justify-between gap-3">
                            <div>
                              <p className="font-semibold text-amber-950">
                                {action.tool_name}
                              </p>
                              <p className="break-all text-xs text-amber-800">
                                {action.tool_call_id}
                              </p>
                            </div>
                            <select
                              value={draft.type}
                              onChange={(event) =>
                                updateDecision(action.tool_call_id, {
                                  type: event.target.value as ApprovalDecisionType,
                                  arguments: draft.arguments,
                                })
                              }
                              className="h-9 rounded-lg border border-amber-200 bg-white px-3 text-xs font-semibold text-amber-950"
                            >
                              {action.allowed_decisions.map((decision) => (
                                <option key={decision} value={decision}>
                                  {decision}
                                </option>
                              ))}
                            </select>
                          </div>
                          {action.reason && (
                            <p className="mt-2 text-xs text-amber-800">
                              {action.reason}
                            </p>
                          )}
                          <pre className="mt-2 max-h-28 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-amber-100/70 px-3 py-2 text-xs text-amber-950">
                            {action.arguments}
                          </pre>
                          {draft.type === "edit" && (
                            <textarea
                              value={draft.arguments}
                              onChange={(event) =>
                                updateDecision(action.tool_call_id, {
                                  type: "edit",
                                  arguments: event.target.value,
                                })
                              }
                              className="mt-2 min-h-24 w-full resize-y rounded-lg border border-amber-200 bg-white px-3 py-2 font-mono text-xs text-amber-950 outline-none focus:border-amber-500"
                            />
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            )}

            {replayExpired && (
              <div className="rounded-[1.2rem] border border-sky-200 bg-sky-50 px-4 py-3 text-sm leading-6 text-sky-900">
                事件历史已过期，已回退到 polling 快照刷新。
              </div>
            )}

            <div className="grid gap-4 xl:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
              <div className="glass-panel rounded-[1.4rem] p-4">
                <p className="eyebrow">Segment_Metadata</p>
                <pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-[1rem] bg-white/75 px-3 py-3 text-xs leading-5 text-[color:var(--color-ink-soft)]">
                  {jsonSummary(snapshot.segment_metadata)}
                </pre>
              </div>
              <div className="glass-panel rounded-[1.4rem] p-4">
                <p className="eyebrow">Terminal result</p>
                <pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap break-words rounded-[1rem] bg-white/75 px-3 py-3 text-xs leading-5 text-[color:var(--color-ink-soft)]">
                  {resultText(snapshot)}
                </pre>
              </div>
            </div>

            <div className="grid gap-4 xl:grid-cols-2">
              <div className="glass-panel rounded-[1.4rem] p-4">
                <p className="eyebrow">Workflow state</p>
                <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-[1rem] bg-white/75 px-3 py-3 text-xs leading-5 text-[color:var(--color-ink-soft)]">
                  {workflowStateText(snapshot)}
                </pre>
              </div>
              <div className="glass-panel rounded-[1.4rem] p-4">
                <p className="eyebrow">Latest collaboration</p>
                <pre className="mt-3 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-[1rem] bg-white/75 px-3 py-3 text-xs leading-5 text-[color:var(--color-ink-soft)]">
                  {collaborationText(snapshot)}
                </pre>
              </div>
            </div>

            <div className="grid gap-4 xl:grid-cols-2">
              <div className="glass-panel rounded-[1.4rem] p-4">
                <p className="eyebrow">Guardrail summary</p>
                <pre className="mt-3 max-h-36 overflow-auto whitespace-pre-wrap break-words rounded-[1rem] bg-white/75 px-3 py-3 text-xs leading-5 text-[color:var(--color-ink-soft)]">
                  {guardrailText(snapshot)}
                </pre>
              </div>
              <div className="glass-panel rounded-[1.4rem] p-4">
                <p className="eyebrow">Recovery status</p>
                <pre className="mt-3 max-h-36 overflow-auto whitespace-pre-wrap break-words rounded-[1rem] bg-white/75 px-3 py-3 text-xs leading-5 text-[color:var(--color-ink-soft)]">
                  {recoveryText(snapshot)}
                </pre>
              </div>
            </div>

            <div className="glass-panel rounded-[1.4rem] p-4">
              <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
                <p className="eyebrow">Run_Event log</p>
                <span className="text-xs text-[color:var(--color-ink-muted)]">
                  {events.length} events
                </span>
              </div>
              <RunEventList events={events} />
            </div>
          </>
        )}
      </div>
    </section>
  );
}
