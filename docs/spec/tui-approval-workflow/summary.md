# 交付总结：本地 TUI 审批与高风险工具闭环（tui-approval-workflow）

落地 `TODO.md` P0.1「本地 coding-agent：TUI 审批与高风险工具闭环」。本地 Textual TUI 从「仅展示 resume API 提示」升级为完整的 inline 交互式审批闭环：中断 → 面板决策（approve/edit/reject）→ 流式续播 → 可再次中断。

## 最终产物

Spec 文档（`docs/spec/tui-approval-workflow/`）：`requirement.md`（7 条需求 + 术语表）、`design.md`（架构/时序、8 项设计决策、6 条正确性属性）、`tasks.md`（6 slice 全部勾选）、`review-log.md`（各 slice 生成侧自审记录）。

代码改动（`epsilon-boot/`）：

| 文件 | 改动 |
|---|---|
| `src/domain/chat/ports.py` | 新增 `ChatServicePort.stream_resume_approval` 端口方法 |
| `src/infrastructure/chat/chat_service_adapter.py` | 抽取共享内核 `_resume_to_agent_result`；新增 `stream_resume_approval`（流式恢复出口） |
| `src/application/cli/runtime.py` | 新增 `resume_main_agent_events` / `load_pending_actions` / `policy_for` / `list_pending_approvals`；`start()` resolve `ApprovalPolicyPort` |
| `src/application/cli/approval_mode.py`（新增） | 纯函数 `evaluate_approval_mode` + 取值域 `_APPROVAL_MODES` |
| `src/application/cli/approval_screen.py`（新增） | `ApprovalScreen(ModalScreen)` 交互式审批面板 |
| `src/application/cli/commands.py` | `/approval` 命令（查看模式/pending、切换本地策略）+ HELP_TEXT |
| `src/application/cli/tui.py` | `approval_required` 分支改造为打开面板 + `_drive_events` 续播闭环 |

测试：新增 4 个测试文件 + 扩充 4 个既有测试文件。

## 关键设计决策

1. **inline 流式恢复通路**：`stream_resume_approval` 与 `resume_approval` 共用 `_resume_to_agent_result` 内核，决策应用（approve/edit/reject）只在 `ReActAgentAdapter.resume` 一处，恢复路径不重复实现（需求 1.6 / Property 5）。
2. **Approval_Mode 三档**（ask/auto/manual）：`auto` 为面向未来扩展位，逐动作依 `ApprovalPolicyPort.policy_for` 判定，任一 `interrupt=True` 高风险动作即强制打开面板，绝不整批放行（Property 2）。
3. **edit 参数只读取回**：面板 edit 预填参数经 `ApprovalStateStorePort.load`（只读不消费）获取完整动作，不放宽事件 metadata 白名单，避免工具参数泄露到通用观测链路。
4. **续播文本整段呈现**：`AgentPort.resume()` 同步返回 `AgentResult`，无 token 分片，翻译为单段 `assistant_delta` + `assistant_done`，不侵入恢复内核。
5. **Esc 取消 = 中止并保留**：不提交决策、不消费审批状态，批次保留可再次恢复。
6. **trace 复用**：恢复路径不补写同批 `ApprovalTrace`，依赖首次中断已写记录，无并行追踪结构（需求 7）。

### 实现级偏离（已记录于 review-log）

- **`push_screen_wait` → `push_screen` + Future**：Textual 8.2.7 中 `push_screen_wait` 在非 worker 上下文抛 `NoActiveWorker`，而本轮恢复运行在普通 `asyncio.Task`（为复用 `action_cancel → self._current_task.cancel()` 取消路径）。故用 `push_screen(screen, callback)` + `asyncio.Future` 实现等价语义；并在恢复流取消时显式 `pop_screen` 清理悬空面板。语义等价，不改对外行为。

## 验证

- 本 feature 相关全部测试通过：`test/application/cli/` + `test/infrastructure/chat/` → 231 passed。
- 全量 `PYTHONPATH=src uv run --frozen pytest` → 2689 passed（另有 3 个 `KeyError: TraceStorePort` 失败位于 `test_container_config.py` / `test_run_container_wiring_unit.py`，经 `git stash` 验证为本 feature 引入前既已存在、与本次改动无关）。
- `ruff check .` → All checks passed。
- 独立 spec-evaluator 最终集成评审：**PASS**（7 个核查点全过，跨 slice 接口一致、高风险红线未绕过、DDD 分层合规、无死代码）。
- ⚠️ 未验证：`pyright` 未安装于本环境（非项目声明依赖）；类型注解完整、无裸 `Any` / `# type: ignore`。

## 后续可选项（非本 feature 范围）

- 前端 Web 控制台 HITL 审批（P1.4 / `frontend-hitl-trace`）。
- `ArtifactTrace` 与 `.epsilon/` 目录规范（P0.2 / P0.3 其余部分）。
- `epsilon exec --json` 结构化输出（P0.4）。
