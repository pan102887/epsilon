# 实现计划：P2 前置——Agent Loop 归属重划的方向 ADR + 特征化测试安全网

> 本文件由已定稿的 `design.md` 展开为可执行、可勾选的任务清单。本 spec 是 P2 搬迁的**前置降风险轮**，交付物仅两样：(1) 方向决策 **ADR-0010**；(2) 针对 `ReActAgentAdapter` 五个对外可观测行为面缺口（仅 G1/G2/G3）的**特征化测试**。全程遵循 `Zero_Logic_Relocation`——**零生产代码改动**，`src/` 目录 `git diff` 须为空；既有测试零删改，仅新增 `test_react_agent_characterization_*.py`。
> 每条任务标注：动作、目标文件、对应 requirement AC 与 design 组件 / Property 编号、可执行验证命令。所有测试 / lint / grep 命令均在 `epsilon-boot/` 下执行（测试命令统一带 `cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest`）。
> **全程硬约束**：不移动、不重写、不删除 `react_agent_adapter.py` 任何一行业务逻辑；不改 `AgentPort` 四方法签名与任何对外可观测行为（`Contract_Invariance`）；不推翻 v3 决策（`V3_Decisions_Frozen`）；不改前端、不改依赖管理（仍仅 `uv`）；特征化测试**只锁定当前实际值**（characterization），发现可疑行为只登记不修复；中文 docstring（`code-documentation.md`）；全量类型标注、禁裸 `Any`、`ruff`/`pyright` 零新增错误（`python-typing-lint.md`）；`Existing_Test_Suite_Green` 前后均成立。

## 概述

执行采用 **波次（Wave）+ Checkpoint 门禁** 结构。因本 spec 零生产代码改动，无分层依赖顺序需遵循；波次以"降风险资产就绪度"编排，安全网测试先行、方向 ADR 后置，最后统一门禁：

- **Wave 1（特征化测试安全网）**：按 design 组件 3 的缺口补测方案，仅对三处真缺口新建两个测试文件——`test_react_agent_characterization_terminated_reason_orthogonality.py`（G1：`completed` 与 `status` 正交）与 `test_react_agent_characterization_hitl_resume_matrix.py`（G2：`resume` edit 续跑 + G3：决策数量/顺序不匹配异常 + 策略型再次审批）。落地前先实读既有 harness（`_v3_stream_helpers.py`、`test_react_agent_hitl_unit.py`）复用，断言照当前实际值写。→ **Checkpoint 1**：两测试文件全绿 + `ruff`/`pyright` 零新增错误 + `git diff src/` 为空。
- **Wave 2（方向 ADR + 文档同步）**：新建 `docs/adr/0010-relocate-agent-loop-to-domain-direction.md`（按 design 组件 1 全部要点写四段式）+ `docs/adr/README.md` 索引追加 0010 行；把 design「疑点登记」两条写入 ADR 后果小节供 P2 参考。→ **Checkpoint 2**：ADR 存在 + `supersedes:` 空 + README 命中 0010。
- **Checkpoint 3（最终门禁）**：全量 `PYTHONPATH=src uv run --frozen pytest` 全绿；`git diff --stat src/` 空；既有 `test/infrastructure/agent/` 文件零改动（仅新增两个 `characterization_*` 文件）；`ruff`/`pyright` 零新增。

> **波次正交**：Wave 1（测试）与 Wave 2（`docs/` 文档）改动文件集互不相交，可并发；但为保证 characterization 断言基于亲手核实的现状，建议先落 Wave 1（先摸清行为再写方向 ADR）。
> **只补缺口纪律**：需求 5（流式时序）、需求 7（handoff/token budget）经 design 组件 2 据实清点判为**已充分覆盖、无需新增**（AC3.4 不重复造轮）；本清单据此**不为其新建任何测试**。`resume + handoff` 为边界、当前无既有支持断言，仅在 ADR 疑点登记，**不补测**（AC7.5）。

---

## Wave 1：特征化测试安全网（仅补缺口 G1/G2/G3，两个新文件，可并发）

> **并发正交证据**：本波创建两个**互不相同的新文件**——`test_react_agent_characterization_terminated_reason_orthogonality.py`（G1）与 `test_react_agent_characterization_hitl_resume_matrix.py`（G2+G3），彼此无 import 依赖，可并发。两文件均**只新增、不改任何既有测试**。
> **落地前置（必读）**：编写断言前先实读 design 组件 3 的 harness 说明与既有 `test/infrastructure/agent/_v3_stream_helpers.py`、`test/infrastructure/agent/test_react_agent_hitl_unit.py`，复用其 fake `ModelAccessPort`（全程 stream）/ stub 工具 / `MemoryApprovalStore` / `StaticPolicy` / `RecordingTool`，**不臆造替身**；所有断言照 `ReActAgentAdapter` 当前实际返回值写（characterization），与现状不符只登记不修改。

- [x] 1. 特征化测试安全网（缺口 G1/G2/G3）
  - [x] 1.1 新建 G1 测试文件：`completed` 自然收尾与 status/terminated_reason 正交
    - 在 `test/infrastructure/agent/test_react_agent_characterization_terminated_reason_orthogonality.py` 新建（当前不存在），含模块中文 docstring 说明其锁定的行为面为「(a) 终止四态之 `completed`」、性质为 characterization 回归基线；全量类型标注、禁裸 `Any`。
    - harness（对齐 design 组件 3.1）：复用 `_v3_stream_helpers.install_stream_mock` + 文件内 `_FakeContextBuilder`（原样透传、空 usage）；`ReActAgentAdapter(tool_registry=<MagicMock>, context_builder=_FakeContextBuilder())`；`AgentConfig(max_rounds=3, prompt_id="chat-default@v1")`。落地前实读 `_v3_stream_helpers.py` 确认 `install_stream_mock` / `FakeStreamModel` 的实际签名与用法，照既有测试同构写法复用。
    - `test_run_plain_text_completed_orthogonal`：单轮纯文本 `LLMResponse(content="ok", tool_calls=[])` 驱动 `run(...)` → 断言 `result.status == "completed"` **且** `result.terminated_reason == "completed"` 且 `result.content == "ok"`（锁定 `AgentRunStatus` 与 `AgentTerminationReason` 正交、纯文本自然收尾二者同为 `completed`；补上既有 `:84` 未断言 `terminated_reason` 的缺口）。
    - `test_run_tool_loop_natural_completion`：`[tool_calls, text]` 两轮正常收尾 → 断言 `result.terminated_reason == "completed"`、`result.content == "done"`（锁定工具循环正常收尾亦为 `completed`，`_iter_rounds` text 分支）。
    - 断言全部照当前实际值写；若实际值与上述预期不符，以实际值为准并在任务说明/`TODO.md` 登记疑点，**不改生产代码**（AC4.7）。
    - _需求: 4.1, 4.2, 4.6, 4.7, 8.1, 8.3, 8.4_ ; _design 组件 3.1（G1）/ Property 1、5、7_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent/test_react_agent_characterization_terminated_reason_orthogonality.py -q`（全绿）。

  - [x] 1.2 新建 G2+G3 测试文件：`resume` edit 续跑 + 决策不匹配异常 + 策略型再次审批
    - 在 `test/infrastructure/agent/test_react_agent_characterization_hitl_resume_matrix.py` 新建（当前不存在），含模块中文 docstring 说明其锁定行为面为「(c) 审批中断/恢复语义（edit 续跑、决策数量/顺序不匹配异常、策略型恢复后再次 approval_required）」、性质为 characterization；全量类型标注、禁裸 `Any`。
    - harness（对齐 design 组件 3.2/3.3）：复用 `test_react_agent_hitl_unit.py` 的 `FakeContextBuilder`/`StaticPolicy`/`MemoryApprovalStore`/`RecordingTool`/`FakeModel` 同构 harness（直接 import 或等价重建）；`ApprovalPolicy("write_file", interrupt=True, allowed_decisions=frozenset({"approve","edit","reject"}))`。异常类型从 `domain.agent.exceptions` import。落地前实读 `test_react_agent_hitl_unit.py` 确认既有 harness 构造方式与 `resume(...)` 调用签名（`context, config, model_access, interrupt, decisions`），照既有 approve/reject 用例同构写法复用。
    - **G2 — edit 续跑**（`test_resume_edit_executes_with_edited_arguments`，AC6.2）：构造 `ApprovalInterrupt`（`round_num=1`，`actions` 含 `write_file`），调用 `resume(..., (ApprovalDecision("edit", "call-1", edited_action=EditedAction("write_file", '{"path":"edited.txt"}')),))` → 断言 `RecordingTool.requests == [{"path": "edited.txt"}]`（编辑后参数被采纳）、`result.status == "completed"`、`result.content == "done"`；`RecordingTool` 的参数 schema 须允许 `{"path": str}` 以过 `cast_params`/`validate_params`。
    - **G3a — 数量不匹配**（`test_resume_decision_count_mismatch_raises`，AC6.3）：`interrupt.actions` 有 1 项、`resume(..., ())` 传空决策序列 → `pytest.raises(ApprovalDecisionCountMismatchError)`；断言其 `code == 60023` 及构造参数 `(expected=1, actual=0)` 语义（照 `domain/agent/exceptions.py` 实际字段）。
    - **G3b — 顺序不匹配**（`test_resume_decision_order_mismatch_raises`，AC6.3）：决策 `tool_call_id` 与 `action.tool_call_id` 不对齐 → `pytest.raises(ApprovalDecisionOrderMismatchError)`；断言 `code == 60024`。
    - **G3c — 策略型再次审批**（`test_resume_policy_reapproval_returns_approval_required`，AC6.4）：恢复后下一轮模型再次返回命中 `ApprovalPolicy.interrupt=True` 的 `tool_calls` → 断言 `result.status == "approval_required"`、`result.approval is not None`、新 `approval_id != 原 approval_id`（锁定**策略型** resume 再中断，区别于既有 `test_react_agent_guardrail_runtime.py` 已覆盖的 guardrail 型）。
    - **不重复造轮**：`ApprovalDecisionNotAllowedError`（60025）已由既有 `test_hitl_respond_decision_is_rejected_after_branch_removal` 锁定，**本文件不添加**（AC3.4）；不改写 `ApprovalStateStorePort` 持久化或审批序列化逻辑（AC6.5）。
    - 断言全部照当前实际值写；异常类型/参数/触发时机以当前生产代码为准，与直觉不符只登记不修复（AC6.6）。
    - _需求: 6.2, 6.3, 6.4, 6.5, 6.6, 8.1, 8.3, 8.4, 8.5_ ; _design 组件 3.2（G2）/ 组件 3.3（G3）/ 错误处理表 / Property 1、5、7_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent/test_react_agent_characterization_hitl_resume_matrix.py -q`（全绿）。

---

## Checkpoint 1：安全网测试全绿 + 零生产改动（门禁）

- [x] 2. CP1 Wave 1 门禁校验（全部通过方可视 Wave 1 完成）
  - 两个新特征化测试全绿：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent/test_react_agent_characterization_*.py -q`（0 failed）。
  - 该子域回归无破坏：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent/ -q`（既有 + 新增全绿，Property 1）。
  - 零生产代码改动（Property 4）：`cd epsilon-boot && git diff --stat -- src/`（期望空输出）；`cd epsilon-boot && git status --porcelain src/`（期望无输出）。
  - 既有测试零删改（Property 2）：`cd epsilon-boot && git diff -- test/infrastructure/agent/test_react_agent_*_unit.py`（期望空——既有 `*_unit.py` 未被改动）；`cd epsilon-boot && git status --porcelain test/infrastructure/agent/`（期望仅出现两个新增 `test_react_agent_characterization_*.py`，无既有文件被修改标记）。
  - lint/类型/文档基线（Property 7）：`cd epsilon-boot && uv run ruff check test/infrastructure/agent/test_react_agent_characterization_*.py` 与 `cd epsilon-boot && uv run pyright test/infrastructure/agent/test_react_agent_characterization_*.py`（零新增错误、无裸 `Any`；中文 docstring 人工核对齐备）。
  - _需求: 4.7, 6.6, 8.1, 8.2, 8.3, 8.4, 8.5, 8.6, 2.1, 2.4_ ; _design Property 1、2、4、5、7_

---

## Wave 2：方向 ADR + 文档同步（仅改 docs/，与测试正交，可并发）

> **正交证据**：本波只改 `docs/adr/` 下文件，与 `epsilon-boot/` 源码与测试零交集。落地前实读既有 `docs/adr/0009-introduce-domain-services-in-task-subdomain.md` 沿用其写作深度与回链风格，并遵循 `docs/steering/adr.md` 四段式与 `docs/steering/doc-sync.md` 索引同步。

- [x] 3. ADR-0010 方向决策及索引登记
  - [x] 3.1 新建 ADR-0010
    - 在 `docs/adr/` 新建 `0010-relocate-agent-loop-to-domain-direction.md`（编号紧接现有 0009），遵循 `docs/adr/0000-template.md` 四段式（背景/决策/后果/备选方案含未采纳原因）。front-matter：`status: Accepted`、`date: 2026-07-06`、`deciders: [后端架构维护者]`、`supersedes:` **留空**（**不 supersede ADR-0001**）、`superseded-by:` 留空。标题：`将 ReAct Agent Loop 编排逻辑归属领域层的方向决策（P2 前置）`。
    - **一、背景与问题（据实证据，AC1.2）**：`Domain_Logic_In_Infrastructure`——ReAct Agent Loop 位于 `src/infrastructure/agent/react_agent_adapter.py`（约 3314 行），模块 docstring 自称"属于基础设施层"；**无 SDK 封装证据**（顶部 import 仅 `asyncio`/`json`/`logging`/`time`/`uuid`/`contextvars`/`dataclasses`/`typing`、`opentelemetry`、`domain.*`、`infrastructure.*`，未 `import openai`/`agents`/`litellm`，模型调用经 `ModelAccessPort` 间接进行）；三层 LOC 失衡（domain ≈ 8.3k / application ≈ 9.9k / infrastructure ≈ 24.5k，Agent Loop 是 infrastructure 膨胀主因）。
    - **二、决策（AC1.2、AC1.3、AC1.4、AC1.5）**：记录"Agent Loop 编排逻辑属领域关注点、应经 `P2_Relocation` 上提到领域层"的方向判断；给出 `Orchestration_Infrastructure_Split_Line` 的**可操作判据**（四条自问：是否封装外部技术/SDK/进程外资源→留基础设施；是否为可脱离运行时的纯业务判定→属领域；是否表达"何时停止/如何推进/产出何种形态"的通用语言→属领域；是否只是把技术观测/记账缝进循环的胶水→留基础设施），**不逐行罗列代码**；据实列出 `Domain_Orchestration_Candidates`（`_iter_rounds` 轮次循环控制、`AgentTerminationReason` 四态判定含 `_is_token_budget_exceeded`/`handoff` 短路/`max_rounds` 耗尽、`_detect_handoff`、`_collect_pending_actions` 审批中断决策、`_outcome_to_agent_result` 翻译、`RoundOutcome` 五态终止形态）；据实列出 `Infrastructure_Encapsulation_Candidates`（`_GuardrailRuntimeAccumulator`、`ToolAbuseDetector`、OTel `tracer` 与 `_record_*` trace、`ApprovalStateStorePort` 审批持久化 I/O、`approval_serialization`/`guardrail_serialization`、`approval_logging`、`_RoundStreamAccumulator`、`handoff_context`、`workflow_capability_runtime`、`merge_usage`）。
    - **三、后果（含 `P2_Invariants`，AC1.6）**：正面（切分有据可依、5 行为面已由特征化测试固化作 P2 回归判据、本轮零行为风险）；负面/代价（切分线为方向性判据、P2 落地仍需处理领域编排与技术记账交织的解耦细节）；锁定 `P2_Invariants` 清单——`AgentPort` 四方法签名不变、`Contract_Invariance`、`V3_Decisions_Frozen`、`Existing_Test_Suite_Green`、不回退 ADR-0001、import 路径调整只改 import 不改断言语义。**并把 design「疑点登记」两条**（① `resume` 入口 handoff 终止未被独立测试锁定、属边界，P2 处理循环控制时须留意；② `AgentResult.model` 在 handoff 分支取 `outcome.response.model` 而非 `HandoffPerformed.model`，属当前实际行为，若 P2 认为应透传目标模型另开 spec）写入后果小节或独立"待 P2 决策"小节，供 P2 spec 参考。
    - **四、备选方案（含未采纳原因，AC1.1、AC1.7）**：方案 A（只改 docstring 不搬迁——未采纳，文字自欺且违背零改动）；方案 B（引入领域事件/事件总线承载循环——未采纳，直接违反 ADR-0001，本 ADR SHALL NOT 把领域事件列为 P2 推荐形态）；方案 C（本轮一次性大爆炸搬迁——未采纳，风险极高，留独立 P2 spec）；方案 D（不写 ADR、P2 临场判断——未采纳，切分判据高价值易漂移应写 ADR）；方案 E（整个适配器含 guardrail/trace/序列化整体上提——未采纳，违反分层，技术关注点应留基础设施）。
    - **合规硬约束**：正文 SHALL NOT 出现"以领域事件/事件总线承载循环逻辑"作为 P2 推荐落地形态的表述；`supersedes:` 保持为空（AC1.7）。
    - _需求: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 2.1, 2.5_ ; _design 组件 1 ADR-0010 草案要点 / 疑点登记 / Property 6_
    - 验证：`test -f docs/adr/0010-relocate-agent-loop-to-domain-direction.md`；`grep -nE "^supersedes:" docs/adr/0010-relocate-agent-loop-to-domain-direction.md`（字段存在且值为空）；人工核对四段式齐备、无"领域事件承载循环"推荐、含切分线判据 + 两类候选清单 + `P2_Invariants` 清单 + 两条疑点登记。

  - [x] 3.2 更新 `docs/adr/README.md` 索引
    - 在 `docs/adr/README.md` 索引表 0009 行之后追加 0010 索引行（编号链接 / 标题「将 ReAct Agent Loop 编排逻辑归属领域层的方向决策（P2 前置）」/ `Accepted` / `2026-07-06`），遵循 `docs/steering/doc-sync.md`。
    - _需求: 1.8, 2.1_ ; _design 组件 1 / Property 6_
    - 验证：`grep -n "0010" docs/adr/README.md`（有命中）。

---

## Checkpoint 2：ADR-0010 合规且不回退 ADR-0001（门禁）

- [x] 4. CP2 Wave 2 门禁校验
  - ADR 存在且四段式：`test -f docs/adr/0010-relocate-agent-loop-to-domain-direction.md`；人工核对四段式（背景/决策/后果/备选）、`status: Accepted`、切分线可操作判据 + `Domain_Orchestration_Candidates` + `Infrastructure_Encapsulation_Candidates` + `P2_Invariants` 六项齐备。
  - 不回退 ADR-0001（Property 6）：`grep -nE "^supersedes:" docs/adr/0010-relocate-agent-loop-to-domain-direction.md`（值为空，未 supersede 0001）；人工核对正文未把"领域事件/事件总线承载循环"列为 P2 推荐形态（AC1.7）。
  - 索引命中：`grep -n "0010" docs/adr/README.md`（有命中，AC1.8）。
  - 疑点登记落地：人工核对 ADR 后果/待 P2 决策小节含 `resume+handoff` 与 `AgentResult.model` 两条登记（design 疑点登记）。
  - _需求: 1.1, 1.6, 1.7, 1.8_ ; _design 组件 1 / Property 6_

---

## Checkpoint 3：最终门禁（Property 全量验收）

- [x] 5. CP3 最终门禁校验（必须全部通过）
  - Property 3（全量绿）：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest`（0 failed，前后成立 `Existing_Test_Suite_Green`）。
  - Property 4（零生产代码改动）：`cd epsilon-boot && git diff --stat -- src/`（期望空）；`cd epsilon-boot && git status --porcelain src/`（期望无输出）。
  - Property 2（既有测试零删改、仅新增 characterization）：`cd epsilon-boot && git status --porcelain test/infrastructure/agent/`（期望仅两个新增 `test_react_agent_characterization_*.py`，既有 `*_unit.py`/`*_property.py` 均无修改标记）；`cd epsilon-boot && git diff -- test/infrastructure/agent/test_react_agent_*_unit.py`（期望空）。
  - Property 7（lint/类型基线）：`cd epsilon-boot && uv run ruff check test/infrastructure/agent/test_react_agent_characterization_*.py` 与 `cd epsilon-boot && uv run pyright test/infrastructure/agent/test_react_agent_characterization_*.py`（零新增错误、无裸 `Any`）。
  - Property 6（ADR 合规）：`grep -n "0010" docs/adr/README.md`（命中）；`grep -nE "^supersedes:" docs/adr/0010-relocate-agent-loop-to-domain-direction.md`（值为空）。
  - 范围锁定（AC2.1/2.2/2.6）：`cd epsilon-boot && git diff --name-only` 中改动仅落 `test/infrastructure/agent/`（两个新增 characterization 文件）+ `docs/adr/`（ADR-0010 + README）；未改 `src/`、未改 `AgentPort`（`src/domain/agent/ports.py`）、未改前端、未改依赖清单（`pyproject.toml`/`uv.lock`）。
  - _需求: 1.8, 2.1, 2.2, 2.5, 2.6, 3.3, 3.4, 8.2, 8.4, 8.5, 8.6_ ; _design Property 1、2、3、4、5、6、7_

---

## 任务 → 需求 AC → design 组件 → 正确性属性 追溯表

| 任务 | 覆盖需求 AC | design 组件 | 正确性属性 |
|---|---|---|---|
| 1.1（G1） | 4.1/4.2/4.6/4.7/8.1/8.3/8.4 | 组件 3.1 | Property 1、5、7 |
| 1.2（G2+G3） | 6.2/6.3/6.4/6.5/6.6/8.1/8.3/8.4/8.5 | 组件 3.2、3.3、错误处理表 | Property 1、5、7 |
| 3.1（ADR-0010） | 1.1–1.7/2.1/2.5 | 组件 1、疑点登记 | Property 6 |
| 3.2（README 索引） | 1.8/2.1 | 组件 1 | Property 6 |
| CP1 | 2.1/2.4/4.7/6.6/8.1–8.6 | 全测试组件 | Property 1、2、4、5、7 |
| CP2 | 1.1/1.6/1.7/1.8 | 组件 1 | Property 6 |
| CP3 | 1.8/2.1/2.2/2.5/2.6/3.3/3.4/8.2/8.4–8.6 | 全组件 | Property 1–7 |

> **需求 3（覆盖缺口清点）**：为 design 阶段产物（组件 2 清点表已据实完成 AC3.1/AC3.2），本清单无独立实现任务；AC3.3（既有测试零删改）、AC3.4（已覆盖处不重复添加）由 CP1/CP3 门禁校验保障。
> **需求 5、需求 7**：经 design 组件 2/3.4/3.5 据实清点判为**已充分覆盖、无需新增**（AC3.4）；本清单据此不设新建测试任务，AC5.6/AC7.5 的疑点由任务 3.1 写入 ADR 登记。

---

## 备注

- **零业务逻辑搬迁（`Zero_Logic_Relocation`）**：本 spec 不移动/不重写/不删除 `react_agent_adapter.py` 任何一行业务逻辑，不新增承载循环的领域服务、不改 DI 装配、不改生产 import 路径。所有缺口经 `AgentPort` 四入口（`run`/`resume`）以 fake `ModelAccessPort` + stub 工具观测，`src/` 目录 `git diff` 应为空（可测试性改动登记：零改动，例外条款未触发）。
- **只补真缺口（change-discipline）**：仅 G1/G2/G3 三处补测；需求 5（流式时序）、需求 7（handoff/token budget）与审批 approve/reject/`ApprovalDecisionNotAllowedError`/max_rounds/token_budget 等已被既有测试充分锁定，**不重复添加等价断言**（AC3.4）。
- **characterization 纪律**：新增测试只断言 `ReActAgentAdapter` 当前实际对外可观测值，不断言"理想应有"行为，不触及 `V3_Decisions_Frozen` 的"改后"形态；编写中若暴露可疑行为，照现状写断言 + 登记（本轮已知两条疑点写入 ADR-0010），修复留待后续 spec（AC4.7/AC5.6/AC6.6/AC7.5/AC8.4–8.6）。
- **harness 复用**：G1 复用 `_v3_stream_helpers.install_stream_mock`/`FakeStreamModel` + 文件内 `_FakeContextBuilder`；G2/G3 复用 `test_react_agent_hitl_unit.py` 的 `FakeContextBuilder`/`StaticPolicy`/`MemoryApprovalStore`/`RecordingTool`/`FakeModel` 同构 harness；不引入新替身以免与既有断言语义分歧（需求 8）。
- **异常复用**：G3 断言的 `ApprovalDecisionCountMismatchError`(60023)/`ApprovalDecisionOrderMismatchError`(60024) 均为 `domain/agent/exceptions.py` 现有异常，直接 import、按现状锁定，不新建、不改错误码。
- **ADR 纪律**：ADR-0010 `Accepted` 后只增不改；`supersedes:` 留空、不 supersede ADR-0001（AC1.7）；不把领域事件列为 P2 推荐落地形态。
- **回滚**：两个测试文件与一篇 ADR 均为独立新增，可直接删除回滚；因零生产改动，回滚不影响既有测试基线。
- **符号/行号说明**：design 组件 1/2/3 中的 `@行号`（如 `_apply_approval_decisions` :2617-2651、`_iter_rounds` :1867）引自 design 定位，落地前以 grep/实读逐点核对实际位置，防止上游文件微调导致偏移；断言以核实后的当前实际行为为准。
