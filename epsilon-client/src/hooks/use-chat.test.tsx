/**
 * useChat Hook 基础测试。
 *
 * 覆盖消息发送成功与错误清理路径，确保前端主链路的状态更新稳定可回归。
 */

import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useChat } from "./use-chat";
import { streamChat } from "@/lib/chat-api";

vi.mock("@/lib/chat-api", () => ({
  clearSession: vi.fn(),
  streamChat: vi.fn(),
  streamContinueChat: vi.fn(),
}));

describe("useChat", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("adds user and assistant messages and applies streamed content", async () => {
    const streamChatMock = vi.mocked(streamChat);
    streamChatMock.mockImplementation((_request, onChunk, onDone) => {
      const controller = new AbortController();
      queueMicrotask(() => {
        onChunk({
          delta_content: "assistant reply",
          finished: false,
        });
        onChunk({
          delta_content: "",
          finished: true,
          status: "completed",
          can_continue: false,
        });
        onDone();
      });
      return controller;
    });

    const { result } = renderHook(() => useChat("session-1"));

    act(() => {
      result.current.sendMessage("hello world");
    });

    await waitFor(() => {
      expect(result.current.messages).toHaveLength(2);
      expect(result.current.messages[0]).toMatchObject({
        role: "user",
        content: "hello world",
      });
      expect(result.current.messages[1]).toMatchObject({
        role: "assistant",
        content: "assistant reply",
        status: "completed",
        canContinue: false,
      });
    });

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false);
    });

    expect(result.current.error).toBeNull();
    expect(streamChatMock).toHaveBeenCalledWith(
      { session_id: "session-1", message: "hello world", model: undefined },
      expect.any(Function),
      expect.any(Function),
      expect.any(Function),
    );
  });

  it("exposes stream error and removes an empty assistant placeholder", async () => {
    const streamChatMock = vi.mocked(streamChat);
    streamChatMock.mockImplementation((_request, _onChunk, _onDone, onError) => {
      const controller = new AbortController();
      queueMicrotask(() => {
        onError(new Error("stream failed"));
      });
      return controller;
    });

    const { result } = renderHook(() => useChat("session-1"));

    act(() => {
      result.current.sendMessage("hello world");
    });

    await waitFor(() => {
      expect(result.current.error).toBe("stream failed");
    });

    expect(result.current.isLoading).toBe(false);
    expect(result.current.messages).toHaveLength(1);
    expect(result.current.messages[0]).toMatchObject({
      role: "user",
      content: "hello world",
    });
  });
});
