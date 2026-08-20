/**
 * 聊天页面顶部栏组件。
 *
 * 显示应用标题、可选的子元素插槽（如模型选择器）和"新对话"按钮。
 * 点击按钮可清除当前会话并开始新对话。
 */

"use client";

interface ChatHeaderProps {
  /** 清除会话回调 */
  onClear: () => void;
  /** 是否正在加载中，加载时禁用清除按钮 */
  isLoading: boolean;
  /** 面板标题 */
  title: string;
  /** 面板说明 */
  description: string;
  /** 可选的子元素，渲染在标题和按钮之间（如模型选择器） */
  children?: React.ReactNode;
}

/**
 * 聊天顶部导航栏。
 *
 * @param onClear - 点击"新对话"时的回调
 * @param isLoading - 加载状态，为 true 时禁用按钮
 * @param children - 插槽内容，渲染在标题右侧
 */
export function ChatHeader({
  onClear,
  isLoading,
  title,
  description,
  children,
}: ChatHeaderProps) {
  return (
    <header className="border-b border-[color:var(--color-line)] px-5 py-5">
      <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
        <div className="space-y-2">
          <p className="eyebrow">Live chat</p>
          <div className="space-y-1">
            <h2 className="font-[family:var(--font-display)] text-3xl leading-none tracking-[-0.04em] text-[var(--color-ink-strong)]">
              {title}
            </h2>
            <p className="max-w-xl text-sm leading-6 text-[color:var(--color-ink-soft)]">
              {description}
            </p>
          </div>
        </div>

        <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
          {children}
          <button
            onClick={onClear}
            disabled={isLoading}
            className="inline-flex h-11 items-center justify-center rounded-full border border-[color:var(--color-line-strong)] px-5 text-sm font-medium text-[var(--color-ink-strong)] transition hover:-translate-y-0.5 hover:bg-white/80 disabled:cursor-not-allowed disabled:opacity-50"
            aria-label="开始新对话"
          >
            新对话
          </button>
        </div>
      </div>
    </header>
  );
}
