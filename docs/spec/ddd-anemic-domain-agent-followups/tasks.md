# 实现计划：贫血领域模型充血化后续片（domain/agent 三候选：委派深度规范化 / 审批查表 / 分段续跑）

> 本文件由已定稿的 `design.md` 展开为可执行、可勾选的任务清单。全程为 **`Behavior_Equivalent_Refactor`（行为等价纯重构）**：把散落在 `infrastructure/agent/` 的三处领域判定按 ADR-0009/0014 既有范式收敛/平移进 `domain/agent/`——候选 A（委派深度规范化，新建 `config_policy.py`）、候选 B（审批默认查表，新建 `approval_lookup.py`）、候选 C（分段续跑判定平移，新建 `segmented_orchestration.py` + 原 infra 文件降垫片）。所有判据、检查顺序、比较运算符（`>=`）、`None` 短路语义、吞异常语义、决策集/风险标签取值均与上提前逐一等价，不新增/删除/更改任何一条业务规则、不引领域事件（尊重 ADR-0001）、不改 Port 签名、不改 `domain/task/policy.py`。
> 每条任务标注：动作、目标文件、对应 requirement AC 与 design 组件 / Property 编号、验证命令，以及**是否需 spec-evaluator 复审**（实现/上提任务需复审；纯 doc-sync/ADR/rename 由 generator 自行判断）。
> **命令基线**：所有测试/lint/import 命令均在 `epsilon-boot/` 目录下执行，依赖仅用 `uv`，测试须带 `PYTHONPATH=src`；ADR/文档校验命令在仓库根（`docs/` 位于仓库根，非后端子目录）。
> **全程硬约束**：三领域文件零 `application`/`infrastructure`/框架/Pydantic/`json`/logging/OTel/ContextVar 依赖、无新第三方依赖、不引领域事件；中文 docstring（`code-documentation.md`）；全量类型标注、禁裸 `Any`（配置原始值用 `object`）、`ruff`/`pyright` 零新增错误（`python-typing-lint.md`）；`Existing_Test_Suite_Green` 每组结束保持通过。三候选相互独立、可分别验收，逐候选推进（`change-discipline.md`）。

## 概述

执行按 **候选分组（Group A/B/C）+ 每候选 Checkpoint + 全局收尾组 + 最终 Checkpoint** 结构组织。三候选互不依赖，可任意顺序或并行推进；组内遵循 design「组件依赖图」的依赖方向（`infrastructure → domain`）自底向上：先新增/承载领域构件 → 领域单测 → 改造 infrastructure 委托/垫片 → 既有测试 import 调整（如需）→ 该候选 Checkpoint 门禁。

- **Group A（候选 A：委派深度规范化上提）**：新建 `domain/agent/config_policy.py`（`DelegationDepthNormalizationPolicy` + `DEFAULT_MAX_DELEGATION_DEPTH`）→ 领域单测 → `AgentRuntimeConfig` validator 改委托、常量改指领域别名 → CP-A。
- **Group B（候选 B：审批默认查表上提）**：新建 `domain/agent/approval_lookup.py`（`ApprovalDefaultLookup` + `DEFAULT_POLICIES`/`LOW_RISK_TOOLS`/决策集常量）→ 领域单测 → `StaticApprovalPolicyProvider` 常量别名 re-export + 默认查表/`value is True` 分支委托、JSON 三方法保留 → CP-B。
- **Group C（候选 C：分段续跑判定平移）**：新建 `domain/agent/segmented_orchestration.py`（平移 `decide_next_segment` + `SegmentContinuationDecision`）→ 领域单测 → `infrastructure/agent/segmented_orchestration.py` 降 re-export 垫片 → 既有 infra 单测按需调 import → CP-C。
- **Group Z（全局收尾）**：ADR-0015 + `docs/adr/README.md` 索引 → doc-sync（`domain-model.md`/`architecture.md`）→ 最终 CP（全量 pytest / ruff / pyright / 改动范围 grep / 三领域文件零基础设施依赖 grep / 两处边界 `domain/task/policy.py` 无 diff）。

> **反断裂纪律**：每组「先新增领域构件 + 领域单测（不触碰既有引用点）→ 再改 infrastructure 委托/垫片 → 最后按需调既有测试 import」。C 的垫片确保既有 3 处消费方（`chat_service_adapter.py`、`task_agent_adapter.py`）与既有 infra 单测零改动通过。A/B 的对外符号（`agent_config` 全局实例、`StaticApprovalPolicyProvider` 类）留原位、无移动，无需垫片。
> **落点核实**：`src/domain/agent/config_policy.py`、`approval_lookup.py`、`segmented_orchestration.py` 三文件当前均不存在，需新建；`src/domain/agent/__init__.py` 与 `test/domain/agent/__init__.py` 均已存在，三领域常量/类**不新增**进 `__init__.py` 的 `__all__`（保持最小改动，消费方经具体模块路径/垫片访问）。既有 `test/infrastructure/agent/test_approval_policy_provider_unit.py` / `_property.py` 仅 import `StaticApprovalPolicyProvider`、不引私有常量，预期零改动通过；`test/infrastructure/agent/test_segmented_orchestration_unit.py` 经垫片零改通过。当前 ADR 最新为 0014，本片取 **0015**。

---

## Group A：委派深度规范化上提（候选 A，需求 2）

- [x] 1. 委派深度规范化领域构件与委托改造
  - [x] 1.1 新建领域服务 `src/domain/agent/config_policy.py`
    - 在 `src/domain/agent/config_policy.py` 新建（当前不存在），含模块级中文 docstring（说明：承载 Agent 运行时配置「委派深度上限」的规范化领域规则，为零基础设施依赖的 `Domain_Service`；无框架、无 Pydantic、无 I/O、无 logging，可脱离配置框架单测；不变量为归一三分支与上提前逐一等价；显式记录与 `domain/task/policy.py::DelegationDepthPolicy` 的边界——本服务做「配置取值一元规范化 `object -> int`」，后者做「运行期深度二元比较 `current vs max`」，语义不同、不合并，详见 ADR-0015）。
    - 顶部 `from __future__ import annotations`；**仅标准库、无第三方、无同层依赖**。
    - 模块级常量 `DEFAULT_MAX_DELEGATION_DEPTH = 3`（含中文 docstring：委派递归深度默认值，自 infrastructure 上提）。
    - `class DelegationDepthNormalizationPolicy`（领域服务，含中文 docstring）：
      - `@staticmethod default_max_delegation_depth() -> int`：返回 `DEFAULT_MAX_DELEGATION_DEPTH`。
      - `@staticmethod normalize(raw: object) -> object`：与 `AgentRuntimeConfig._clamp_max_delegation_depth` 三分支逐一等价——`raw is None` 原样返回；能转 int 且 `int(raw) <= 0` 返回 `DEFAULT_MAX_DELEGATION_DEPTH`；`int(raw)` 抛 `TypeError`/`ValueError` 时原样返回（吞异常、保留原值）；能转 int 且 `> 0` 原样返回。`int(raw)` 调用点用窄豁免（局部 `# type: ignore[call-overload]`）避免引入裸 `Any`，行为与上提前字面一致，全量类型标注。
    - _需求: 2.1, 2.2, 7.1, 7.2, 7.3_ ; _design 组件 1 / Property 1_
    - 复审：**需 spec-evaluator 复审**（上提领域构件）。
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run python -c "from domain.agent.config_policy import DelegationDepthNormalizationPolicy, DEFAULT_MAX_DELEGATION_DEPTH"`（import 正常）。

  - [x] 1.2 新增领域单测 `test/domain/agent/test_config_policy_unit.py`（全分支，脱离运行时）
    - 在 `test/domain/agent/test_config_policy_unit.py` 新建，**仅 import `domain.agent.config_policy`**，不 import `application`/`infrastructure`/框架运行时（脱离运行时单测，AC6.1）。
    - 参数化覆盖 `normalize`：`None` 原样返回；`0`/`-5`/`"0"`（可转 int 的 `<=0` 串）归一为 `3`；`5`/`"7"` 保持；`"abc"`/非数字对象（触发 `TypeError`/`ValueError`）保留原值；`3.9`（float 转 int）按 `int(3.9)=3>0` 保持原值 `3.9`（等价性锚点）；`default_max_delegation_depth() == 3`（AC6.2）。
    - _需求: 6.1, 6.2, 6.5_ ; _design 测试策略第 1 项 / Property 1_
    - 复审：需 spec-evaluator 复审（等价性锁定断言）。
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/agent/test_config_policy_unit.py`。

  - [x] 1.3 `AgentRuntimeConfig` validator 改委托领域策略
    - 修改 `src/infrastructure/agent/agent_config.py`（配置类**留原位**，保留 pydantic-settings、`AGENT_` 前缀、`agent_config` 全局实例、`max_delegation_depth`/`delegate_tool_enabled` 字段与默认值）：
      - 新增 import：`from domain.agent.config_policy import (DEFAULT_MAX_DELEGATION_DEPTH as _DEFAULT_MAX_DELEGATION_DEPTH, DelegationDepthNormalizationPolicy)`；移除本地 `_DEFAULT_MAX_DELEGATION_DEPTH = 3` 定义（改指领域常量别名，字段默认值 `max_delegation_depth: int = _DEFAULT_MAX_DELEGATION_DEPTH` 不变）。
      - `_clamp_max_delegation_depth` validator 改为：`if "max_delegation_depth" in values: values["max_delegation_depth"] = DelegationDepthNormalizationPolicy.normalize(values["max_delegation_depth"])`；`return values`。等价性关键（写入等价性说明注释/docstring）：原实现 `raw = values.get(...)` 键缺失返回 `None` → 不进 if → 不改动；新实现「键缺失 → 不进 if → 不改动」，键存在且值 `None` → `normalize(None)=None` 写回 `None`（与不改动等价，pydantic 后续用字段默认）；两者逐一等价。`values` 类型标注由 `dict[str, Any]` 收窄为 `dict[str, object]`（禁裸 `Any` 的等价调整，不改运行期行为），移除不再使用的 `from typing import Any`（如已无其他用途）。
    - _需求: 2.3, 2.4, 7.4_ ; _design 组件 2 / Property 1、7_
    - 复审：**需 spec-evaluator 复审**（委托边界等价性）。
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/application/test_container_config.py test/application/test_agent_delegation_config_properties.py`（`agent_config.max_delegation_depth` 装配读取等价、既有委派配置属性测试全绿）。

## Checkpoint A：候选 A 就绪 + 领域纯净度（门禁）

- [x] 2. CP-A 门禁校验（全部通过方视候选 A 完成）
  - 新构件可解析：`cd epsilon-boot && PYTHONPATH=src uv run python -c "import domain.agent.config_policy"`（无报错）。
  - 领域单测全绿：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/agent/test_config_policy_unit.py`。
  - 领域纯净度（Property 9）：`cd epsilon-boot && grep -rnE "import (application|infrastructure|fastapi|pydantic|json|logging)|from (application|infrastructure|fastapi|pydantic|json|logging)" src/domain/agent/config_policy.py`（期望零命中）；`grep -nE "ContextVar|opentelemetry|event_bus|DomainEvent|publish" src/domain/agent/config_policy.py`（期望零命中）；`grep -nE "^from |^import " src/domain/agent/config_policy.py`（人工核对仅含 `__future__`）。
  - 边界不修改（Property 2 / AC1.4）：`cd .. && git diff --name-only | grep -q "src/domain/task/policy.py" && echo "VIOLATION" || echo "OK"`（`domain/task/policy.py` 无 diff，`DelegationDepthPolicy` 未被修改）。
  - 规范合规：`cd epsilon-boot && uv run ruff check src/domain/agent/config_policy.py src/infrastructure/agent/agent_config.py` 与 `cd epsilon-boot && uv run pyright src/domain/agent/config_policy.py src/infrastructure/agent/agent_config.py`（零新增错误、无裸 `Any` 越界；中文 docstring 人工核对齐备）。
  - _需求: 2.1, 2.2, 2.3, 2.4, 2.5, 6.1, 6.2, 7.1, 7.2, 7.3, 7.4_ ; _design Property 1、2、9_

---

## Group B：审批默认查表上提（候选 B，需求 3）

- [x] 3. 审批默认查表领域构件与委托改造
  - [x] 3.1 新建领域服务 `src/domain/agent/approval_lookup.py`
    - 在 `src/domain/agent/approval_lookup.py` 新建（当前不存在），含模块级中文 docstring（说明：承载「工具名 → 默认审批策略」纯查表领域规则，为零基础设施依赖的 `Domain_Service`；无 `json`、无框架、无 I/O，可脱离配置字符串单测；JSON 配置解析依 ADR-0008 属配置边界技术关注点、保留在 infrastructure；不变量为查表判据/决策集/`risk_label` 取值与上提前逐一等价）。
    - 顶部 `from __future__ import annotations`；唯一导入 `from domain.agent.value_objects import ApprovalPolicy`（标准库外**不引** `json`/`application`/`infrastructure`/框架）。
    - 模块级公开常量（去前导下划线，各含中文 docstring）：`APPROVE_REJECT = frozenset({"approve", "reject"})`；`APPROVE_EDIT_REJECT = frozenset({"approve", "edit", "reject"})`；`DEFAULT_POLICIES: dict[str, tuple[frozenset[str], str]]`（6 条工具 → (决策集, 风险标签)，字面自 infrastructure 逐字迁移：`write_file`/`edit_file`/`shell_exec`/`python_exec`/`delegate_to_agent` 用 `APPROVE_REJECT`，`http_request` 用 `APPROVE_EDIT_REJECT`，风险标签逐字不变）；`LOW_RISK_TOOLS = frozenset({"read_file", "list_dir", "web_fetch", "web_search"})`。
    - `class ApprovalDefaultLookup`（领域服务，含中文 docstring）：
      - `@staticmethod policy_for(tool_name: str) -> ApprovalPolicy`：与 `StaticApprovalPolicyProvider.policy_for` 无 override 默认分支逐一等价——命中 `DEFAULT_POLICIES` 返回 `interrupt=True` + `frozenset(decisions)` + `risk_label`；未命中返回 `interrupt=False` + 空 `allowed_decisions` + `risk_label`（`tool_name in LOW_RISK_TOOLS` 为「低风险工具」否则 `""`）。
      - `@staticmethod decisions_for(tool_name: str) -> tuple[frozenset[str], str]`：与 `_policy_from_value` 中 `_DEFAULT_POLICIES.get(tool_name, (_APPROVE_REJECT, "用户配置审批工具"))` 逐一等价，命中返回对应元组、未命中返回 `(APPROVE_REJECT, "用户配置审批工具")`。
    - _需求: 3.1, 7.1, 7.2, 7.3_ ; _design 组件 3 / Property 3、4_
    - 复审：**需 spec-evaluator 复审**（上提领域构件 + 常量字面等价）。
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run python -c "from domain.agent.approval_lookup import ApprovalDefaultLookup, DEFAULT_POLICIES, LOW_RISK_TOOLS, APPROVE_REJECT, APPROVE_EDIT_REJECT"`（import 正常）。

  - [x] 3.2 新增领域单测 `test/domain/agent/test_approval_lookup_unit.py`（查表全分支，脱离运行时）
    - 在 `test/domain/agent/test_approval_lookup_unit.py` 新建，**仅 import `domain.agent.approval_lookup` 与 `domain.agent.value_objects`**（AC6.1）。
    - `policy_for` 覆盖：6 个 `DEFAULT_POLICIES` 工具（区分 5 个 `APPROVE_REJECT` vs `http_request` 的 `APPROVE_EDIT_REJECT`，断言 `interrupt=True`/`allowed_decisions`/`risk_label` 逐值）；`LOW_RISK_TOOLS` 4 工具（`interrupt=False`、`allowed_decisions` 空、`risk_label="低风险工具"`）；未命中且非低风险工具（`interrupt=False`、`risk_label=""`）（AC6.3）。
    - `decisions_for` 覆盖：命中（如 `decisions_for("write_file") == (APPROVE_REJECT, "高风险文件写入")`）与未命中默认元组（`decisions_for("unknown") == (APPROVE_REJECT, "用户配置审批工具")`）。
    - _需求: 6.1, 6.3, 6.5_ ; _design 测试策略第 1 项 / Property 3、4_
    - 复审：需 spec-evaluator 复审（等价性锁定断言）。
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/agent/test_approval_lookup_unit.py`。

  - [x] 3.3 `StaticApprovalPolicyProvider` 常量别名 re-export + 查表委托
    - 修改 `src/infrastructure/agent/approval_policy_provider.py`（类**留原位**，保留类身份、构造签名 `(enabled, interrupt_on)`、`ApprovalPolicyPort` 继承、`json`/`HitlConfigInvalidError` 依赖、`_VALID_DECISIONS`）：
      - 常量改别名 re-export：删除本地 `_APPROVE_REJECT`/`_APPROVE_EDIT_REJECT`/`_DEFAULT_POLICIES`/`_LOW_RISK_TOOLS` 定义，改为 `from domain.agent.approval_lookup import (APPROVE_EDIT_REJECT as _APPROVE_EDIT_REJECT, APPROVE_REJECT as _APPROVE_REJECT, DEFAULT_POLICIES as _DEFAULT_POLICIES, LOW_RISK_TOOLS as _LOW_RISK_TOOLS, ApprovalDefaultLookup)`（保留下划线别名使内部引用与既有测试零改）。
      - `policy_for`：`enabled=False` 分支与 `override 命中` 分支**留原不变**（依赖实例状态 `self._enabled`/`self._overrides`）；仅「无 override 的默认查表」两条分支（`if tool_name in _DEFAULT_POLICIES: ... else: ...`）替换为 `return ApprovalDefaultLookup.policy_for(tool_name)`。
      - `_policy_from_value` 的 `value is True` 分支：`decisions, risk_label = _DEFAULT_POLICIES.get(...)` 改为 `decisions, risk_label = ApprovalDefaultLookup.decisions_for(tool_name)`，其余构造字面不变。
      - `_parse_interrupt_on`/`_validate_decisions`、`value is False`/`list`/`dict` 分支、`HitlConfigInvalidError` 抛出条件与消息**字面不变**（JSON 解析留 infrastructure，ADR-0008）；`_policy_from_value` 面向配置值的既有 `Any` 标注留 infrastructure 解析侧。
    - _需求: 3.2, 3.3, 3.4, 3.5, 7.4_ ; _design 组件 4 / Property 3、4、7_
    - 复审：**需 spec-evaluator 复审**（委托边界 + JSON 解析留 infra 等价性）。
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent/test_approval_policy_provider_unit.py test/infrastructure/agent/test_approval_policy_provider_property.py`（既有单测/属性测试含非法 JSON、`True`/`False`/`list`/`dict`、非法决策全绿，断言不改）。

## Checkpoint B：候选 B 就绪 + 领域纯净度（门禁）

- [x] 4. CP-B 门禁校验（全部通过方视候选 B 完成）
  - 新构件可解析：`cd epsilon-boot && PYTHONPATH=src uv run python -c "import domain.agent.approval_lookup"`（无报错）。
  - 领域单测全绿：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/agent/test_approval_lookup_unit.py`。
  - 领域纯净度（Property 9）：`cd epsilon-boot && grep -rnE "import (application|infrastructure|fastapi|pydantic|json|logging)|from (application|infrastructure|fastapi|pydantic|json|logging)" src/domain/agent/approval_lookup.py`（期望零命中——尤其不引 `json`）；`grep -nE "ContextVar|opentelemetry|event_bus|DomainEvent|publish" src/domain/agent/approval_lookup.py`（期望零命中）；`grep -nE "^from |^import " src/domain/agent/approval_lookup.py`（人工核对仅含 `__future__`、`domain.agent.value_objects`）。
  - JSON 解析仍在 infra（AC3.3 / Property 4）：`cd epsilon-boot && grep -nE "def _parse_interrupt_on|def _policy_from_value|def _validate_decisions|import json" src/infrastructure/agent/approval_policy_provider.py`（三方法与 `json` 依赖仍在）；`grep -nE "def _parse_interrupt_on|def _validate_decisions|json" src/domain/agent/approval_lookup.py`（期望零命中）。
  - Port/装配契约不变（AC3.4/3.5 / Property 7）：`cd epsilon-boot && grep -n "def policy_for(self, tool_name: str) -> ApprovalPolicy" src/domain/agent/ports.py`（Port 签名未改）；`grep -n "def __init__(self, enabled: bool, interrupt_on: str)" src/infrastructure/agent/approval_policy_provider.py`（构造签名未改）；`PYTHONPATH=src uv run --frozen pytest test/application/test_container_config.py`（`_create_approval_policy` 装配对外行为不变）。
  - 规范合规：`cd epsilon-boot && uv run ruff check src/domain/agent/approval_lookup.py src/infrastructure/agent/approval_policy_provider.py` 与 `cd epsilon-boot && uv run pyright src/domain/agent/approval_lookup.py src/infrastructure/agent/approval_policy_provider.py`（零新增错误；中文 docstring 齐备）。
  - _需求: 3.1, 3.2, 3.3, 3.4, 3.5, 6.1, 6.3, 7.1, 7.2, 7.3, 7.4_ ; _design Property 3、4、7、9_

---

## Group C：分段续跑判定平移（候选 C，需求 4）

- [x] 5. 分段续跑判定平移领域构件、垫片与既有测试
  - [x] 5.1 新建领域模块 `src/domain/agent/segmented_orchestration.py`（平移，字面不变）
    - 在 `src/domain/agent/segmented_orchestration.py` 新建（当前不存在），**整体平移** `infrastructure/agent/segmented_orchestration.py` 的 `SegmentContinuationDecision`（frozen dataclass：`should_continue: bool` / `stop_reason: SegmentStopReason`）与 `decide_next_segment`（keyword-only 签名、默认值、12 门判定顺序、`>=` 运算符、`None` 阈值短路、`×1000`、每条 `stop_reason` 返回值**逐行字面不变**）。
    - 导入集合与平移前**完全一致**：`from dataclasses import dataclass`、`from domain.agent.segmented_execution import (SegmentBudgetUsage, SegmentExecutionPolicy, SegmentProgressSnapshot, SegmentStopReason)`；顶部 `from __future__ import annotations`。**仅**模块 docstring 更新为「分段执行编排决策领域模块。判定逻辑自 infrastructure/agent 平移至领域层同子域（与 `segmented_execution.py` 同层，ADR-0015），为零基础设施依赖的纯领域判定；不改动 `Segmented_Execution_Value_Objects`」。不改动 `domain/agent/segmented_execution.py`。
    - _需求: 4.1, 4.2, 7.1, 7.3_ ; _design 组件 5 / Property 5_
    - 复审：**需 spec-evaluator 复审**（平移字面等价性）。
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run python -c "from domain.agent.segmented_orchestration import decide_next_segment, SegmentContinuationDecision"`（import 正常）。

  - [x] 5.2 新增领域单测 `test/domain/agent/test_segmented_orchestration_unit.py`（12 门 + 短路 + 全未触发，脱离运行时）
    - 在 `test/domain/agent/test_segmented_orchestration_unit.py` 新建，**仅 import `domain.agent.segmented_orchestration` 与 `domain.agent.segmented_execution`**（AC6.1）。
    - 逐门参数化命中：`completed` → `approval_required`（含 `status=="approval_required"`）→ `continue_precondition_failed`（`can_continue=False`）→ `tool_boundary_unavailable` → `risk_gate_required` → `auto_disabled` → `max_continuations_reached`(`>=`) → `total_token_budget_reached`(`>=`) → `total_duration_budget_reached`(`×1000` + `>=`) → `consecutive_paused_limit`(`>=`) → `no_progress`(`>=`) → `repeated_tool_call`(`>=`)；每条命中断言 `should_continue=False` + 对应 `stop_reason`。
    - `None` 阈值短路：`max_total_tokens=None` 与 `max_duration_seconds=None` 时对应门不命中。
    - 全部门未触发：断言 `decide_next_segment(...) == SegmentContinuationDecision(True, "completed")`（AC6.4）。
    - 垫片同一对象专项（Property 8 / AC5.3）：断言 `import infrastructure.agent.segmented_orchestration as infra_so, domain.agent.segmented_orchestration as dom_so` 后 `infra_so.SegmentContinuationDecision is dom_so.SegmentContinuationDecision` 且 `infra_so.decide_next_segment is dom_so.decide_next_segment`。
    - _需求: 6.1, 6.4, 6.5, 5.3_ ; _design 测试策略第 1 项 / Property 5、8_
    - 复审：需 spec-evaluator 复审（12 门等价性 + 垫片同一对象断言）。
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/agent/test_segmented_orchestration_unit.py`。

  - [x] 5.3 `infrastructure/agent/segmented_orchestration.py` 降为 re-export 垫片
    - 整体替换 `src/infrastructure/agent/segmented_orchestration.py` 为：模块 docstring（标注「判定逻辑已平移至 `domain/agent/segmented_orchestration.py`（ADR-0015），本模块保留为向后兼容垫片，re-export 领域实现，保护既有 import 路径与测试引用；参照 ADR-0011/0014 垫片范式；后续片可按 change-discipline 删除本垫片并改所有引用点；re-export 的 `SegmentContinuationDecision` 与领域模块为同一类对象、`decide_next_segment` 为同一函数对象，`isinstance`/`==` 语义不破裂」）+ `from __future__ import annotations` + `from domain.agent.segmented_orchestration import (SegmentContinuationDecision, decide_next_segment)` + `__all__ = ["SegmentContinuationDecision", "decide_next_segment"]`。
    - 移除原文件全部判定实现（`SegmentContinuationDecision` dataclass 定义、`decide_next_segment` 方法体、`from dataclasses import dataclass` 及分段值对象 import）。
    - _需求: 5.1, 5.3_ ; _design 组件 6 / Property 8_
    - 复审：**需 spec-evaluator 复审**（垫片 re-export 契约）。
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run python -c "from infrastructure.agent.segmented_orchestration import decide_next_segment, SegmentContinuationDecision"`（垫片可解析）。

  - [x] 5.4 既有 infra 单测与消费方 import 按需处理（仅 import，不改断言/时序）
    - 消费方 `src/infrastructure/chat/chat_service_adapter.py`（约 444、839 行调用点）与 `src/infrastructure/task/task_agent_adapter.py`（约 663 行调用点）：`from infrastructure.agent.segmented_orchestration import decide_next_segment` 经垫片零改可用，**默认不改**；仅当 lint（如私有/未使用规则）要求直指领域时改为 `from domain.agent.segmented_orchestration import decide_next_segment`，调用参数/返回消费/时序**一律不改**（AC4.3）。
    - 既有单测 `test/infrastructure/agent/test_segmented_orchestration_unit.py`：优先经垫片零改通过；仅当 lint/规范要求时改指领域路径，**断言一律不改**（AC5.2）。
    - _需求: 4.3, 5.1, 5.2, 5.3_ ; _design 组件 7 / 测试策略第 2 项 / Property 7、8_
    - 复审：纯 import 调整，由 generator 自行判断（如实际改了 import 则轻量复核）。
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent/test_segmented_orchestration_unit.py test/infrastructure/chat/test_chat_service_adapter_unit.py test/application/test_segmented_container_wiring_static.py`。

## Checkpoint C：候选 C 就绪 + 领域纯净度 + 垫片同一对象（门禁）

- [x] 6. CP-C 门禁校验（全部通过方视候选 C 完成）
  - 新构件可解析：`cd epsilon-boot && PYTHONPATH=src uv run python -c "import domain.agent.segmented_orchestration"`（无报错）。
  - 领域单测全绿：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/agent/test_segmented_orchestration_unit.py`。
  - 领域纯净度（Property 9）：`cd epsilon-boot && grep -rnE "import (application|infrastructure|fastapi|pydantic|json|logging)|from (application|infrastructure|fastapi|pydantic|json|logging)" src/domain/agent/segmented_orchestration.py`（期望零命中）；`grep -nE "ContextVar|opentelemetry|event_bus|DomainEvent|publish" src/domain/agent/segmented_orchestration.py`（期望零命中）；`grep -nE "^from |^import " src/domain/agent/segmented_orchestration.py`（人工核对仅含 `__future__`、`dataclasses`、`domain.agent.segmented_execution`——与平移前一致）。
  - 垫片无残留判定（Property 8）：`cd epsilon-boot && grep -nE "def decide_next_segment|should_continue >= |usage\.|policy\.max_" src/infrastructure/agent/segmented_orchestration.py`（期望零命中——判定已平移，仅剩 re-export）。
  - 边界不修改（Property 6 / AC1.4）：`cd .. && git diff --name-only | grep -q "src/domain/task/policy.py" && echo "VIOLATION" || echo "OK"`（`TaskContinuationPolicy` 未被修改）；不改 `src/domain/agent/segmented_execution.py`：`git diff --name-only | grep -q "src/domain/agent/segmented_execution.py" && echo "VIOLATION" || echo "OK"`。
  - 规范合规：`cd epsilon-boot && uv run ruff check src/domain/agent/segmented_orchestration.py src/infrastructure/agent/segmented_orchestration.py` 与 `cd epsilon-boot && uv run pyright src/domain/agent/segmented_orchestration.py src/infrastructure/agent/segmented_orchestration.py`（零新增错误；中文 docstring 齐备）。
  - _需求: 4.1, 4.2, 4.3, 4.4, 5.1, 5.2, 5.3, 6.1, 6.4, 7.1, 7.3_ ; _design Property 5、6、8、9_

---

## Group Z：ADR-0015 + 文档同步 + 全局最终门禁（与代码正交，最后执行）

> **正交证据**：ADR/doc-sync 只改 `docs/` 下文件，与 `epsilon-boot/` 源码零交集；最终 CP 只读校验、无代码改动。

- [x] 7. ADR-0015 撰写与索引
  - [x] 7.1 新增 ADR-0015
    - 落地前核验编号：`cd .. && ls docs/adr/ | grep -E "^0015"`（期望零命中，确认 0015 未占用；当前最新为 0014）。
    - 在 `docs/adr/` 新建 `0015-uplift-agent-config-normalization-approval-lookup-and-relocate-segment-continuation.md`，遵循 `docs/adr/0000-template.md` 四段式。front matter：`status: Accepted`、`date: 2026-07-07`、`deciders: [后端架构维护者]`、`supersedes:` **留空**（**不 supersede ADR-0001**）。
    - 标题「在 domain/agent 上提委派深度规范化与审批默认查表、平移分段续跑判定（充血化后续片）」；四段按 design「ADR-0015 草案要点」写：
      - **背景**：ADR-0014 已把 `StaticAgentGuardrailPolicy` 上提 `domain/agent`，并显式把 `agent_config` 规范化、`approval_policy_provider` 查表、`segmented_orchestration` 续跑判定列为后续片；三者均为 `Domain_Logic_In_Infrastructure`，与 ADR-0009（`domain/task` 范式）、ADR-0014（`domain/agent` 首片）同源。
      - **决策**：(A) 新建 `domain/agent/config_policy.py` 承载委派深度归一领域服务与默认值常量，`AgentRuntimeConfig`（pydantic-settings）留 infrastructure 但委托之；(B) 新建 `domain/agent/approval_lookup.py` 承载审批默认查表常量与判定，`StaticApprovalPolicyProvider` 保留类身份/JSON 解析、默认查表委托领域构件；(C) 把 `decide_next_segment`+`SegmentContinuationDecision` 平移到新建 `domain/agent/segmented_orchestration.py`，原 infra 文件降 re-export 垫片；三者判据逐一字面等价。
      - **两处边界厘清（显式记录，AC8.3）**：`Delegation_Depth_Normalization`（配置取值一元归一 `object -> int`）vs `DelegationDepthPolicy`（运行期深度二元比较 `current vs max`）——不合并、不修改后者；`Segment_Continuation_Decision_Logic`（分段编排 12 门续跑门）vs `TaskContinuationPolicy`（单次终止原因 → 是否 PAUSED 映射）——不重叠、不合并、不重复上提、不修改后者。
      - **留 infrastructure 取舍（AC8.5）**：`AgentRuntimeConfig` 依赖 pydantic-settings 须留 infrastructure；`Approval_Json_Config_Parsing` 依赖 `json`、面向 `HITL_INTERRUPT_ON` 配置字符串，按 ADR-0008 属配置边界技术关注点，不进领域层。
      - **后果 + 依据（AC8.2/8.4）**：三候选判定住进领域层、可脱离运行时单测；C 垫片与领域临时并存，清理留后续片；`Behavior_Equivalent_Refactor`、不改对外行为、不引第三方依赖、不引领域事件（**不 supersede ADR-0001**，不复活事件总线，尊重 `ddd-tactical-modeling.md` §8）；回链 ADR-0009/0014（范式来源与同源方向）、ADR-0008（配置解析归属）。
      - **备选方案与未采纳原因**：(a) 维持散落（差距本身）；(b) `AgentRuntimeConfig`/JSON 解析整体移入领域（引框架/`json` 入领域，违 §4/ADR-0008）；(c) C 并入 `segmented_execution.py`（混淆值对象定义与编排判定，违 SRP，独立同名模块使垫片更直观）；(d) 合并 A 与 `DelegationDepthPolicy`、C 与 `TaskContinuationPolicy`（语义不同、错误耦合）；(e) 引领域事件承载判定（违 ADR-0001 与 §8）。
    - _需求: 8.1, 8.2, 8.3, 8.4, 8.5_ ; _design ADR-0015 草案要点_
    - 复审：doc/ADR 任务，由 generator 自行判断（无源码改动）。
    - 验证：`cd .. && test -f docs/adr/0015-uplift-agent-config-normalization-approval-lookup-and-relocate-segment-continuation.md`；`grep -nE "supersedes:" docs/adr/0015-*.md`（字段存在且值为空）；`grep -nE "ADR-0009|ADR-0014|ADR-0008|ADR-0001" docs/adr/0015-*.md`（回链存在）。

  - [x] 7.2 更新 `docs/adr/README.md` 索引
    - 在 `docs/adr/README.md` 索引表 0014 行之后追加 0015 索引行（编号 `[0015](0015-uplift-agent-config-normalization-approval-lookup-and-relocate-segment-continuation.md)` / 标题「在 domain/agent 上提委派深度规范化与审批默认查表、平移分段续跑判定（充血化后续片）」/ `Accepted` / `2026-07-07`）。
    - _需求: 8.1_ ; _design ADR-0015 草案要点_
    - 复审：doc 任务，generator 自行判断。
    - 验证：`cd .. && grep -n "0015" docs/adr/README.md`（有命中）。

  - [x] 7.3 按 doc-sync 同步主题文档（克制，最小粒度）
    - 按 `doc-sync.md` 判断并同步受影响主题文档：在 `docs/domain-model.md` 与/或 `docs/architecture.md` 补指向性说明——「`domain/agent` 新增委派深度规范化领域服务 `config_policy.py::DelegationDepthNormalizationPolicy`、审批默认查表领域服务 `approval_lookup.py::ApprovalDefaultLookup`；分段续跑判定 `segmented_orchestration.py::decide_next_segment` 已平移至领域层，基础设施同名文件为向后兼容垫片（ADR-0015）；`AgentRuntimeConfig`（pydantic-settings）与审批 JSON 解析因依赖边界留 infrastructure」；参照前序片同步粒度，仅新增指向性说明、不重写章节。
    - _需求: 7.5_ ; _design AC → 交付物追溯表 7.5 行_
    - 复审：doc-sync 任务，generator 自行判断。
    - 验证：`cd .. && grep -rnE "config_policy|approval_lookup|segmented_orchestration|DelegationDepthNormalizationPolicy|ApprovalDefaultLookup" docs/domain-model.md docs/architecture.md`（有命中，至少一处）。

## Checkpoint 最终：11 条正确性属性 + 全量 AC 全量验收（最终门禁）

- [x] 8. CP-最终 门禁校验（必须全部通过）
  - **Property 11（既有测试全绿）**：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest`（前后全绿、无新增 failed）。
  - **Property 1/3/4/5（判定等价）**：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/agent/test_config_policy_unit.py test/domain/agent/test_approval_lookup_unit.py test/domain/agent/test_segmented_orchestration_unit.py test/infrastructure/agent/test_approval_policy_provider_unit.py test/infrastructure/agent/test_approval_policy_provider_property.py test/infrastructure/agent/test_segmented_orchestration_unit.py`（三领域新单测 + 既有 infra 单测同绿）。
  - **Property 8（垫片同一对象）**：`cd epsilon-boot && PYTHONPATH=src uv run python -c "import infrastructure.agent.segmented_orchestration as i, domain.agent.segmented_orchestration as d; assert i.SegmentContinuationDecision is d.SegmentContinuationDecision and i.decide_next_segment is d.decide_next_segment"`（同一类/函数对象）。
  - **Property 7（契约/时序不变）**：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/chat/test_chat_service_adapter_unit.py test/application/test_container_config.py test/application/test_agent_delegation_config_properties.py test/application/test_segmented_container_wiring_static.py`；`grep -n "def policy_for(self, tool_name: str) -> ApprovalPolicy" src/domain/agent/ports.py`（`ApprovalPolicyPort` 签名未改）。
  - **Property 9（三领域文件零基础设施依赖）**：`cd epsilon-boot && grep -rnE "import (application|infrastructure|fastapi|pydantic|json|logging)|from (application|infrastructure|fastapi|pydantic|json|logging)" src/domain/agent/config_policy.py src/domain/agent/approval_lookup.py src/domain/agent/segmented_orchestration.py`（零命中）；`grep -nE "ContextVar|opentelemetry" src/domain/agent/config_policy.py src/domain/agent/approval_lookup.py src/domain/agent/segmented_orchestration.py`（零命中）。
  - **Property 2/6（两处边界不修改）**：`cd .. && git diff --name-only | grep -q "src/domain/task/policy.py" && echo "VIOLATION" || echo "OK"`（`DelegationDepthPolicy`/`TaskContinuationPolicy` 未被修改）。
  - **Property 10（不引事件/新依赖）**：`cd epsilon-boot && grep -nE "event_bus|DomainEvent|publish" src/domain/agent/config_policy.py src/domain/agent/approval_lookup.py src/domain/agent/segmented_orchestration.py`（零命中）；`cd .. && git diff --name-only | grep -E "pyproject.toml|uv.lock"`（期望零命中——依赖清单不变）。
  - **规范合规（需求 7）**：`cd epsilon-boot && uv run ruff check src/domain/agent/config_policy.py src/domain/agent/approval_lookup.py src/domain/agent/segmented_orchestration.py src/infrastructure/agent/agent_config.py src/infrastructure/agent/approval_policy_provider.py src/infrastructure/agent/segmented_orchestration.py` 与 `cd epsilon-boot && uv run pyright src/domain/agent/config_policy.py src/domain/agent/approval_lookup.py src/domain/agent/segmented_orchestration.py`（零新增错误、无裸 `Any` 越界；中文 docstring 齐备）。
  - **范围锁定（AC1.1–1.5）**：`cd .. && git diff --name-only` 中源码改动仅落——`src/domain/agent/{config_policy,approval_lookup,segmented_orchestration}.py`（新增）+ `src/infrastructure/agent/{agent_config,approval_policy_provider,segmented_orchestration}.py`（A/B 委托、C 垫片）+ `test/domain/agent/`（3 新增单测）+（如触发）既有测试/消费方 import；文档改动仅落 `docs/`；**未改** `domain/agent/guardrail_policy.py`、`domain/agent/ports.py`、`domain/agent/value_objects.py`、`domain/agent/segmented_execution.py`、`agent_loop_policy.py`/`agent_loop_orchestration.py`、`domain/task/policy.py`、`pyproject.toml`/`uv.lock`。
  - **ADR（需求 8）**：`cd .. && test -f docs/adr/0015-uplift-agent-config-normalization-approval-lookup-and-relocate-segment-continuation.md && grep -n "0015" docs/adr/README.md`。
  - _需求: 1.1, 1.2, 1.3, 1.4, 1.5, 2.4, 2.5, 3.4, 3.5, 4.3, 4.4, 5.4, 6.5, 7.5, 8.1_ ; _design Property 1、2、3、4、5、6、7、8、9、10、11_

---

## 任务 → 需求 AC → design 组件 → 正确性属性 追溯表

| 任务 | 覆盖需求 AC | design 组件 | 正确性属性 |
|---|---|---|---|
| 1.1 | 2.1/2.2/7.1/7.2/7.3 | 组件 1（config_policy 领域服务） | Property 1 |
| 1.2 | 6.1/6.2/6.5 | 测试策略第 1 项 | Property 1 |
| 1.3 | 2.3/2.4/7.4 | 组件 2（AgentRuntimeConfig 委托） | Property 1、7 |
| CP-A | 2.1–2.5/6.1/6.2/7.1–7.4 | 组件 1、2 | Property 1、2、9 |
| 3.1 | 3.1/7.1/7.2/7.3 | 组件 3（approval_lookup 领域服务） | Property 3、4 |
| 3.2 | 6.1/6.3/6.5 | 测试策略第 1 项 | Property 3、4 |
| 3.3 | 3.2/3.3/3.4/3.5/7.4 | 组件 4（Provider 委托） | Property 3、4、7 |
| CP-B | 3.1–3.5/6.1/6.3/7.1–7.4 | 组件 3、4 | Property 3、4、7、9 |
| 5.1 | 4.1/4.2/7.1/7.3 | 组件 5（segmented_orchestration 平移） | Property 5 |
| 5.2 | 6.1/6.4/6.5/5.3 | 测试策略第 1 项 | Property 5、8 |
| 5.3 | 5.1/5.3 | 组件 6（垫片） | Property 8 |
| 5.4 | 4.3/5.1/5.2/5.3 | 组件 7（消费方/既有测试） | Property 7、8 |
| CP-C | 4.1–4.4/5.1–5.3/6.1/6.4/7.1/7.3 | 组件 5、6、7 | Property 5、6、8、9 |
| 7.1/7.2 | 8.1/8.2/8.3/8.4/8.5 | ADR-0015 草案要点 | —（可追溯性） |
| 7.3 | 7.5 | 目录/模块落点表 | —（doc-sync） |
| CP-最终 | 1.1–1.5/2.4/2.5/3.4/3.5/4.3/4.4/5.4/6.5/7.5/8.1 | 全组件 | Property 1–11 |

---

## 备注

- **逐候选独立可验收**：Group A/B/C 互不依赖，可任意顺序或并行推进，各自 Checkpoint（CP-A/B/C）即该候选的独立验收门；Group Z 最终 CP 做三候选合并的全量门禁。
- **范围纪律（change-discipline）**：仅列达成需求所必需的改动。明确**不改**：`domain/agent/value_objects.py`（`ApprovalPolicy`/`ApprovalDecisionType`）、`domain/agent/ports.py`（Port 签名）、`domain/agent/segmented_execution.py`（分段值对象）、`domain/agent/guardrail_policy.py`（ADR-0014）、`agent_loop_policy.py`/`agent_loop_orchestration.py`（ADR-0010/0011/0012）、`domain/task/policy.py`（`DelegationDepthPolicy`/`TaskContinuationPolicy` 仅厘清边界、不合并不修改）、`pyproject.toml`/`uv.lock`（依赖清单不变）。
- **A/B 无垫片、C 有垫片**：A 的 `AgentRuntimeConfig`/`agent_config` 全局实例、B 的 `StaticApprovalPolicyProvider` 类身份与 import 路径本就留原位，无移动即无需垫片；仅 C 的符号物理迁走，须 re-export 垫片保护 3 处消费方与既有 infra 单测零改。
- **常量去下划线 + infra 别名 re-export**：B 上提到领域层的常量改公开命名（`DEFAULT_POLICIES` 等），infrastructure 侧用 `as _DEFAULT_POLICIES` 别名保护既有内部/测试私有名引用；既有 provider 单测/属性测试仅 import `StaticApprovalPolicyProvider`、不引私有常量，预期零改动通过。
- **既有测试 import 优先零改**：C 的既有 `test/infrastructure/agent/test_segmented_orchestration_unit.py` 与 2 处消费方优先依赖垫片零改，仅当 lint/规范要求时才改指领域路径，断言/时序一律不改。
- **禁裸 Any**：三领域文件禁裸 `Any`——A 用 `object` 承载配置原始值、`int(raw)` 处窄豁免 `# type: ignore[call-overload]`；`approval_policy_provider` 面向配置值的既有 `Any` 用法留 infrastructure 解析侧。
- **不返回异常**：三领域构件不 `raise`——A 的 `normalize` 保留吞异常语义（`except (TypeError, ValueError)` 返回原值）；B 的 `HitlConfigInvalidError` 抛出点全留 infrastructure JSON 解析三方法；C 的 `decide_next_segment` 以 `SegmentContinuationDecision` 表达判定，`SegmentExecutionPolicy`/`SegmentBudgetUsage` 的 `__post_init__` 校验留 `segmented_execution.py`。
- **复审策略**：所有新增/上提/委托/平移/垫片实现任务（1.1/1.3/3.1/3.3/5.1/5.3）及等价性锁定单测（1.2/3.2/5.2）**需 spec-evaluator 复审**；纯 import 调整（5.4）、ADR/doc-sync（7.1/7.2/7.3）由 generator 自行判断。
- **回滚**：三领域构件为独立新文件、委托与垫片为局部替换，可按候选组 `git revert`；因行为等价，回滚不影响既有测试基线。
- **命令约定**：源码测试/lint 在 `epsilon-boot/` 下执行、带 `PYTHONPATH=src`、依赖仅用 `uv`；ADR/文档校验命令 `cd ..` 回仓库根（`docs/` 位于仓库根，非后端子目录）。
- **行号说明**：本文中「约 N 行」引自 design.md/当前代码定位（`chat_service_adapter.py` 444/839、`task_agent_adapter.py` 663），落地前以 grep 逐点核对实际位置，防止上游文件微调导致偏移。
