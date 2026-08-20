/**
 * useRun Hook 基础测试。
 *
 * 覆盖快照刷新、事件列表更新与取消后回拉快照行为，保护后台 Run 监控主链路。
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useRun } from "./use-run";
import {
  approveRun,
  cancelRun,
  fetchRun,
  fetchRunEvents,
  streamRunEvents,
  type RunEvent,
  type RunSnapshot,
} from "@/lib/chat-api";

vi.mock("@/lib/chat-api", () => ({
  approveRun: vi.fn(),
  cancelRun: vi.fn(),
  continueRun: vi.fn(),
  fetchRun: vi.fn(),
  fetchRunEvents: vi.fn(),
  streamRunEvents: vi.fn(),
}));

function createSnapshot(
  overrides: Partial<RunSnapshot> = {},
): RunSnapshot {
  return {
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
    workflow_name: null,
    workflow_run_state: null,
    collaboration_summary: null,
    created_at: "2026-06-17T00:00:00Z",
    updated_at: "2026-06-17T00:00:00Z",
    version: 1,
    ...overrides,
  };
}

function createEvent(overrides: Partial<RunEvent> = {}): RunEvent {
  return {
    run_id: "run-1",
    cursor: 1,
    event_type: "status_changed",
    payload: { status: "running" },
    created_at: "2026-06-17T00:00:01Z",
    ...overrides,
  };
}

describe("useRun", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(streamRunEvents).mockImplementation(
      () => new AbortController(),
    );
  });

  it("refresh updates snapshot and events", async () => {
    vi.mocked(fetchRun).mockResolvedValue(
      createSnapshot({ status: "running", latest_event_cursor: 2 }),
    );
    vi.mocked(fetchRunEvents).mockResolvedValue({
      code: 0,
      events: [createEvent({ cursor: 2, payload: { status: "running" } })],
      latest_cursor: 2,
    });

    const { result } = renderHook(() => useRun("run-1"));

    await waitFor(() => {
      expect(result.current.snapshot?.status).toBe("running");
      expect(result.current.events).toHaveLength(1);
    });

    vi.mocked(fetchRun).mockResolvedValue(
      createSnapshot({ status: "paused", latest_event_cursor: 3 }),
    );
    vi.mocked(fetchRunEvents).mockResolvedValue({
      code: 0,
      events: [createEvent({ cursor: 3, payload: { status: "paused" } })],
      latest_cursor: 3,
    });

    await act(async () => {
      await result.current.refresh();
    });

    expect(result.current.snapshot?.status).toBe("paused");
    expect(result.current.events).toEqual([
      expect.objectContaining({
        cursor: 3,
        payload: { status: "paused" },
      }),
    ]);
  });

  it("cancel requests cancellation and re-fetches the latest snapshot", async () => {
    vi.mocked(fetchRun)
      .mockResolvedValueOnce(
        createSnapshot({ status: "running", latest_event_cursor: 1 }),
      )
      .mockResolvedValueOnce(
        createSnapshot({ status: "cancelled", latest_event_cursor: 2 }),
      );
    vi.mocked(fetchRunEvents)
      .mockResolvedValueOnce({
        code: 0,
        events: [createEvent({ cursor: 1 })],
        latest_cursor: 1,
      })
      .mockResolvedValueOnce({
        code: 0,
        events: [
          createEvent({
            cursor: 2,
            event_type: "cancelled",
            payload: { status: "cancelled" },
          }),
        ],
        latest_cursor: 2,
      });
    vi.mocked(cancelRun).mockResolvedValue(
      createSnapshot({ status: "cancel_requested", latest_event_cursor: 1 }),
    );

    const { result } = renderHook(() => useRun("run-1"));

    await waitFor(() => {
      expect(result.current.snapshot?.status).toBe("running");
    });

    await act(async () => {
      await result.current.cancelRun();
    });

    await waitFor(() => {
      expect(result.current.snapshot?.status).toBe("cancelled");
      expect(result.current.events).toEqual([
        expect.objectContaining({
          cursor: 2,
          event_type: "cancelled",
        }),
      ]);
    });

    expect(cancelRun).toHaveBeenCalledWith("run-1");
    expect(fetchRun).toHaveBeenCalledTimes(2);
    expect(fetchRunEvents).toHaveBeenCalledTimes(2);
  });

  it("submits approval decisions and refreshes the latest snapshot", async () => {
    vi.mocked(fetchRun)
      .mockResolvedValueOnce(
        createSnapshot({ status: "awaiting_approval", latest_event_cursor: 1 }),
      )
      .mockResolvedValueOnce(
        createSnapshot({ status: "running", latest_event_cursor: 2 }),
      );
    vi.mocked(fetchRunEvents)
      .mockResolvedValueOnce({
        code: 0,
        events: [createEvent({ cursor: 1, event_type: "approval_required" })],
        latest_cursor: 1,
      })
      .mockResolvedValueOnce({
        code: 0,
        events: [createEvent({ cursor: 2, payload: { status: "running" } })],
        latest_cursor: 2,
      });
    vi.mocked(approveRun).mockResolvedValue(
      createSnapshot({ status: "running", latest_event_cursor: 2 }),
    );

    const { result } = renderHook(() => useRun("run-1"));

    await waitFor(() => {
      expect(result.current.snapshot?.status).toBe("awaiting_approval");
    });

    await act(async () => {
      await result.current.approveRun([{ type: "approve", tool_call_id: "tc-1" }]);
    });

    expect(approveRun).toHaveBeenCalledWith(
      "run-1",
      [{ type: "approve", tool_call_id: "tc-1" }],
      undefined,
    );
    await waitFor(() => {
      expect(result.current.snapshot?.status).toBe("running");
    });
  });
});
