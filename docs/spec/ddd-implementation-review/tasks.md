# Tasks — ddd-implementation-review（需求 6：补齐 DDD 战术建模规范约束）

> 本文件由 `design.md` 展开为可执行、可勾选的任务清单。**本轮范围仅需求 6**（requirement.md AC1–AC11），为**纯文档 / ADR 变更，零源码改动**——不动 `epsilon-boot/src/**` 与 `test/**` 任何文件。
> 每条任务标注：产出/修改的确切文件、对应 design 组件与 AC 编号、验证方式（引用 design「测试策略」的 grep/test 命令）。
> 依赖路径：**任务 1（战术规范正文）→ 任务 2（修订 pydantic-model.md）→ 任务 3（新增 ADR-0007）→ 任务 4（三处索引同步）→ 任务 5（全量一致性校验 checkpoint）**。任务 1–3 之间弱依赖（正文与 ADR 互相回链），建议按上列顺序落地以保证回链锚点齐备。
>
> **change-discipline 硬约束**：全程 §1 最小改动——只改达成 AC 所必需的行/文件，不碰无关内容。其中**任务 2 修订 `pydantic-model.md` 属 change-discipline §4「确需调整规范本身的独立显式改动」**，须在提交说明与 ADR-0007 中显式标注「这是一次显式规范修订，非顺手改动」。
> **零源码影响硬约束**：所有改动文件必须全部落在 `docs/` 下；`git diff --name-only` 在 `epsilon-boot/` 下须零命中（AC10 / Property 6，任务 5.7 验证）。
> **文本逐字匹配约束**：任务 2 三处修订的 `before` 文本已与 `docs/steering/pydantic-model.md` 当前第 3 / 9 / 21 行逐字核对一致，落地时须用整行精确替换，禁止改动未列出的其他行的换行符或措辞。

---

## 任务 1：新增战术建模规范正文 `docs/steering/ddd-tactical-modeling.md`

> 对应 design **组件 1**（覆盖 AC1 / AC2 / AC3 / AC4 / AC7 / AC8，并奠定 AC5 的领域侧口径）。
> 依赖：无。落地后为任务 2/3/4 提供回链锚点。

- [x] **1.1** 新建文件 `docs/steering/ddd-tactical-modeling.md`，文件头沿用其它 steering 文档风格：加 `--- inclusion: always ---` front matter（与 `ddd-architecture.md` 一致，标明常驻上下文）；首段一句话说明本规范与 `ddd-architecture.md` 的分工（本文件管**战术建模**：实体/聚合/领域服务/值对象/仓储语义/限界上下文/通用语言；`ddd-architecture.md` 管**分层依赖方向、Port/Adapter 归属**），并互相回链。【design 组件 1「文件头」；AC1】
- [x] **1.2** 写「第 1 节 概述与适用范围」：声明本规范补齐 `ddd-architecture.md` 未覆盖的战术设计维度；明确本规范是**护栏而非一次性改造令**——只规定「今后如何建模」，既有贫血模型的渐进纠偏归属需求 2 的单子域试点（显式回链需求 2 / AC11）。【design 组件 1 第 1 节；AC1、AC11】
- [x] **1.3** 写「第 2 节 值对象（Value Object）」：建模规则（领域值对象用 `@dataclass(frozen=True)`，判据为「相等即同值、不可变、无独立标识」，构造期校验放 `__post_init__` 或专用 `validate()`，举例 `WorkflowExecutionPolicy.validate()`）；放置规则 `domain/<子域>/value_objects.py`；显式声明「值对象**不用 Pydantic**」，并回链组件 2（`pydantic-model.md`）与 `ddd-architecture.md`「明确禁止的依赖」，确保三处口径一致。【design 组件 1 第 2 节；AC2、AC5；Property 1】
- [x] **1.4** 写「第 3 节 实体（Entity）」：建模规则（稳定标识 + 生命周期内可变状态 → 实体，行为/不变量内聚在实体上）；放置规则 `domain/<子域>/entities.py`（约定「今后有实体时置于此」）；说明现状 `domain/task/value_objects.py::Task`、`domain/agent/value_objects.py::AgentConfig` 属需求 2 待评估的充血化候选，本规范不强制立即改（回链 AC11）。【design 组件 1 第 3 节；AC2、AC11】
- [x] **1.5** 写「第 4 节 领域服务（Domain Service）与放置规则」：建模规则（无自然归属实体/值对象的跨对象业务规则才建模为领域服务，零 `application/`/`infrastructure/` 依赖、不引框架 API）；放置规则 `domain/<子域>/domain_service.py` 或与既有样板一致的具名模块（承认既有具名组织合法，不强制改名）；逐一举例 `RunStateMachine` / `WorkflowExecutionPolicy.validate()` / `ReadinessAggregator.check_readiness()` / `WorkspacePolicy.resolve()`，并点明其共有正确特征（零基础设施依赖、规则内聚、可单测）。【design 组件 1 第 4 节；AC2、AC3；Property 3】
- [x] **1.6** 写「第 5 节 聚合根与聚合边界」：建模规则（聚合根为唯一外部入口、聚合边界即一致性/事务边界）；**本仓库上下文约束**——Agent 工作台状态多为会话态/流式态、强一致性事务边界影响有限，**不强制每个子域引入聚合根**；以「何时才需要引入聚合边界」判定指引清单表达（仅当「一组对象须在同一次变更内保持不变量、且存在并发写竞争或跨对象一致性约束」时才引入，否则用值对象/实体组合避免过度设计）。**禁止**出现「所有子域必须引入聚合根」类强制条款。【design 组件 1 第 5 节；AC8、AC11；Property 4】
- [x] **1.7** 写「第 6 节 仓储（Repository）语义与本项目 Port 命名的关系」：说明本仓库以 `domain/<子域>/ports.py` 的 **Port（Protocol）** 承载经典 Repository 语义，Adapter 在 `infrastructure/` 实现（举例既有 `TraceStorePort` / `ArtifactStorePort`）；**不新增独立 `repository.py` 命名**（回链决策 6）。【design 组件 1 第 6 节；AC2】
- [x] **1.8** 写「第 7 节 限界上下文与通用语言」：以既有子域目录 `agent`/`chat`/`task`/`run`/`model_access`/`workspace`/`health`/`prompt`/`storage` 为天然限界上下文；约定术语在各上下文内保持一致、跨上下文同名不同义须显式区分；明确**不引入**重量级上下文映射（Context Map）机制。【design 组件 1 第 7 节；AC7】
- [x] **1.9** 写「第 8 节 不推荐的构件：领域事件」：显式声明领域事件/事件总线**不是**本仓库推荐战术构件，已由 ADR-0001（`Accepted`）主动移除，跨模块副作用走 Port/Adapter 与 trace 抽象；回链 `docs/adr/0001-remove-domain-event-bus.md`，禁止后续 agent 以「补齐战术构件」为由复活事件总线。文中「领域事件」须仅出现在「不推荐/已移除」语境。【design 组件 1 第 8 节；AC4；Property 2】
- [x] **1.10** 写「第 9 节 与其它规范的衔接」：回链 `srp-principle.md`（序列化/日志属技术关注点，不入领域对象）、`code-documentation.md`（领域类中文 docstring）、`python-typing-lint.md`（全量类型标注、禁裸 `Any`）、`change-discipline.md`（引入领域服务/实体等一等抽象属架构级决策，须先写 ADR）。【design 组件 1 第 9 节；AC2】

**任务 1 验证**（design 测试策略第 2 项，对应 Property 3 / AC1 / AC3）：
- `test -f docs/steering/ddd-tactical-modeling.md` → 存在。
- `grep -n "RunStateMachine\|WorkflowExecutionPolicy\|ReadinessAggregator\|WorkspacePolicy" docs/steering/ddd-tactical-modeling.md` → 四个真实符号均命中（Property 3，举例真实、无虚构类）。
- `grep -n "ADR-0001\|0001-remove-domain-event-bus" docs/steering/ddd-tactical-modeling.md` → 有命中（Property 2）。
- 人工核查：第 5 节含「何时才需要引入聚合边界」判定指引 + 「会话/流式态、一致性边界影响有限」上下文说明，无「所有子域必须引入聚合根」强制条款（Property 4 / AC8）；第 8 节「领域事件」仅出现在「不推荐/已移除」语境、无推荐措辞（Property 2 / AC4）。

---

## 任务 2：修订 `docs/steering/pydantic-model.md`（消解 Pydantic 二义）

> 对应 design **组件 2**（覆盖 AC5 / AC6，关联 Property 1）。
> **本任务属 change-discipline §4 显式规范修订**——须在提交说明与 ADR-0007 中标注「这是一次显式规范修订而非顺手改动」。
> 依赖：建议在任务 1 之后（修订后文本回链 `ddd-tactical-modeling.md`，需其已存在）。

- [x] **2.1** 修订点 A（AC6 指名第一处，`pydantic-model.md` 第 3 行）：把整行
  - before：`后端使用 Pydantic 2（\`pydantic>=2.12\`、\`pydantic-settings\`）作为数据校验与序列化的统一方案。API 边界与领域数据传递优先使用 Pydantic 模型，衔接 DDD 值对象与请求/响应契约。`
  - after（见 design 组件 2 修订点 A）：改为「Pydantic 仅作为 **API/DTO 与配置边界** 的数据校验与序列化方案；**领域层（`domain/`）不使用 Pydantic**，领域值对象/实体一律用 Python 原生类型与 `@dataclass(frozen=True)`」，并回链 `ddd-architecture.md`「明确禁止的依赖」与 `ddd-tactical-modeling.md`。【design 组件 2 修订点 A；AC5、AC6；Property 1】
- [x] **2.2** 修订点 B（AC6 指名第二处，`pydantic-model.md` 第 9 行）：把整行
  - before：`- 领域值对象优先使用不可变模型：\`model_config = ConfigDict(frozen=True)\``
  - after（见 design 组件 2 修订点 B）：改为「**领域值对象不使用 Pydantic**，用 `@dataclass(frozen=True)` 表达不可变性（依据：`domain/` 下 19 个文件用 dataclass、0 个用 Pydantic `BaseModel`）；`ConfigDict(frozen=True)` 仅用于 API/DTO 边界确需不可变的 Pydantic 模型」。【design 组件 2 修订点 B；AC5、AC6；Property 1】
- [x] **2.3** 修订点 C（「分层与职责」首条，`pydantic-model.md` 第 21 行，补强内部自洽）：把整行
  - before：`- API 层的请求/响应模型（DTO）与领域模型分离，避免直接把领域对象暴露到 HTTP 边界，遵循 [ddd-architecture.md](ddd-architecture.md)`
  - after（见 design 组件 2 修订点 C）：改为「DTO（Pydantic）与领域模型（`domain/`，dataclass）分离，DTO↔领域对象转换在应用层/基础设施层完成，避免领域对象暴露到 HTTP 边界、也避免 Pydantic 反向引入领域层」，回链 `ddd-architecture.md` 与 `ddd-tactical-modeling.md`。落地时若发现文件其余行仍残留「领域…Pydantic」倾向措辞，一并纳入本次显式修订并在提交说明列明。【design 组件 2 修订点 C（+说明）；AC6；Property 1；change-discipline §1】

**任务 2 验证**（design 测试策略第 1 项，对应 Property 1 / AC5 / AC6）：
- `grep -n "领域数据传递优先使用 Pydantic" docs/steering/pydantic-model.md` → **零命中**（原第 3 行冲突措辞已改）。
- `grep -n "领域值对象优先使用不可变模型" docs/steering/pydantic-model.md` → **零命中**（原第 9 行冲突措辞已改）。
- `grep -n "领域层.*不使用 Pydantic\|Pydantic 仅用于 API/DTO" docs/steering/pydantic-model.md` → **有命中**（新措辞已落地）。
- 人工核查：`ddd-architecture.md`（「明确禁止的依赖」列 Pydantic）、`pydantic-model.md`（修订点 A/B/C）、`ddd-tactical-modeling.md`（第 2 节 + 第 9 节）三份文档对「领域层不用 Pydantic、Pydantic 仅在 API/DTO/配置边界」表述字面一致（Property 1）。

---

## 任务 3：新增 ADR-0007

> 对应 design **组件 3**（覆盖 AC9，关联 Property 2 / Property 5）。
> 依赖：建议在任务 1/2 之后（ADR 决策段引用战术规范与 pydantic 修订的既定内容）。

- [x] **3.1** 新建文件 `docs/adr/0007-establish-domain-tactical-modeling-and-pydantic-boundary.md`，遵循 `docs/adr/0000-template.md` 四段式。front matter：`status: Accepted`、`date: 2026-07-06`、`deciders: [架构评审]`、`supersedes:`（**留空**——不取代任何 ADR，尤其不 supersede ADR-0001）、`superseded-by:`（留空）；标题 `ADR-0007：确立领域层战术建模范式与 Pydantic 边界`。【design 组件 3「文件名/front matter/标题」；AC9；Property 2】
- [x] **3.2** 写「背景与问题（Context）」段：陈述 `DDD_Tactical_Modeling_Gap`（现 steering 战术设计维度覆盖不完整——`ddd-architecture.md` 仅一句提及「实体/值对象/领域事件」三词、无建模规则，聚合根/实体/领域服务/仓储语义/限界上下文/通用语言全缺）、其为需求 1–5 偏差的规范根源、`Pydantic_Domain_Boundary_Clarification` 方向性二义与「代码以脚投票（`domain/` 19 文件 dataclass、0 Pydantic）」。【design 组件 3 第 1 段；AC9】
- [x] **3.3** 写「决策（Decision）」段（用「我们将……」陈述）：新增 `ddd-tactical-modeling.md` 确立战术建模范式（以 `Domain_Model_Positive_Baseline` 为范例基准）；确立「领域层用 Python 原生类型 / `@dataclass(frozen=True)`，Pydantic 仅用于 API/DTO 与配置边界」并据此修订 `pydantic-model.md`；聚合以「轻量约束 + 判定指引」表达、不强制全域引入；**尊重 ADR-0001**，领域事件不列为推荐战术构件、本 ADR 不 supersede ADR-0001。【design 组件 3 第 2 段；AC9；Property 2】
- [x] **3.4** 写「后果（Consequences）」段：正面（后续 agent 有据可依、消解 Pydantic 二义、需求 2 建模范式获规范背书）；负面/代价（新增一份必读 steering 的增量阅读成本、既有贫血模型与规范暂时落差靠需求 2 渐进纠偏）；后续影响（今后引入实体/聚合/领域服务仍属架构级决策须按 change-discipline §2 先写新 ADR；三份文档对「领域层可否用 Pydantic」须永久一致）。【design 组件 3 第 3 段；AC9】
- [x] **3.5** 写「备选方案（Alternatives）」段（`adr.md` 硬要求含未采纳原因）：方案 A 并入 `ddd-architecture.md`（未采纳：违反 SRP）、方案 B 改 `ddd-architecture.md` 允许领域用 Pydantic（未采纳：与既成实践/领域纯净度相悖）、方案 C 强制全域立即引入聚合根/充血实体（未采纳：过度设计、与最小改动冲突）、方案 D 引入领域事件补齐「事件」构件（未采纳：ADR-0001 已 Accepted 移除、复活须走 supersede 流程且无稳定订阅方）。【design 组件 3 第 4 段；AC4、AC9；Property 2】

**任务 3 验证**（design 测试策略第 5 项，对应 Property 2 / Property 5 / AC9）：
- `test -f docs/adr/0007-establish-domain-tactical-modeling-and-pydantic-boundary.md` → 存在。
- `grep -nA6 "^---" docs/adr/0007-*.md | grep -n "supersedes:"` → 该字段存在且值为空（不 supersede ADR-0001）。
- 人工核查：ADR-0007 含四段（背景/决策/后果/备选方案），备选方案含未采纳原因；决策段/备选方案 D 显式尊重、不回退 ADR-0001 领域事件决策（Property 2）。

---

## 任务 4：三处索引同步

> 对应 design **组件 4**（覆盖 AC1 的登记要求 + AC9 的 ADR 登记，遵循 doc-sync §3，关联 Property 5）。
> 依赖：任务 1（战术规范文件）、任务 3（ADR-0007 文件）须先存在，否则索引指向空文件。

- [x] **4.1** 修改 `docs/steering/README.md`：在 `ddd-architecture.md` 行（当前第 7 行）之后新增一行（保持战术建模紧邻分层规范），内容见 design 组件 4a：`| [ddd-tactical-modeling.md](ddd-tactical-modeling.md) | DDD 战术建模：值对象/实体/领域服务/聚合边界判定/仓储 Port 语义/限界上下文=子域目录/通用语言；领域层用 dataclass 不用 Pydantic。 |`。【design 组件 4a；AC1；Property 5；doc-sync §3】
- [x] **4.2** 修改 `docs/adr/README.md`：在 0006 行（当前第 16 行）之后新增一行，内容见 design 组件 4b：`| [0007](0007-establish-domain-tactical-modeling-and-pydantic-boundary.md) | 确立领域层战术建模范式与 Pydantic 边界 | Accepted | 2026-07-06 |`。【design 组件 4b；AC9；Property 5；doc-sync §3】
- [x] **4.3** 修改仓库根 `CLAUDE.md`「项目规范（强制性）」表格：在 `ddd-architecture.md` 行之后新增一行，内容见 design 组件 4c：`| [docs/steering/ddd-tactical-modeling.md](docs/steering/ddd-tactical-modeling.md) | DDD 战术建模：值对象/实体/领域服务/聚合边界判定/仓储 Port 语义/限界上下文与通用语言；领域层用 dataclass，Pydantic 仅在 API/DTO/配置边界。 |`。落地时若发现仓库根存在 `AGENT.md`（经 design 核查当前不存在，仅 `CLAUDE.md`），须一并新增同样一行；否则本项不适用。【design 组件 4c（+说明）；AC1；Property 5；doc-sync §3】

**任务 4 验证**（design 测试策略第 6 项，对应 Property 5 / AC1 / AC9）：
- `grep -n "ddd-tactical-modeling" docs/steering/README.md` → 有命中。
- `grep -n "ddd-tactical-modeling" CLAUDE.md` → 有命中。
- `grep -n "0007" docs/adr/README.md` → 有命中。

---

## 任务 5：全量一致性校验（Checkpoint）

> 逐条跑通 design「测试策略」7 组校验命令，对应 6 条正确性属性，并证明零源码影响。全部命令在仓库根 `/workspace` 下执行（涉及测试的在 `epsilon-boot/` 下）。
> 依赖：任务 1–4 全部完成。

- [x] **5.1** Property 1（AC5/AC6，测试策略第 1 项）— Pydantic 冲突措辞已消除：
  - `grep -n "领域数据传递优先使用 Pydantic" docs/steering/pydantic-model.md` → 零命中。
  - `grep -n "领域值对象优先使用不可变模型" docs/steering/pydantic-model.md` → 零命中。
  - `grep -n "领域层.*不使用 Pydantic\|Pydantic 仅用于 API/DTO" docs/steering/pydantic-model.md` → 有命中。
- [x] **5.2** Property 3（AC1/AC3，测试策略第 2 项）— 战术规范存在且举例真实：
  - `test -f docs/steering/ddd-tactical-modeling.md` → 存在。
  - `grep -n "RunStateMachine\|WorkflowExecutionPolicy\|ReadinessAggregator\|WorkspacePolicy" docs/steering/ddd-tactical-modeling.md` → 四符号均命中。
- [x] **5.3** Property 2（AC4，测试策略第 3 项）— 不推荐领域事件并回链 ADR-0001：
  - `grep -n "ADR-0001\|0001-remove-domain-event-bus" docs/steering/ddd-tactical-modeling.md` → 有命中。
  - 人工核查：文中「领域事件」仅出现在「不推荐/已移除」语境，无推荐措辞。
- [x] **5.4** Property 4（AC8，测试策略第 4 项）— 聚合以判定指引表达：人工核查第 5 节含「何时才需要引入聚合边界」判定指引与「会话/流式态、一致性边界影响有限」上下文说明，无「所有子域必须引入聚合根」强制条款。
- [x] **5.5** Property 5（AC9，测试策略第 5 项）— ADR-0007 存在且已登记：
  - `test -f docs/adr/0007-establish-domain-tactical-modeling-and-pydantic-boundary.md` → 存在。
  - `grep -n "0007" docs/adr/README.md` → 有命中。
  - `grep -nA6 "^---" docs/adr/0007-*.md | grep -n "supersedes:"` → 字段存在且值为空。
  - 人工核查：ADR-0007 含四段，备选方案含未采纳原因。
- [x] **5.6** Property 5（AC1/AC9，测试策略第 6 项）— 索引一致：
  - `grep -n "ddd-tactical-modeling" docs/steering/README.md` → 有命中。
  - `grep -n "ddd-tactical-modeling" CLAUDE.md` → 有命中。
- [x] **5.7** Property 6（AC10/AC11，测试策略第 7 项）— 零源码影响：
  - `git diff --name-only` → 改动文件全部落在 `docs/` 下，`epsilon-boot/` **零命中**（本次仅新增 `docs/steering/ddd-tactical-modeling.md`、`docs/adr/0007-*.md` 与修改 `docs/steering/pydantic-model.md`、`docs/steering/README.md`、`docs/adr/README.md`、根 `CLAUDE.md`）。
  - `cd epsilon-boot && PYTHONPATH=src uv run --frozen pytest` → 全绿（作为零源码影响旁证；本需求不新增/修改任何测试）。

---

## 任务 → 组件 → AC → 正确性属性 追溯表

| 任务 | design 组件 | 覆盖 AC | 正确性属性 |
|---|---|---|---|
| 任务 1（战术规范正文） | 组件 1 | AC1、AC2、AC3、AC4、AC7、AC8、AC11 | Property 2、3、4 |
| 任务 2（修订 pydantic-model.md） | 组件 2 | AC5、AC6 | Property 1 |
| 任务 3（新增 ADR-0007） | 组件 3 | AC4、AC9 | Property 2、5 |
| 任务 4（三处索引同步） | 组件 4 | AC1、AC9 | Property 5 |
| 任务 5（全量一致性校验） | 全交付物 | AC10、AC11（+ 复核 AC1–AC9） | Property 1–6 |

---

## 备注

- **范围纪律**：本文件严格限于需求 6，不含需求 1–5 的任何代码改动任务；需求 1–5 的落地不在本轮 tasks 范围内。
- **change-discipline §4 显式修订标注**：任务 2 是本轮唯一「修改既有规范文本」的动作，须在 commit message 与 ADR-0007 决策段中显式声明「显式规范修订，非顺手改动」；其余任务均为新增文件或索引追加，属常规最小改动。
- **文件动作一览**（与 design「涉及文件与动作一览」一致）：新增 `docs/steering/ddd-tactical-modeling.md`、`docs/adr/0007-establish-domain-tactical-modeling-and-pydantic-boundary.md`；修订 `docs/steering/pydantic-model.md`（第 3/9/21 行三处）、`docs/steering/README.md`（+1 行）、`docs/adr/README.md`（+0007 行）、根 `CLAUDE.md` 规范表（+1 行）。
- **回滚**：所有改动均在 `docs/` 下且为独立文件/独立行，`git revert` 本次提交即可完整回滚；因零源码改动，回滚无需重跑构建，既有测试全绿基线不受影响。

## 任务总数

| 分组 | 落地任务 | 验证/Checkpoint | 小计 |
|---|---|---|---|
| 任务 1（战术规范正文） | 10 | 1 组（4 项） | 10 + 校验 |
| 任务 2（pydantic-model.md 修订） | 3 | 1 组（4 项） | 3 + 校验 |
| 任务 3（ADR-0007） | 5 | 1 组（3 项） | 5 + 校验 |
| 任务 4（索引同步） | 3 | 1 组（3 项） | 3 + 校验 |
| 任务 5（全量一致性 Checkpoint） | — | 7 组命令（对应 Property 1–6） | 7 |
| **合计** | **21 落地子任务** | **每组末验证 + 任务 5 全量校验** | — |
