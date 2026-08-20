/**
 * 聊天面板组合组件。
 *
 * 将 ChatHeader、ModelSelector、MessageList、ChatInput 和错误提示组合为完整的聊天界面。
 * 内部使用 useChat Hook 管理所有聊天状态，是聊天功能的顶层容器。
 * 支持模型选择，用户可在对话过程中切换不同的 AI 模型。
 */

"use client";

import { useCallback } from "react";
import { useState } from "react";
import { useChat } from "@/hooks/use-chat";
import { createRun } from "@/lib/chat-api";
import { ChatHeader } from "./chat-header";
import { ChatInput } from "./chat-input";
import { MessageList } from "./message-list";
import { ModelSelector } from "./model-selector";

interface ChatPanelProps {
  /** 会话唯一标识符 */
  sessionId: string;
  /** 当前选中的模型 */
  selectedModel: string;
  /** 模型切换回调 */
  onSelectedModelChange: (modelId: string) => void;
  /** 后台 Run 创建完成回调 */
  onRunCreated?: (runId: string) => void;
}

/**
 * 聊天面板组件，聊天功能的完整 UI 容器。
 *
 * 组合头部导航（含模型选择器）、消息列表、输入框和错误提示，
 * 通过 useChat Hook 统一管理状态。
 *
 * @param sessionId - 会话 ID，传递给 useChat 用于后端关联
 */
export function ChatPanel({
  sessionId,
  selectedModel,
  onSelectedModelChange,
  onRunCreated,
}: ChatPanelProps) {
  const {
    messages,
    isLoading,
    error,
    sendMessage,
    continueLast,
    clearChat,
    abort,
  } = useChat(sessionId);
  const [runError, setRunError] = useState<string | null>(null);
  const [isRunLoading, setIsRunLoading] = useState(false);

  const handleSend = useCallback(
    (content: string) => {
      sendMessage(content, selectedModel || undefined);
    },
    [sendMessage, selectedModel],
  );

  const handleContinueLast = useCallback(() => {
    continueLast(selectedModel || undefined);
  }, [continueLast, selectedModel]);

  const handleCreateRun = useCallback(
    async (content: string) => {
      const message = content.trim();
      if (!message || isRunLoading) return;
      setRunError(null);
      setIsRunLoading(true);
      try {
        const snapshot = await createRun({
          kind: "chat",
          client_request_id: `${sessionId}:chat:${Date.now()}`,
          chat: {
            session_id: sessionId,
            message,
            model: selectedModel || undefined,
          },
        });
        onRunCreated?.(snapshot.run_id);
      } catch (err) {
        setRunError((err as Error).message);
      } finally {
        setIsRunLoading(false);
      }
    },
    [isRunLoading, onRunCreated, selectedModel, sessionId],
  );

  return (
    <section className="panel-shell flex min-h-[720px] flex-col overflow-hidden">
      <ChatHeader
        onClear={clearChat}
        isLoading={isLoading}
        title="Conversation rail"
        description="Stream responses, preserve the session, and pivot into task mode when the discussion becomes actionable."
      >
        <ModelSelector
          value={selectedModel}
          onChange={onSelectedModelChange}
          disabled={isLoading}
        />
      </ChatHeader>

      {error && (
        <div
          className="mx-5 mt-4 rounded-2xl border border-red-200/80 bg-red-50/90 px-4 py-3 text-sm text-red-700 shadow-[0_18px_50px_rgba(192,38,38,0.08)]"
          role="alert"
        >
          {error}
        </div>
      )}

      {runError && (
        <div
          className="mx-5 mt-4 rounded-2xl border border-red-200/80 bg-red-50/90 px-4 py-3 text-sm text-red-700 shadow-[0_18px_50px_rgba(192,38,38,0.08)]"
          role="alert"
        >
          {runError}
        </div>
      )}

      <MessageList
        messages={messages}
        isLoading={isLoading}
        onContinueLast={handleContinueLast}
      />
      <ChatInput
        isLoading={isLoading}
        isRunLoading={isRunLoading}
        onSend={handleSend}
        onRun={handleCreateRun}
        onAbort={abort}
      />
    </section>
  );
}
