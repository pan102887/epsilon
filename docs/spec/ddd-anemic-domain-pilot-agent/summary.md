# ddd-anemic-domain-pilot-agent — 落地总结（domain/agent 贫血模型充血化试点）

## Feature

`ddd-anemic-domain-pilot-agent`：gap report **P1（贫血模型单子域充血化试点）** 的**第二子域**落地，承接姊妹试点 `ddd-anemic-domain-pilot`（`domain/task`，ADR-0009）建立的范式，对 `domain/agent` 子域推进。

把散落在基础设施层、本质属 agent 领域判定的 `StaticAgentGuardrailPolicy`（原 `infrastructure/agent/static_guardrail_policy.py`，216 行纯判定）**行为等价上提**到领域层。全程 `Behavior_Equivalent_Refactor`，判据零改动、Port 契约不变、不引领域事件。evaluator 最终门禁 CP3 裁决 **PASS**。

## 最终产物清单

### 新增（源码）
- `epsilon-boot/src/domain/agent/guardrail_policy.py` — 领域层护栏评估策略：`StaticAgentGuardrailPolicy`（**保留原类名**）整类逐条字面等价迁入，结构化实现领域内既有 `AgentGuardrailPolicyPort`（Protocol）。含 `classify_run`/`classify_payload`（任务分类）、四个 `evaluate_*`（护栏决策）、`_budget_decision`（token/duration/context/repeated/failure 五类阈值）、`_risk_decision`（critical/high/observe 风险门）、模块级 `_looks_batch`/`_segment_count` 启发式。`_json_safe` 复用 `domain/agent/guardrails` 已有等价实现。零 infrastructure/框架/pydantic/logging/OTel import。

### 新增（测试）
- `epsilon-boot/test/domain/agent/test_guardrail_policy_unit.py` — 脱离运行时领域单测 **54 用例**：分类全分支、四 evaluate、五类阈值命中/未命中、OBSERVE vs ENFORCE、风险门 critical/high/observe、**`_risk_decision` metadata 逐值专项断言**、启发式边界。

### 修改（基础设施委托）
- `epsilon-boot/src/infrastructure/agent/static_guardrail_policy.py` — 降为 re-export 兼容垫片（`from domain.agent.guardrail_policy import StaticAgentGuardrailPolicy, _looks_batch, _segment_count` + `__all__`），保护 7 处既有 import，`Infra.StaticAgentGuardrailPolicy is 领域版` 成立，isinstance/== 不破裂。垫片清理留后续片。
- `epsilon-boot/src/application/container_config.py::_create_guardrail_policy` — DI 装配 import 改指领域类（作正向样板，不经垫片），行为不变。

### 新增/修改（文档）
- `docs/adr/0014-introduce-guardrail-domain-service-in-agent-subdomain.md`（`Accepted`）+ `docs/adr/README.md` 索引追加。不 supersede，回链 ADR-0009（范式来源）/ADR-0010（同源方向判据）/ADR-0001（不回退事件总线）。
- `docs/domain-model.md`（新增「护栏策略领域服务」节）、`docs/architecture.md`（domain/agent 落点补注）。

## 关键设计决策

| 决策 | 选定方案 | 理由 |
|---|---|---|
| 领域落点与形态 | 新增 `domain/agent/guardrail_policy.py`，保留 `StaticAgentGuardrailPolicy` 类名 | 对齐 task 试点 `policy.py` 范式；保留类名规避既有测试断言/isinstance 语义漂移 |
| Port 关系 | 领域类结构化实现领域内 `AgentGuardrailPolicyPort`（Protocol） | Port 本就在 domain，领域类实现领域 Protocol 无反向依赖，签名零改动 |
| `static_guardrail_policy.py` 处置 | 降 re-export 垫片（非删除+改全部引用） | 参照 ADR-0011 round_outcome 垫片范式，保护 7 处既有 import 零改动，最小 diff；清理留后续片 |
| `_json_safe` 归属 | 复用 `domain/agent/guardrails._json_safe` | 逐值复核确认对 `_risk_decision` metadata 字面等价，避免序列化 helper 重复，领域类自洽 |
| ADR | 新增 ADR-0014，不 supersede，回链 0009/0010 | 引入领域策略一等抽象属方向决策；agent 子域对应 task 子域的 ADR-0009 |

## 验证结论

- **全量测试**：`PYTHONPATH=src uv run --frozen pytest -q` → **2975 passed, 3 skipped, 0 failed**（较 Wave 前基线增 54 领域单测，无新增 failed）。
- **行为等价**：evaluator 用 `git show HEAD` 取原实现逐行比对，判定逻辑（五类阈值 `>=`+None 短路+固定顺序、风险门 critical→STOP/high→REQUIRE_APPROVAL/OBSERVE 降级、`_looks_batch`/`_segment_count` 边界）逐字面等价，仅 docstring 与 `_json_safe` 来源变化。
- **契约不变**：`ports.py` diff 为空；垫片 `Infra is Dom = True`；DI 返回类型不变；消费方 react_agent_adapter 鸭子调用行为不变。
- **领域纯净度**：`guardrail_policy.py` 无 code 级 infrastructure/framework/pydantic/logging/OTel import（仅 docstring 提及），无 EventBus/DomainEvent/publish/subscribe。
- **lint**：ruff All checks passed；pyright 改动文件零新增错误（container_config:1213 经 git stash 对照确认为既存基线错误，与本次无关）。
- **范围纪律**：改动仅限交付物清单文件；未改已 Accepted 的 ADR 0001–0013 正文；后续片候选未被触碰。
- **evaluator 裁决**：CP3 PASS（26 AC + 8 正确性属性全维度）。

## 后续事项（Follow-ups）

- **agent 子域其余充血候选（后续片）**：`agent_config.py::max_delegation_depth` 规范化、`approval_policy_provider.py` 策略查表、`segmented_orchestration.py::decide_next_segment`（后者与 task 的 `TaskContinuationPolicy` 可能重叠，需先厘清边界）——本试点按范围纪律未做，留后续片评估。
- **`static_guardrail_policy.py` 垫片清理**：待后续片无外部依赖后删除（对齐 ADR-0011→0012 的垫片清理节奏）。
- **其余贫血子域**：按 gap report P1 逐子域推进，复用本试点 + task 试点建立的领域策略/服务范式。
- **P3（应用层大文件拆分）与治理收尾**：按 gap report `Priority_Roadmap` 排在 P1 之后。
