# 需求文档：P0 Adapter 瘦身

## 背景

后端主干已经完成多轮 DDD 与 adapter 重构：`agent-adapter-refactor-v3` 解决 ReAct 流式、工具超时与 token 预算；`ddd-agent-loop-relocation-slice2` 已把 ReAct 轮次编排主体上提到 `domain/agent/agent_loop_orchestration.py`，`ReActAgentAdapter` 通过 `AgentLoopEffects` 承接副作用；`ddd-infrastructure-logic-remediation` 与 `ddd-followup-refinements` 已收敛 application → infrastructure 反向依赖、拆出 Chat 应用服务、Handoff policy 与部分 ReAct 基础设施协作者。

当前所有既有 `docs/spec/*/tasks.md` 均无未勾选任务，但代码现状仍暴露新的 P0 维护风险：

- `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` 仍约 2502 行，虽然已是门面，但仍同时承担工具执行副作用、审批恢复、guardrail/checkpoint 缝合、流式最终轮、事件通道映射等多个变化原因。
- `epsilon-boot/src/infrastructure/chat/chat_service_adapter.py` 仍约 1111 行，已委托 `ChatSessionContextWorkflow` / `ChatApplicationService`，但 adapter 内仍承载同步、流式、分段、审批恢复与模型直连路径的多种映射。
- `epsilon-boot/src/infrastructure/task/task_agent_adapter.py` 仍约 852 行，任务模板、trace 提取、分段执行、审批恢复和结果映射仍集中在单类。
- `epsilon-boot/src/application/container_config.py` 约 2057 行，组合根例外合法，但注册、配置解析、对象工厂和运行时后端选择混杂，成为 adapter 瘦身后的装配瓶颈。

根据 `docs/steering/change-discipline.md`，本次属于多文件结构性重构，且会触及 Port/Adapter 边界、SRP 与组合根职责；必须继续遵循 spec-dev 的 `requirement -> design -> tasks -> implementation -> evaluator` 流程。若 design 阶段确认新增长期一等抽象或改变已 Accepted ADR 的结论，还必须先执行 ADR 判断。

## 目标

以行为等价为前提，继续推进 P0 级 adapter 瘦身，把 adapter 收敛为外部协议、DTO、事件、工具副作用和组合根装配边界，避免 adapter 再次累积领域规则或跨用例编排。

本期目标不是一次性追求最小行数，而是消除最影响后续迭代的职责混杂点，并建立可继续分片的结构。

## 范围

### P0 必做

1. ReAct adapter 门面继续瘦身：识别 `react_agent_adapter.py` 中仍可安全外移的基础设施协作职责，按行为等价方式拆到 `infrastructure/agent/` 内部协作者或已存在领域策略；保留 `AgentPort` 与 `AgentLoopEffects` 签名不变。
2. Chat adapter 瘦身：将 `chat_service_adapter.py` 中仍属于用例编排、分段决策或会话保存协调的逻辑继续委托到 application 层既有服务或窄协议，adapter 保留模型解析、流式/事件包装和 API 兼容映射。
3. Task adapter 瘦身：将 `task_agent_adapter.py` 中可独立测试的 trace 提取、结果映射、分段继续/恢复编排拆出为 application/domain 可承载的纯逻辑或窄服务；保留工具 schema、Agent 调用和外部副作用适配。
4. 组合根拆分：在不改变容器对外注册语义的前提下，把 `container_config.py` 中高内聚的工厂群拆到局部装配模块，组合根仍作为唯一允许引用 infrastructure concrete adapter 的入口。
5. 静态守卫与回归：补充或更新架构边界测试，防止 application 重新直接导入 infrastructure adapter，防止 adapter 瘦身后出现循环依赖或隐藏默认回退。

### P1 后续

- 统一注册机、Agent/Tool 动态匹配、MCP server 配置加载。
- 上下文调度算法优化、workspace/sandbox 方案调研。
- Storage 抽象进一步上提。

### 明确不做

- 不新增业务功能、工具能力、API 字段、事件类型或配置键，除非 design 证明是拆分所必需且行为等价。
- 不重写 ReAct loop 协议、不改变 `AgentPort` 四入口签名。
- 不改变 HITL 审批决策语义、工具执行顺序约束、guardrail 策略、checkpoint/recovery 持久化语义。
- 不修改未跟踪的 `TODO.md` 条目归属，不把注册机/MCP 等产品能力混入本 P0 瘦身。

## 术语

| 术语 | 定义 |
| --- | --- |
| Adapter 瘦身 | 在保持外部契约不变的前提下，把 adapter 中非边界职责拆到更合适的 domain/application/infrastructure 协作者，使 adapter 只承担适配与副作用边界。 |
| 行为等价重构 | 不改变 API 响应、事件序列、错误类型、持久化格式、日志敏感信息边界、模型/工具调用次数和用户可见文本的重构。 |
| 门面 adapter | 保留原 public class 与 Port 实现，内部委托协作者，避免大范围调用方改动。 |
| 组合根例外 | `application/container_config.py` 及其受控拆分模块允许引用 infrastructure concrete adapter 完成装配，但不得承载业务编排。 |

## 需求

### 需求 1：继续遵循 spec-dev 流程

**用户故事：** 作为维护者，我希望 P0 adapter 瘦身仍受 spec-dev 约束，以便高风险重构可追溯、可评审、可回滚。

#### 验收标准

1. THE P0_Adapter_Slimming SHALL maintain `requirement.md`, `design.md`, `tasks.md`, `review-log.md`, and final `summary.md` under `docs/spec/p0-adapter-slimming/`.
2. WHEN design changes Port/Adapter ownership, introduces a long-lived first-class abstraction, or supersedes an Accepted ADR, THE implementation SHALL perform ADR judgment before code changes.
3. THE implementation SHALL execute tasks in reviewed slices, and SHALL NOT batch unrelated adapter slimming work into one unreviewable rewrite.
4. THE final summary SHALL record changed files, behavior-equivalence evidence, tests run, and any deferred adapter hotspots.

### 需求 2：ReAct adapter 门面职责收敛

**用户故事：** 作为后端开发者，我希望 `ReActAgentAdapter` 更接近门面，以便修改工具执行、审批恢复、trace、guardrail 或 stream 映射时不互相牵连。

#### 验收标准

1. THE implementation SHALL preserve `AgentPort.run`, `run_streaming`, `run_events`, and `resume` method signatures and externally observable semantics.
2. THE implementation SHALL preserve `AgentLoopEffects` method signatures unless design and ADR judgment explicitly approve a change.
3. WHEN extracting infrastructure collaborators, THE collaborators SHALL remain under `src/infrastructure/agent/` unless the extracted logic is proven pure domain policy.
4. THE extracted collaborators SHALL receive narrow protocols or value objects instead of the whole `ReActAgentAdapter` where practical.
5. THE implementation SHALL keep model call count, tool call count, event ordering, approval metadata shape, guardrail metadata shape, and checkpoint writes behavior-equivalent.
6. THE implementation SHALL not move OTel spans, ContextVar runtime state, tool registry access, or concrete tool execution into domain.

### 需求 3：Chat adapter 用例编排继续下沉

**用户故事：** 作为维护者，我希望 `ChatServiceAdapter` 只做聊天边界适配，以便会话继续、审批恢复和分段执行可在 application 层独立测试。

#### 验收标准

1. THE implementation SHALL preserve `ChatServicePort` public method signatures and response/event DTO semantics.
2. THE adapter SHALL retain model resolution, direct LLM technical path, stream chunk/event wrapping, and prompt boundary concerns that are infrastructure-specific.
3. THE implementation SHALL move any remaining application use-case orchestration that can be expressed without concrete infrastructure dependencies into `src/application/chat/` or an existing application service.
4. THE implementation SHALL preserve session load/save/index behavior, prompt id propagation, continue/resume validation order, and segmented metadata.
5. THE implementation SHALL add focused application-level tests for any orchestration moved out of the adapter.

### 需求 4：Task adapter 分段与结果映射收敛

**用户故事：** 作为任务功能维护者，我希望 `TaskAgentAdapter` 的任务映射、分段执行和审批恢复边界更清晰，以便后续扩展任务运行时不会继续膨胀单个 adapter。

#### 验收标准

1. THE implementation SHALL preserve `TaskAgentPort.execute`, `continue_task`, `resume_approval`, and checkpoint restore behavior.
2. THE implementation SHALL keep concrete `AgentPort`, `ToolRegistry`, prompt loading, and trace store side effects out of domain.
3. THE implementation SHALL extract pure result mapping, trace shaping, or continuation precondition logic only when behavior-equivalence tests can lock current outputs.
4. THE implementation SHALL preserve task trace timestamp semantics, tool schema exposure, segmented execution metadata, and approval resume error ordering.

### 需求 5：组合根拆分但不稀释边界

**用户故事：** 作为系统装配维护者，我希望 `container_config.py` 可读且职责分块，同时仍保留组合根作为唯一装配入口。

#### 验收标准

1. THE implementation SHALL preserve existing DI registrations, singleton/transient scopes, default backend selection, and config source semantics.
2. THE implementation MAY split cohesive factory groups into `src/application/container_*` modules, but SHALL keep all concrete infrastructure construction inside application composition-root modules.
3. THE implementation SHALL NOT instantiate concrete infrastructure adapters from routers, application services, domain services, or infrastructure consumers outside the composition root.
4. THE implementation SHALL include static or wiring tests that prove key Port-to-Adapter bindings remain available.

### 需求 6：架构边界与文档同步

**用户故事：** 作为后续 coding-agent，我希望重构结果在文档和静态守卫中可见，以便不会把旧职责重新塞回 adapter。

#### 验收标准

1. THE implementation SHALL update `docs/architecture.md`, `docs/agent.md`, `docs/domain-model.md`, and `docs/di-container.md` when responsibilities move.
2. THE implementation SHALL update `test/static/test_architecture_import_boundaries.py` or equivalent guards when new boundary rules or exceptions are introduced.
3. THE implementation SHALL keep any temporary exception explicit, named, and linked to a cleanup plan.
4. THE implementation SHALL ensure new modules include Chinese module/class/public method docstrings according to steering.

### 需求 7：验证基线

**用户故事：** 作为维护者，我希望每个瘦身切片都有足够回归证据，以便 P0 重构不牺牲运行时稳定性。

#### 验收标准

1. THE implementation SHALL run focused tests for each touched adapter area before marking a task complete.
2. THE final checkpoint SHALL run backend static architecture tests and the full backend pytest suite, or record exact blockers and partial coverage.
3. THE implementation SHALL run `ruff check` for touched source files.
4. THE implementation SHALL run pyright for newly introduced domain/application modules where feasible, and SHALL not add new type errors.
5. THE evaluator SHALL review every code slice that changes adapter responsibilities before its checkbox is marked complete.

## 非功能要求

- **行为等价优先**：本 spec 的默认策略是拆分与委托，不做语义修正。发现疑似 bug 时记录为 follow-up，除非它阻塞重构正确性。
- **最小改动**：每个任务只处理一个 adapter 或一个职责簇，不跨域夹带无关格式化。
- **可回滚**：每个切片必须能通过文件范围和测试范围单独审查。
- **可读性**：新协作者应有清晰命名，避免 `helper` / `manager` 式泛化类继续制造职责不清。
- **性能不退化**：不得让现有并发工具执行、流式增量、checkpoint recovery 或 segmented execution 引入额外串行瓶颈。
