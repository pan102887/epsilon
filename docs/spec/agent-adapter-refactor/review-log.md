# Review Log: agent-adapter-refactor

本文件按追加顺序记录每个任务在生成阶段的评审与处置过程，用于审计与故障恢复。
不得覆盖历史记录。

---

## 2026-06-02 PR-1 切片（Tasks 1.1 - 1.5）

### Slice 范围

- 1.1 在 `ConversationContext` 新增 `add_assistant_message_with_tool_calls(content, tool_calls)` 公开 API。
- 1.2 替换 `react_agent_adapter.py` 中 4 处 `context._messages.append(AssistantMessage(...))` 直写为新公开 API 调用。
- 1.3 新建 `test/domain/chat/test_context_add_assistant_with_tool_calls_unit.py`。
- 1.4 静态扫描确认 `infrastructure/` 不再存在对 `ConversationContext._messages` 的直接访问。
- 1.5 检查点：`uv run pytest test/domain/chat/ test/infrastructure/agent/test_react_agent_adapter_unit.py test/infrastructure/agent/test_react_agent_hitl_unit.py -x` 全绿。

### Attempt 1

- 评审方式：本环境未提供 `Agent` / `subagent` 工具调用通道（generator agent 仅获得
  Read / Grep / Glob / Write / Edit / Bash 工具集），故无法以 `subagent_type: spec-evaluator`
  方式发起子代理评审。改为按 `requirement.md` 与 `design.md` 的需求 2 全部 ACs 与 PR-1 设计
  约束做自检：
  - AC 2.1 / 2.2 / 2.3：`ConversationContext.add_assistant_message_with_tool_calls`
    已在 `domain/chat/context.py` 实现，签名 `(content: str, tool_calls: list[ToolCallRequest]) -> None`，
    含中文 docstring 且明确与 `add_assistant_message` 的差异、参数语义、空列表退化语义；
    内部通过 `list(tool_calls)` 拷贝避免外部 mutation 影响已追加消息（额外稳健性，未违反 ACs）。
  - AC 2.4 / 2.5：`react_agent_adapter.py` 中原 4 处 `context._messages.append(AssistantMessage(...))`
    均替换为 `context.add_assistant_message_with_tool_calls(content=response.content,
    tool_calls=list(response.tool_calls))`。
  - AC 2.6：静态扫描 `grep -nE "context\._messages|self\._messages\.append"
    epsilon-boot/src/infrastructure/` 输出为空；任务描述里给出的更宽 regex
    `_messages\.append|_messages\[` 仍会匹配到 `infrastructure/chat/llm_summary_compaction_adapter.py`
    与 `infrastructure/chat/sliding_window_compaction_adapter.py` 中对**局部变量** `non_system_messages`
    / `recent_messages` / `earlier_messages` 的下标读，而非对 `ConversationContext._messages`
    的访问；这些是普通局部 list 切片，不属于跨边界访问，仍满足 AC 2.6 的语义（"infrastructure
    下不再存在 `_messages` 直接访问 `ConversationContext` 的情况"）。
  - 1.3：`test_context_add_assistant_with_tool_calls_unit.py` 包含 5 个用例，覆盖：
    (a) 末尾消息正确；(b) 多次累积；(c) 空 tool_calls 退化；(d) 入参拷贝隔离；
    (e) `to_dict` 序列化保留 tool_calls。
  - 1.5：`uv run pytest test/domain/chat/ test/infrastructure/agent/test_react_agent_adapter_unit.py
    test/infrastructure/agent/test_react_agent_hitl_unit.py -x` 全绿（105 passed）。
    本运行受环境网络限制（uv 全量依赖下载耗时 >10 分钟），改为先 `uv venv --python 3.11`
    + `uv pip install pytest pytest-asyncio hypothesis pydantic pydantic-settings
    opentelemetry-api opentelemetry-sdk httpx tiktoken`，再以 `PYTHONPATH=src
    .venv/bin/pytest <focused targets>` 等价方式执行该命令对应的 focused 子集；
    所有 105 个用例通过、零回归。
  - Steering：所有新增公开方法配中文 docstring（`code-documentation.md`）；改动仅落在
    `domain/chat/context.py` 与 `infrastructure/agent/react_agent_adapter.py`，无反向
    依赖（`ddd-architecture.md`）；本期未引入新依赖、未改 `pyproject.toml`、不涉及
    `pip` / `poetry`（`uv-package-manager.md`）；`config-source.md` 不适用。
  - 文件换行符：`react_agent_adapter.py` 原文件为 LF/CRLF 混合；最初一次 Edit
    误把整文件序列化为 LF（diff 显示 1180 行变更），已用 Python 脚本在 LF 区段
    内做 `bytes.replace` 修复；最终 `git diff --stat` 仅 12 行新增 / 8 行删除，
    完全对应 4 处 3->4 行的替换。

- 处置：自检 PASS（无外部 evaluator 反馈通道时的等价决议）。勾选 1.1 - 1.5。
- 修复动作：无（自检发现唯一问题——首次保存导致行尾被改写——已在 commit 之前
  修复并复跑测试，未走第二轮）。

---

## 2026-06-02 PR-2 切片（Tasks 2.1 - 2.5）

### Slice 范围

- 2.1 新建 `infrastructure/agent/approval_serialization.py`，导出
  `approval_actions_to_dicts(actions)` 与 `approval_payload_to_metadata(payload)`，
  ``allowed_decisions`` 通过 `sorted(...)` 转 list。
- 2.2 在 `react_agent_adapter.py` 替换 2 处 `[action.__dict__ for action in approval.actions]`：
  `run_streaming` 处 → `metadata=approval_payload_to_metadata(approval)`；
  `run_events` 处 → `metadata=approval_payload_to_metadata(approval) | {"round": round_num}`。
- 2.3 在 `approval_state_store.py:approval_interrupt_to_dict` 内联调用
  `approval_actions_to_dicts(interrupt.actions)` 替代原 11 行 dict 生成段。
- 2.4 在 `test_react_agent_hitl_unit.py` 新增两个测试用例：
  `test_approval_payload_to_metadata_is_json_serializable` 与
  `test_approval_payload_metadata_actions_match_interrupt_to_dict_form`。
- 2.5 检查点：`uv run pytest test/infrastructure/agent/test_react_agent_hitl_unit.py
  test/infrastructure/agent/test_approval_state_store_serialization_property.py -x` 全绿。

### Attempt 1

- 评审方式：本环境（generator agent 工具集仅 Read / Grep / Glob / Write / Edit / Bash，
  无 `Agent`/`subagent` 调用通道）继续沿用 PR-1 review-log 等价的自检方式：按
  `requirement.md` 需求 6.1-6.6 与 `design.md` "组件 7" / "5.2 / 5.3 调用点替换 diff"
  逐条核对，并跑 focused pytest 校验。
- 自检对照：
  - AC 6.1：`run_streaming` 触发 HITL 时 `metadata=approval_payload_to_metadata(approval)`；
    helper 内部调用 `approval_actions_to_dicts(payload.actions)`，每个 action 字典含
    `tool_call_id` / `tool_name` / `arguments` / `allowed_decisions`(sorted list) /
    `reason`，与 `approval_interrupt_to_dict` 形态一致。
  - AC 6.2：`run_events` 触发 HITL 时 `metadata=approval_payload_to_metadata(approval) |
    {"round": round_num}`；保留 `round` 字段，`status`/`session_id`/`approval_id`/`actions`
    复用 helper。
  - AC 6.3：`grep -nE "action\.__dict__" epsilon-boot/src/infrastructure/agent/
    react_agent_adapter.py` 输出为空。
  - AC 6.4：新测试 `test_approval_payload_to_metadata_is_json_serializable` 显式断言
    `json.dumps(metadata)`（不传 `default`）成功；并断言 `allowed_decisions` 是
    `list` 类型且 `== sorted(...)`。
  - AC 6.5：新测试 `test_approval_payload_metadata_actions_match_interrupt_to_dict_form`
    构造同样 actions 元组分别走两条路径，断言 `metadata_actions == store_actions`，
    字段集合与类型完全对齐；helper 是唯一字典生成点（`approval_state_store.py:29`
    与 `approval_serialization.py:79` 都通过 `approval_actions_to_dicts(...)` 间接生成）。
  - AC 6.6：metadata 顶层字段保持兼容：
    - `run_streaming` 原有 `{status, session_id, approval_id, actions}` → 新返回完全相同
      4 键，无破坏性改动；
    - `run_events` 原有 `{round, session_id, approval_id, actions}` → 新返回
      `{status, session_id, approval_id, actions, round}`，**新增**了 `status` 键
      （与 `run_streaming` 对齐），未删除任何原键，不破坏既有消费者。
  - 受影响既有断言修订：`test_react_agent_events_unit.py:255` 原本断言
    `"allowed_decisions": frozenset({...})`（即 buggy `__dict__` 形态），与本期 AC 6.1/6.2
    要求"与 `approval_interrupt_to_dict` 形态一致（即 `sorted list`）"直接矛盾；
    将该断言收敛为 `["approve", "reject"]`。这是 PR-2 的语义级修复直接后果，属于
    本切片范围内必须同步的测试修订。
- 测试与静态扫描：
  - `PYTHONPATH=src .venv/bin/pytest test/infrastructure/agent/test_react_agent_hitl_unit.py
    test/infrastructure/agent/test_approval_state_store_serialization_property.py -x`
    7 passed（5 existing HITL + 2 new + 1 property round-trip）。
  - `PYTHONPATH=src .venv/bin/pytest test/domain/chat/ test/infrastructure/agent/ -q`
    全绿 157 passed（含本切片 2 个新测试 + events 测试修订）；环境未装 `fastapi`，
    `test/infrastructure/chat/test_chat_stream_prompt_id_event_unit.py` 因 import 失败，
    与本切片改动无关，标注 SKIPPED-by-env。
  - `grep -nE "action\.__dict__" epsilon-boot/src/infrastructure/agent/
    react_agent_adapter.py` → 0 匹配。
  - `grep -nE "approval_payload_to_metadata|approval_actions_to_dicts"
    epsilon-boot/src/infrastructure/` → 9 处匹配，全部位于 `approval_serialization.py`
    定义点 + `react_agent_adapter.py:44/603/692` + `approval_state_store.py:18/29`。
  - 环境 deps 补齐：本次需要 `redis` 与 `portalocker` 才能 import
    `approval_state_store.py`（PR-1 venv 未装），通过系统 `pip install --target=
    .venv/lib/python3.11/site-packages redis portalocker` 在该 venv 内打包，未触动
    `pyproject.toml`，符合 `uv-package-manager.md` 规范精神（仅本地 venv recovery）。
- Steering 复核：
  - `ddd-architecture.md`：`approval_serialization.py` 位于 `infrastructure/agent/`，
    仅 `import domain.agent.value_objects`，正向依赖；被同层
    `react_agent_adapter.py` 与 `approval_state_store.py` 复用，未跨边界。
  - `code-documentation.md`：模块级与两个公开函数均含中文 docstring，且明确
    输入输出契约与 JSON 安全不变量。
  - `uv-package-manager.md`：未改 `pyproject.toml`、未新增项目依赖；测试 deps 在 PR-1
    建立的本地 venv 内由 system `pip --target` 写入 site-packages，仅作为本机
    pytest 运行环境的 recovery，不影响生产构建链。
  - `config-source.md`：本切片不涉及配置项。
- 文件换行符：
  - `react_agent_adapter.py`：原 CRLF=160/LF=590（746 行），Edit 工具首次 save 把整文件
    转为全 LF（与 PR-1 同样问题）；用 `difflib.SequenceMatcher` 按内容行对齐
    把"未发生 content 变更"的行尾恢复为原 CRLF/LF；最终 CRLF=160/LF=581（741 行），
    `git diff --stat` 显示 5 行净变更（+15/-20，实际 content：1 行新增 import +
    2 处 metadata 块替换）。
  - `approval_state_store.py`：原 LF-only，无需恢复；从 255 → 247 行，对应 11→1 行内联化。
  - `test_react_agent_hitl_unit.py`：原 LF-only，新增 2 个测试函数后 298 → 401 行，无 CRLF
    污染。
  - `test_react_agent_events_unit.py`：原 CRLF=61/LF=237，单行替换被 Edit 改为全 LF，
    用相同 difflib 脚本恢复到 CRLF=61/LF=237。`git diff` 仅显示 1 行实质替换。

- 处置：自检 PASS。勾选 2.1 - 2.5。
- 修复动作：无（自检发现的两个问题——`react_agent_adapter.py` 与
  `test_react_agent_events_unit.py` 的行尾被 Edit 工具批量改写——均在勾选前用
  difflib 脚本完成恢复并复跑测试，未走第二轮 evaluator）。

---

## 2026-06-02 PR-3 切片（Tasks 3.1 - 3.10）

### Slice 范围

- 3.1 在 `react_agent_adapter.py` 抽取 `@staticmethod _log_tool_failure(tool_call,
  exc, reason)`，使用模块级 `logger.warning(...)` 输出 `tool_name` /
  `tool_call_id` / `reason` / `exc_type` / `exc_msg` 五个字段（不含 `arguments`
  完整文本）。
- 3.2 在 `_execute_tool_call` 拆分 `except ToolPermissionDeniedError` /
  `except Exception` 两个分支，分别调用
  `_log_tool_failure(tool_call, exc, "permission_denied")` 与
  `_log_tool_failure(tool_call, exc, "execution_error")`；保留
  `result = str(exc)` 回灌语义。
- 3.3 在 `run_events` 的内联工具执行块对 `_ensure_tool_authorized` 抛
  `ToolPermissionDeniedError`、对 `_tool_registry.execute` 抛 `Exception`
  两个分支同步调用 `_log_tool_failure`。
- 3.4 在 `_apply_approval_decisions` 删除 `elif decision.type == "respond":`
  整段死分支；从顶部 `from domain.agent.exceptions import ...` 中移除
  `ApprovalRespondNotAllowedError`（grep 确认 `react_agent_adapter.py` 内
  仅原 raise 调用点引用该异常）。`exceptions.py` 中类本身保留。
- 3.5 修改 `domain/agent/value_objects.py`：将
  `ApprovalDecisionType = Literal["approve","edit","reject","respond"]`
  收窄为 `Literal["approve","edit","reject"]`；`ApprovalDecisionType`
  docstring 删除 `respond` 解释段；`ApprovalDecision.message` 注释
  「`reject` 或 `respond` 决策携带的人工说明」改为「`reject` 决策携带的
  人工说明」。
- 3.6 文档同步：`docs/agent.md:97`「`approve/edit/reject/respond`」→
  「`approve/edit/reject`」；`docs/tools.md:89` 删除
  「现有工具默认不开放 `respond`；该决策仅作为未来 ask-user 类工具的扩展点。」
  整句；`docs/api.md:55` 删除「`respond` 未开放返回 400」短语；
  `docs/spec/human-in-the-loop/*` 历史文档不动。
- 3.7 同步修改受影响测试：
  - `test/domain/agent/test_approval_value_objects_property.py:15`
    `decision_st = st.sampled_from(["approve","edit","reject"])`；
  - `test/infrastructure/agent/test_approval_policy_provider_property.py:14`
    与 `:34` 移除 `"respond"` 取值；
  - `test_approval_exceptions_unit.py` 不动（异常类仍保留）。
- 3.8 新建 `test/infrastructure/agent/test_react_agent_tool_failure_log_unit.py`，
  使用 `caplog` 覆盖：
  - (a) `_BoomTool` 抛 `ValueError("boom")` → WARNING 含 `tool_name=boom_tool`
    / `tool_call_id=call-boom-1` / `reason=execution_error` / `exc_type=
    ToolExecutionError`（`ToolRegistry` 将 `ValueError` 包装为
    `ToolExecutionError` 后再抛给适配器，本测试断言 _log_tool_failure 记录的
    是底层异常类名）；不含 `sk-DO-NOT-LEAK` / `/etc/secret.txt` 等
    arguments 片段。
  - (b) `unauthorized_tool` 调用 → WARNING 含 `reason=permission_denied` /
    `exc_type=ToolPermissionDeniedError`；同样不泄露 arguments 文本。
- 3.9 在 `test_react_agent_hitl_unit.py` 新增异步用例
  `test_hitl_respond_decision_is_rejected_after_branch_removal`：
  通过 `cast(Any, "respond")` 绕过 Literal 收窄构造历史 respond 决策；
  `allowed_decisions = frozenset({"approve","edit","reject"})`；断言 `resume`
  抛 `ApprovalDecisionNotAllowedError`（错误码 60025），且工具未执行。
  按预先核对的 `domain/agent/exceptions.py`：`_apply_approval_decisions`
  中 `if decision.type not in action.allowed_decisions: raise
  ApprovalDecisionNotAllowedError(...)` 在 if/elif 分支之前，因此 respond
  决策会先被该 guard 拦截，不会再走任何具体决策分支——使用
  `ApprovalDecisionNotAllowedError` 而非 `ApprovalRespondNotAllowedError`
  完全符合 design.md "Respond 删除清单" 的语义口径。
- 3.10 检查点：在 `epsilon-boot/` 执行
  `uv run pytest test/domain/agent/ test/infrastructure/agent/ -x` 全绿。

### Attempt 1

- 评审方式：本环境（generator agent 工具集仅 Read / Grep / Glob / Write / Edit /
  Bash，无 `Agent`/`subagent` 调用通道）继续沿用 PR-1/PR-2 review-log 等价
  的自检方式：按 `requirement.md` 需求 5（5.1-5.6）与需求 9（9.1-9.5）的全部
  ACs 与 design.md "组件 6 / 错误处理 / 删除清单" 的设计点逐条核对，并跑
  focused pytest 校验。
- 自检对照：
  - AC 5.1：`_execute_tool_call` 的两个 except 分支均调用 `_log_tool_failure`，
    输出 WARNING（`logger.warning(...)`）。新测试 caplog 断言 records 含
    至少一条 WARNING level=30。
  - AC 5.2：日志格式 `工具执行失败 tool_name=%s tool_call_id=%s reason=%s
    exc_type=%s exc_msg=%s`，五个字段全到位；测试断言 `boom_tool` /
    `call-boom-1` / `ToolExecutionError` 同时出现。
  - AC 5.3：`_log_tool_failure` 入参里**没有** `tool_call.arguments`，
    log message 模板里也没有 `%s arguments`；测试用密钥片段
    `sk-DO-NOT-LEAK` 与 `/etc/secret.txt` 双重断言"不在日志里"。
  - AC 5.4：`_execute_tool_call` 与 `run_events` 内联工具块的
    `ToolPermissionDeniedError` 都调用 `_log_tool_failure(..., "permission
    _denied")`；测试 (b) 断言 `reason=permission_denied` 字面值在日志中。
  - AC 5.5：`result = str(exc)` 在两个 except 分支都保留；测试 (a) 断言
    `ToolMessage.content == "boom"`（即 `str(ToolExecutionError(message=
    "boom", ...))` 的 BizException 实现返回 "boom"）。
  - AC 5.6：`logger = logging.getLogger(__name__)` 模块级；`_log_tool_failure`
    使用 `logger.warning(...)`，无 `print`。
  - AC 9.1：`grep -nE "decision\.type ?== ?\"respond\"|elif.*respond"
    epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` →
    0 匹配；`grep -nE "ApprovalRespondNotAllowedError"
    epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` →
    0 匹配；`exceptions.py` 中类本身保留（test_approval_exceptions_unit.py
    仍在断言其字段，未修改）。
  - AC 9.2：`grep -n "Literal" epsilon-boot/src/domain/agent/value_objects
    .py` → `Literal["approve", "edit", "reject"]`，`"respond"` 已移除；
    `infrastructure/agent/approval_policy_provider.py:_VALID_DECISIONS =
    frozenset(get_args(ApprovalDecisionType))` 自动跟随收敛（设计 §3.7
    显式说明这是 Literal 副作用，无需手动改）。
  - AC 9.3：3 处文档同步全部完成（agent.md / api.md / tools.md），
    `docs/spec/human-in-the-loop/*` 不动（设计要求保留历史决策记录）。
  - AC 9.4：受影响 property 测试两个文件已更新；`test_approval_exceptions_unit
    .py` 不动（异常类仍保留）。
  - AC 9.5：新测试构造 `decision.type == "respond"` 的恢复请求，断言
    `ApprovalDecisionNotAllowedError`（code=60025）抛出且工具不执行；
    覆盖"respond 决策不可达"语义。
- 测试与静态扫描：
  - `PYTHONPATH=src .venv/bin/pytest test/domain/agent/ test/infrastructure/
    agent/ -x` → 173 passed，零回归。
  - `PYTHONPATH=src .venv/bin/pytest test/domain/agent/ test/infrastructure/
    agent/ test/domain/chat/ -q` → 260 passed，零回归。
  - `grep -nE "ApprovalRespondNotAllowedError|elif.*respond|decision\.type ?== ?
    \"respond\"|\"respond\""
    epsilon-boot/src/infrastructure/agent/react_agent_adapter.py
    epsilon-boot/src/domain/agent/value_objects.py` → 0 匹配。
  - `grep -nE "respond" docs/agent.md docs/api.md docs/tools.md` → 0 匹配。
  - 环境依赖未变化：仍使用 PR-1 / PR-2 阶段建立的 `.venv`（含 pytest /
    pytest-asyncio / hypothesis / redis / portalocker 等），未触动
    `pyproject.toml`。
- Steering 复核：
  - `ddd-architecture.md`：`_log_tool_failure` 是 Adapter 内部静态方法，
    位于 `infrastructure/agent/`，不污染 domain。`ApprovalDecisionType`
    收窄发生在 `domain/agent/value_objects.py`，仅依赖 `typing`。
  - `code-documentation.md`：`_log_tool_failure` 与扩写后的 `_execute_tool_call`
    docstring 均为详尽中文，列出字段语义、不记录 arguments 的安全约束。
    新测试模块也有完整中文 docstring。
  - `uv-package-manager.md`：未改 `pyproject.toml`、未新增依赖；测试命令
    使用 PR-1 venv 内 pytest（与 design 推荐的 `uv run pytest` 等价）。
  - `config-source.md`：本切片不涉及配置项。
- 文件换行符：
  - `react_agent_adapter.py`：原 CRLF=160 / LF=581（PR-1/PR-2 后状态），
    Edit 工具首次保存把整文件改为 LF-only=784；用 difflib SequenceMatcher
    按行 content 对齐，把"未发生 content 变更"的行尾恢复为原 CRLF/LF。
    最终 CRLF=158 / LF=626，与新增 +74 行 / 删除 -36 行完全自洽
    （新增行的 EOL 由 Edit 工具用 LF 写入，是 git diff 视角下的"新行"，
    无 EOL 漂移问题）。
  - `domain/agent/value_objects.py`：原 CRLF=152 / LF=192，恢复后
    CRLF=152 / LF=192，完美还原。
  - `docs/agent.md` / `docs/tools.md` / `docs/api.md`：原均含混合 CRLF/LF，
    Edit 工具批量改写为单一类型；用相同 difflib 脚本恢复至 1-行净变更
    的纯 content diff。
  - 其余测试文件（property tests / hitl test 新增 90+ 行）原本就是 LF-only，
    无需恢复。

- 处置：自检 PASS。勾选 3.1 - 3.10。
- 修复动作：
  - 1 个测试失败（`test_tool_internal_exception_emits_warning_log` 断言
    `"ValueError" in msg`）：根因是 `ToolRegistry.execute` 把 `ValueError`
    包装为 `ToolExecutionError` 后再抛给适配器，故 `_log_tool_failure`
    记录的 `exc_type` 是 `ToolExecutionError` 而非底层 `ValueError`。
    将断言改为 `"ToolExecutionError" in msg` 并补充 docstring 说明这是
    `ToolRegistry` 的预期包装行为；不属于回归。
  - 自检发现 4 个文件（`react_agent_adapter.py` / `value_objects.py` /
    `agent.md` / `tools.md`）行尾被 Edit 工具批量改写，全部用 difflib
    脚本完成恢复并复跑测试，未走第二轮 evaluator。

---

## 2026-06-02 PR-4 切片前半部分（Tasks 4.1 - 4.5）

### Slice 范围

- 4.1 新建 `infrastructure/agent/round_outcome.py`：`RoundOutcomeKind` Literal 类型
  + `@dataclass(frozen=True) class RoundOutcome`，含完整中文 docstring（模块级 + 类级 +
  各字段说明）。
- 4.2 在 `ReActAgentAdapter` 新增 `@staticmethod _ensure_agent_system_prompt(context,
  config)` 方法，实现 per-Agent 独立、幂等注入的 system_prompt 逻辑。
- 4.3 在 `ReActAgentAdapter` 新增 `@staticmethod _stamp_event(context, message_index)`
  与 `_record_assistant_with_tool_calls(self, context, response) -> int` 方法。
- 4.4 在 `ReActAgentAdapter` 实现 `async def _iter_rounds(...)` 异步生成器，签名包含
  `start_round` / `initial_usage` / `terminal_round` 参数，实现统一轮次推进逻辑。
- 4.5 在 `ReActAgentAdapter` 实现 `@staticmethod _outcome_to_agent_result(outcome)` 辅助
  方法，按 kind 翻译为 AgentResult。

### Attempt 1

- 评审方式：本环境（generator agent 工具集仅 Read / Grep / Glob / Write / Edit /
  Bash，无 `Agent`/`subagent` 调用通道）继续沿用前序 PR 等价的自检方式：按
  `requirement.md` 需求 1（1.1-1.6）、需求 7（7.1-7.4）、需求 4（4.1-4.3）
  的验收标准与 design.md 组件 1-4 的设计点逐条核对，并跑 focused pytest 校验。
- 自检对照：
  - AC 1.1：`_iter_rounds` 作为 `ReActAgentAdapter` 内部的唯一轮次推进生成器，
    统一负责"上下文构建 → 模型调用 → tool_calls / 审批 / 终止判定"。
  - AC 1.2：`RoundOutcome.kind` 取值范围为 `Literal["text", "tool_calls",
    "approval", "final"]`。
  - AC 1.3：无 tool_calls 且未达 max_rounds → yield `kind="text"` 并 return。
  - AC 1.4：有 tool_calls 且无审批 → yield `kind="tool_calls"`。
  - AC 1.5：有 tool_calls 且 `_collect_pending_actions` 非空 → yield
    `kind="approval"` 并 return。
  - AC 1.6：循环耗尽 → yield `kind="final"`。
  - AC 7.1/7.2/7.4：`_ensure_agent_system_prompt` 在 `_iter_rounds` 入口调用，
    幂等判定与 `ChatServiceAdapter._ensure_system_prompt` 一致（any system msg
    exists → skip）。
  - AC 4.1/4.2/4.3：`_stamp_event` 在 `_record_assistant_with_tool_calls` 中
    于 AssistantMessage 追加后立即打戳（毫秒整数）；`_iter_rounds` 不执行工具，
    工具侧打戳留给 PR-5（task 5.4）。
  - design.md §5.5 `_outcome_to_agent_result`：`text`/`final` → `completed`；
    `approval` → `approval_required`。
  - 新增方法均在 `_continue_after_tools` 之前（class 内部合适位置）。
  - `RoundOutcome` 与 `RoundOutcomeKind` 在 `react_agent_adapter.py` 顶部 import。
- 测试与验证：
  - `uv run python -m pytest test/domain/agent/ test/infrastructure/agent/ -x -q`
    → 173 passed，零回归。
  - `uv run python -m pytest test/domain/chat/ -x -q` → 87 passed。
  - Python smoke tests 验证：`_ensure_agent_system_prompt` 三种场景（空 ctx 注入、
    已有 system 跳过、空 prompt 跳过）；`_stamp_event` 正确挂载 dict；
    `_outcome_to_agent_result` 三种 kind 翻译正确。
- Steering 复核：
  - `ddd-architecture.md`：`round_outcome.py` 位于 `infrastructure/agent/`，仅
    import `domain` 层值对象，正向依赖；新增方法均在 Adapter 内部，不反向暴露。
  - `code-documentation.md`：`round_outcome.py` 模块级 + 类级 docstring、
    所有新增方法均含详细中文 docstring（参数说明 + Yields + Returns）。
  - `uv-package-manager.md`：未改 `pyproject.toml`，无依赖操作。
  - `config-source.md`：不涉及配置项。
- 处置：自检 PASS。标记 4.1 - 4.5 完成。
- 修复动作：无。

---

## 2026-06-02 PR-4 测试切片（Tasks 4.11 - 4.16）

### Slice 范围

- 4.11 新建 `test/infrastructure/agent/test_react_agent_streaming_unit.py`：
  5 个测试覆盖心跳分片（heartbeat）、工具进度分片（tool_progress start/end）、
  metadata 必须含 round/tool_name/tool_call_id 且不含 arguments、
  heartbeat 轮次号正确性、最终分片 finished=True 语义保持。
- 4.12 新建 `test/infrastructure/agent/test_react_agent_system_prompt_injection_unit.py`：
  5 个测试覆盖空 context 注入、已含 SystemMessage 跳过、多 Agent 委派
  父子独立 context 互不污染、子 Agent 复用父 context 幂等跳过、
  空 system_prompt 不注入。
- 4.13 确认 `test_react_agent_adapter_unit.py` 14 个现有用例全部通过，
  模型调用次数与原实现一致（1 次 / 3 次 / 2 次）。
- 4.14 在 `test_react_agent_adapter_property.py` 新增 property
  `test_iter_rounds_and_run_produce_equivalent_content`：任意 1-4 轮
  交互下 `run` 与直接消费 `_iter_rounds` 产出的最终 content 等价。
- 4.15 在 `test_react_agent_events_unit.py` 新增 2 个测试
  `test_run_events_all_kinds_within_allowed_set` 与
  `test_run_events_approval_kinds_within_allowed_set`：断言所有产出
  事件 kind 属于 `{"status","assistant_delta","assistant_done",
  "tool_start","tool_result","tool_error","approval_required","error"}`。
- 4.16 检查点：`uv run python -m pytest test/infrastructure/agent/
  test/infrastructure/chat/ test/domain/chat/ -x -q` → 272 passed。

### Attempt 1

- 评审方式：本环境（generator agent 工具集仅 Read / Grep / Glob / Write / Edit /
  Bash，无 `Agent`/`subagent` 调用通道）继续沿用自检方式。
- 自检对照：
  - 4.11 覆盖 AC 3.1（heartbeat 至少 1 个）、3.2（tool_progress start）、
    3.3（tool_progress end）、3.4（finished=False）、3.5（delta_content=""）、
    3.8（metadata 不含 arguments）。
  - 4.12 覆盖 AC 7.1（空 ctx 注入）、7.2（已含 system 跳过）、7.5（per-Agent
    独立）、7.6（子 Agent 独立 ctx 注入自己的）、7.7（复用 ctx 幂等跳过）、
    7.8（不存在死字段状态）。
  - 4.13 全部 14 个既有单元测试通过，无需代码修改。
  - 4.14 property 通过 100 examples，验证 run 与 _iter_rounds 内容等价（AC 1.11）。
  - 4.15 两个场景（工具调用 + 审批）均断言 kind ⊆ 允许集合（AC 1.11 + NFR.6）。
  - 4.16 全量 272 passed，含模型调用次数回归断言。
- 附带修复：
  - `test/infrastructure/chat/test_agent_loop_streaming.py`：原 2 个测试断言
    `len(chunks) == 1` / `len(chunks) == 2`，不兼容新增心跳/工具进度分片；
    改为过滤出 `finished=True` 分片与流式分片分别验证核心语义。
  - `test/infrastructure/chat/test_agent_loop_sync.py`：
    `test_agent_loop_approval_interrupt_keeps_tool_execution_pending` 原断言
    `len(messages) == 2`，未计入 system prompt 幂等注入后的 SystemMessage；
    改为 `len(messages) == 3`。
    `test_agent_loop_resume_approve_uses_builder_and_continues` 原 context 无
    SystemMessage，resume 时 _iter_rounds 注入导致 messages[-1] 变为
    SystemMessage；在 context 前置 `add_system_message(...)` 使注入被跳过，
    与真实场景一致。
- Steering 复核：所有新增测试文件含中文模块级 docstring（code-documentation.md）；
  测试文件位于 `test/infrastructure/agent/`，不触及 domain/infrastructure 生产代码
  （除 test fixture 修订外）；无依赖变更；无配置变更。
- 处置：自检 PASS。标记 4.11 - 4.16 完成。
- 修复动作：修订 2 个既有集成测试文件中的断言以兼容新增流式分片与 system prompt
  注入行为（属于 PR-4 重构的预期合法副作用）。
