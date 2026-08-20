---
status: Accepted
date: 2026-07-06
deciders: [后端架构维护者]
supersedes:
superseded-by:
---

# ADR-0008：将领域对象序列化职责外移至基础设施层序列化映射器

## 背景与问题（Context）

前置 spec `ddd-implementation-review` 的调研（「本项目 DDD 落地 vs 业界主流」）识别出 `Domain_Serialization_Concern`：`domain/` 内多个值对象自带 `to_dict()` / `to_http_dict()` / `to_event_payload()` 方法，并在模块级维护私有序列化辅助（`_dataclass_to_json_safe_dict` / `_json_safe`）。经代码复核，这类对外序列化方法分布于：

- `domain/run/workflow.py`：8 个 `to_dict` + 私有 `_dataclass_to_json_safe_dict` / `_json_safe`；
- `domain/agent/guardrails.py`：3 个 `to_dict` + `to_event_payload` + 私有 `_json_safe`；
- `domain/health/value_objects.py`：2 个 `to_dict`；
- `domain/agent/segmented_execution.py`：`SegmentBudgetUsage.to_dict` + `SegmentRunMetadata.to_http_dict`。

「如何把领域对象变成线格式 dict」属**基础设施关注点**，与 [srp-principle.md](../steering/srp-principle.md) 及 [ddd-tactical-modeling.md](../steering/ddd-tactical-modeling.md) 第 9 节「序列化、日志等技术关注点不入领域对象」冲突。领域对象因此背负了「知道自己如何序列化」的额外职责，偏离「领域层只承载业务语义」的目标。

本 ADR 记录该结构性/职责归属变更的方向决策；实际落地由 spec `ddd-tactical-remediation` 需求 B 执行，全程为**行为等价的纯重构**（对外线格式字面不变）。

## 决策（Decision）

我们将把领域对象的**对外序列化职责**从 `domain/` 外移到基础设施层：按子域在 `infrastructure/<子域>/*_serialization.py` 提供**模块级独立映射函数**（接受领域对象、产出与原 `to_dict()` 逐字段字面等价的 dict），与既有范式 `infrastructure/agent/approval_serialization.py` 保持一致。具体落点：

- `infrastructure/run/workflow_serialization.py`（workflow 8 函数）；
- `infrastructure/agent/guardrail_serialization.py`（guardrails 3 个 to_dict + `guardrail_observation_to_event_payload`）；
- `infrastructure/health/health_serialization.py`（health 2 函数）；
- `infrastructure/agent/segment_serialization.py`（segmented 2 函数）。

领域对象对外不再暴露 `to_dict` / `to_http_dict` / `to_event_payload`。**领域行为方法保留在领域层**：`GuardrailModelPricing.from_raw` / `estimate_cost`、`GuardrailRuntimeStats.from_model_usage`、`GuardrailDecision.to_summary`、`canonicalize_collaboration_summary`（领域归一逻辑）——它们承载业务语义或领域构造，非「领域数据→线格式」的纯序列化。

对**领域内部编排**所需的规范内部表示（如 `merge_guardrail_summary` / `mark_guardrail_summary_stale` / `canonicalize_collaboration_summary` 内部生成的 dict），按最小改动的 **A 方案**保留领域私有 helper（`domain/run/workflow.py` 保留 `_json_safe` + `_dataclass_to_json_safe_dict` 各 1 份；`domain/agent/guardrails.py` 保留 `_json_safe` + `_runtime_stats_payload`）——承认领域编排对内部表示的合理依赖，对外序列化职责已净移出领域公开面。「彻底零私有残留」（选项 B：把 `canonicalize_collaboration_summary`、guardrails 汇总编排一并上提）留待后续 spec。

本 ADR **不 supersede [ADR-0001](0001-remove-domain-event-bus.md)**：领域事件总线的移除是既定前提，本决策与之无关、不回退。

## 后果（Consequences）

- **正面**：领域层对外零序列化知识，回归「只承载业务语义」；序列化逻辑集中于基础设施层、可独立演进；符合 SRP 与战术建模第 9 节护栏。
- **负面 / 代价**：调用点从 `obj.to_dict()` 改为 `mapper(obj)`，`application/` 与 `infrastructure/` 中原本只依赖 `domain` 的部分文件新增了对 `infrastructure/*_serialization` 的 import 边界；`application/run/*` 若在模块级 import `infrastructure.run.*` 会触发既有 `infrastructure/run/__init__.py` eager-import 造成的循环依赖，落地时以**函数内局部 import** 最小规避（未重构 `__init__.py`，登记为后续观察项）。
- **后续影响**：领域侧保留少量私有序列化 helper（A 方案，已在 spec Property 5 明确记录为目标基线，非违约）；线格式字面等价由映射器等价性测试 + 既有回归测试双重锁定；新增映射器模块及其测试。

## 备选方案（Alternatives）

- **方案 A：集中式单一 `serialization` 包** —— 未采纳原因：会在单模块反向聚合多个子域的领域导入，破坏子域内聚，与既有按子域组织的 `approval_serialization.py` 范式分裂。
- **方案 B：引入映射器基类 / 注册表抽象** —— 未采纳原因：过度设计；`approval_serialization.py` 已证明模块级独立函数足以承载该职责。
- **方案 C：保留领域 `to_dict`（不外移）** —— 未采纳原因：即 `Domain_Serialization_Concern` 差距本身，违反 SRP 与战术建模第 9 节。
- **方案 D：领域内部也彻底零 dict 生成（选项 B）** —— 本期未采纳原因：需上提 `canonicalize_collaboration_summary` 与 guardrails 汇总编排、改动其全部调用点，超出「低风险行为等价重构」定位；登记为后续 spec。
