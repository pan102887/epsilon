# 实现计划：本地 TUI 审批与高风险工具闭环（tui-approval-workflow）

## 概述

本计划将 `design.md` 拆分为 6 个可独立评审的 slice（A–F），依赖顺序为：基建/后端流式恢复通路（A）→ CliRuntime 编排入口（B）→ Approval_Mode 纯函数（C）→ ApprovalScreen 面板（D）→ `/approval` 命令（E）→ tui.py 集成（F）。每个 slice 均包含「实现任务 + 验证任务（pytest + ruff + pyright）+ checkpoint（可评审边界）」。

约束与事实（binding）：

- 后端根 `epsilon-boot/`，源码 `src/`，导入根 `src`。全量测试：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest`；单测：`PYTHONPATH=src uv run --frozen pytest test/path::test_name`。
- `ruff check .` 与 `pyright` 零新增告警为每个 checkpoint 的硬门槛（见 `docs/steering/python-typing-lint.md`）。
- 依赖管理仅 `uv`，禁 `pip`/`poetry`。
- DDD 分层依赖方向：Port 定义于 `domain/`，Adapter 于 `infrastructure/`，`CliRuntime`/`TUI`/`SlashCommandRouter`/`ApprovalScreen`/`approval_mode` 归 `application/cli/`；`application` 层不得绕过 Port 直连基础设施，只在 `CliRuntime.start()` 经容器 resolve（见 `docs/steering/ddd-architecture.md`）。
- 所有新增模块/类/公开方法配中文 docstring（见 `docs/steering/code-documentation.md`），单一职责（见 `docs/steering/srp-principle.md`）。
- **不新增任何值对象/DTO**，全部复用既有 `frozen dataclass` 领域值对象；**不重写** HITL v1 的决策应用、审批校验、trace 写入逻辑（复用 `ChatServiceAdapter.resume_approval` 内核与 `ReActAgentAdapter.resume`）。
- 文件修改一律保留原换行符，优先局部替换（Edit），禁止整文件重写。

新增文件（纯新增）：

- `epsilon-boot/src/application/cli/approval_mode.py`
- `epsilon-boot/src/application/cli/approval_screen.py`
- `epsilon-boot/test/infrastructure/chat/test_chat_service_stream_resume_unit.py`
- `epsilon-boot/test/application/cli/test_approval_mode_unit.py`
- `epsilon-boot/test/application/cli/test_approval_screen_unit.py`
- `epsilon-boot/test/application/cli/test_tui_approval_flow_integration.py`

修改文件（既有）：

- `epsilon-boot/src/domain/chat/ports.py`
- `epsilon-boot/src/infrastructure/chat/chat_service_adapter.py`
- `epsilon-boot/src/application/cli/runtime.py`
- `epsilon-boot/src/application/cli/commands.py`
- `epsilon-boot/src/application/cli/tui.py`
- `epsilon-boot/test/application/cli/test_runtime.py`（扩充）
- `epsilon-boot/test/application/cli/test_commands.py`（扩充）
- `epsilon-boot/test/application/cli/test_tui_hitl_approval.py`（回归更新）
- `epsilon-boot/test/infrastructure/chat/test_chat_service_hitl_unit.py`（扩充回归）

## Tasks

- [x] 1. Slice A — 后端流式恢复通路（ChatServicePort + ChatServiceAdapter）
  - [x] 1.1 在 `ChatServicePort` 新增 `stream_resume_approval` 端口方法（修改既有文件）
    - 修改 `epsilon-boot/src/domain/chat/ports.py`：在 `ChatServicePort`（Protocol）内、`resume_approval` 附近新增方法签名 `def stream_resume_approval(self, request: ApprovalResumeRequestVO) -> AsyncIterator[AgentStreamEvent]: ...`，配中文 docstring（说明与 `stream_chat_events` 同构、自然完成产出 `assistant_delta`/`assistant_done`、再次中断产出 `approval_required`、决策应用复用 `resume_approval` 内核）。
    - `ApprovalResumeRequestVO` 与 `AgentStreamEvent` 已在文件顶部 `TYPE_CHECKING` 块导入，无需新增导入；保留文件原换行符，局部替换。
    - 对应设计组件：design §组件与接口 1；数据模型复用 `ApprovalResumeRequestVO`/`AgentStreamEvent`。
    - _需求: 1.1_
  - [x] 1.2 从 `resume_approval` 抽取共享内核 `_resume_to_agent_result`（修改既有文件）
    - 修改 `epsilon-boot/src/infrastructure/chat/chat_service_adapter.py`：将现有 `resume_approval`（第 590–651 行）主体中「load → `is_expired` → 数量校验 → 顺序与 `allowed_decisions` 校验 → `consume` → `ConversationContext.from_dict` → `_resolve_model_access` → 构造 `AgentConfig` → `agent.resume(...)`」抽为私有异步方法 `async def _resume_to_agent_result(self, request: ApprovalResumeRequestVO) -> tuple[ConversationContext, AgentResult]`，返回 `(context, agent_result)`。
    - 将 `resume_approval` 改写为：`context, agent_result = await self._resume_to_agent_result(request)` → 复用既有 `_save_context_for_agent_result(...)` → `return self._to_chat_response(...)`，保持对外签名与返回语义不变。
    - 顶部 `import time` 已存在于模块（`resume_approval` 内局部 `import time` 可保留或上提，保持最小改动）；不改动任何校验逻辑，仅做提取，满足需求 1.6「不重复实现动作应用」。
    - 对应设计组件：design §组件与接口 2；正确性属性 5。
    - _需求: 1.6_
  - [x] 1.3 新增 `ChatServiceAdapter.stream_resume_approval`（修改既有文件）
    - 修改 `epsilon-boot/src/infrastructure/chat/chat_service_adapter.py`：新增 `async def stream_resume_approval(self, request: ApprovalResumeRequestVO) -> AsyncIterator[AgentStreamEvent]`，实现为：`context, agent_result = await self._resume_to_agent_result(request)` → `await self._save_context_for_agent_result(...)` → 当 `agent_result.status == "approval_required"` 时产出 `AgentStreamEvent(kind="approval_required", content=<既有提示文本>, usage=agent_result.usage, metadata=approval_payload_to_metadata(approval))` 并 `return`；否则 `content` 非空时产出 `assistant_delta`，再统一产出 `assistant_done`（`metadata={"terminated_reason": agent_result.terminated_reason}`）。
    - 新增导入：`from infrastructure.agent.approval_serialization import approval_payload_to_metadata`（同层，符合依赖方向）；`AgentStreamEvent` 已导入。
    - paused（`terminated_reason in ("max_rounds","token_budget_exceeded")`）时 `content` 为空、仅产出 `assistant_done`，与 design §组件与接口 2「说明」一致，本特性不新增 paused 续跑 UI。
    - 对应设计组件：design §组件与接口 2；正确性属性 4。
    - _需求: 1.2、1.3、4.2_
  - [x] 1.4 验证：新增 `stream_resume_approval` 单测（新增文件）
    - 创建 `epsilon-boot/test/infrastructure/chat/test_chat_service_stream_resume_unit.py`：用假 `AgentPort`（可返回 `AgentResult(status="completed", content=...)` 或 `status="approval_required"`）与假 `ApprovalStateStorePort` 驱动。
    - 用例覆盖：(a) 自然完成产出 `assistant_delta` + `assistant_done`（1.2）；(b) 恢复后再次中断产出 `kind="approval_required"`，断言 metadata 含新的 `session_id`/`approval_id`/`action_summaries`（1.3/4.2，形态与 `approval_payload_to_metadata` 一致）；(c) `_resume_to_agent_result` 与 `resume_approval` 共用内核——用记录调用次数的假 `agent.resume` 断言恢复路径只调用一次 `agent.resume`，且无第二份决策应用代码（1.6/Property5）。
    - _需求: 1.2、1.3、1.6、4.2_
  - [x] 1.5 验证：tool_call_id 不匹配 / 数量不匹配 / 重复恢复的错误传播单测（同上新增文件追加）
    - 在 `test_chat_service_stream_resume_unit.py` 追加用例：tool_call_id 顺序/不匹配 → `ApprovalDecisionOrderMismatchError`（60024），断言不以 approve 静默执行任何动作；决策数量与动作数不一致 → `ApprovalDecisionCountMismatchError`（60023）；对同一 `approval_id` 重复恢复（第二次 `consume` 返回 `None`）→ `ApprovalConsumedError`（60022）。这些错误由 `_resume_to_agent_result` 内既有校验抛出并经 `stream_resume_approval` 传播。
    - 对应设计组件：design §错误处理表；正确性属性 1。
    - _需求: 1.5_
  - [x] 1.6 Checkpoint：Slice A 编译/测试/类型门槛（pytest 通过；ruff 通过；⚠️ pyright 未验证：本环境未安装）
    - 运行 `cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/chat/test_chat_service_stream_resume_unit.py`。
    - 运行既有回归 `PYTHONPATH=src uv run --frozen pytest test/infrastructure/chat/test_chat_service_hitl_unit.py`（确保 `resume_approval` 抽取内核后同步路径不回归）。
    - 运行 `ruff check .` 与 `pyright`，零新增告警。
    - 验证边界：domain 端口新增 + infrastructure 内核抽取与流式出口，未触碰 application 层。

- [x] 2. Slice B — CliRuntime 编排入口
  - [x] 2.1 `CliRuntime` 新增 `approval_policy` 字段与 `_require_approval_policy` 并在 `start()` resolve（修改既有文件）
    - 修改 `epsilon-boot/src/application/cli/runtime.py`：`__init__` 新增 `self.approval_policy: ApprovalPolicyPort | None = None`（置于 `self.approval_store` 声明附近）；`start()` 新增 `self.approval_policy = await self._container.resolve(ApprovalPolicyPort)`（`ApprovalPolicyPort` 已由 `container_config` 注册的 `StaticApprovalPolicyProvider` 绑定，此处只 resolve 不 new 实例）；新增 `_require_approval_policy(self) -> ApprovalPolicyPort`（与既有 `_require_*` 同构，未启动抛 `RuntimeError`）。
    - 新增导入：`from domain.agent.ports import ApprovalPolicyPort`（`ApprovalStateStorePort` 已导入）；`from domain.agent.value_objects import ...` 追加 `ApprovalPolicy, PendingActionRequest`（该行已导入 `AgentStreamEvent, ApprovalDecision, ApprovalInterruptSummary`）。保留原换行符，局部替换。
    - 对应设计组件：design §组件与接口 3；架构图 `RT --> APP`。
    - _需求: 6.6_
  - [x] 2.2 `CliRuntime` 新增 `resume_main_agent_events` / `load_pending_actions` / `policy_for`（修改既有文件）
    - 修改同文件：新增 `async def resume_main_agent_events(self, session_id, approval_id, decisions: list[ApprovalDecision], *, model: str | None = None) -> AsyncIterator[AgentStreamEvent]`，构造 `ApprovalResumeRequestVO(session_id, approval_id, decisions=tuple(decisions), model=model)` 后 `async for event in self._require_chat_service().stream_resume_approval(request): yield event`（与 `stream_main_agent_events` 对称）。
    - 新增 `async def load_pending_actions(self, session_id, approval_id) -> tuple[PendingActionRequest, ...]`：`approval_store is None` 或批次不存在（`load` 返回 `None`）时返回 `()`；否则返回 `interrupt.actions`（只读 `ApprovalStateStorePort.load`，不消费）。
    - 新增 `def policy_for(self, tool_name: str) -> ApprovalPolicy`：`return self._require_approval_policy().policy_for(tool_name)`。
    - 新增导入 `ApprovalResumeRequestVO`（来自 `domain.chat.value_objects`，需确认该模块已在文件导入区，若无则新增导入行）。全部方法配中文 docstring。
    - 对应设计组件：design §组件与接口 3。
    - _需求: 1.4、6.6_
  - [x] 2.3 验证：`test_runtime.py` 扩充（修改既有文件）
    - 修改 `epsilon-boot/test/application/cli/test_runtime.py`：新增用例断言 `resume_main_agent_events` 逐个转发 `stream_resume_approval` 产出的事件、签名与流式语义与 `stream_main_agent_events` 对称（用假 `ChatServicePort`）；`load_pending_actions` 命中批次返回完整 `actions`、空批次/`approval_store is None` 返回 `()`（只读、不调用 `consume`）；`policy_for` 透传假 `ApprovalPolicyPort.policy_for` 的返回。
    - _需求: 1.4、6.6_
  - [x] 2.4 Checkpoint：Slice B 编译/测试/类型门槛（CLI 全量 68 passed；ruff 通过；⚠️ pyright 未验证：本环境未安装）
    - 运行 `cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/application/cli/test_runtime.py`。
    - 运行 `ruff check .` 与 `pyright`，零新增告警。
    - 验证边界：application 层只经 Port（`ChatServicePort`/`ApprovalStateStorePort`/`ApprovalPolicyPort`）编排，未直连基础设施实现。

- [x] 3. Slice C — Approval_Mode 纯函数
  - [x] 3.1 新增 `approval_mode.py` 与 `evaluate_approval_mode` + `_APPROVAL_MODES`（新增文件）
    - 创建 `epsilon-boot/src/application/cli/approval_mode.py`：模块级中文 docstring；`from __future__ import annotations`；导入 `from collections.abc import Callable`、`from domain.agent.value_objects import ApprovalDecision, ApprovalPolicy, PendingActionRequest`。
    - 定义模块级取值域常量 `_APPROVAL_MODES = frozenset({"ask", "auto", "manual"})`（供 `commands.py` 复用，避免漂移）。
    - 实现 `def evaluate_approval_mode(mode: str, actions: tuple[PendingActionRequest, ...], policy_for: Callable[[str], ApprovalPolicy]) -> list[ApprovalDecision] | None`：`mode == "manual"` 恒返回 `None`；`mode == "auto"` 仅当**每个** action 的 `policy_for(a.tool_name).interrupt is False` 且 `"approve" in a.allowed_decisions` 时返回与 actions 顺序一致的全 `approve` 决策序列，任一 `interrupt is True` 即返回 `None`；其它值（含 `ask` 与非法值）返回 `None`。docstring 显式说明 `auto` 为面向未来的扩展位与不绕过高风险红线的安全约束；严禁硬编码工具名/风险分级，风险来源唯一为 `policy_for`。
    - 对应设计组件：design §组件与接口 4；正确性属性 2。
    - _需求: 6.1、6.4、6.5、6.6_
  - [x] 3.2 验证：新增 `test_approval_mode_unit.py`（新增文件）
    - 创建 `epsilon-boot/test/application/cli/test_approval_mode_unit.py`：用注入的假 `policy_for`（返回构造的 `ApprovalPolicy`）驱动。用例：manual 恒 `None`；auto 且整批低风险（`interrupt=False`、含 `approve`）返回与 actions 顺序一致的全 approve 序列；auto 含任一高风险（某 action `policy_for.interrupt=True`）返回 `None`；ask/非法值返回 `None`；断言判定只依赖注入的 `policy_for`，函数内无工具名硬编码（可断言未预期分级）。
    - _需求: 6.1、6.4、6.5、6.6_
  - [x] 3.3 Checkpoint：Slice C 编译/测试/类型门槛（pytest 通过；ruff 通过；⚠️ pyright 未验证：本环境未安装）
    - 运行 `cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/application/cli/test_approval_mode_unit.py`。
    - 运行 `ruff check .` 与 `pyright`，零新增告警。
    - 验证边界：纯函数模块，无副作用、无 I/O、不依赖 Textual。

- [x] 4. Slice D — ApprovalScreen 交互式面板
  - [x] 4.1 新增 `approval_screen.py` 与 `ApprovalScreen(ModalScreen)` 骨架与决策状态机（新增文件）
    - 创建 `epsilon-boot/src/application/cli/approval_screen.py`：模块级中文 docstring；`from __future__ import annotations`；`import json`；`from textual.app import ComposeResult`、`from textual.screen import ModalScreen`；`from domain.agent.value_objects import ApprovalDecision, EditedAction, PendingActionRequest`。
    - 定义 `class ApprovalScreen(ModalScreen[list[ApprovalDecision] | None])`，`__init__(self, actions: tuple[PendingActionRequest, ...], risk_labels: dict[str, str]) -> None`，字段 `_actions`/`_risk_labels`/`_decisions: list[ApprovalDecision]`/`_index=0`/`_editing=False`；`BINDINGS = [("a","approve",...),("e","edit",...),("r","reject",...),("escape","cancel",...)]`。
    - 实现 `compose`（展示当前 `_actions[_index]` 的 `tool_name`、`risk_labels[tool_name]`、`arguments`、`allowed_decisions`，及 edit 时的 JSON 编辑区）、`_decision_allowed(decision_type) -> bool`（`decision_type in self._actions[self._index].allowed_decisions`）、`_advance_or_finish(decision)`（记录决策、`_index += 1`，覆盖全部 actions 时 `dismiss(self._decisions)`）。全部方法配中文 docstring。
    - 对应设计组件：design §组件与接口 5；正确性属性 1（决策顺序与动作顺序严格一致）。
    - _需求: 2.2、2.3、2.5、2.6_
  - [x] 4.2 实现 approve/reject/edit/submit_edit/cancel 动作与 allowed 门禁 + edit JSON 校验（同上新增文件）
    - 在 `approval_screen.py` 补齐：`action_approve` / `action_reject`——当 `_decision_allowed(...)` 为假时忽略（禁止提交不允许的决策类型，需求 2.4），否则构造对应 `ApprovalDecision(type=..., tool_call_id=当前 action.tool_call_id)` 并 `_advance_or_finish`。
    - `action_edit`——`_decision_allowed("edit")` 为假时忽略；否则进入 `_editing=True`，展示预填当前 `arguments`（JSON 字符串）的可编辑区（需求 3.1）。
    - `action_submit_edit`——对编辑区文本 `json.loads` 校验；`json.JSONDecodeError` 时原地展示 `str(exc)`、保留 `_editing=True`、不推进不 `dismiss`不提交（需求 3.3/Property3）；成功则构造 `ApprovalDecision(type="edit", tool_call_id=当前 action.tool_call_id, edited_action=EditedAction(name=当前 action.tool_name, arguments=校验通过文本))` 并 `_advance_or_finish`（需求 3.4）。
    - `action_cancel`——`dismiss(None)`（取消整个审批，语义为中止本轮恢复）。
    - 对应设计组件：design §组件与接口 5；正确性属性 3；错误处理·edit JSON 校验失败。
    - _需求: 2.4、3.1、3.2、3.3、3.4_
  - [x] 4.3 验证：新增 `test_approval_screen_unit.py`（新增文件）
    - 创建 `epsilon-boot/test/application/cli/test_approval_screen_unit.py`：沿用 Textual `app.run_test()` / `pilot.pause()` 模式（参照 `test/application/cli/test_tui_hitl_approval.py`）。用例：逐条推进顺序与 `actions` 一致、产出 `list[ApprovalDecision]` 顺序即 actions 顺序（2.5/2.6/Property1）；`allowed_decisions` 不含某类型时该 action 提交被忽略/置灰（2.4）；edit 预填原 `arguments`（3.1）；edit 非法 JSON 原地报错、不推进不关面板不提交（3.2/3.3）；edit 合法 JSON 构造 `EditedAction(name == 原 tool_name)`（3.4）；Esc 取消 `dismiss(None)`（4.3 语义）。
    - _需求: 2.4、2.5、2.6、3.1、3.2、3.3、3.4_
  - [x] 4.4 Checkpoint：Slice D 编译/测试/类型门槛（pytest 通过；ruff 通过；⚠️ pyright 未验证：本环境未安装）
    - 运行 `cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/application/cli/test_approval_screen_unit.py`。
    - 运行 `ruff check .` 与 `pyright`，零新增告警。
    - 验证边界：`ApprovalScreen` 只负责决策采集与 JSON 校验，不做恢复编排、不引用 `CliRuntime`/Port（SRP）。

- [x] 5. Slice E — /approval 斜杠命令
  - [x] 5.1 `CliRuntime.list_pending_approvals` 薄封装（修改既有文件）
    - 修改 `epsilon-boot/src/application/cli/runtime.py`：新增 `async def list_pending_approvals(self, session_id: str) -> list[ApprovalInterruptSummary]`，`approval_store is None` 时返回 `[]`，否则 `return await self.approval_store.list_pending_by_session(session_id)`（只读，不消费，满足需求 5.2）。配中文 docstring。`ApprovalInterruptSummary` 已在文件导入。
    - 对应设计组件：design §组件与接口 6；正确性属性 6。
    - _需求: 5.2_
  - [x] 5.2 `SlashCommandRouter` 新增 `/approval` 分支与帮助文本（修改既有文件）
    - 修改 `epsilon-boot/src/application/cli/commands.py`：在 `handle` 的 `/model` 分支后、`/config doctor` 前新增 `if command == "/approval" or command.startswith("/approval "): return await self._handle_approval_command(command, state)`。
    - 新增 `async def _handle_approval_command(self, command, state) -> CommandResult`：`rest = command.removeprefix("/approval").strip()`，无参 → `_render_approval_overview`；`action == "mode"` 且 `value in _APPROVAL_MODES` → 更新 `state.approval_mode = value` 并返回切换提示，否则返回 `APPROVAL_MODE_USAGE`（不改 state，需求 6.3）；其它 → 返回 `APPROVAL_USAGE`。
    - 新增 `async def _render_approval_overview(self, state) -> CommandResult`：`summaries = await self._runtime.list_pending_approvals(state.session_id)`；首行展示当前 `state.approval_mode`；无 pending 时明确输出「暂无待处理审批」（需求 5.4）；有则逐条展示 `approval_id`、`tool_names`、过期时间（复用 `_format_epoch(summary.expires_at_epoch)`，与既有 `_format_resume_result` 一致）。
    - 新增模块级常量 `APPROVAL_USAGE`、`APPROVAL_MODE_USAGE`；`_APPROVAL_MODES` 从 `approval_mode.py` 导入（`from .approval_mode import _APPROVAL_MODES`）以复用同一取值域。
    - 更新 `HELP_TEXT`：在 `/model <name>` 后增补 `/approval` 与 `/approval mode <ask|auto|manual>` 两行（需求 5.5）。保留原换行符，局部替换。
    - 对应设计组件：design §组件与接口 6；数据模型 `_APPROVAL_MODES`。
    - _需求: 5.1、5.3、5.4、5.5、6.1、6.2、6.3_
  - [x] 5.3 验证：`test_commands.py` 扩充（修改既有文件）
    - 修改 `epsilon-boot/test/application/cli/test_commands.py`：用假 `CliRuntime`（提供 `list_pending_approvals`）。用例：`/approval` 无参展示当前模式与 pending 概览（含 `approval_id`/`tool_names`/过期时间）（5.1/5.3）；无 pending 时输出「暂无待处理审批」（5.4）；`/approval mode manual` 更新 `state.approval_mode`（6.2）；`/approval mode <非法值>` 返回用法提示且 `state.approval_mode` 不变（6.3）；`/help`（`HELP_TEXT`）含 `/approval`（5.5）；断言只调用 `list_pending_by_session`（经 `list_pending_approvals`）不消费（5.2）。
    - _需求: 5.1、5.2、5.3、5.4、5.5、6.2、6.3_
  - [x] 5.4 Checkpoint：Slice E 编译/测试/类型门槛（CLI 全量 72 passed；ruff 通过；⚠️ pyright 未验证：本环境未安装）
    - 运行 `cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/application/cli/test_commands.py`。
    - 运行 `ruff check .` 与 `pyright`，零新增告警。
    - 验证边界：`/approval` 只读查询与本地状态切换，不触发恢复、不消费审批状态。

- [x] 6. Slice F — tui.py 集成（approval_required 分支改造 + 续播闭环）
  - [x] 6.1 改造 `_handle_event` 的 `approval_required` 分支为打开 ApprovalScreen + Approval_Mode 判定（修改既有文件）
    - 修改 `epsilon-boot/src/application/cli/tui.py`：将 `_handle_event` 中 `approval_required` 分支（现第 256–279 行的纯文本渲染）改造为：从 `event.metadata` 读取 `session_id`/`approval_id` → `actions = await self._runtime.load_pending_actions(session_id, approval_id)`；`actions` 为空（批次已过期/清理）时回退到用 `event.metadata["action_summaries"]` 渲染纯文本提示（无 arguments，edit 不可用，不崩溃，见错误处理表）。
    - 非空时：`decisions = evaluate_approval_mode(self._state.approval_mode, actions, self._runtime.policy_for)`；若返回自动 approve 序列则不打开面板；否则 `risk_labels = {a.tool_name: self._runtime.policy_for(a.tool_name).risk_label for a in actions}` 后 `decisions = await self.push_screen_wait(ApprovalScreen(actions, risk_labels))`。
    - 新增导入：`from .approval_mode import evaluate_approval_mode`、`from .approval_screen import ApprovalScreen`。保留原换行符，局部替换。
    - 由于续播需要在同一 `_run_agent_turn` 任务内串接，`approval_required` 的处理与续播编排应从 `_handle_event`（纯渲染）上移或以协作方式暴露给 `_run_agent_turn`（见 6.2）；本子任务先落地「读取 actions + 判定 + 打开面板/自动放行」并向调用方返回决策结果。
    - 对应设计组件：design §组件与接口 7；架构时序图。
    - _需求: 2.1、6.5_
  - [x] 6.2 `_run_agent_turn` 事件驱动续跑：收到 approval_required 后转入 resume_main_agent_events（修改既有文件）
    - 修改同文件 `_run_agent_turn`（现第 161–180 行）：在遍历 `stream_main_agent_events` 事件时，当收到 `kind="approval_required"` 事件，先按 6.1 得到 `session_id`/`approval_id`/`decisions`；`decisions is None`（用户 Esc 取消）时中止本轮恢复、不提交决策、不消费审批状态（保留可再次恢复），复用既有取消语义结束续播；否则结束当前 `stream_main_agent_events` 循环，转入 `async for event in self._runtime.resume_main_agent_events(session_id, approval_id, decisions, model=self._state.model)` 循环，继续用 `_handle_event` 渲染 `assistant_delta`/`assistant_done`/`tool_*` 事件（需求 4.1）。
    - 再次中断：若续播过程中再次产出 `kind="approval_required"`，再次执行 6.1 打开 `ApprovalScreen` 并再次进入 `resume_main_agent_events`（循环闭环，需求 4.2）。
    - 取消与错误：保持 `_run_agent_turn` 单一事件循环与既有取消路径——`action_cancel` 沿用 `self._current_task.cancel()`（inline 无 `_active_run_id`），`asyncio.CancelledError` 分支展示「已中止」，会话不进入不可恢复状态（需求 4.3）；续播流产出 `kind="error"` 事件时由 `_handle_event` 既有 error 分支渲染并结束本轮续播（需求 4.4）。
    - 实现应抽出内部续播协程（如 `_stream_and_handle(event_source)` 或以循环变量切换 `event_source`）以避免 `_run_agent_turn` 函数体过长、多阶段混杂（SRP）。
    - 对应设计组件：design §组件与接口 7；错误处理·取消/error 事件。
    - _需求: 4.1、4.2、4.3、4.4_
  - [x] 6.3 回归更新：`test_tui_hitl_approval.py` 断言改为「打开 ApprovalScreen」（修改既有文件）
    - 修改 `epsilon-boot/test/application/cli/test_tui_hitl_approval.py`：将原断言「渲染纯文本审批提示」更新为「收到 `approval_required` 时打开 `ApprovalScreen`」（需求 2.1 替换语义），保持流程不崩溃断言；用假 runtime 提供 `load_pending_actions`/`policy_for`/`resume_main_agent_events`。
    - _需求: 2.1_
  - [x] 6.4 验证：新增 `test_tui_approval_flow_integration.py`（新增文件）
    - 创建 `epsilon-boot/test/application/cli/test_tui_approval_flow_integration.py`：用假 runtime 驱动 `_EpsilonTextualApp.run_test()`。场景：首次 `approval_required` → 打开 `ApprovalScreen` → 提交 approve → `resume_main_agent_events` 续播 `assistant_delta`/`assistant_done`（4.1）→ 再次 `approval_required` → 再次打开面板（闭环，4.2）；续播流 `kind="error"` 结束续播（4.4）；进行中取消不使会话进入不可恢复状态（4.3）；面板逐条决策展示（2.1–2.3）。
    - _需求: 2.1、2.2、2.3、4.1、4.2、4.3、4.4_
  - [x] 6.5 验证：`test_chat_service_hitl_unit.py` 扩充 auto 模式高风险仍中断回归（修改既有文件）
    - 修改 `epsilon-boot/test/infrastructure/chat/test_chat_service_hitl_unit.py`：新增用例断言 auto 模式下高风险动作仍中断——后端 `StaticApprovalPolicyProvider` 对高风险工具返回 `interrupt=True`，配合 `evaluate_approval_mode` 返回 `None`（强制打开面板），端到端确认高风险红线不被 auto 绕过（需求 6.5）。
    - 对应设计组件：design §测试策略·集成测试；正确性属性 2。
    - _需求: 6.5_
  - [x] 6.6 Checkpoint：Slice F 全量回归/类型门槛（最终边界）（本 feature 测试全通过，全量 2689 passed；3 个预先存在的容器装配失败 test_container_config/test_run_container_wiring 经 git stash 验证与本 feature 无关；ruff 通过；⚠️ pyright 未验证：本环境未安装）
    - 运行 `cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/application/cli/ test/infrastructure/chat/`（涵盖 Slice A–F 全部新增与回归）。
    - 运行 `ruff check .` 与 `pyright`，零新增告警。
    - 运行全量 `PYTHONPATH=src uv run --frozen pytest` 确认无跨模块回归。
    - 验证边界：TUI 集成层只经 `CliRuntime` 编排（`resume_main_agent_events`/`load_pending_actions`/`policy_for`），审批闭环（首次中断 → 面板 → 续播 → 再次中断）可反复运行。

## 备注

- **追溯完整性**：需求 1（1.1/1.2/1.3/1.4/1.5/1.6）覆盖于 Slice A+B；需求 2（面板）于 Slice D+F；需求 3（edit 校验）于 Slice D；需求 4（续播/再次中断/取消/error）于 Slice F；需求 5（/approval 查看）于 Slice E；需求 6（切换模式/不绕过红线）于 Slice C+E+F；需求 7（trace）由既有 HITL v1 写入路径满足，本特性无新增任务（见下）。
- **需求 7（结构化 trace）无独立实现任务**：依 design §设计决策「trace 写入职责」与正确性属性 6，被恢复批次的 `ApprovalTrace` 已在首次中断时由 `run_events`/`run` 经 `_build_approval_trace` 写入，再次中断的新批次 trace 由 `resume()` 内 `_iter_rounds` 经既有 `_record_trace` 写入；本特性复用既有写入路径、不新增并行审批追踪结构（需求 7.1/7.2），trace 写入失败由既有 `_record_trace` 的 `try/except` 吞掉且不阻断恢复主流程（需求 7.3）。故不在 tasks 引入超出 design 的 trace 写入代码；正确性属性 6 的验证在 Slice A（1.4/1.6）与 Slice E（5.2 只读）的用例中一并覆盖。
- **正确性属性对应验证任务**：Property1 → 1.5 + 4.3；Property2 → 3.2 + 6.5；Property3 → 4.3（edit 校验用例）；Property4 → 1.4；Property5 → 1.4（agent.resume 只调用一次断言）；Property6 → 5.3（只读断言）+ 备注中 trace 复用说明。
- **依赖顺序**：A（domain 端口 + infrastructure 内核，无上游依赖）必须先于 B（CliRuntime 依赖 `stream_resume_approval`）；C（纯函数）与 D（面板）互不依赖，可并行评审但 F 依赖二者；E 依赖 B 的 `list_pending_approvals`；F 依赖 A/B/C/D/E 全部就绪。
- **范围守护**：不改动 `resume_approval` 对外语义、不扩展 `/run approve` 通路、不改 `HITL_INTERRUPT_ON` 与后端默认风险分级、不涉及 `epsilon-client/`，与 requirement §范围外行为一致。
- **换行符与最小改动**：所有既有文件（`ports.py`/`chat_service_adapter.py`/`runtime.py`/`commands.py`/`tui.py` 及既有测试）一律局部替换、保留原换行符；新增文件为纯新增模块。
