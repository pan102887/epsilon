# ddd-agent-loop-relocation — 落地总结（P2 首片）

## Feature

`ddd-agent-loop-relocation`：DDD 落地评估（`docs/spec/ddd-gap-analysis/report.md`）最大差距 **P2（`Domain_Logic_In_Infrastructure`）** 的**正式落地首片**。落地 ADR-0010 确立的方向,采用**分片增量策略**——本首片只搬迁风险最低、零 I/O、给定输入即定输出的**纯编排叶子函数 + `RoundOutcome` 值对象**;`_iter_rounds` 循环主体与技术记账（`_execute_tool_call`/审批中断/流式累加）明确留后续片。

全程 `Behavior_Equivalent_Refactor`,遵守 ADR-0010 的 `P2_Invariants` 六条,evaluator 裁决 **PASS**。

## 最终产物清单

### 新增（源码）
- `epsilon-boot/src/domain/agent/agent_loop_policy.py` — 领域层 Agent Loop 编排模块:
  - `RoundOutcome` / `RoundOutcomeKind` 值对象**真身**（从 infrastructure 迁入,10 字段逐一等价）
  - 4 个模块级纯函数:`compute_total_tokens`、`is_token_budget_exceeded`、`detect_handoff`、`outcome_to_agent_result`（去 `_` 前缀、去 `@staticmethod`,行为逐行等价）

### 新增（测试）
- `epsilon-boot/test/domain/agent/test_agent_loop_policy_unit.py` — 脱离运行时单测,覆盖 token 命中/回退/空、预算 None/等于/超限、handoff 命中/未命中/尾部非 ToolMessage/非末尾命中、各 kind 翻译（含疑点2）、RoundOutcome 默认值+frozen

### 新增（文档）
- `docs/adr/0011-relocate-agent-loop-leaf-orchestration-to-domain.md`（`Accepted`,不 supersede 0001/0010,落地 0010 方向）+ `docs/adr/README.md` 索引

### 修改（基础设施委托）
- `src/infrastructure/agent/react_agent_adapter.py` — 去薄封装:删 4 个 `@staticmethod`、import 领域函数、5 处调用点直调、`_log_token_budget_exceeded` 内改调领域 `compute_total_tokens`（本体含 logger 留基础设施）
- `src/infrastructure/agent/round_outcome.py` — 降为 re-export 兼容垫片（`RoundOutcome is 领域 RoundOutcome` 同一类,isinstance/== 不破裂）

### 修改（测试 import/调用形式,断言零改动）
- `test/domain/agent/test_value_objects_terminated_reason_unit.py` — import 改指领域新模块
- `test/infrastructure/agent/test_react_agent_token_budget_unit.py` — import + 对 `_outcome_to_agent_result` 直调改为领域函数直调

### 修改（文档同步,doc-sync）
- `docs/architecture.md` — Port/Adapter 表 AgentPort 行补注 + ReAct Agent Loop 流程节说明纯编排叶子入领域层
- `docs/domain-model.md` — 新增「Agent Loop 编排构件」节

## 关键设计决策

| 决策 | 选定方案 | 理由 |
|---|---|---|
| 领域模块落点 | 单文件 `domain/agent/agent_loop_policy.py`（4 纯函数 + RoundOutcome 值对象同处） | 同子域关注点强内聚,对齐 P1 `domain/task/policy.py` 具名样板 |
| 纯函数形态 | 模块级纯函数（非领域服务类） | 4 者职责各异、无状态,对齐 `workspace/policy.py` 纯函数风格与 SRP |
| RoundOutcome 迁移兼容 | 真身迁 domain + `round_outcome.py` 留 re-export 垫片 | 最小化既有 import 改动,满足 P2_Invariants 第6条;垫片临时,后续片清理 |
| 适配器委托方式 | 去薄封装,调用点直调（不留空壳 @staticmethod） | 避免"两处像入口"认知负担,领域为唯一权威落点 |
| 疑点2处理 | handoff 分支 model 取 `outcome.response.model` 照搬,**不修正** | ADR-0010 疑点2登记"另开 spec 决策",行为等价纯重构不改取值 |

## 分片增量的价值

P2 是本会话风险最高的一步（3313 行核心算法搬迁）。ADR-0010 已否决"一次性大爆炸搬迁"。本首片以最低风险打通"领域层承载 Agent Loop 编排构件"的第一块:
- **验证上提路径可行**:领域模块零反向依赖、既有 import 经垫片无缝解析、全量测试全绿。
- **建立样板**:后续片（`_iter_rounds` 主体、`_execute_tool_call` 解耦）可复用本片的领域模块 + 垫片 + 委托范式。
- **风险隔离**:高度交织的循环主体与技术记账留待独立后续片,不被首片牵动。

## 验证结论

- **全量测试**：`PYTHONPATH=src uv run --frozen pytest` → **2893 passed, 3 skipped, 0 failed**（含特征化测试 6 passed 作行为等价回归基线）。
- **行为等价**：4 函数与 RoundOutcome 与被删原实现逐行字面等价（evaluator 用 `git show HEAD` 比对确认）;`_iter_rounds` 循环主体、handoff 短路、token budget pending 标记逻辑除函数调用替换外未动。
- **垫片正确性**：`RoundOutcome is 领域 RoundOutcome` 成立,无重复定义。
- **P2_Invariants 六条**：AgentPort 四签名未变（ports.py 零 diff）;Contract_Invariance 靠特征化测试全绿佐证;V3 决策未动;既有测试零断言改动;不引领域事件;import 只改不改断言。
- **领域纯净度**：`agent_loop_policy.py` 零 application/infrastructure/框架/Pydantic import;中文 docstring;全量类型标注;ruff/pyright 零新增错误。
- **范围未越界**：源码仅 3 文件改动如约束,未动前端/依赖;未搬 `_iter_rounds` 主体/`_execute_tool_call`/`_collect_pending_actions`/流式累加。
- **evaluator 裁决**：PASS（全维度,仅两条非阻塞正向观察:docstring 增强、垫片临时性标注良好）。

需求 1–6 与 Property 1–8 全覆盖（详见 tasks.md 追溯表与 review-log.md）。

## 后续事项（P2 后续片 Follow-ups）

- **P2 第二片及以后**：`_iter_rounds` 循环主体上提领域层——需处理与 `_execute_tool_call`（控制流 + guardrail/trace/checkpoint 副作用高度交织）的解耦,可能需引入领域服务 + 端口回调结构（ADR-0010 后果节已警示）。
- **审批中断决策 `_collect_pending_actions`**：涉 ApprovalPolicyPort I/O 时序,后续片处理。
- **round_outcome.py 垫片清理**：后续片 `_iter_rounds` 上提完成、无外部依赖垫片后清理（ADR-0011 后果节登记）。
- **ADR-0010 疑点1（resume+handoff 未独立锁定）、疑点2（handoff 分支 model 取父模型）**：若后续认为应透传目标模型,另开 spec 决策。
- **其余贫血子域**：按 change-discipline 复用 P1 领域服务范式逐子域推进。
