# 实现计划：贫血领域模型单子域充血化试点（domain/agent）

> 本文件由已定稿的 `design.md` 展开为可执行、可勾选的任务清单。全程为 **`Behavior_Equivalent_Refactor`（行为等价纯重构）**：把现居 `infrastructure/agent/static_guardrail_policy.py::StaticAgentGuardrailPolicy`（216 行、只 import 领域类型、无 I/O/框架依赖）的全部纯判定逐条字面等价上提到领域层新增文件 `src/domain/agent/guardrail_policy.py`；基础设施文件降为 re-export 垫片，DI 装配点改指领域类。所有判据、检查顺序、比较运算符（`>=`）、`None` 短路语义、OBSERVE/ENFORCE 分支、启发式边界均与上提前逐一等价，不新增/删除/更改任何一条业务规则、不引领域事件、不改 Port 签名。
> 每条任务标注：动作、目标文件、对应 requirement AC 与 design 组件 / Property 编号、验证命令。所有测试/lint/import 命令均在 `epsilon-boot/` 目录下执行，依赖仅用 `uv`，测试须带 `PYTHONPATH=src`。
> **全程硬约束**：领域服务零 `application`/`infrastructure`/框架/Pydantic/logging/OTel/ContextVar 依赖、无新第三方依赖、不引领域事件（尊重 ADR-0001）；中文 docstring（`code-documentation.md`）；全量类型标注、禁裸 `Any`（分类启发式对 `dict[str, Any]` 的既有用法保留）、`ruff`/`pyright` 零新增错误（`python-typing-lint.md`）；`Existing_Test_Suite_Green` 每波结束保持通过。

## 概述

执行采用 **波次（Wave）+ Checkpoint 门禁** 结构，遵循 design「组件依赖图」的依赖方向（`application/infrastructure → domain`）自底向上推进：

- **Wave 1（领域模块落地）**：新建 `src/domain/agent/guardrail_policy.py`，迁入 `StaticAgentGuardrailPolicy` 全部方法（含 `classify_run` 完整保留、四个 `evaluate_*`、`_budget_decision`、`_risk_decision`）与模块级 `_looks_batch`/`_segment_count`，结构化实现同层 `AgentGuardrailPolicyPort`，`_json_safe` 复用 `domain/agent/guardrails.py` 既有实现；新增脱离运行时的领域单测 `test/domain/agent/test_guardrail_policy_unit.py` 覆盖全判定分支，含 `_risk_decision` metadata 等价专项。此波**只新增领域构件与单测，不触碰任何调用点/垫片**，保证既有代码不断裂。→ **Checkpoint 1**：领域模块可解析、零基础设施/框架/pydantic/logging import（grep 断言）、领域单测绿、lint 零新增错误。
- **Wave 2（基础设施垫片 + DI 装配）**：`static_guardrail_policy.py` 降为 re-export 垫片；`container_config._create_guardrail_policy` 改 import 指领域类；既有测试按需处理（经垫片零改，如需只改 import 不改断言）。→ **Checkpoint 2**：全量 pytest 前后全绿、无新增 failed；改动文件 `ruff`/`pyright` 零新增错误；消费方（`react_agent_adapter`）鸭子调用行为不变。
- **Wave 3（ADR-0014 + 文档同步）**：新增 ADR-0014 + `docs/adr/README.md` 索引；按 `doc-sync.md` 判断是否需在 `docs/domain-model.md`/`docs/architecture.md` 补一句 guardrail 策略入领域。→ **Checkpoint 3（最终门禁）**：8 条正确性属性全量验收 + 需求 26 AC 覆盖核对。

> **反断裂纪律**：Wave 1 只新增领域文件与领域单测、不改任何既有引用点 → Wave 2 才把基础设施文件降为垫片并改 DI import，垫片确保既有 7 处 `from infrastructure.agent.static_guardrail_policy import ...` 引用零改动即通过 → Wave 3 只改 `docs/`，与源码零交集。每个 Checkpoint 处测试可绿。
> **`_json_safe` 等价复核硬约束**：Wave 1 复用 `guardrails._json_safe`（递归实现）替换基础设施本地一层 dict 推导副本时，必须逐值复核对 `_risk_decision` metadata（`{"tool_name": str|None, "risk_level": ToolRiskLevel}`）的产出与原副本字面等价，并由领域单测覆盖；若复核发现不等价，回退为在 `guardrail_policy.py` 内保留等价副本。该复核作为 T-1.3 的明确验收子项。

---

## Wave 1：领域模块落地（新增领域服务 + 领域单测）

> **落点确认**：`src/domain/agent/guardrail_policy.py` 当前不存在，需新建；`src/domain/agent/__init__.py` 与 `test/domain/agent/__init__.py` 均已存在，不新增导出（保持最小改动，消费方经 Port 与垫片访问，不依赖包顶层导出）。

- [x] 1. 领域护栏策略构件与单测
  - [x] 1.1 新建领域服务 `src/domain/agent/guardrail_policy.py`
    - 在 `src/domain/agent/guardrail_policy.py` 新建（当前不存在），含模块中文 docstring（说明：承载 Agent 护栏的任务类型分类与预算/风险护栏判定，为零基础设施依赖的 `Domain_Service`；结构化实现 `domain.agent.ports.AgentGuardrailPolicyPort`，无需继承；不变量为所有判据/检查顺序/比较运算符/`None` 短路/OBSERVE-ENFORCE 分支与上提前逐一等价）。
    - 顶部 `from __future__ import annotations`；`from typing import Any`。
    - 导入集合（相较上提前**新增复用** `guardrails._json_safe`）：`from domain.agent.guardrails import (GuardrailAction, GuardrailDecision, GuardrailEvaluationContext, GuardrailMode, GuardrailPolicy, GuardrailReason, TaskExecutionClass, ToolRiskLevel, _json_safe)`；`from domain.run import RunKind, RunPayload, RunSnapshot`。**不引** `application`/`infrastructure`/框架/Pydantic/logging/OTel/ContextVar。
    - `class StaticAgentGuardrailPolicy`（**保留原类名**，不改名，规避测试断言/`isinstance` 语义漂移）：
      - `__init__(self, policy: GuardrailPolicy) -> None` 存 `self._policy`；`@property policy(self) -> GuardrailPolicy` 返回 `self._policy`（签名与语义字面不变，消费方经 `getattr(_, "policy", None)` 读取）。
      - `classify_run(self, snapshot: RunSnapshot) -> TaskExecutionClass`：**完整保留**，判据 `latest_checkpoint_id is not None or can_continue or _segment_count(segment_metadata) > 1 → LONG_TASK`，否则 `return self.classify_payload(snapshot.payload, has_tools=True)`（字面不变，不因「无运行期消费方」删除或标注废弃）。
      - `classify_payload(self, payload: RunPayload, *, has_tools: bool) -> TaskExecutionClass`：`data = payload.task if payload.kind is RunKind.TASK else payload.chat`；`_looks_batch(data or {}) → BATCH_TASK`；`RunKind.TASK` 分支按 `has_tools` 派 `TOOL_TASK`/`LONG_TASK`；其余按 `has_tools` 派 `TOOL_TASK`/`SHORT_QA`（字面不变）。
      - `evaluate_run_start` / `evaluate_model_completed` / `evaluate_tool_after_execution(self, context: GuardrailEvaluationContext) -> GuardrailDecision`：均 `return self._budget_decision(context)`（字面不变）。
      - `evaluate_tool_before_execution(self, context) -> GuardrailDecision`：先 `_budget_decision`，`action is not ALLOW` 直接返回；再 `CRITICAL + enforce_critical_tools → _risk_decision(STOP)`、`HIGH + enforce_high_risk_tools → _risk_decision(REQUIRE_APPROVAL)`、其余 `GuardrailDecision.allow()`（字面不变）。
      - `_risk_decision(self, *, action: GuardrailAction, context, message: str) -> GuardrailDecision`：`metadata = {"tool_name": context.tool_name, "risk_level": context.tool_risk_level}`；OBSERVE 模式返回 `observe`、`REQUIRE_APPROVAL` 返回 `require_approval`、否则 `stop`，三分支 `metadata=_json_safe(metadata)`（reason 恒为 `TOOL_RISK_GATE_REQUIRED`）。**唯一改动**：两处 `_json_safe` 现引用 `domain.agent.guardrails._json_safe`（行为等价，见 T-1.3 复核）。
      - `_budget_decision(self, context) -> GuardrailDecision`：5 项 `checks` 列表（token → duration(`×1000`) → context_growth → repeated_tool → consecutive_failure），保留检查顺序、`>=` 比较、`None` 阈值短路、首个命中项 OBSERVE→`observe`/ENFORCE→`stop`、无命中 `allow()`（字面不变）。
    - 模块级 `_looks_batch(data: dict[str, Any]) -> bool`（`items`/`batch`/`targets`/`inputs` 为长度 > 1 的 list，或 `constraints` list 含「批量」子串）与 `_segment_count(metadata: dict[str, Any] | None) -> int`（非 dict 返回 0、`segment_count` 容错转 int、`TypeError/ValueError` 归 0），均含中文 docstring，判据字面不变。
    - **移除**基础设施本地 `_json_safe` 定义（不在 `guardrail_policy.py` 再定义，复用 `guardrails._json_safe`）。
    - _需求: 1.1, 2.1, 2.2, 2.3, 2.4, 2.5, 3.4, 4.1, 4.2, 4.3, 4.4, 4.5_ ; _design 组件 1 / Property 1、2、3、4、6、7_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run python -c "from domain.agent.guardrail_policy import StaticAgentGuardrailPolicy, _looks_batch, _segment_count"`（import 正常）。

  - [x] 1.2 新增领域单测 `test/domain/agent/test_guardrail_policy_unit.py`（全判定分支，脱离运行时）
    - 在 `test/domain/agent/test_guardrail_policy_unit.py` 新建，**仅 import `domain.*`**（`domain.agent.guardrail_policy`、`domain.agent.guardrails`、`domain.run`），不 import `application`/`infrastructure`/框架运行时（脱离运行时单测，AC5.1）。
    - `classify_run`：checkpoint（`latest_checkpoint_id` 非空）/ `can_continue=True` / `_segment_count(segment_metadata) > 1` 三条 LONG_TASK 触发分支，以及三者皆不满足时委托 `classify_payload(payload, has_tools=True)` 的分支（Property 1）。
    - `classify_payload`：batch（`_looks_batch` 命中）/ `RunKind.TASK × has_tools=True→TOOL_TASK`、`×has_tools=False→LONG_TASK` / `RunKind.CHAT × has_tools=True→TOOL_TASK`、`×has_tools=False→SHORT_QA` 全组合（Property 1）。
    - `_budget_decision`（经 `evaluate_run_start`/`evaluate_model_completed`/`evaluate_tool_after_execution` 触发）：每条阈值（token / duration×1000 / context_growth / repeated_tool / consecutive_failure）单独命中；`max_total_tokens`/`max_duration_seconds`/`max_context_growth_messages` 为 `None` 时短路不命中；OBSERVE→`observe` vs ENFORCE→`stop`；多阈值同时满足时命中顺序（token 优先）；三个委托型 `evaluate_*` 在同一 context 下与 `_budget_decision` 结果一致（Property 2）。
    - `evaluate_tool_before_execution`：预算非 ALLOW 时短路返回预算决策；`CRITICAL × enforce_critical_tools` 开/关、`HIGH × enforce_high_risk_tools` 开/关 4 组；OBSERVE 模式降级为 `observe`（Property 3）。
    - `_looks_batch` / `_segment_count` 边界：列表长度 0/1/2、非 list 值、`constraints` 含/不含「批量」；`segment_count` 缺失/非数字（触发 `TypeError/ValueError` 归 0）/合法值、`metadata` 非 dict 返回 0（Property 4）。
    - _需求: 5.1, 5.2_ ; _design 测试策略第 1 项 / Property 1、2、3、4_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/agent/test_guardrail_policy_unit.py`。

  - [x] 1.3 `_json_safe` 等价复核并补 `_risk_decision` metadata 专项断言（硬约束验收子项）
    - **逐值复核**（人工 + 测试双重）：确认 `domain.agent.guardrails._json_safe`（递归实现：`Enum→.value`、`datetime→isoformat`、`dict→{str(key): _json_safe(item)}`、`list/tuple→[...]`、`set/frozenset→sorted`、标量透传、其余 `str(value)`）对 `_risk_decision` 的 metadata（一层 dict、值为 `str | None | ToolRiskLevel`）产出与基础设施原副本（`{key: item.value if hasattr(item, "value") else item}`）**逐值字面等价**：`ToolRiskLevel` 是 `StrEnum`（既命中递归版 `isinstance Enum` 取 `.value`、也命中原副本 `hasattr .value` 取 `.value`）；`tool_name` 为 `str` 或 `None` 均原样透传（键为 `str` 不变）。
    - **若复核发现任一取值不等价**：回退——在 `guardrail_policy.py` 内保留一份与原基础设施副本字面一致的一层 `_json_safe(value: dict[str, Any]) -> dict[str, Any]` 定义（不复用递归版），并在本任务记录回退结论。
    - 在 T-1.2 的领域单测中**新增专项断言**覆盖 `_risk_decision` 的 metadata 输出：对 `CRITICAL`（ENFORCE→stop）与 `HIGH + enforce_high_risk_tools`（ENFORCE→require_approval）及 OBSERVE 降级三种路径，断言返回 `GuardrailDecision.metadata == {"tool_name": <透传值>, "risk_level": <ToolRiskLevel.value 字符串>}`——即 `risk_level` 为枚举转 value（如 `"critical"`/`"high"`）、`tool_name` 原样透传；并断言 `tool_name=None` 时 metadata 为 `{"tool_name": None, "risk_level": ...}`（Property 3 / AC3.4）。
    - _需求: 3.4, 4.5, 5.2_ ; _design 组件 1 / 设计决策第 3 行 / Property 3、7_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/agent/test_guardrail_policy_unit.py -k "metadata or risk"`（metadata 等价断言全绿）。

---

## Checkpoint 1：领域模块就绪 + 零基础设施依赖（门禁）

- [x] 2. CP1 Wave 1 门禁校验（全部通过方可进入 Wave 2）
  - 新构件可解析：`cd epsilon-boot && PYTHONPATH=src uv run python -c "import domain.agent.guardrail_policy"`（无报错）。
  - 领域单测全绿：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/agent`（含新增单测 + 既有 agent 领域单测）。
  - 领域纯净度（Property 6）：`cd epsilon-boot && grep -rnE "import (application|infrastructure|fastapi|pydantic|logging)|from (application|infrastructure|fastapi|pydantic|logging)" src/domain/agent/guardrail_policy.py`（期望零命中）。
  - 无框架/上下文/事件依赖（Property 6、7）：`cd epsilon-boot && grep -nE "ContextVar|opentelemetry|event_bus|DomainEvent|publish" src/domain/agent/guardrail_policy.py`（期望零命中）。
  - 依赖集合等价（Property 6）：`cd epsilon-boot && grep -nE "^from |^import " src/domain/agent/guardrail_policy.py`（人工核对仅含 `__future__`、`typing.Any`、`domain.agent.guardrails`、`domain.run`）。
  - 规范合规（Property 6）：`cd epsilon-boot && uv run ruff check src/domain/agent/guardrail_policy.py` 与 `cd epsilon-boot && uv run pyright src/domain/agent/guardrail_policy.py`（零新增错误、无裸 `Any` 越界；中文 docstring 人工核对齐备）。
  - _需求: 2.1, 2.6, 4.1, 4.2, 4.3, 4.4, 4.5, 5.1_ ; _design Property 1、2、3、4、6、7_

---

## Wave 2：基础设施垫片 + DI 装配（降垫片、改 DI import、既有测试按需）

> **迁移原则**：基础设施文件降为纯 re-export 垫片，判定逻辑不再在基础设施层保留；DI 装配点改指领域类以体现「应用层装配领域实现」的正向样板；既有 7 处 `from infrastructure.agent.static_guardrail_policy import StaticAgentGuardrailPolicy` 引用经垫片零改动通过。落地时逐点 grep 核对无遗留内联判定。

- [x] 3. 基础设施 `static_guardrail_policy.py` 降为 re-export 垫片
  - 修改 `src/infrastructure/agent/static_guardrail_policy.py`：整体替换为模块 docstring（标注「判定逻辑已上提至 `domain/agent/guardrail_policy.py`（ADR-0014），本模块保留为向后兼容临时垫片，re-export 领域实现，保护既有 import 路径与测试引用；后续片可按 change-discipline 删除本垫片并改所有引用点」）+ `from __future__ import annotations` + `from domain.agent.guardrail_policy import (StaticAgentGuardrailPolicy, _looks_batch, _segment_count)` + `__all__ = ["StaticAgentGuardrailPolicy", "_looks_batch", "_segment_count"]`。
    - 移除原文件全部判定实现（`classify_*`/`evaluate_*`/`_budget_decision`/`_risk_decision`/本地 `_json_safe`）；`_looks_batch`/`_segment_count` 的 re-export 为防御性冗余（既有测试仅 import 类），`__all__` 显式声明避免 lint 未使用告警。
    - _需求: 1.1, 3.3_ ; _design 组件 3 / Property 5、8_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run python -c "from infrastructure.agent.static_guardrail_policy import StaticAgentGuardrailPolicy, _looks_batch, _segment_count"`（垫片可解析）。

- [x] 4. DI 装配点 `container_config._create_guardrail_policy` 改 import 指领域类
  - 修改 `src/application/container_config.py::_create_guardrail_policy`（@1114–1120）：把 `from infrastructure.agent.static_guardrail_policy import StaticAgentGuardrailPolicy` 改为 `from domain.agent.guardrail_policy import StaticAgentGuardrailPolicy`；`from infrastructure.agent.guardrail_config import agent_guardrail_config` 与 `new` 语句 `return StaticAgentGuardrailPolicy(agent_guardrail_config.to_policy())` **不动**；注册语句 `container.register(AgentGuardrailPolicyPort, _create_guardrail_policy, Scope.SINGLETON)`（@1904）不动。装配对外返回类型仍满足 `AgentGuardrailPolicyPort`、注入位置与配置不变（`Contract_Invariance`）。
    - _需求: 3.1, 3.3_ ; _design 组件 3 设计决策 DI 装配行 / Property 5_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/application`（含容器装配相关测试）。

- [x] 5. 既有 guardrail 测试 import 按需处理（仅 import，不改断言）
  - 逐一核查 7 处 `from infrastructure.agent.static_guardrail_policy import StaticAgentGuardrailPolicy` 引用文件：`test/infrastructure/agent/test_static_guardrail_policy_unit.py`、`test_react_agent_guardrail_unit.py`、`test_react_agent_guardrail_runtime.py`、`test_workflow_hitl_guardrail_regression_unit.py`、`test/application/run/test_run_application_service_unit.py`、`test/application/test_long_task_phase6_recovery_collaboration_integration.py`、`test/integration/test_long_task_runtime_convergence_p0.py`、`test/integration/test_long_task_runtime_convergence_p1.py`。
    - **默认零改动**：垫片保证以上 import 仍可解析、断言语义不变（AC5.3）。仅当 lint（如 ruff 未使用/私有 import 规则）要求直指领域时，才把对应 import 改为 `from domain.agent.guardrail_policy import StaticAgentGuardrailPolicy`，**断言一律不改**。
    - _需求: 5.3_ ; _design 测试策略第 2 项 / Property 8_
    - 验证：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent test/application test/integration`。

---

## Checkpoint 2：垫片 + DI 就绪、全量测试绿（门禁）

- [x] 6. CP2 Wave 2 门禁校验
  - 全量测试前后全绿、无新增 failed：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest`（对齐既有基线，failed 数不高于收敛前）。
  - 垫片无残留判定：`cd epsilon-boot && grep -nE "def _budget_decision|def _risk_decision|def classify_|def evaluate_" src/infrastructure/agent/static_guardrail_policy.py`（期望零命中——判定已全部上提，仅剩 re-export）。
  - DI 已改指领域：`cd epsilon-boot && grep -n "from domain.agent.guardrail_policy import StaticAgentGuardrailPolicy" src/application/container_config.py`（有命中）；`grep -n "from infrastructure.agent.static_guardrail_policy import" src/application/container_config.py`（期望零命中）。
  - 改动文件 lint 零新增错误：`cd epsilon-boot && uv run ruff check src/infrastructure/agent/static_guardrail_policy.py src/application/container_config.py` 与 `cd epsilon-boot && uv run pyright src/infrastructure/agent/static_guardrail_policy.py src/application/container_config.py`。
  - 消费方鸭子调用行为不变（Property 5）：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent/test_react_agent_guardrail_unit.py test/infrastructure/agent/test_react_agent_guardrail_runtime.py test/infrastructure/agent/test_workflow_hitl_guardrail_regression_unit.py`（`react_agent_adapter` 经 `getattr` 读取 `policy` 属性与三个 `evaluate_*` 语义不变）。
  - _需求: 2.6, 3.1, 3.2, 3.3, 5.3, 5.4_ ; _design Property 5、8_

---

## Wave 3：ADR-0014 + 文档同步（与代码正交，最后执行）

> **正交证据**：本波只改 `docs/` 下文件，与 `epsilon-boot/` 源码零交集。

- [x] 7. ADR-0014 及文档同步
  - [x] 7.1 新增 ADR-0014
    - 落地前核验编号：`cd .. && ls docs/adr/ | grep 0014`（期望零命中，确认 0014 未占用；当前最新为 0013）。
    - 在 `docs/adr/` 新建 `0014-introduce-guardrail-domain-service-in-agent-subdomain.md`，遵循 `docs/adr/0000-template.md` 四段式。front matter：`status: Accepted`、`date: 2026-07-07`、`deciders: [后端架构维护者]`、`supersedes:` **留空**（**不 supersede ADR-0001**）。
    - 标题：「在 domain/agent 引入护栏策略领域服务一等抽象（充血化试点）」；四段按 design「ADR-0014 草案要点」写：
      - **背景**：`domain/agent` 护栏判定（任务分类、预算/风险决策、分类启发式）纯规则却落在 `infrastructure/agent/static_guardrail_policy.py`，只 import 领域类型、无 I/O/框架，是典型 `Domain_Logic_In_Infrastructure`（与 ADR-0010 对 Agent Loop 判断同源）；ADR-0009 已在 `domain/task` 建立可复制范式。
      - **决策**：在 `domain/agent/guardrail_policy.py` 引入承载全部纯判定的领域服务（**保留类名 `StaticAgentGuardrailPolicy`**），结构化实现同层 `AgentGuardrailPolicyPort`（Protocol，无反向依赖、无 `import ports`）；`_json_safe` 复用 `domain/agent/guardrails.py` 既有等价实现；`infrastructure/agent/static_guardrail_policy.py` 降为 re-export 垫片；DI 装配改指领域类。
      - **后果**：护栏领域判定住进领域层、可脱离运行时单测；本试点只覆盖 `Static_Agent_Guardrail_Policy`，`agent_config`/`approval_policy_provider`/`segmented_orchestration` 留待后续按 `change-discipline` 逐候选推进；本决策为 `Behavior_Equivalent_Refactor`、不改任何对外可观测行为、不引第三方依赖、不引领域事件（尊重 §8 与 ADR-0001，**不 supersede ADR-0001**）；回链 ADR-0009（范式来源）、ADR-0010（同源方向判断）。
      - **备选方案与未采纳原因**：(a) 维持散落（未采纳：`Domain_Logic_In_Infrastructure` 差距本身）；(b) 改名 `GuardrailEvaluationPolicy`（未采纳：增加既有测试断言/`isinstance` 语义漂移，违反最小改动）；(c) `_json_safe` 留基础设施副本（未采纳：判定内嵌步骤重复序列化 helper，领域同包已有等价实现）；(d) 直接删除基础设施文件 + 改全部引用（未采纳本片：扩大改动面，留后续片）；(e) 显式继承 Port（未采纳：Protocol 结构化匹配无需继承，反增耦合）；此外不引入领域事件机制（未采纳：尊重 ADR-0001）。
    - _需求: 6.1, 6.2, 6.3, 6.4_ ; _design ADR-0014 草案要点_
    - 验证：`cd .. && test -f docs/adr/0014-introduce-guardrail-domain-service-in-agent-subdomain.md`；`grep -nE "supersedes:" docs/adr/0014-*.md`（字段存在且值为空）；`grep -nE "ADR-0009|ADR-0010" docs/adr/0014-*.md`（回链存在）。

  - [x] 7.2 更新 `docs/adr/README.md` 索引
    - 在 `docs/adr/README.md` 索引表 0013 行之后追加 0014 索引行（编号 `[0014](0014-introduce-guardrail-domain-service-in-agent-subdomain.md)` / 标题「在 domain/agent 引入护栏策略领域服务一等抽象（充血化试点）」/ `Accepted` / `2026-07-07`）。
    - _需求: 6.1_ ; _design ADR-0014 草案要点_
    - 验证：`cd .. && grep -n "0014" docs/adr/README.md`（有命中）。

  - [x] 7.3 按 doc-sync 同步主题文档（克制，最小粒度）
    - 按 `doc-sync.md` 判断并同步受影响主题文档：在 `docs/domain-model.md` 与/或 `docs/architecture.md` 补一句「`domain/agent` 护栏策略判定已上提为领域服务 `guardrail_policy.py::StaticAgentGuardrailPolicy`（结构化实现 `AgentGuardrailPolicyPort`），基础设施同名文件为向后兼容垫片（ADR-0014）」；参照 P2 同步粒度，仅新增指向性一句、不重写章节。
    - _需求: 4.6_ ; _design 目录/模块落点表 / AC → 交付物追溯表 4.6 行_
    - 验证：`cd .. && grep -rnE "guardrail_policy|护栏策略领域服务" docs/domain-model.md docs/architecture.md`（有命中，至少一处）。

---

## Checkpoint 3：最终门禁（8 条正确性属性 + 26 AC 全量验收）

- [x] 8. CP3 最终门禁校验（必须全部通过）
  - **Property 8（既有测试全绿）**：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest`（前后全绿、无新增 failed）。
  - **Property 1/2/3/4（判定等价）**：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/domain/agent/test_guardrail_policy_unit.py test/infrastructure/agent/test_static_guardrail_policy_unit.py`（新领域单测 + 既有单测同绿；含 `_risk_decision` metadata 等价断言）。
  - **Property 5（契约/时序不变）**：`cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest test/infrastructure/agent/test_react_agent_guardrail_unit.py test/infrastructure/agent/test_react_agent_guardrail_runtime.py test/infrastructure/agent/test_workflow_hitl_guardrail_regression_unit.py test/application/run/test_run_application_service_unit.py test/integration/test_long_task_runtime_convergence_p0.py test/integration/test_long_task_runtime_convergence_p1.py`；`grep -nE "def classify_payload|def evaluate_run_start|def evaluate_model_completed|def evaluate_tool_before_execution|def evaluate_tool_after_execution" src/domain/agent/ports.py`（Port 五方法签名未改）。
  - **Property 6（领域纯净度）**：`cd epsilon-boot && grep -rnE "import (application|infrastructure|fastapi|pydantic|logging)|from (application|infrastructure|fastapi|pydantic|logging)" src/domain/agent/guardrail_policy.py`（零命中）；`grep -nE "ContextVar|opentelemetry" src/domain/agent/guardrail_policy.py`（零命中）。
  - **Property 7（不引事件/新依赖）**：`cd epsilon-boot && grep -nE "event_bus|DomainEvent|publish" src/domain/agent/guardrail_policy.py`（零命中）；`cd .. && git diff --name-only | grep -E "pyproject.toml|uv.lock"`（期望零命中——依赖清单不变）。
  - **规范合规（需求 4）**：`cd epsilon-boot && uv run ruff check src/domain/agent/guardrail_policy.py src/infrastructure/agent/static_guardrail_policy.py src/application/container_config.py` 与 `cd epsilon-boot && uv run pyright src/domain/agent/guardrail_policy.py`（零新增错误、无裸 `Any` 越界；中文 docstring 齐备）。
  - **范围锁定（AC1.1–1.4）**：`cd .. && git diff --name-only` 中源码改动仅落 `src/domain/agent/guardrail_policy.py`（新增）+ `src/infrastructure/agent/static_guardrail_policy.py`（垫片）+ `src/application/container_config.py`（仅 import）+ `test/domain/agent/`（新增单测）+（如触发）既有测试 import；文档改动仅落 `docs/`；未改 `domain/agent/guardrails.py`、`domain/agent/ports.py`、`agent_loop_policy.py`/`agent_loop_orchestration.py`、`agent_config.py`/`approval_policy_provider.py`/`segmented_orchestration.py`。
  - **ADR（需求 6）**：`cd .. && test -f docs/adr/0014-introduce-guardrail-domain-service-in-agent-subdomain.md && grep -n "0014" docs/adr/README.md`。
  - _需求: 1.1, 1.2, 1.3, 1.4, 2.6, 3.1, 3.2, 3.3, 4.1, 4.5, 4.6, 5.3, 5.4, 6.1, 6.3_ ; _design Property 1、2、3、4、5、6、7、8_

---

## 任务 → 需求 AC → design 组件 → 正确性属性 追溯表

| 任务 | 覆盖需求 AC | design 组件 | 正确性属性 |
|---|---|---|---|
| 1.1 | 1.1/2.1/2.2/2.3/2.4/2.5/3.4/4.1/4.2/4.3/4.4/4.5 | 组件 1（领域服务） | Property 1、2、3、4、6、7 |
| 1.2 | 5.1/5.2 | 测试策略第 1 项 | Property 1、2、3、4 |
| 1.3 | 3.4/4.5/5.2 | 组件 1 / 设计决策第 3 行 | Property 3、7 |
| 3 | 1.1/3.3 | 组件 3（垫片） | Property 5、8 |
| 4 | 3.1/3.3 | 组件 3 DI 装配行 | Property 5 |
| 5 | 5.3 | 测试策略第 2 项 | Property 8 |
| 7.1/7.2 | 6.1/6.2/6.3/6.4 | ADR-0014 草案要点 | —（可追溯性） |
| 7.3 | 4.6 | 目录/模块落点表 | —（doc-sync） |
| CP1 | 2.1/2.6/4.1–4.5/5.1 | 组件 1 | Property 1、2、3、4、6、7 |
| CP2 | 2.6/3.1/3.2/3.3/5.3/5.4 | 组件 3 | Property 5、8 |
| CP3 | 1.1–1.4/2.6/3.1–3.3/4.1/4.5/4.6/5.3/5.4/6.1/6.3 | 全组件 | Property 1–8 |

---

## 备注

- **范围纪律（change-discipline）**：仅列达成需求所必需的改动；`domain/agent/guardrails.py` 的值对象/枚举、`domain/agent/ports.py` 的 Port、已在领域层的 Agent Loop 编排（`agent_loop_policy.py`/`agent_loop_orchestration.py`，ADR-0010/0011/0012）、以及 `agent_config.py`/`approval_policy_provider.py`/`segmented_orchestration.py`（后续片）明确**不改**。
- **保留原类名**：领域类沿用 `StaticAgentGuardrailPolicy`（不改名 `GuardrailEvaluationPolicy`），使既有测试断言与 `isinstance` 语义零变化，仅换 import 路径。
- **垫片而非删除**：`static_guardrail_policy.py` 降为 re-export 垫片（保护 7 处既有 import 引用零改动），删除垫片 + 改全部引用留后续片按 `change-discipline` 处理；DI 装配点例外，直接改指领域类作正向样板。
- **`_json_safe` 复用与复核**：领域 `_risk_decision` 复用 `guardrails._json_safe`（递归实现），移除基础设施本地一层副本；T-1.3 作为明确验收子项逐值复核对 metadata（`{"tool_name","risk_level"}`）字面等价并补测试，不等价则回退保留等价副本。
- **不下沉/不上提技术关注点**：护栏观测持久化（`RunGuardrailRecorderPort.record_observation`）、OTel span、guardrail 运行时统计累加、审批/阻断中断路径与文案全部留在 `react_agent_adapter.py`，调用位置与评估时序不动。
- **不返回异常**：领域服务不 `raise`、不新增 try/except、不吞异常；判定结果以 `GuardrailDecision`/`TaskExecutionClass`/`bool`/`int` 表达。`_segment_count` 的 `TypeError/ValueError` 归 0 与 `_looks_batch` 的 `isinstance` 守卫字面保留。
- **回滚**：领域构件为独立新文件、垫片与 DI 为局部替换，可按波次 `git revert`；因行为等价，回滚不影响既有测试基线。
- **命令约定**：源码测试/lint 在 `epsilon-boot/` 下执行、带 `PYTHONPATH=src`、依赖仅用 `uv`；文档校验命令 `cd ..` 回到仓库根（`docs/` 位于仓库根，非后端子目录）。
- **行号说明**：本文中 `@行号` 引自 design.md/当前代码定位，落地前以 grep 逐点核对实际位置，防止上游文件已微调导致偏移。
