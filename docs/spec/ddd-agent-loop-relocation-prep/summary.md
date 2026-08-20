# ddd-agent-loop-relocation-prep — 落地总结

## Feature

`ddd-agent-loop-relocation-prep`：DDD 落地评估（`docs/spec/ddd-gap-analysis/report.md`）中**最大差距 P2（`Domain_Logic_In_Infrastructure`，🔴 高风险）** 的**前置降风险轮**。P2 本身是把 `infrastructure/agent/react_agent_adapter.py`（3313 行）的 ReAct Agent Loop 上提到领域层——直接搬迁风险极高。本 spec **不搬迁任何一行业务逻辑**，只产出让未来 P2 可安全落地的两样降风险资产：

1. **方向 ADR（ADR-0010）**：确立 Agent Loop 归属领域层的判断、划出「领域编排逻辑 vs 真技术封装」切分线、锁定 P2 不可破坏的不变量清单。
2. **特征化测试安全网**：把 Agent Loop 当前对外可观测行为固化为回归基线，作为未来 P2「行为等价」的判据。

全程**零生产代码改动**（`git diff src/` 为空）。

## 最终产物清单

### 新增（测试，仅补真缺口）
- `epsilon-boot/test/infrastructure/agent/test_react_agent_characterization_terminated_reason_orthogonality.py`（G1：纯文本自然收尾 + 工具循环正常收尾，`status`/`terminated_reason` 同为 `completed` 的正交锁定）
- `epsilon-boot/test/infrastructure/agent/test_react_agent_characterization_hitl_resume_matrix.py`（G2：`resume` edit 续跑采纳编辑参数；G3：决策数量不匹配 `ApprovalDecisionCountMismatchError`(60023)、顺序不匹配 `ApprovalDecisionOrderMismatchError`(60024)、策略型恢复后再次 `approval_required` 新 approval_id）

### 新增（文档）
- `docs/adr/0010-relocate-agent-loop-to-domain-direction.md`（`Accepted`，`supersedes:` 留空，不 supersede ADR-0001）+ `docs/adr/README.md` 索引一行

## 关键设计决策

| 决策 | 选定方案 | 理由 |
|---|---|---|
| 本轮范围 | 只降风险、不搬迁 | 3313 行核心算法直接搬迁风险极高，先备安全网 + 方向 |
| 特征化测试范围 | 先据实清点既有覆盖，**只补 G1/G2/G3 三处真缺口** | change-discipline 最小改动；5 个行为面绝大多数已被既有测试充分锁定，不重复造轮 |
| 需求 5（流式时序）/需求 7（handoff/token budget） | 判为已充分覆盖，无需新增 | 既有 `events`/`tool_arguments_delta`/`handoff`/`token_budget` 测试已锁定，evaluator 已抽查确认非"假已覆盖" |
| resume + handoff | 不补测，仅疑点登记 | AC7.2 只要求锁定"当前实际支持"入口，强补有断言未验证行为之嫌 |
| 生产代码改动 | 零 | 三处缺口均可经 AgentPort 四入口（run/resume）观测，无需抽纯函数/暴露探针 |

## ADR-0010 要点

- **切分线判据**（4 条可操作自问）：是否封装外部技术/SDK；是否可脱离运行时的纯业务判定；是否表达"何时停止/如何推进/产出何形态"通用语言；是否只是技术观测缝合胶水。
- **Domain_Orchestration_Candidates（应上提）**：`_iter_rounds` 循环控制、`AgentTerminationReason` 四态判定、`_is_token_budget_exceeded`/`_compute_total_tokens`、`_detect_handoff`、`_collect_pending_actions`、`_outcome_to_agent_result`、`RoundOutcome` 五态。
- **Infrastructure_Encapsulation_Candidates（留基础设施）**：`_GuardrailRuntimeAccumulator`、`ToolAbuseDetector`、OTel tracer 与 `_record_*`、`ApprovalStateStorePort` I/O、`approval_serialization`/`guardrail_serialization`、`approval_logging`、`_RoundStreamAccumulator`、`handoff_context`、`workflow_capability_runtime`、`merge_usage`。
- **P2_Invariants（6 条）**：AgentPort 四签名不变、Contract_Invariance、V3_Decisions_Frozen、Existing_Test_Suite_Green、不回退 ADR-0001、import 只改不改断言。

## 疑点登记（characterization 暴露，本轮只登记不修复，供 P2 spec 决策）

1. **`resume` 入口的 handoff 终止未被独立测试锁定**：resume 经 `_iter_rounds` 与 run 共享 handoff 短路逻辑，理论可达但无既有支持断言，属边界；P2 搬迁循环控制时需留意。
2. **`AgentResult.model` 在 handoff 分支取父模型**：`_outcome_to_agent_result` 用 `outcome.response.model`（上一轮父模型），未采纳 `HandoffPerformed.model`（目标 Agent 模型）；若 P2 认为应透传目标模型，另开 spec 决策。

## 执行过程中的受控偏差（如实记录）

1. **行数口径修正**：ADR 初稿沿用整合报告"3314 行"，实测 `react_agent_adapter.py` 为 **3313 行**；编排者统一修正 ADR 三处为 3313（协调性修复）。
2. **evaluator 首轮 FAIL → 修复后 PASS**：`spec-generator` 写 ADR 时把工具调用 XML 标签 `</content>`/`</invoke>` 泄漏进文件末尾（第 88-89 行）。evaluator 抓到此唯一阻塞缺陷（内容本身全部合规）。编排者删除该两行（正文实际结束于方案 E），并全量扫描两个测试文件确认无同类泄漏。
3. **evaluator 调用时机**：`spec-generator` 子代理不可自起 `spec-evaluator`，由编排者在全部落地后统一发起复审。

## 验证结论

- **全量测试**：`PYTHONPATH=src uv run --frozen pytest` → **2875 passed, 3 skipped, 0 failed**（较前基线 2869 多 6，来自 G1/G2/G3 六个特征化测试）。
- **零生产代码改动**：`git diff --stat src/` 为空；`react_agent_adapter.py` 与 `AgentPort` 契约未动。
- **既有测试零删改**：`test/infrastructure/agent/*_unit.py` 无改动，仅新增两个 `characterization_*` 文件。
- **规范合规**：新增测试 `ruff`/`pyright` 零新增错误、中文 docstring、全量类型标注。
- **ADR-0010 合规**：四段式齐全、`Accepted`、`supersedes:` 空、不推荐领域事件承载循环、含切分线判据+两类候选清单+P2_Invariants+疑点登记+5 备选、README 索引一致、行数 3313、无泄漏标签。
- **evaluator 裁决**：修复泄漏标签后达 clean PASS（全维度通过）。

## 后续事项（Follow-ups）

- **P2 搬迁（独立后续 spec，如 `ddd-agent-loop-relocation`）**：以 ADR-0010 切分线为据、以本轮特征化测试为安全网，先按 P2_Invariants 约束落地。落地时需处理领域编排与技术记账高度交织（如 `_execute_tool_call` 同时含控制流与 guardrail/trace/checkpoint 副作用）的解耦，及上述两条疑点。
- **P1 范式推广**：其余贫血子域按 change-discipline 逐个复用 `ddd-anemic-domain-pilot` 的领域服务范式。
- **P3（应用层大文件拆分）**：待 P2 方向落定后再评估。
