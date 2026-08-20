# 前端 / UX 人工巡检清单（`ux_probe`）

> 本清单对齐 `design.md` 组件 7 与 `docs/evaluation/dimensions/7-frontend-ux.md`。
> 本期不跑 `bun` / `npm` 脚本，所有巡检项由评测者在本地 dev server 或 staging
> 环境按清单逐条勾选；发现的问题作为 `Improvement_Recommendation` 录入主报告。

## 巡检范围

- 前端代码仅限 `epsilon-client/src/`。
- 本期评测不引入自动化 UI 测试；后续可在 `epsilon-client/package.json`
  下新增独立脚本（不影响既有 `dev` / `build` / `start` / `lint`）。

## 巡检项清单

### 1. SSE 完整性

- [ ] 正常流式：连续回复超过 30 个分片，UI 增量拼接顺序与后端 `delta_content` 一致，无重复、无缺字。
  - 证据：`epsilon-client/src/lib/chat-api.ts:L112-L141`（`ReadableStream` + `\n` 切分 + 尾行保留）
- [ ] `[DONE]` 标记：收到 `[DONE]` 后调用 `onDone`，`isLoading` 回落、`abortRef` 清空。
  - 证据：`epsilon-client/src/lib/chat-api.ts:L128-L133`
- [ ] 上游异常：人为让后端抛异常（触发 `_event_generator` 的 `except Exception`），前端收到 `error` 字段 → UI 红色 banner + 空占位消息被清理。
  - 证据：后端 `epsilon-boot/src/application/routers/chat.py:L124-L133` / 前端 `use-chat.ts:L111-L122`

### 2. AbortController 行为

- [ ] 流式进行中点击 "Stop"：`abortRef.current.abort()` 被调用，fetch reader 收到 `AbortError`；`streamChat` 静默返回，`isLoading=false`。
  - 证据：`epsilon-client/src/hooks/use-chat.ts:L63-L67`
- [ ] 流式进行中触发 `clearChat`：应先 `abort()` 再请求 `DELETE /api/chat/sessions/<id>`；后端 `clear_session` 返回 200 时 UI 消息列表清空。
- [ ] 重复点击 `abort`：无多次 toast、无异常；`abortRef` 幂等置 `null`。

### 3. 模型选择与会话管理

- [ ] 模型下拉：切换模型后下一条消息 `ChatRequest.model` 字段为新模型名；进行中的流不会被中断。
- [ ] 会话 ID：`sessionId` 变更后 `useChat` 自动清空历史，输入新消息后后端 `ChatRequestVO.session_id` 与前端一致。

### 4. trace / execution_trace 可见性

- [ ] 任务执行：`/api/task/execute` 返回的 `trace` 在 `TaskWorkspace` 可见；`step` / `action` / `timestamp_ms` / `detail` 四字段完整。
  - 证据：`epsilon-client/src/components/task/task-workspace.tsx:L199-L222`
- [ ] **缺口**：聊天侧（`ChatPanel` / `MessageList`）无 trace 视图；工具调用次数、延迟、token 用量都无法在聊天界面直接查看。
  - 登记为 `Improvement_Recommendation`（见 `docs/evaluation/dimensions/7-frontend-ux.md` 改进建议 1）。

### 5. 错误态与失败重试

- [ ] 错误 banner：`role="alert"` 使用正确；屏幕阅读器能读到红色提示。
  - 证据：`epsilon-client/src/components/chat/chat-panel.tsx:L65-L72`
- [ ] **缺口**：无 `Retry` 按钮；失败后用户只能重新敲一次消息，无法对原消息直接重试。
- [ ] **缺口**：错误未按类型分级（`AbortError` 与其它错误都汇到 `error` 字段）。

### 6. 反馈通道（P1 缺失）

- [ ] 点赞 / 点踩 / 复制 / 重试：当前无任何反馈入口。
- [ ] 跟进理由：无法在点踩时填理由，评测数据无法闭环回流。
- [ ] 建议：新增 `/api/feedback` 端点 + 评测脚本增加"反馈满意率"指标。

### 7. 可访问性（a11y）

- [ ] 流式气泡：当前 `MessageList` 未声明 `aria-live`；屏幕阅读器无法实时读到新分片。
- [ ] `ChatInput` loading 态：未声明 `aria-busy` / `aria-disabled`。
- [ ] 键盘导航：Tab 顺序应为"输入框 → 发送 → 中止 → 模型下拉 → 清空"。
- [ ] 颜色对比：错误 banner（红底红字）需要在主 / 辅助色环境下复验 WCAG AA。

## 巡检结果归档

- 勾选结果 / 发现的缺陷以一次性截图 + 说明写入 `docs/evaluation/results/<timestamp>_ux_probe.md`（阶段 6 主报告交付时产出）；
- 已知 P1 / P2 缺陷同步登记到主报告的 "改进清单" 章节。

## 引用框架条款

- **OpenAI — A Practical Guide to Building Agents**：UX for Agent output（流式 / 中止 / 失败重试）。
- **Anthropic — Building effective agents**：Human feedback loops（点赞点踩回流到评测集）。
- **Nielsen Norman Group — 10 Usability Heuristics**：Visibility of system status / Error prevention / User control and freedom。
- **W3C WAI-ARIA Authoring Practices**：`role="alert"` / `aria-live` / 键盘可达焦点管理。
