/**
 * 应用首页。
 *
 * 渲染聊天面板作为主界面，使用固定的默认会话 ID。
 * 后续可扩展为动态会话管理（多会话切换等）。
 */

"use client";

import { useState } from "react";
import { ChatPanel } from "@/components/chat/chat-panel";
import { RunView } from "@/components/run/run-view";
import { TaskWorkspace } from "@/components/task/task-workspace";

/**
 * 生成默认会话 ID。
 * 使用时间戳确保每次刷新页面时创建新会话。
 */
function createSessionId(): string {
  return `session-${Date.now()}`;
}

/**
 * 首页组件，承载聊天面板。
 */
export default function Home() {
  const [sessionId] = useState(createSessionId);
  const [selectedModel, setSelectedModel] = useState("");
  const [activeRunId, setActiveRunId] = useState<string | null>(null);

  return (
    <main className="min-h-screen px-4 py-6 sm:px-6 lg:px-8">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-6">
        <section className="hero-shell overflow-hidden rounded-[1.5rem] px-5 py-4 sm:px-6 sm:py-5">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="space-y-1">
              <p className="eyebrow">Epsilon</p>
              <h1 className="font-[family:var(--font-display)] text-xl leading-tight tracking-[-0.04em] text-[var(--color-ink-strong)] sm:text-2xl">
                Agent console for chat, task runs, and execution visibility.
              </h1>
            </div>

            <div className="flex flex-wrap gap-2">
              <div className="stat-card-compact">
                <span className="stat-label">Model</span>
                <span className="stat-value text-sm">
                  {selectedModel || "Auto default"}
                </span>
              </div>
              <div className="stat-card-compact">
                <span className="stat-label">Workflows</span>
                <span className="stat-value text-sm">Chat + Task Run</span>
              </div>
            </div>
          </div>
        </section>

        <section className="grid gap-6 xl:grid-cols-[minmax(0,1.1fr)_minmax(360px,0.9fr)]">
          {sessionId ? (
            <>
              <ChatPanel
                sessionId={sessionId}
                selectedModel={selectedModel}
                onSelectedModelChange={setSelectedModel}
                onRunCreated={setActiveRunId}
              />
              <TaskWorkspace
                sessionId={sessionId}
                selectedModel={selectedModel}
                onRunCreated={setActiveRunId}
              />
              <div className="xl:col-span-2">
                <RunView runId={activeRunId} selectedModel={selectedModel} />
              </div>
            </>
          ) : (
            <div className="glass-panel rounded-[1.8rem] px-6 py-10 text-sm text-[color:var(--color-ink-soft)] xl:col-span-2">
              正在初始化会话工作区...
            </div>
          )}
        </section>
      </div>
    </main>
  );
}
