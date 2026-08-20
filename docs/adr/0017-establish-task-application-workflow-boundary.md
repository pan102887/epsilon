---
status: Accepted
date: 2026-07-09
deciders: [Codex, scoped evaluator]
supersedes:
superseded-by:
---

# ADR-0017：确立 Task application workflow 边界

## 背景与问题（Context）

`TaskAgentAdapter` 作为 `TaskAgentPort` 的基础设施实现，长期同时承担任务用例编排、会话推进、审批恢复顺序、trace shaping、`AgentResult` 到 `TaskResult` 映射、prompt/tool/model/AgentPort 适配与 TraceStore 持久化。该形态让 adapter 过厚，也模糊了 application 用例流程与 infrastructure 技术适配的边界。

本项目已有相邻决策：ADR-0009 确立 task 子域可承载纯领域服务，ADR-0016 确立 Chat workflow/service 的 application 边界。本次 P0 adapter 瘦身需要为 Task 执行、续跑和审批恢复建立同等边界，同时保持既有 `TaskAgentPort`、`AgentPort` 与静态导入规则不变。

## 决策（Decision）

我们将 `application/task/TaskApplicationService` 确认为 Task 用例编排边界，负责 execute / continue / approval resume 的 session 编排、分段续跑聚合、风险门 metadata、trace shaping 接入、`TaskResultMapper` 映射，以及 approval load / expired / count / order / allowed / consume / agent resume 顺序。

我们将 `application/task/TaskTraceWorkflow` 确认为 application 层无 I/O workflow，用于把 `ConversationContext` 中的 assistant/tool 消息塑形为 `TraceEntry`。我们将 `domain/task/result_mapping.py::TaskResultMapper` 确认为 task 子域纯映射服务，只依赖 domain 类型。

`TaskAgentAdapter` 保留基础设施职责：prompt 构造、tool schema 与 scoped ToolRegistry、model registry、`AgentConfig` 构造、`AgentPort.run/resume` 回调、TraceStore 持久化与基础设施异常兜底。由于 infrastructure 不得导入 application，adapter 只通过结构协议消费组合根注入的 `TaskApplicationService`。

## 后果（Consequences）

- **正面**：Task 用例流程与技术适配分离，adapter 变薄；execute / continue / approval resume 的顺序可在 application 层用 fake callback 单测；domain 只保留纯映射，静态边界清晰。
- **负面 / 代价**：组合根需要显式构造并注入 `TaskApplicationService` 与 `TaskTraceWorkflow`；adapter 需要维护结构协议以避免反向导入 application。
- **后续影响**：后续新增 Task 用例流程优先放入 `application/task`；新增纯 task 判定优先放入 `domain/task`；TraceStore、ToolRegistry、PromptRegistry、ModelRegistry 与 concrete Agent 调用仍不得进入 application service 的直接基础设施依赖。

## 备选方案（Alternatives）

- **继续让 `TaskAgentAdapter` 承载全部编排** —— 未采纳原因：adapter 会继续膨胀，execute / continue / approval resume 顺序难以独立测试，也与 Chat 已确立的 application workflow 边界不一致。
- **把 Task trace shaping 放入 domain** —— 未采纳原因：trace shaping 依赖用例上下文和时间戳回退语义，不是 task 子域纯业务规则；放入 application 更符合无 I/O workflow 的职责。
- **让 infrastructure adapter 直接 import `TaskApplicationService`** —— 未采纳原因：违反 `infrastructure -> application` 禁止依赖的静态边界，会破坏当前六边形架构约束。
