# 文档—代码同步规范（Doc Sync）

本仓库的 `CLAUDE.md` / `AGENT.md` / `docs/` 是 coding-agent 的**主要上下文来源**——agent 依据它们理解架构、契约与约束后再动手。一旦文档与代码脱节，**每一个后续 agent 都会基于过时认知跑偏**，且越迭代偏差越大。因此「改代码就同步文档」是强制纪律，而非事后补充。

对标业界 docs-as-code / living-documentation 实践，以及 Codex `AGENTS.md`、Claude Code `CLAUDE.md` 把仓库内文档当作 agent 一等上下文的约定。与 [change-discipline.md](change-discipline.md)、[adr.md](adr.md) 联合构成 anti-drift 闭环。

## 1. 核心原则

- 文档与代码**同一次改动内**一起更新，进入同一个提交/PR，不留「稍后补文档」。
- 文档描述的是**当前真实状态**，不是历史意图或未来计划（计划归 `TODO.md` / spec，历史决策归 ADR）。
- 文档索引（`CLAUDE.md`、`AGENT.md`、各 `README.md` 的表格）必须与实际文件保持一致：**新增规范/主题文档就登记，删除就移除。**

## 2. 改动 → 必须同步的文档映射

发生下列代码/契约变化时，必须在**同一次改动**中更新对应主题文档：

| 代码/契约变化 | 必须同步的文档 |
|---|---|
| 新增/修改/删除 HTTP 端点、路由 | [../api.md](../api.md) |
| 新增/修改工具、参数、开关、HITL 策略、注册条件 | [../tools.md](../tools.md)（并遵循 [tool-authoring.md](tool-authoring.md)） |
| 新增/修改配置键、Provider 配置组、默认值 | [../configuration.md](../configuration.md) |
| 改分层结构、Port/Adapter、Agent Loop、DI 装配 | [../architecture.md](../architecture.md)、[../di-container.md](../di-container.md) |
| 改领域模型、值对象、上下文/任务模型 | [../domain-model.md](../domain-model.md) |
| 改模型路由策略、Provider 注册 | [../model-routing.md](../model-routing.md) |
| 改前端页面结构、API 代理、流式状态、任务工作区 | [../frontend.md](../frontend.md) |
| 改开发/测试/评估命令、依赖管理流程 | [../development.md](../development.md) |
| 改 Prompt 资产布局、版本注册 | [../prompts.md](../prompts.md) |
| 新增/删除顶层目录或调整目录职责 | [../repository-map.md](../repository-map.md) |

> 表未穷尽：任何被 `docs/` 明确描述过的行为发生变化，其对应文档都要同步。

## 3. 索引一致性（强制）

新增或删除一篇 steering / 主题 / ADR 文档时，必须同步更新**所有**登记它的索引：

- steering 文档 → `docs/steering/README.md`、根 `CLAUDE.md`、`AGENT.md` 的规范表。
- 主题文档 → `CLAUDE.md`「主题文档索引」、`AGENT.md`「文档索引」。
- ADR → `docs/adr/README.md` 索引表（见 [adr.md](adr.md)）。

索引与实际文件不一致，视为文档缺陷。

## 4. 安全红线（不可写入文档）

文档会进入 agent 上下文 / system prompt，一律**当作可公开内容对待**：

- 禁止把凭证/密钥/token、内网仓库或 Nexus 地址、`config.properties` / `.env` 中的敏感明文写入任何文档、注释、示例或日志。
- 举例需要占位时用明显假值（如 `xxxx`、`<YOUR_KEY>`），不得粘贴真实值。

## 5. 与 spec / ADR 的分工

- `docs/`（主题文档）：系统**当前**是什么样、怎么用——随代码同步。
- `docs/steering/`：**强制规范**——约束怎么改。
- `docs/adr/`：**为什么这么定**——只增不改的决策日志。
- `docs/spec/<feature>/` 与 `TODO.md`：**打算做什么**——需求、设计、任务与路线。

四者边界清晰、互不替代，改动时各归其位。

## 自检清单（改动完成前）

1. 我这次改的契约/结构/配置/工具/命令，落在 §2 表里哪几行？对应文档更新了吗？
2. 我新增或删除了文档吗？§3 列出的所有索引都同步了吗？
3. 文档里有没有写入不该公开的敏感信息（§4）？
4. 我写进 `docs/` 的内容，描述的是「当前真实状态」而非计划或历史意图吗？
</content>
