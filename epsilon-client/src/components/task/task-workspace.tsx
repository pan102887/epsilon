/**
 * 任务执行工作区组件。
 *
 * 面向结构化任务执行场景，提交任务目标到后端并展示状态、结果和执行轨迹。
 */

"use client";

import { useCallback, useState } from "react";
import {
  createRun,
  continueTask,
  executeTask,
  type TaskExecuteResponse,
  type SegmentStopReason,
  type TaskTraceEntry,
  type TerminationReason,
} from "@/lib/chat-api";

interface TaskWorkspaceProps {
  /** 当前会话 ID */
  sessionId: string;
  /** 当前选中的模型 */
  selectedModel: string;
  /** 后台 Run 创建完成回调 */
  onRunCreated?: (runId: string) => void;
}

function formatLatency(latencyMs: number): string {
  if (latencyMs < 1000) {
    return `${Math.round(latencyMs)} ms`;
  }
  return `${(latencyMs / 1000).toFixed(2)} s`;
}

function formatTraceTime(timestampMs: number): string {
  return new Date(timestampMs).toLocaleTimeString("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

function reasonLabel(reason?: TerminationReason): string {
  if (reason === "max_rounds") return "达到单段轮次上限";
  if (reason === "token_budget_exceeded") return "达到单段 Token 预算";
  return "阶段边界";
}

function segmentReasonLabel(reason?: SegmentStopReason): string {
  if (reason === "completed") return "Completed";
  if (reason === "auto_disabled") return "Auto off";
  if (reason === "max_continuations_reached") return "Continuation limit";
  if (reason === "total_token_budget_reached") return "Token budget";
  if (reason === "total_duration_budget_reached") return "Time budget";
  if (reason === "no_progress") return "No progress";
  if (reason === "repeated_tool_call") return "Repeated tool";
  if (reason === "tool_boundary_unavailable") return "Tool boundary";
  if (reason === "approval_required") return "Approval";
  return "Stopped";
}

function TraceRow({ entry }: { entry: TaskTraceEntry }) {
  return (
    <li className="relative rounded-[1.35rem] border border-[color:var(--color-line)] bg-white/70 px-4 py-4 shadow-[0_14px_36px_rgba(15,23,42,0.06)]">
      <div className="flex flex-wrap items-center gap-2 text-xs uppercase tracking-[0.2em] text-[color:var(--color-ink-muted)]">
        <span>Step {entry.step}</span>
        <span className="rounded-full bg-[var(--color-surface-strong)] px-2 py-1 text-[10px] font-semibold text-[var(--color-ink-strong)]">
          {entry.action}
        </span>
        <span>{formatTraceTime(entry.timestamp_ms)}</span>
      </div>
      <p className="mt-3 text-sm leading-6 text-[var(--color-ink-strong)]">
        {entry.detail}
      </p>
    </li>
  );
}

/**
 * 任务执行工作区。
 *
 * @param sessionId - 当前页面共享的会话 ID
 * @param selectedModel - 当前选择的模型
 */
export function TaskWorkspace({
  sessionId,
  selectedModel,
  onRunCreated,
}: TaskWorkspaceProps) {
  const [goal, setGoal] = useState("");
  const [result, setResult] = useState<TaskExecuteResponse | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [isRunLoading, setIsRunLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = useCallback(async () => {
    const trimmedGoal = goal.trim();
    if (!trimmedGoal || isLoading) {
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const response = await executeTask({
        goal: trimmedGoal,
        session_id: sessionId,
        model: selectedModel || undefined,
      });
      setResult(response);
    } catch (submitError) {
      setError((submitError as Error).message);
    } finally {
      setIsLoading(false);
    }
  }, [goal, isLoading, selectedModel, sessionId]);

  const handleContinue = useCallback(async () => {
    if (!result?.can_continue || isLoading) {
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      const response = await continueTask(
        sessionId,
        selectedModel || undefined,
      );
      setResult(response);
    } catch (continueError) {
      setError((continueError as Error).message);
    } finally {
      setIsLoading(false);
    }
  }, [isLoading, result?.can_continue, selectedModel, sessionId]);

  const handleCreateRun = useCallback(async () => {
    const trimmedGoal = goal.trim();
    if (!trimmedGoal || isRunLoading) {
      return;
    }

    setIsRunLoading(true);
    setError(null);

    try {
      const snapshot = await createRun({
        kind: "task",
        client_request_id: `${sessionId}:task:${Date.now()}`,
        task: {
          goal: trimmedGoal,
          session_id: sessionId,
          model: selectedModel || undefined,
        },
      });
      onRunCreated?.(snapshot.run_id);
    } catch (runError) {
      setError((runError as Error).message);
    } finally {
      setIsRunLoading(false);
    }
  }, [goal, isRunLoading, onRunCreated, selectedModel, sessionId]);

  return (
    <section className="panel-shell flex min-h-[720px] flex-col overflow-hidden">
      <header className="border-b border-[color:var(--color-line)] px-5 py-5">
        <div className="space-y-2">
          <p className="eyebrow">Task execution</p>
          <div className="space-y-1">
            <h2 className="font-[family:var(--font-display)] text-3xl leading-none tracking-[-0.04em] text-[var(--color-ink-strong)]">
              Execution desk
            </h2>
            <p className="max-w-xl text-sm leading-6 text-[color:var(--color-ink-soft)]">
              Submit a concrete goal to the task agent and inspect the final
              answer, latency, token usage, and execution trace in one place.
            </p>
          </div>
        </div>
      </header>

      <div className="flex flex-1 flex-col gap-5 px-5 py-5">
        <div className="glass-panel rounded-[1.8rem] p-4 sm:p-5">
          <label
            htmlFor="task-goal"
            className="text-xs font-semibold uppercase tracking-[0.24em] text-[color:var(--color-ink-muted)]"
          >
            Task goal
          </label>
          <textarea
            id="task-goal"
            value={goal}
            onChange={(event) => setGoal(event.target.value)}
            placeholder="例如：总结当前会话上下文，给出 3 个下一步实施建议，并标注优先级。"
            className="mt-3 min-h-36 w-full resize-y rounded-[1.4rem] border border-[color:var(--color-line-strong)] bg-white/85 px-4 py-3 text-sm leading-6 text-[var(--color-ink-strong)] outline-none transition placeholder:text-[color:var(--color-ink-muted)] focus:border-[color:var(--color-accent)]"
            disabled={isLoading}
          />

          <div className="mt-4 flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex flex-wrap gap-2 text-xs text-[color:var(--color-ink-soft)]">
              <span className="rounded-full border border-[color:var(--color-line)] px-3 py-1.5">
                Model: {selectedModel || "Auto default"}
              </span>
            </div>

            <div className="flex flex-col gap-2 sm:flex-row">
              <button
                onClick={handleCreateRun}
                disabled={isRunLoading || isLoading || !goal.trim()}
                className="inline-flex h-12 items-center justify-center rounded-full border border-[color:var(--color-line-strong)] bg-white/85 px-5 text-sm font-medium text-[var(--color-ink-strong)] transition hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {isRunLoading ? "创建中..." : "后台运行"}
              </button>
              <button
                onClick={handleSubmit}
                disabled={isLoading || !goal.trim()}
                className="inline-flex h-12 items-center justify-center rounded-full bg-[linear-gradient(135deg,#0f766e,#1d4ed8)] px-5 text-sm font-medium text-white transition hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {isLoading ? "执行中..." : "运行任务"}
              </button>
            </div>
          </div>
        </div>

        {error && (
          <div
            className="rounded-[1.4rem] border border-red-200/80 bg-red-50/90 px-4 py-3 text-sm text-red-700"
            role="alert"
          >
            {error}
          </div>
        )}

        {!result ? (
          <div className="glass-panel flex flex-1 flex-col items-center justify-center rounded-[1.9rem] px-6 py-10 text-center">
            <p className="eyebrow justify-center">Run preview</p>
            <p className="mt-4 font-[family:var(--font-display)] text-3xl leading-none tracking-[-0.04em] text-[var(--color-ink-strong)]">
              结果和执行轨迹会显示在这里
            </p>
            <p className="mt-3 max-w-md text-sm leading-6 text-[color:var(--color-ink-soft)]">
              当前任务接口是请求响应模式。提交后会返回完整结果和轨迹，
              不会伪装成实时流式步骤面板。
            </p>
          </div>
        ) : (
          <div className="flex flex-1 flex-col gap-5">
            <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-5">
              <div className="stat-card">
                <span className="stat-label">Status</span>
                <span className="stat-value capitalize">{result.status}</span>
                {result.status === "paused" && (
                  <span className="mt-2 inline-flex w-fit rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-900">
                    {reasonLabel(result.terminated_reason)}
                  </span>
                )}
              </div>
              <div className="stat-card">
                <span className="stat-label">Latency</span>
                <span className="stat-value">
                  {formatLatency(result.latency_ms)}
                </span>
              </div>
              <div className="stat-card">
                <span className="stat-label">Model</span>
                <span className="stat-value">{result.model}</span>
              </div>
              <div className="stat-card">
                <span className="stat-label">Trace steps</span>
                <span className="stat-value">{result.trace.length}</span>
              </div>
              <div className="stat-card">
                <span className="stat-label">Segments</span>
                <span className="stat-value">
                  {result.segment_index}/{result.segment_count}
                </span>
                <span className="mt-2 text-xs text-[color:var(--color-ink-soft)]">
                  {segmentReasonLabel(result.segment_stop_reason)}
                </span>
              </div>
            </div>

            {result.status === "paused" && (
              <div className="rounded-[1.4rem] border border-amber-200 bg-amber-50/90 px-4 py-3 text-sm text-amber-900">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <p className="font-semibold">任务已暂停</p>
                    <p className="mt-1 text-amber-800">
                      {reasonLabel(result.terminated_reason)}
                    </p>
                  </div>
                  {result.can_continue && (
                    <button
                      type="button"
                      onClick={handleContinue}
                      disabled={isLoading}
                      className="inline-flex h-10 items-center justify-center rounded-full bg-white px-4 text-sm font-semibold text-amber-950 shadow-sm transition hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      {isLoading ? "继续中..." : "继续任务"}
                    </button>
                  )}
                </div>
              </div>
            )}

            <div className="glass-panel rounded-[1.8rem] p-5">
              <div className="flex items-center justify-between gap-3">
                <p className="eyebrow">Final content</p>
                <span className="text-xs text-[color:var(--color-ink-muted)]">
                  Tokens: {Object.values(result.usage).reduce((sum, value) => sum + value, 0)} · Segment tokens: {result.budget_usage.total_tokens}
                </span>
              </div>
              <div className="mt-4 whitespace-pre-wrap rounded-[1.5rem] border border-[color:var(--color-line)] bg-white/80 px-4 py-4 text-sm leading-7 text-[var(--color-ink-strong)]">
                {result.content || "等待继续执行后生成最终内容。"}
              </div>
            </div>

            <div className="glass-panel rounded-[1.8rem] p-5">
              <div className="flex items-center justify-between gap-3">
                <div>
                  <p className="eyebrow">Trace log</p>
                  <p className="mt-2 text-sm leading-6 text-[color:var(--color-ink-soft)]">
                    Ordered steps returned by the backend task execution result.
                  </p>
                </div>
                <span className="rounded-full border border-[color:var(--color-line)] px-3 py-1 text-xs text-[color:var(--color-ink-soft)]">
                  {result.trace.length} entries
                </span>
              </div>

              {result.trace.length === 0 ? (
                <div className="mt-4 rounded-[1.5rem] border border-dashed border-[color:var(--color-line-strong)] px-4 py-6 text-sm text-[color:var(--color-ink-soft)]">
                  本次任务未返回执行轨迹。
                </div>
              ) : (
                <ol className="mt-4 flex flex-col gap-3">
                  {result.trace.map((entry) => (
                    <TraceRow key={`${entry.step}-${entry.timestamp_ms}`} entry={entry} />
                  ))}
                </ol>
              )}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
