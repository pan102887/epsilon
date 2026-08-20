/**
 * 消息列表组件。
 *
 * 渲染聊天消息列表，支持自动滚动到最新消息。
 * 当消息列表为空时显示欢迎提示。
 */

"use client";

import { useEffect, useRef } from "react";
import type { ChatMessage } from "@/hooks/use-chat";
import { MessageBubble } from "./message-bubble";

interface MessageListProps {
  messages: ChatMessage[];
  isLoading: boolean;
  onContinueLast: () => void;
}

/**
 * 消息列表容器组件。
 *
 * 内部维护一个滚动容器，每当消息列表变化时自动滚动到底部，
 * 确保用户始终能看到最新的消息。
 *
 * @param messages - 聊天消息数组
 */
export function MessageList({
  messages,
  isLoading,
  onContinueLast,
}: MessageListProps) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center px-5 py-10">
        <div className="glass-panel max-w-md rounded-[1.75rem] px-6 py-8 text-center">
          <p className="eyebrow justify-center">Session ready</p>
          <p className="mt-4 font-[family:var(--font-display)] text-3xl leading-none tracking-[-0.04em] text-[var(--color-ink-strong)]">
            开始一段可执行的对话
          </p>
          <p className="mt-3 text-sm leading-6 text-[color:var(--color-ink-soft)]">
            输入消息进行推理、澄清或规划。需要执行结构化任务时，
            直接切换到右侧任务面板，无需创建新会话。
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto px-5 py-6">
      <div className="mx-auto flex max-w-4xl flex-col gap-4">
        {messages.map((msg) => (
          <MessageBubble
            key={msg.id}
            message={msg}
            isLoading={isLoading}
            onContinue={onContinueLast}
          />
        ))}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
