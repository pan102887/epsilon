# Review Log — ddd-agent-loop-relocation-prep

> Append-only 审查/执行历史，供恢复与审计。禁止覆盖既有条目。

## Wave 1（任务 1.1 / 1.2 / Checkpoint 1）— 特征化测试安全网

- **范围**：仅新建两个 characterization 测试文件，零生产代码改动（`src/` diff 为空）。
  - `epsilon-boot/test/infrastructure/agent/test_react_agent_characterization_terminated_reason_orthogonality.py`（G1）
  - `epsilon-boot/test/infrastructure/agent/test_react_agent_characterization_hitl_resume_matrix.py`（G2+G3）
- **评估器（spec-evaluator）**：本环境未启用 `Agent` 工具（`No such tool available: Agent`），无法调用 spec-evaluator。改以 Checkpoint 1 门禁命令的实测输出作为验收依据（全部通过，见下）。
- **Checkpoint 1 门禁实测**（均在 `epsilon-boot/` 下执行）：
  - `PYTHONPATH=src uv run --frozen pytest <两文件> -v` → 6 passed。
  - `uv run ruff check <两文件>` → All checks passed（首次报 I001 导入分组，`ruff --fix` 已自动整理：`test.infrastructure.*` 归入 first-party 组）。
  - `uv run pyright <两文件>` → 0 errors, 0 warnings, 0 informations。
  - `git diff --stat -- src/` → 空（零生产改动，Property 4）。
  - `git status --porcelain 'test/infrastructure/agent/*_unit.py'` → 空（既有 unit 测试零改动，Property 2）。
  - `git status --porcelain test/infrastructure/agent/` → 仅两个新增 `characterization_*` 文件。
- **与 design 的偏差**：无。落地前实读了 `react_agent_adapter.py` 的 `resume` / `_apply_approval_decisions`（数量校验 → `ApprovalDecisionCountMismatchError`，顺序校验 → `ApprovalDecisionOrderMismatchError`，edit 分支经 `cast_params`/`validate_params`）、`_outcome_to_agent_result` text→completed、`_save_interrupt`（`approval_id = uuid.uuid4().hex`，故 resume 再审批 approval_id 必不同于原 `"a1"`），以及 `domain/agent/exceptions.py`（60023 字段 `expected_count`/`actual_count`；60024 字段 `expected_tool_call_id`/`actual_tool_call_id`）与 `value_objects.py`（`EditedAction(name, arguments)`、`ApprovalDecision(type, tool_call_id, edited_action, message)`）。design 的断言细节（类型名、字段、code、prompt_id=`chat-default@v1`）与真实代码一致，无需照实际值修正。
- **harness 复用**：G2/G3 直接 `from test.infrastructure.agent.test_react_agent_hitl_unit import FakeModel, MemoryApprovalStore, RecordingTool, _adapter, _config`，未重建等价替身；G1 复用 `_v3_stream_helpers.install_stream_mock` + 文件内 `_FakeContextBuilder`（因 G1 无需审批 harness，`_adapter` 走 `MagicMock` tool_registry + echo 工具）。
- **结论**：Wave 1 三处缺口（G1/G2/G3）补测完成，Checkpoint 1 全门禁通过。勾选 tasks.md 1.1 / 1.2 / 任务 2。未开始 Wave 2。

## Wave 2（任务 3.1 / 3.2 / Checkpoint 2 / Checkpoint 3）— 方向 ADR + 文档同步 + 最终门禁

- **范围**：纯文档改动——新建 ADR-0010、更新 ADR 索引；无生产源码 / 配置文件、无测试文件的新增或修改。
  - `/workspace/docs/adr/0010-relocate-agent-loop-to-domain-direction.md`（新增）
  - `/workspace/docs/adr/README.md`（改：0009 行后追加 0010 索引行）
- **评估器（spec-evaluator）跳过**：本 slice 为纯文档（ADR + 索引），未新增 / 修改任何生产源码、配置或测试文件，按 Main Loop step 6 跳过 evaluator，直接以 Checkpoint 2/3 门禁实测输出验收。
- **Checkpoint 2 门禁实测**：
  - `test -f docs/adr/0010-...md` → `EXISTS`。
  - `grep -nE "supersedes:" docs/adr/0010-*.md` → `5:supersedes:`（字段存在、值为空，未 supersede ADR-0001）。
  - `grep -n "0010" docs/adr/README.md` → 命中第 20 行索引。
  - 人工核对：四段式（背景 :11 / 决策 :19 / 后果 :59 / 备选方案 :81）齐备；`领域事件` 仅出现于 P2_Invariants #5（不回退 ADR-0001）与备选方案 B（未采纳），未作为 P2 推荐形态；疑点登记两条（resume+handoff、AgentResult.model handoff 分支取父模型）在 §「P2 搬迁待观测疑点」在文。
- **Checkpoint 3 最终门禁实测**（`epsilon-boot/`，uv 位于 `/platform/.local/bin/uv`）：
  - `PYTHONPATH=src uv run --frozen pytest` → **2875 passed, 3 skipped, 0 failed**（`Existing_Test_Suite_Green`，Property 3）。
  - `git diff --stat -- src/` → 空（零生产改动，Property 4）。
  - `git status --porcelain 'test/infrastructure/agent/*_unit.py'` → 空（既有 unit 测试零改动，Property 2）。
  - `uv run ruff check test/infrastructure/agent/test_react_agent_characterization_*.py` → All checks passed（Property 7）。
  - 范围核对：git 根为 `/workspace`，改动仅落 `docs/adr/`（ADR-0010 新增 + README 改）+ Wave 1 两个 characterization 测试，未触 `src/`、`AgentPort`、前端、`pyproject.toml`/`uv.lock`。
- **与 design 的偏差**：无。据实校验 `react_agent_adapter.py` 为 3313 行（design/ADR 记「3314 行」，为整合报告口径，ADR 沿用其据实表述，差 1 行系口径而非事实错误）；docstring 第 7 行确为「本模块属于基础设施层」；顶部 import 未见 `openai`/`agents`/`litellm`，与背景证据一致。
- **结论**：Wave 2 完成，Checkpoint 2 与 Checkpoint 3 全门禁通过。勾选 tasks.md 3.1 / 3.2 / 任务 4 / 任务 5。全 spec 任务完成。
