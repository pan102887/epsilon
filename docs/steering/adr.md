# 架构决策记录（ADR）规范

本项目定位为「可被 coding-agent 长期迭代」的 Agent 工作台。让后续每一个 agent（与人）不推翻已定结论、不在同一问题上反复摇摆的核心机制，是**架构决策记录（Architecture Decision Record, ADR）**：一份**只增不改、可追溯**的重大决策日志。

本规范对标业界通行做法（Michael Nygard 的 ADR 提案与 [MADR](https://adr.github.io/madr/) 模板、[adr.github.io](https://adr.github.io/) 组织实践），并结合本仓库的 DDD/六边形架构约束落地。

## 何时必须写 ADR

出现以下**架构级或方向级**决策时，必须新增一条 ADR：

- 引入、替换或移除一个架构组件或机制（如「移除领域事件总线」「自建 DI 容器而非引入框架」）。
- 改变分层依赖方向、Port/Adapter 归属或跨层边界（与 [ddd-architecture.md](ddd-architecture.md) 相关的结构性决策）。
- 选型：模型路由策略、会话/状态后端、序列化/持久化方案、沙箱/隔离方案、鉴权与多租户方案。
- 引入新的一等抽象（如 Trace/Artifact/Skill/MCP Registry、Patch/Edit 抽象、Sandbox Port）。
- 确立或修订跨模块契约、协议、错误传播策略等影响面大的约定。
- 引入重量级第三方依赖或外部系统集成（配合依赖引入判断）。

**不需要**写 ADR 的：局部实现细节、bug 修复、文案调整、单文件重构、纯风格改动。这些走常规提交与 spec 流程即可。

> 判定口诀：**「若三个月后新来的 agent 可能问『当初为什么这么定』，就写一条 ADR。」**

## 存放与命名

- 目录：`docs/adr/`
- 文件名：`NNNN-kebab-case-标题.md`，`NNNN` 为四位递增序号（`0001`、`0002`……），全局唯一、只增不减。
- 模板：见 `docs/adr/0000-template.md`，新增时复制它并取下一个序号。
- 索引：`docs/adr/README.md` 维护「编号 → 标题 → 状态」一览表；新增或改状态时同步更新。

## 状态机（生命周期）

每条 ADR 的 `status` 只在以下取值间流转：

| 状态 | 含义 |
|---|---|
| `Proposed` | 已提出、待评审。 |
| `Accepted` | 已采纳，当前生效。 |
| `Deprecated` | 不再推荐，但尚无替代结论。 |
| `Superseded by ADR-NNNN` | 已被后续 ADR 取代。 |

## 只增不改原则（Immutability）—— 防跑偏的关键

- **已 `Accepted` 的 ADR 正文不得事后改写。** 结论变化时，**新增一条**ADR，并把旧条目状态改为 `Superseded by ADR-NNNN`，新条目 `supersedes: ADR-旧号`。
- 除状态字段与「supersede 双向链接」外，历史 ADR 内容保持不变——它记录的是**当时**的背景与权衡，是审计与「不重复踩坑」的依据。
- 允许的原地编辑仅限：修正笔误、补链接、更新 `status` / `superseded-by` 字段。

## 一致性要求

- ADR 与 spec 的关系：spec（`docs/spec/<feature>/`）描述「做什么、怎么做」，ADR 记录「为什么这么定、否掉了什么」。重大 spec 的关键选型应在 `design.md` 中回链对应 ADR 编号。
- ADR 与 steering 的关系：当某条 ADR 沉淀为长期强制约束时，应在 `docs/steering/` 补充或更新对应规范，并在该规范中回链 ADR。
- 决策若影响架构主题文档（`docs/architecture.md`、`docs/tools.md`、`docs/configuration.md` 等），须同步更新这些文档（见文档—代码同步要求）。

## 模板字段说明

见 `docs/adr/0000-template.md`。核心四段（对齐 Nygard/MADR）：**背景与问题 → 决策 → 后果（含正面/负面/后续影响）→ 备选方案（含未采纳原因）**。「备选方案 + 未采纳原因」是本规范的硬要求——它正是防止后续 agent 重新提出已被否决方案的护栏。
</content>
