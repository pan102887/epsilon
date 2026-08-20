/**
 * 聊天消息气泡组件。
 *
 * 根据消息角色（用户/助手）渲染不同样式的消息气泡，
 * 用户消息靠右显示蓝色背景，助手消息靠左显示灰色背景。
 * 助手消息为空时显示打字动画指示器。
 */

"use client";

import type { ChatMessage } from "@/hooks/use-chat";
import type { SegmentStopReason, TerminationReason } from "@/lib/chat-api";

/**
 * 打字指示器动画组件。
 * 三个圆点依次跳动，表示 AI 正在生成回复。
 */
function TypingIndicator() {
  return (
    <span className="inline-flex items-center gap-1" aria-label="正在输入">
      <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce [animation-delay:0ms]" />
      <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce [animation-delay:150ms]" />
      <span className="w-1.5 h-1.5 rounded-full bg-gray-400 animate-bounce [animation-delay:300ms]" />
    </span>
  );
}

interface MessageBubbleProps {
  message: ChatMessage;
  isLoading: boolean;
  onContinue?: () => void;
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

/**
 * 单条消息气泡组件。
 *
 * @param message - 聊天消息对象，包含角色和内容
 */
export function MessageBubble({
  message,
  isLoading,
  onContinue,
}: MessageBubbleProps) {
  const isUser = message.role === "user";
  const isPaused = !isUser && message.status === "paused";

  return (
    <div className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
      <div
        className={`max-w-[85%] rounded-[1.6rem] px-4 py-3 text-sm leading-7 whitespace-pre-wrap break-words shadow-[0_18px_48px_rgba(15,23,42,0.08)] ${
          isUser
            ? "rounded-br-sm bg-[linear-gradient(135deg,#0f766e,#1d4ed8)] text-white"
            : "rounded-bl-sm border border-[color:var(--color-line)] bg-[rgba(255,251,245,0.92)] text-[var(--color-ink-strong)]"
        }`}
      >
        {message.content || (!isUser && <TypingIndicator />)}
        {!isUser && message.segmentCount && (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-xs text-[color:var(--color-ink-soft)]">
            <span className="rounded-full border border-[color:var(--color-line)] bg-white/70 px-2.5 py-1 font-semibold text-[var(--color-ink-strong)]">
              Segment {message.segmentIndex ?? message.segmentCount}/{message.segmentCount}
            </span>
            <span>{segmentReasonLabel(message.segmentStopReason)}</span>
            {typeof message.budgetUsage?.total_tokens === "number" && (
              <span>{message.budgetUsage.total_tokens} tokens</span>
            )}
          </div>
        )}
        {isPaused && (
          <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-[color:var(--color-line)] pt-3">
            <span className="rounded-full bg-amber-100 px-3 py-1 text-xs font-semibold text-amber-900">
              已暂停
            </span>
            <span className="text-xs text-[color:var(--color-ink-soft)]">
              {reasonLabel(message.terminatedReason)}
            </span>
            {message.canContinue && (
              <button
                type="button"
                onClick={onContinue}
                disabled={isLoading}
                className="ml-auto inline-flex h-8 items-center justify-center rounded-full border border-[color:var(--color-line-strong)] bg-white px-3 text-xs font-semibold text-[var(--color-ink-strong)] transition hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50"
              >
                继续
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
