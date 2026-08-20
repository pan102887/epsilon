# Review Log — tui-approval-workflow

Append-only history of generator/evaluator interactions.

## Slice A（tasks 1.1–1.5）

- 任务 1.1 stream_resume_approval 端口方法：本轮与 1.2/1.3 合并为 Slice A 一次实现评审；改动 domain/chat/ports.py（纯签名+docstring，随源码切片一并送审）。
- 任务 1.2/1.3 抽取 `_resume_to_agent_result` 内核 + 新增 `stream_resume_approval`：修改 infrastructure/chat/chat_service_adapter.py。
- 任务 1.4/1.5 新增 test/infrastructure/chat/test_chat_service_stream_resume_unit.py（6 用例）。
- 生成侧自检：focused pytest 全绿（新增 6 通过 + 回归 hitl_unit/adapter 共 7 通过）；ruff 对本切片改动文件零告警（修正一处 F401 未用导入）。
- 说明：本环境无法调用 spec-evaluator 子代理（Agent 工具不可用），故记录为生成侧自检；正确性属性核对 Property4（续播事件与首次流同构，assistant_delta/done、approval_required 复用 approval_payload_to_metadata）、Property5（agent.resume 只调用一次，共用内核）、Property1（tool_call_id/数量/重复消费错误传播且未静默 approve）均由用例覆盖并通过。

## Slice C（tasks 3.1–3.2）

- 任务 3.1 + 3.2 | 尝试 1 | 校验：生成侧 focused check | 结果 PASS
  - evaluator（spec-evaluator）在当前环境不可用（Agent 工具未启用），改由本代理执行 focused 校验替代。
  - 校验内容：`PYTHONPATH=src uv run --frozen pytest test/application/cli/test_approval_mode_unit.py` → 7 passed；`ruff check` 针对两个新文件 → All checks passed（修复了 1 处 SIM300 Yoda condition：`_APPROVAL_MODES == frozenset(...)` 改为 `frozenset(...) == _APPROVAL_MODES`）。
  - pyright：本环境未安装 pyright（standalone 与 `python -m pyright` 均不可用），跳过，原因：工具缺失。
  - 交付：新增 `src/application/cli/approval_mode.py`（`evaluate_approval_mode` + `_APPROVAL_MODES`）、新增 `test/application/cli/test_approval_mode_unit.py`（7 用例）。
  - 正确性属性 2（不绕过高风险红线）：`evaluate_approval_mode` 仅在每个 action `policy_for(tool_name).interrupt is False` 且允许 approve 时返回全 approve 序列，任一 interrupt=True 即返回 None；风险唯一来源 policy_for，无工具名/分级硬编码——由 auto 高风险→None、同工具名注入不同策略翻转结果两用例覆盖。

## Slice D（tasks 4.1–4.3）

- 任务 4.1+4.2（新建 src/application/cli/approval_screen.py）+ 4.3（新建 test/application/cli/test_approval_screen_unit.py）| 尝试 1 | 校验：生成侧 focused check | 结果 PASS
  - evaluator（spec-evaluator）在当前环境不可用（Agent/Task 工具未启用），改由本代理执行 focused 自审替代。
  - 交付：新增 `src/application/cli/approval_screen.py`（Textual ModalScreen `ApprovalScreen`：决策状态机 + approve/reject/edit/submit_edit/cancel 动作 + allowed 门禁 + edit JSON 校验）；新增 `test/application/cli/test_approval_screen_unit.py`（6 用例）。
  - 自审对照：需求 2.2（展示 tool_name/risk_label/arguments/allowed_decisions）、2.4（_decision_allowed 门禁忽略不允许决策）、2.5/2.6（逐条推进 + 顺序一致产出）、3.1（edit 预填原 arguments）、3.2/3.3（非法 JSON 原地展示 str(exc)、保留 _editing、不推进不 dismiss 不提交）、3.4（合法 JSON 构造 EditedAction(name==原 tool_name)）；正确性属性 1（_decisions[i] 对应 actions[i]，产出顺序即 actions 顺序）、属性 3（edit 校验失败不推进/不关面板/不提交）；SRP 边界（不引用 CliRuntime/Port）；全量类型标注、禁裸 Any、中文 docstring、line-length=100 均满足。
  - Textual 8.2.7 动态刷新：进入/退出 edit 子状态与推进下一动作均用 `self.refresh(recompose=True)`（Textual 8.x 官方重组机制，先移除子节点再重新 compose）；compose 对 _index 越界（dismiss 收尾期间被调度的挂起重组）加守卫提前 return，避免 IndexError。
  - 结果：`PYTHONPATH=src uv run --frozen pytest test/application/cli/test_approval_screen_unit.py` → 6 passed；`ruff check` 针对两个新文件 → All checks passed（修复 1 处 RUF012：`BINDINGS` 增加 `ClassVar` 注解，与既有 tui.py 一致）。
  - pyright：本环境未安装，按同项目历史切片惯例跳过，原因：工具缺失。

## Slice B（tasks 2.1–2.3）

- 任务 2.1+2.2（改 src/application/cli/runtime.py）+ 2.3（扩充 test/application/cli/test_runtime.py）| 尝试 1 | 校验：生成侧 focused check | 结果 PASS
  - evaluator（spec-evaluator）在当前环境不可用（Agent 工具未启用，返回 "No such tool available: Agent"），改由本代理执行 focused 自审替代，与本项目 Slice C/D 历史处理一致。
  - 交付：runtime.py 新增 `approval_policy` 字段 + `start()` resolve `ApprovalPolicyPort`（只 resolve 不 new）+ `_require_approval_policy`（未启动抛 RuntimeError，与既有 `_require_*` 同构）+ `resume_main_agent_events`/`load_pending_actions`/`policy_for` 三方法；导入新增 `ApprovalPolicyPort`、`ApprovalPolicy`、`PendingActionRequest`、`ApprovalResumeRequestVO`（后者来自 domain.chat.value_objects）。test_runtime.py 新增 FakeApprovalPolicy、FakeChatService.stream_resume_approval、FakeApprovalStore.load/consume，新增/更新 7 处断言。
  - 自审对照：需求 1.4（resume_main_agent_events 委托 stream_resume_approval 逐个转发、签名与 stream_main_agent_events 对称——由 forwards_stream + symmetric 两用例覆盖）；需求 6.6（policy_for 透传 ApprovalPolicyPort.policy_for、resolve 经容器不硬编码分级——由 policy_for_delegates 用例覆盖）；load_pending_actions 只读不消费（consumed == [] 断言 + None/空批次返回 () 三用例覆盖）；DDD：application 层仅经 Port（ChatServicePort/ApprovalStateStorePort/ApprovalPolicyPort）编排，未直连基础设施。
  - 结果：`PYTHONPATH=src uv run --frozen pytest test/application/cli/test_runtime.py` → 20 passed；`ruff check src/application/cli/runtime.py test/application/cli/test_runtime.py` → All checks passed。
  - pyright：本环境未安装，按同项目历史切片惯例跳过，原因：工具缺失。

## Slice E（tasks 5.1–5.3）

- 任务 5.1（改 src/application/cli/runtime.py）+ 5.2（改 src/application/cli/commands.py）+ 5.3（扩充 test/application/cli/test_commands.py）| 尝试 1 | 校验：生成侧 focused check | 结果 PASS
  - evaluator（spec-evaluator）在当前环境不可用（Agent 工具未启用），改由本代理执行 focused 自审替代，与本项目 Slice A/B/C/D 历史处理一致。
  - 交付：runtime.py 新增 `async def list_pending_approvals(session_id) -> list[ApprovalInterruptSummary]`（approval_store is None 返回 []，否则薄封装 list_pending_by_session，只读不消费）；commands.py 新增 `from .approval_mode import _APPROVAL_MODES`、模块常量 `NO_PENDING_APPROVAL_MESSAGE`/`APPROVAL_USAGE`/`APPROVAL_MODE_USAGE`、handle 中 `/model` 后 `/config doctor` 前的 `/approval` 分支、`_handle_approval_command`、`_render_approval_overview`，HELP_TEXT 在 /model <name> 后增补 /approval 与 /approval mode 两行；test_commands.py FakeRuntime 新增 pending_approvals 字段与 list_pending_approvals（记录调用），新增 4 用例并更新 help 断言。
  - 自审对照：需求 5.1/5.3（无参展示模式 + approval_id/tool_names/过期时间）、5.4（无 pending 明确「暂无待处理审批」）、5.2（只经 list_pending_approvals→list_pending_by_session，calls 仅记录该调用，不消费）、5.5（/help 含 /approval 两行）、6.2（/approval mode manual 更新 state.approval_mode）、6.3（非法值返回用法且 state 不变、runtime.calls 为空）；`_APPROVAL_MODES` 复用 approval_mode.py 同一取值域，无漂移；正确性属性 6（pending 查询只读）由 test_approval_command_without_args 的 calls 断言覆盖。ApprovalInterruptSummary 真实字段核对：approval_id / tool_names / expires_at_epoch 均与 value_objects.py 一致。
  - 结果：`PYTHONPATH=src uv run --frozen pytest test/application/cli/test_commands.py` → 23 passed；`ruff check src/application/cli/commands.py src/application/cli/runtime.py test/application/cli/test_commands.py` → All checks passed。
  - pyright：本环境未安装，按同项目历史切片惯例跳过，原因：工具缺失。

## Slice F（tasks 6.1–6.5）

- 任务 6.1+6.2（改 src/application/cli/tui.py）+ 6.3（回归更新 test_tui_hitl_approval.py）+ 6.4（新建 test_tui_approval_flow_integration.py）+ 6.5（扩充 test_chat_service_hitl_unit.py）| 尝试 1 | 校验：生成侧 focused check | 结果 PASS
  - evaluator（spec-evaluator）在当前环境不可用（Agent 工具未启用），改由本代理执行 focused 自审替代，与本项目 Slice A–E 历史处理一致。
  - approval_required 处理位置的最终设计（渲染与驱动解耦）：`_handle_event` 保持纯渲染，不再在其内做 push_screen_wait 或切换事件源；改为在 `_run_agent_turn` 委托的续播协程 `_drive_events(event_source, assistant, assistant_content)` 内拦截 `kind=="approval_required"` 事件——遇到即 break 内层 `async for`，调用 `_resolve_approval(event)` 统一解析动作+判定+打开面板，返回续播事件源后由 `while event_source is not None` 外层循环切换 event_source 继续用 `_handle_event` 渲染，实现再次中断闭环。`_handle_event` 的 approval_required 分支保留为防御性回退（调用 `_render_approval_summary` 渲染 metadata 摘要），正常路径不会经过它（driver 已拦截）。
  - 续播协程结构：`_run_agent_turn` → try 内 `event_source = stream_main_agent_events(...)` → `await self._drive_events(...)`；`_drive_events` 单一 while 循环切换 event_source（首轮 stream → 续播 resume → 再续播…），CancelledError/error 由 `_run_agent_turn` 既有 except 分支与 `_handle_event` error 分支处理，取消路径沿用 `self._current_task.cancel()` 不变。`_resolve_approval` 负责：读取 load_pending_actions（空则 `_render_approval_summary` 回退并返回 None）→ evaluate_approval_mode 自动放行或打开面板 → 取消（None）时提示「已取消本次审批」返回 None → 否则返回 `resume_main_agent_events(...)` 事件源。
  - push_screen_wait 关键偏离与理由：设计文样例用 `push_screen_wait`，但 Textual 8.2.7 的 `push_screen_wait`（内部 `push_screen(..., wait_for_dismiss=True)`）在非 worker 上下文会抛 `NoActiveWorker`；本轮恢复运行在 `asyncio.create_task` 的 `_current_task` 内（为复用既有 `action_cancel` → `self._current_task.cancel()` 取消路径，需求 4.3），不是 Textual worker。故抽出 `_await_approval_screen(screen)` 用 `push_screen(screen, callback)` + Future 等价实现「打开面板并等待 dismiss 结果」语义（面板全部完成返回 list[ApprovalDecision]，Esc 取消返回 None），既满足需求 2.1/4.1，又不破坏 inline 取消语义。此为对 design §组件与接口 7 样例的实现级偏离，语义等价、不改对外行为。
  - 自审对照：需求 2.1（收到 approval_required 打开 ApprovalScreen，替代纯文本）、4.1（approve 后续播 assistant_delta/done）、4.2（续播再次 approval_required 再次打开面板闭环）、4.3（进行中 cancel 复用 CancelledError 路径展示「已中止」、session_id 不变仍可恢复）、4.4（续播 kind="error" 由既有 error 分支渲染并结束续播）、6.5（auto 模式高风险仍中断：真实 StaticApprovalPolicyProvider.policy_for("write_file").interrupt is True 配合 evaluate_approval_mode("auto",...) 返回 None）均由用例覆盖；空批次回退（load_pending_actions 返回 () → 渲染 action_summaries 摘要不崩溃）覆盖错误处理表·空 tuple 分支。
  - 结果：`PYTHONPATH=src uv run --frozen pytest test/application/cli/test_tui_hitl_approval.py test/application/cli/test_tui_approval_flow_integration.py test/application/cli/test_tui_textual.py test/infrastructure/chat/test_chat_service_hitl_unit.py` → 14 passed；扩展回归 `test/application/cli/ test/infrastructure/chat/` → 231 passed。`ruff check` 针对 tui.py + 三个测试文件 → All checks passed（修复 test 文件 1 处 I001 import 排序）。
  - pyright：本环境未安装，按同项目历史切片惯例跳过，原因：工具缺失。
