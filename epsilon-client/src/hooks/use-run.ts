/**
 * 后台 Run 状态管理 Hook。
 */

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  approveRun as approveRunRequest,
  cancelRun as cancelRunRequest,
  continueRun as continueRunRequest,
  fetchRun,
  fetchRunEvents,
  streamRunEvents,
  type ApprovalDecisionRequest,
  type RunEvent,
  type RunReplayExpiredEvent,
  type RunSnapshot,
  type RunStatus,
} from "@/lib/chat-api";

const TERMINAL_STATUSES = new Set([
  "cancelled",
  "succeeded",
  "failed",
  "lost",
]);

const RUN_STATUSES = new Set<RunStatus>([
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

function applyRunEventToSnapshot(
  snapshot: RunSnapshot | null,
  event: RunEvent,
): RunSnapshot | null {
  if (!snapshot) return snapshot;
  const payload = event.payload;
  const next: RunSnapshot = {
    ...snapshot,
    latest_event_cursor: event.cursor,
    updated_at: event.created_at,
  };

  const status = payload.status;
  if (typeof status === "string" && RUN_STATUSES.has(status as RunStatus)) {
    next.status = status as RunStatus;
  }
  if ("result" in payload) {
    next.result = asRecordOrNull(payload.result);
  }
  if ("error" in payload) {
    next.error = asRecordOrNull(payload.error);
  }
  if ("approval_id" in payload) {
    next.approval_id =
      typeof payload.approval_id === "string" ? payload.approval_id : null;
  }
  if ("can_continue" in payload && typeof payload.can_continue === "boolean") {
    next.can_continue = payload.can_continue;
  }
  if ("terminal_reason" in payload) {
    next.terminal_reason =
      typeof payload.terminal_reason === "string"
        ? payload.terminal_reason
        : null;
  }
  if ("segment_metadata" in payload) {
    next.segment_metadata = asRecordOrNull(payload.segment_metadata);
  }
  if ("workflow_run_state" in payload) {
    next.workflow_run_state = asRecordOrNull(payload.workflow_run_state);
  }
  if ("collaboration_summary" in payload) {
    next.collaboration_summary = asRecordOrNull(payload.collaboration_summary);
  }
  return next;
}

function asRecordOrNull(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

export interface UseRunReturn {
  snapshot: RunSnapshot | null;
  events: RunEvent[];
  isLoading: boolean;
  isWatching: boolean;
  error: string | null;
  replayExpired: RunReplayExpiredEvent | null;
  refresh: () => Promise<void>;
  continueRun: (model?: string) => Promise<void>;
  approveRun: (decisions: ApprovalDecisionRequest[], model?: string) => Promise<void>;
  cancelRun: () => Promise<void>;
}

/**
 * 后台 Run 状态管理 Hook。
 *
 * 负责加载 Run 快照、拉取历史事件、订阅事件流以及封装继续/取消操作，
 * 供 Run 监控面板统一消费。
 *
 * @param runId - 当前选中的后台 Run 标识；为空时重置状态
 * @returns Run 监控所需的状态与操作集合
 */
export function useRun(runId: string | null): UseRunReturn {
  const [snapshot, setSnapshot] = useState<RunSnapshot | null>(null);
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [isWatching, setIsWatching] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [replayExpired, setReplayExpired] =
    useState<RunReplayExpiredEvent | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const refresh = useCallback(async () => {
    if (!runId) {
      setSnapshot(null);
      setEvents([]);
      return;
    }

    setIsLoading(true);
    setError(null);
    try {
      const nextSnapshot = await fetchRun(runId);
      const response = await fetchRunEvents(runId, null, 100);
      setSnapshot(nextSnapshot);
      setEvents(response.events);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setIsLoading(false);
    }
  }, [runId]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const snapshotStatus = snapshot?.status ?? null;
  const latestEventCursor = snapshot?.latest_event_cursor ?? null;

  useEffect(() => {
    abortRef.current?.abort();
    setReplayExpired(null);

    if (!runId || (snapshotStatus !== null && TERMINAL_STATUSES.has(snapshotStatus))) {
      setIsWatching(false);
      return;
    }

    setIsWatching(true);
    const controller = streamRunEvents(
      runId,
      latestEventCursor,
      (event) => {
        setEvents((prev) => {
          if (prev.some((item) => item.cursor === event.cursor)) {
            return prev;
          }
          return [...prev, event].sort((left, right) => left.cursor - right.cursor);
        });
        setSnapshot((prev) => applyRunEventToSnapshot(prev, event));
      },
      (event) => {
        setReplayExpired(event);
        void refresh();
      },
      () => setIsWatching(false),
      (err) => {
        setError(err.message);
        setIsWatching(false);
      },
    );
    abortRef.current = controller;

    return () => {
      controller.abort();
      setIsWatching(false);
    };
  }, [latestEventCursor, refresh, runId, snapshotStatus]);

  const continueRun = useCallback(
    async (model?: string) => {
      if (!runId) return;
      setIsLoading(true);
      setError(null);
      try {
        const nextSnapshot = await continueRunRequest(runId, model);
        setSnapshot(nextSnapshot);
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setIsLoading(false);
      }
    },
    [runId],
  );

  const cancelRun = useCallback(async () => {
    if (!runId) return;
    setIsLoading(true);
    setError(null);
    try {
      const nextSnapshot = await cancelRunRequest(runId);
      setSnapshot(nextSnapshot);
      await refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setIsLoading(false);
    }
  }, [refresh, runId]);

  const approveRun = useCallback(
    async (decisions: ApprovalDecisionRequest[], model?: string) => {
      if (!runId) return;
      setIsLoading(true);
      setError(null);
      try {
        const nextSnapshot = await approveRunRequest(runId, decisions, model);
        setSnapshot(nextSnapshot);
        await refresh();
      } catch (err) {
        setError((err as Error).message);
      } finally {
        setIsLoading(false);
      }
    },
    [refresh, runId],
  );

  return {
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
  };
}
