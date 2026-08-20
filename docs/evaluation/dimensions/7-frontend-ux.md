# 维度 7：前端 / UX

## 评估结论

**评分：3 / 5**。`ChatPanel` + `useChat` 实现了 SSE 流式增量渲染、`[DONE]` 协议、`AbortController` 主动中止、错误态占位清理、模型选择与会话清除；`TaskWorkspace` 能展示 status / latency / model / trace。距离 4 分的主要差距：**聊天侧没有 trace 可见性**（仅任务侧返回 trace 数组）、**没有点赞/点踩/复制/重试等反馈通道**、**没有可访问性（a11y）验证**、**error 态仅为顶部全局横幅**。

## 证据与分析

- [`epsilon-client/src/hooks/use-chat.ts:L56-L128`](../../../epsilon-client/src/hooks/use-chat.ts)
  `useChat` Hook 通过 `abortRef` 保存当前请求的 `AbortController`，`abort()` 调用 `abortRef.current?.abort()`；`streamChat` 回调 `onChunk` 把 `delta_content` 追加到助手消息占位；`onError` 清理空占位消息并上报错误；`clearChat` 先 `abort()` 再 `clearSession(sessionId)`，符合"流式中止 → 会话清除 → UI 重置"的闭环。
- [`epsilon-client/src/lib/chat-api.ts:L86-L154`](../../../epsilon-client/src/lib/chat-api.ts)
  `streamChat` 用 `fetch` + `ReadableStream.getReader()` 解析 SSE：按 `\n` 拆行，保留不完整尾行到下次 decode；识别 `data:` 前缀并跳过 `[DONE]`；`AbortError` 不上报（用户主动中止不是错误），其余异常交给 `onError`。
- [`epsilon-client/src/components/chat/chat-panel.tsx:L35-L78`](../../../epsilon-client/src/components/chat/chat-panel.tsx)
  `ChatPanel` 组合 `ChatHeader` / `ModelSelector` / `MessageList` / `ChatInput`，把 `abort` 回调传给 `ChatInput.onAbort`、`clearChat` 传给 `ChatHeader.onClear`，错误态以顶部红色 banner + `role="alert"` 展示。
- [`epsilon-client/src/components/task/task-workspace.tsx:L61-L229`](../../../epsilon-client/src/components/task/task-workspace.tsx)
  `TaskWorkspace` 展示 `result.status / result.latency_ms / result.model / result.trace`；`trace` 为空时显示"本次任务未返回执行轨迹"。说明后端已把 `execution_trace` 暴露给前端，但只在 **任务侧** 可见；聊天侧没有接入等效的 trace 视图。

**UX 巡检清单**（详见 [`tests/evaluation/frontend/ux_probe.md`](../../../tests/evaluation/frontend/ux_probe.md)）罗列了 SSE / AbortController / 错误分类 / trace 可见性 / 反馈通道 / 无障碍 6 类人工巡检项。

## 业界框架对照

- **OpenAI — A Practical Guide to Building Agents（UX for Agent output）**：建议 Agent 输出应有"流式渲染 + 中止 + 失败重试"。前端已实现前二项，"失败重试"目前只能靠用户重新敲一次消息，缺少 UI 级的 `Retry` 按钮。
- **Anthropic — Building effective agents（Human feedback loops）**：鼓励在界面层收集显式反馈（点赞/点踩/纠错）进入评测回流。本项目当前没有任何反馈入口。
- **Nielsen Norman Group — Visibility of system status / Error prevention**（<https://www.nngroup.com/articles/ten-usability-heuristics/>）：要求"系统当前状态随时可见"。项目在加载态上有 `isLoading`，但没有显式的"已中止 / 已完成 / N 个工具调用"分级可见。
- **W3C WAI-ARIA Authoring Practices — 键盘可达 / 焦点管理**：红色错误 banner 用了 `role="alert"`，但未观察到 `aria-live` 其他区域（如流式消息气泡）的声明；需要完整的 ARIA 巡检。

## 改进建议

1. **P1 — 聊天侧暴露 trace 视图**：在 `MessageList` 下新增可折叠 `<ExecutionTrace>` 区域，订阅后端流里每轮的 tool_call / delegation 事件（需要后端配合在 SSE 里追加 `event: trace` 的分片）。引用 **Anthropic — Building effective agents** 对"Show reasoning when helpful"的建议。
2. **P1 — 用户反馈通道**：在每条助手消息气泡右侧加 "Copy / Retry / 👍 / 👎"四个动作，点踩带可选理由跟进；把反馈发送到新后端端点 `/api/feedback`，并在评测脚本中增加"反馈满意率"作为可选指标。引用 **Anthropic — Human feedback loops** / NN/g Usability Heuristics。
3. **P2 — 可访问性基线**：把 `aria-live="polite"` 加到流式气泡，`ChatInput` 的 loading 状态用 `aria-busy`；引入 axe-core 做 PR 级 a11y lint。
4. **P2 — 错误分级展示**：当前错误仅区分 `AbortError` 与其它；建议按"网络错误 / 4xx / 5xx / 流已结束但无 data"分类展示，不同类别给不同提示与操作入口。

<!-- AUTO-START: aggregate_scores -->
### 自动生成区块

**评分**：3 / 5，**权重**：0.08，**加权得分**：0.240

**人工打分理由**：`ChatPanel` + `useChat` 实现了 SSE 流式增量渲染、`[DONE]` 协议、`AbortController` 主动中止、错误态占位清理、模型选择与会话清除，`TaskWorkspace` 能展示 status / latency / model / execution_trace，满足 OpenAI "A Practical Guide to Building Agents — UX for Agent output" 对流式渲染与中止的要求，也部分满足 LangChain "LangGraph Agent patterns" 对状态可见性的建议。但聊天侧没有 trace 可见性、没有 Anthropic "Building effective agents — Human feedback loops" 所强调的点赞/点踩/复制/重试反馈通道，没有系统化的可访问性（a11y）验证，错误仅以顶部全局 banner 展示 而未做分级——距离 4 分仍有明显差距。

#### 全局自动化指标（供维度交叉参考）

| 指标 | 分子 | 分母 | 比率 | 样本数 |
| --- | --- | --- | --- | --- |
| `tool_call_success_rate` | 6 | 20 | 0.3000 | 20 |
| `delegation_correctness` | 6 | 15 | 0.4000 | 15 |
| `context_compaction_effectiveness` | 36 | 36 | 1.0000 | 36 |

#### 证据清单（来自 `tests/evaluation/evidence/catalog.py`）

- `epsilon-client/src/hooks/use-chat.ts:56-128`
- `epsilon-client/src/lib/chat-api.ts:86-154`
- `epsilon-client/src/components/chat/chat-panel.tsx:35-78`
- `epsilon-client/src/components/task/task-workspace.tsx:61-229`

<!-- AUTO-END: aggregate_scores -->
