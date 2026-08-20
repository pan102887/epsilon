# 前端控制台

## 技术栈

- Next.js `16.2.0`（`next.config.ts` 启用 `reactCompiler: true`）
- React `19.2.4`、`babel-plugin-react-compiler`
- TypeScript 5
- Tailwind CSS 4（`@tailwindcss/postcss`）
- Bun lockfile 已入库，项目脚本仍通过 `package.json` 暴露 `dev`、`build`、`start`、`lint`

前端源码位于 `epsilon-client/src/`。

> 注意：此版本 Next.js 较新，细节 API 可能与常见训练数据不一致。新增代码前请优先阅读 `node_modules/next/dist/docs/` 下的当前文档（见仓库根 `epsilon-client/AGENTS.md` 的提醒）。

## 页面结构

```text
src/app/page.tsx
  -> ChatPanel
     -> ChatHeader
     -> ModelSelector
     -> MessageList
     -> ChatInput
  -> TaskWorkspace
  -> RunView
     -> RunEventList
```

首页会在浏览器端生成 `session-${Date.now()}` 作为当前会话 ID，并在聊天、任务和后台 Run 工作区之间共享。模型选择状态同样由首页维护，传递给聊天、任务和 Run continue 请求。`activeRunId` 由首页保存，Chat/Task 显式点击“后台运行”后更新到 `RunView`。

## 后端代理

`epsilon-client/next.config.ts` 配置 rewrites：

- `/api/:path*` -> `${NEXT_PUBLIC_API_BASE_URL}/api/:path*`
- `/v1/:path*` -> `${NEXT_PUBLIC_API_BASE_URL}/v1/:path*`

未设置 `NEXT_PUBLIC_API_BASE_URL` 时，默认代理到 `http://localhost:7777`。

## API 封装

`src/lib/chat-api.ts` 是前端访问后端的集中入口：

- `streamChat()`：`POST /api/chat`，强制 `stream: true`，使用 fetch + ReadableStream 解析 SSE `data:` 行。
- `streamContinueChat()`：`POST /api/chat/sessions/{session_id}/continue`，继续 paused chat。
- `clearSession()`：`DELETE /api/chat/sessions/{session_id}`。
- `fetchModels()`：`GET /v1/models`。
- `executeTask()`：`POST /api/task/execute`。
- `continueTask()`：`POST /api/task/sessions/{session_id}/continue`。
- `createRun()`、`fetchRun()`、`fetchRunEvents()`、`streamRunEvents()`、`cancelRun()`、`continueRun()`、`approveRun()`：访问 `/api/runs*`，用于后台 Run 创建、快照查询、事件订阅、取消、继续和审批恢复。

## 状态管理

`src/hooks/use-chat.ts` 管理聊天状态：

- 维护用户/助手消息列表。
- 发送消息时先插入用户消息和空助手占位。
- SSE 分片到达时增量拼接助手消息内容。
- 支持 AbortController 中止请求。
- 清空会话时先中止当前流，再调用后端删除会话上下文。

任务执行状态由 `TaskWorkspace` 局部管理，请求完成后展示完整响应，不模拟实时流式步骤。

`src/hooks/use-run.ts` 管理后台 Run 状态：

- 先查询 `RunSnapshot` 和事件历史，再通过 `streamRunEvents()` 订阅后续事件。
- `replay_expired` 控制事件会触发 polling fallback，重新查询快照和事件历史。
- queued/running 时允许取消；paused 且 `can_continue=true` 时允许继续；`awaiting_approval` 时展示审批状态；终态禁用控制动作。
- Run 事件是控制事件，不会拼接到 Chat assistant 文本中。
- RunView 展示 `RunSnapshot` 与事件流中的事实字段：checkpoint/recovery、task classification、guardrail summary/runtime stats、workflow current phase / active role / handoff state、collaboration `latest_steps`、role capability rejection、child run link/wait/reconcile 事件和终态结果/错误。
- 前端只做字段读取、fallback 和安全摘要渲染，不复制 guardrail 或 workflow 策略判断；历史 `recent_steps` 仅作为旧快照兼容读取，规范写路径为 `latest_steps`。

## 本地开发

后端默认端口是 `7777`。前端默认端口是 Next.js 的 `3000`。

```bash
cd epsilon-client
bun install
bun run dev
```

如需连接其他后端地址：

```bash
NEXT_PUBLIC_API_BASE_URL=http://localhost:7777 bun run dev
```

## 修改约定

- 新增后端接口时，先在 `src/lib/chat-api.ts` 增加类型和请求函数，再接入组件。
- 聊天流式协议当前只解析 SSE `data:` 行，`[DONE]` 表示结束。
- Run 事件流同样解析 SSE `event:` / `data:` 行，但 `replay_expired`、`error` 等控制事件必须由 Run 面板处理，不能混入聊天消息。
- 前端请求路径优先使用相对路径，让 Next.js rewrites 处理跨域和后端地址。
- 为保证受限网络环境可构建，当前根布局使用系统字体 CSS 变量，不通过 `next/font/google` 在构建期下载外部字体。
- 组件内已有中文 docstring 和注释风格，新增复杂组件时保持一致。
