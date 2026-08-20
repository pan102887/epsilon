/**
 * 聊天状态管理 Hook 模块。
 *
 * 封装聊天消息列表、发送消息、流式接收、会话管理等核心状态逻辑，
 * 供聊天页面组件直接使用。内部通过 chat-api 模块与后端通信。
 */

"use client";

import { useCallback, useRef, useState } from "react";
import {
  clearSession,
  streamChat,
  streamContinueChat,
  type BudgetUsage,
  type SegmentStopReason,
  type StreamChunk,
  type TerminationReason,
} from "@/lib/chat-api";

/** 消息角色类型 */
export type MessageRole = "user" | "assistant";

/** 单条聊天消息 */
export interface ChatMessage {
  id: string;
  role: MessageRole;
  content: string;
  status?: "completed" | "paused";
  terminatedReason?: TerminationReason;
  canContinue?: boolean;
  segmentIndex?: number;
  segmentCount?: number;
  autoContinueAttempted?: boolean;
  segmentStopReason?: SegmentStopReason;
  budgetUsage?: BudgetUsage;
}

/** useChat Hook 的返回值类型 */
export interface UseChatReturn {
  /** 当前消息列表 */
  messages: ChatMessage[];
  /** 是否正在等待/接收 AI 响应 */
  isLoading: boolean;
  /** 错误信息，无错误时为 null */
  error: string | null;
  /** 发送用户消息并触发流式响应 */
  sendMessage: (content: string, model?: string) => void;
  /** 基于最后一条可继续助手消息继续执行 */
  continueLast: (model?: string) => void;
  /** 清除当前会话并重置消息列表 */
  clearChat: () => Promise<void>;
  /** 中止当前正在进行的流式响应 */
  abort: () => void;
}

/**
 * 生成唯一消息 ID。
 * 使用时间戳 + 随机数组合，满足客户端去重需求。
 */
function generateId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

/**
 * 聊天状态管理 Hook。
 *
 * 管理完整的聊天生命周期：消息列表维护、用户消息发送、
 * SSE 流式响应接收与增量拼接、错误处理、会话清除。
 *
 * @param sessionId - 会话唯一标识符，用于后端关联对话上下文
 * @returns 聊天状态和操作方法
 */
export function useChat(sessionId: string): UseChatReturn {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const assistantIdRef = useRef<string>("");

  const applyChunkToAssistant = useCallback((chunk: StreamChunk) => {
    setMessages((prev) =>
      prev.map((msg) =>
        msg.id === assistantIdRef.current
          ? {
              ...msg,
              content:
                chunk.event_type === "segment_done"
                  ? msg.content
                  : msg.content + (chunk.delta_content ?? ""),
              status: chunk.finished
                ? chunk.status === "paused"
                  ? "paused"
                  : "completed"
                : msg.status,
              terminatedReason:
                chunk.terminated_reason ?? msg.terminatedReason,
              canContinue:
                typeof chunk.can_continue === "boolean"
                  ? chunk.can_continue
                  : msg.canContinue,
              segmentIndex: chunk.segment_index ?? msg.segmentIndex,
              segmentCount: chunk.segment_count ?? msg.segmentCount,
              autoContinueAttempted:
                chunk.auto_continue_attempted ?? msg.autoContinueAttempted,
              segmentStopReason:
                chunk.segment_stop_reason ?? msg.segmentStopReason,
              budgetUsage: chunk.budget_usage ?? msg.budgetUsage,
            }
          : msg,
      ),
    );
  }, []);

  const abort = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsLoading(false);
  }, []);

  const sendMessage = useCallback(
    (content: string, model?: string) => {
      if (!content.trim() || isLoading) return;

      setError(null);

      // 添加用户消息
      const userMsg: ChatMessage = {
        id: generateId(),
        role: "user",
        content: content.trim(),
      };

      // 预创建助手消息占位
      const assistantId = generateId();
      assistantIdRef.current = assistantId;
      const assistantMsg: ChatMessage = {
        id: assistantId,
        role: "assistant",
        content: "",
      };

      setMessages((prev) => [...prev, userMsg, assistantMsg]);
      setIsLoading(true);

      const controller = streamChat(
        { session_id: sessionId, message: content.trim(), model },
        (chunk: StreamChunk) => {
          applyChunkToAssistant(chunk);
        },
        () => {
          // 流结束
          setIsLoading(false);
          abortRef.current = null;
        },
        (err: Error) => {
          setError(err.message);
          setIsLoading(false);
          abortRef.current = null;
          // 移除空的助手消息占位
          setMessages((prev) =>
            prev.filter(
              (msg) =>
                !(msg.id === assistantIdRef.current && msg.content === ""),
            ),
          );
        },
      );

      abortRef.current = controller;
    },
    [applyChunkToAssistant, isLoading, sessionId],
  );

  const continueLast = useCallback(
    (model?: string) => {
      if (isLoading) return;

      const target = [...messages]
        .reverse()
        .find((message) => message.role === "assistant" && message.canContinue);
      if (!target) return;

      setError(null);
      const assistantId = generateId();
      assistantIdRef.current = assistantId;
      setMessages((prev) => [
        ...prev.map((message) =>
          message.id === target.id ? { ...message, canContinue: false } : message,
        ),
        {
          id: assistantId,
          role: "assistant",
          content: "",
        },
      ]);
      setIsLoading(true);

      const controller = streamContinueChat(
        sessionId,
        model,
        applyChunkToAssistant,
        () => {
          setIsLoading(false);
          abortRef.current = null;
        },
        (err: Error) => {
          setError(err.message);
          setIsLoading(false);
          abortRef.current = null;
          setMessages((prev) =>
            prev.filter(
              (msg) =>
                !(msg.id === assistantIdRef.current && msg.content === ""),
            ),
          );
        },
      );

      abortRef.current = controller;
    },
    [applyChunkToAssistant, isLoading, messages, sessionId],
  );

  const clearChat = useCallback(async () => {
    abort();
    try {
      await clearSession(sessionId);
    } catch {
      // 清除失败不阻塞 UI 重置
    }
    setMessages([]);
    setError(null);
  }, [abort, sessionId]);

  return {
    messages,
    isLoading,
    error,
    sendMessage,
    continueLast,
    clearChat,
    abort,
  };
}
