# ddd-anemic-domain-agent-followups — 落地总结（domain/agent 三候选充血化后续片）

## Feature

`ddd-anemic-domain-agent-followups`：gap report **P1（贫血模型充血化）** 在 `domain/agent` 子域的**后续片**，承接前序试点 `ddd-anemic-domain-pilot`（`domain/task`，ADR-0009）与 `ddd-anemic-domain-pilot-agent`（`domain/agent` 首片 `StaticAgentGuardrailPolicy`，ADR-0014）建立的范式，落地这两片显式列为「后续片」的三个候选：

- **候选 A（委派深度规范化）**：`agent_config.py::_clamp_max_delegation_depth` 的「`<=0` 回退默认值 3」归一规则；
- **候选 B（审批默认查表）**：`approval_policy_provider.py` 的 `_DEFAULT_POLICIES`/`_LOW_RISK_TOOLS`/决策集常量与默认查表判定；
- **候选 C（分段续跑判定平移）**：`segmented_orchestration.py::decide_next_segment`（本已是纯领域判定，仅物理放错层）。

全程 `Behavior_Equivalent_Refactor`：判据逐字面等价、Port 契约不变、不引领域事件（尊重 ADR-0001）、领域层零 infrastructure/framework/pydantic/json/logging/OTel 依赖。独立 spec-evaluator 最终裁决 **PASS**（26+ AC + 11 条正确性属性全维度）。

## 最终产物清单

### 新增（领域源码）
- `epsilon-boot/src/domain/agent/config_policy.py` — 候选 A：领域服务 `DelegationDepthNormalizationPolicy`（`normalize(raw)`/`default_max_delegation_depth()`）+ 常量 `DEFAULT_MAX_DELEGATION_DEPTH=3`。承载归一三分支（`None` 不动 / 可转 int 且 `<=0` → 3 / 转 int 抛异常保留原值 / `>0` 保持）。仅标准库依赖。
- `epsilon-boot/src/domain/agent/approval_lookup.py` — 候选 B：领域服务 `ApprovalDefaultLookup`（`policy_for`/`decisions_for`）+ 公开常量 `DEFAULT_POLICIES`/`LOW_RISK_TOOLS`/`APPROVE_REJECT`/`APPROVE_EDIT_REJECT`。仅依赖 `domain.agent.value_objects.ApprovalPolicy`，不引 `json`。
- `epsilon-boot/src/domain/agent/segmented_orchestration.py` — 候选 C：`decide_next_segment` + `SegmentContinuationDecision` 自 infrastructure 逐行字面平移（12 门顺序、`>=`、`is not None` 短路、`*1000`、每条 stop_reason 不变）。导入集合与平移前完全一致。

### 新增（领域单测，脱离运行时，共 48 用例）
- `test/domain/agent/test_config_policy_unit.py`（15 用例）— 候选 A 全分支 + float 锚点等价性。
- `test/domain/agent/test_approval_lookup_unit.py`（16 用例）— 候选 B 命中 6 工具 / 低风险 4 工具 / 未命中非低风险 / `decisions_for` 命中与默认元组。
- `test/domain/agent/test_segmented_orchestration_unit.py`（17 用例）— 候选 C 12 门逐条命中 + None 短路 + 全未触发 + 垫片同一对象断言。

### 修改（基础设施委托 / 垫片）
- `epsilon-boot/src/infrastructure/agent/agent_config.py` — `AgentRuntimeConfig` 留原位（保留 pydantic-settings、`AGENT_` 前缀、`agent_config` 全局实例），validator 改委托 `DelegationDepthNormalizationPolicy.normalize`，常量改指领域别名，`dict[str, Any]`→`dict[str, object]`（禁裸 Any）。无垫片（符号未移动）。
- `epsilon-boot/src/infrastructure/agent/approval_policy_provider.py` — `StaticApprovalPolicyProvider` 留原位（类身份/构造签名/`ApprovalPolicyPort` 继承/JSON 三方法/`HitlConfigInvalidError` 不变），`policy_for` 无 override 默认分支委托 `ApprovalDefaultLookup.policy_for`，`_policy_from_value(value is True)` 委托 `ApprovalDefaultLookup.decisions_for`。
- `epsilon-boot/src/infrastructure/agent/segmented_orchestration.py` — 降为 re-export 垫片（`from domain.agent.segmented_orchestration import ...` + `__all__`），保护 3 处消费方（`chat_service_adapter.py`×2、`task_agent_adapter.py`×1）与既有 infra 单测零改动，`isinstance`/`==`/同一对象语义不破裂（对齐 ADR-0011/0014 垫片范式）。

### 新增/修改（文档）
- `docs/adr/0015-uplift-agent-config-normalization-approval-lookup-and-relocate-segment-continuation.md`（`Accepted`）+ `docs/adr/README.md` 索引追加。不 supersede ADR-0001，回链 ADR-0009（task 范式）/ADR-0014（agent 首片同源）/ADR-0008（配置解析归属基础设施）。
- `docs/domain-model.md`、`docs/architecture.md` — 补三领域构件落点 + 两处边界结论 + 委托方留 infra 的指向性说明。

## 关键设计决策

| 决策 | 选定方案 | 理由 |
|---|---|---|
| 三候选落点 | 各新建独立领域模块（`config_policy.py`/`approval_lookup.py`/`segmented_orchestration.py`），与 `guardrail_policy.py` 并列 | 对齐 task/agent 首片范式；独立模块保持 SRP（配置归一/审批查表/分段编排三类职责分离）；C 独立同名模块使垫片 re-export 更直观、diff 最小 |
| A/B 无垫片、C 有垫片 | A 的 `agent_config` 全局实例、B 的 `StaticApprovalPolicyProvider` 类留原位改内部委托；仅 C 的符号物理迁走须垫片 | 对外符号未移动即无需垫片，最小改动 |
| 配置边界留 infrastructure | `AgentRuntimeConfig`（pydantic-settings）与审批 JSON 解析（`_parse_interrupt_on`/`_policy_from_value`/`_validate_decisions`，依赖 `json`）保留 infra | 依赖框架/`json`、面向配置字符串，按 ADR-0008 属配置边界技术关注点，不进领域层 |
| 两处边界厘清 | A `Delegation_Depth_Normalization`（一元归一 `object→int`）≠ `DelegationDepthPolicy`（二元比较 `current vs max`）；C 分段 12 门续跑门 ≠ `TaskContinuationPolicy`（单次终止原因→PAUSED 映射） | 语义不重叠，不合并、不重复上提、不修改既有 task 领域服务；ADR-0015 显式记录 |
| ADR | 新增 ADR-0015，不 supersede，回链 0009/0014/0008/0001 | 三候选上提/平移 + 两处边界厘清属方向决策 |

## 验证结论

- **全量测试**：`PYTHONPATH=src uv run --frozen pytest` → **3023 passed, 3 skipped, 0 failed**（较首片基线 2975 增 48 领域单测，无新增 failed）。
- **行为等价**：evaluator 用 `git show HEAD` 取三处原实现逐行比对——A 归一三分支（含 validator「键缺失 vs 值为 None」收敛等价）、B `policy_for`/`decisions_for` 逐值等价（复用同一字面对象、frozenset 拷贝、risk_label）、C 12 门顺序/`>=`/`is not None` 短路/`*1000`/stop_reason 逐行字面平移，均判定等价。
- **契约不变**：`ApprovalPolicyPort.policy_for` 签名、`ApprovalPolicy`/`SegmentContinuationDecision` 字段、构造签名、DI 装配（`_create_approval_policy`/`agent_config`）对外行为不变；消费方经垫片/委托零改。
- **领域纯净度**：三领域文件代码级零 infrastructure/framework/pydantic/json/logging/OTel/ContextVar 依赖（仅 docstring 文字提及）、无 EventBus/DomainEvent/publish。
- **边界不破坏**：`domain/task/policy.py` 零 diff（`DelegationDepthPolicy`/`TaskContinuationPolicy` 未改/未合并）；`segmented_execution.py`/`guardrail_policy.py`/`ports.py`/`value_objects.py`/`agent_loop_*` 零 diff。
- **lint/type**：ruff 六文件 All checks passed；pyright 三领域文件 0 errors。`approval_policy_provider.py` 残留 1 处 `reportArgumentType` 经 `git show HEAD` 核验为**既有基线错误**（改动前 2 处，本片降为 1 处），无新增。
- **依赖清单**：`pyproject.toml`/`uv.lock` 零 diff，未引入任何第三方依赖。
- **范围纪律**：改动仅限交付物清单文件；未改已 Accepted 的 ADR 0001–0014 正文。
- **evaluator 裁决**：独立 spec-evaluator 复审 **PASS**（Requirement Compliance / Design Adherence / 11 条 Correctness Property / Code Quality / Error Handling / Task Completeness 全维度）。

## 后续事项（Follow-ups）

- **垫片清理**：`infrastructure/agent/segmented_orchestration.py` re-export 垫片待后续片将 3 处消费方 import 改指领域路径后删除（对齐 ADR-0011→0012 的垫片清理节奏）。
- **pyright 基线清零（可选，非阻断）**：`approval_policy_provider.py` 的 1 处既有 `reportArgumentType`（`frozenset[str]`→`frozenset[ApprovalDecisionType]` Literal 收窄）可在 provider 分支补 `# type: ignore[arg-type]` 彻底清零，与本片无关。
- **其余贫血子域**：按 gap report P1 逐子域推进，复用 task/agent 试点建立的领域策略/服务范式。
- **P3（应用层大文件拆分）与治理收尾**：按 gap report `Priority_Roadmap` 排在 P1 之后。
