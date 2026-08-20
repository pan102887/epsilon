/**
 * 聊天输入框组件。
 *
 * 提供消息输入区域和发送按钮，支持 Enter 发送（Shift+Enter 换行）。
 * 在 AI 响应过程中显示停止按钮，允许用户中止流式响应。
 */

"use client";

import { useCallback, useRef, type KeyboardEvent } from "react";

interface ChatInputProps {
  /** 是否正在等待 AI 响应 */
  isLoading: boolean;
  /** 发送消息回调 */
  onSend: (content: string) => void;
  /** 创建后台 Run 回调 */
  onRun?: (content: string) => void;
  /** 是否正在创建后台 Run */
  isRunLoading?: boolean;
  /** 中止响应回调 */
  onAbort: () => void;
}

/**
 * 聊天消息输入组件。
 *
 * 使用 textarea 支持多行输入，通过 ref 直接读取值避免受控组件的频繁渲染。
 * Enter 键发送消息，Shift+Enter 插入换行。
 *
 * @param isLoading - 加载状态，为 true 时禁用发送
 * @param onSend - 发送消息的回调函数
 * @param onAbort - 中止流式响应的回调函数
 */
export function ChatInput({
  isLoading,
  onSend,
  onRun,
  isRunLoading = false,
  onAbort,
}: ChatInputProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  const consumeValue = useCallback(() => {
    const value = textareaRef.current?.value ?? "";
    if (!value.trim()) return null;
    if (textareaRef.current) {
      textareaRef.current.value = "";
      textareaRef.current.style.height = "auto";
    }
    return value;
  }, []);

  const handleSend = useCallback(() => {
    if (isLoading) return;
    const value = consumeValue();
    if (value === null) return;
    onSend(value);
  }, [consumeValue, isLoading, onSend]);

  const handleRun = useCallback(() => {
    if (!onRun || isLoading || isRunLoading) return;
    const value = consumeValue();
    if (value === null) return;
    onRun(value);
  }, [consumeValue, isLoading, isRunLoading, onRun]);

  const handleKeyDown = useCallback(
    (e: KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        handleSend();
      }
    },
    [handleSend],
  );

  /**
   * 自动调整 textarea 高度以适应内容，最大不超过 160px。
   */
  const handleInput = useCallback(() => {
    const el = textareaRef.current;
    if (el) {
      el.style.height = "auto";
      el.style.height = `${Math.min(el.scrollHeight, 160)}px`;
    }
  }, []);

  return (
    <div className="border-t border-[color:var(--color-line)] px-5 py-5">
      <div className="mx-auto flex max-w-4xl items-end gap-3">
        <textarea
          ref={textareaRef}
          rows={1}
          placeholder="输入消息，Enter 发送，Shift+Enter 换行"
          className="min-h-14 flex-1 resize-none rounded-[1.4rem] border border-[color:var(--color-line-strong)] bg-white/80 px-4 py-3 text-sm text-[var(--color-ink-strong)] outline-none transition placeholder:text-[color:var(--color-ink-muted)] focus:-translate-y-0.5 focus:border-[color:var(--color-accent)] focus:bg-white"
          onKeyDown={handleKeyDown}
          onInput={handleInput}
          disabled={isLoading}
          aria-label="聊天消息输入框"
        />
        {isLoading ? (
          <button
            onClick={onAbort}
            className="inline-flex h-14 shrink-0 items-center justify-center rounded-full bg-[linear-gradient(135deg,#dc2626,#f97316)] px-5 text-sm font-medium text-white transition hover:-translate-y-0.5"
            aria-label="停止生成"
          >
            停止
          </button>
        ) : (
          <div className="flex shrink-0 flex-col gap-2 sm:flex-row">
            {onRun && (
              <button
                onClick={handleRun}
                disabled={isRunLoading}
                className="inline-flex h-14 items-center justify-center rounded-full border border-[color:var(--color-line-strong)] bg-white/85 px-4 text-sm font-medium text-[var(--color-ink-strong)] transition hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50"
                aria-label="后台运行聊天"
              >
                {isRunLoading ? "创建中" : "后台运行"}
              </button>
            )}
            <button
              onClick={handleSend}
              className="inline-flex h-14 items-center justify-center rounded-full bg-[linear-gradient(135deg,#0f766e,#1d4ed8)] px-6 text-sm font-medium text-white transition hover:-translate-y-0.5 disabled:opacity-50"
              aria-label="发送消息"
            >
              发送
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
