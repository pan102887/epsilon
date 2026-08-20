# 变更范围纪律规范（Change Discipline）

coding-agent 长期迭代「跑偏」的头号来源不是代码写得不对，而是**改动范围失控**：顺手重构无关代码、把一个小修复扩张成大重写、绕过既定流程直接改架构。本规范固化「一次改动该有多大、该走什么门」，让每一次 agent 迭代都可追溯、可评审、可回滚。

对标业界最新实践：OpenAI Codex `AGENTS.md`、Claude Code `CLAUDE.md` 约定与 Anthropic building-effective-agents 中的「最小自主、人类监督、surface out-of-scope 而非自行扩张」原则。与 [adr.md](adr.md)、[srp-principle.md](srp-principle.md)、[ddd-architecture.md](ddd-architecture.md)、[doc-sync.md](doc-sync.md) 联合生效。

## 1. 最小改动原则（硬规则）

- **只改达成当前目标所必需的文件与代码行**。不碰与本次目标无关的代码，即使它「看起来可以顺手优化」。
- 禁止在功能改动中夹带无关的重命名、格式化、重排 import、批量风格调整。格式化以 `ruff format` / lint 自动结果为准，不手工对抗。
- 发现范围外的问题（坏味道、疑似 bug、可优化点）时：**记录并上报**，不自行扩张本次改动。可写入 `TODO.md`、新开 spec 或提 issue，由人决定是否单独处理。
- 单次提交/PR 聚焦单一意图；混合意图的改动应拆分为多次。

## 2. 按改动规模选择流程门（Spec-first 纪律）

本项目已有 spec 工作流（`docs/spec/<feature>/`）与 ADR（`docs/adr/`）。改动前先判定规模，选择对应的「门」：

| 改动类型 | 判定信号 | 必经流程 |
|---|---|---|
| **琐碎改动** | 文案/注释/单点 bug 修复、单文件局部调整、不改契约与结构 | 直接改 + 测试 + 遵循 steering；无需 spec |
| **常规特性** | 新增/修改功能，涉及多文件但不改架构方向 | 走 spec（`requirement → design → tasks → 实现 → 评审`）；见 [spec-dev 工作流](../spec/) |
| **架构/方向级决策** | 改分层依赖、Port/Adapter 归属、引入一等抽象、选型、移除机制、引入重依赖 | **先写 ADR**（见 [adr.md](adr.md)），再走 spec 落地 |

> 判定不确定时，**向上取一档**：拿不准算常规还是架构级，就先起草 ADR 让人评审，而不是直接动手。

## 3. 可追溯性

- 非琐碎改动应能追溯到来源：对应的 spec 目录、ADR 编号、TODO 条目或需求描述。
- 提交信息遵循仓库既有约定（Conventional Commits：`feat` / `fix` / `docs` / `style` / `ci` / `refactor` 等），必要时在正文回链 spec 或 ADR 编号。
- 涉及方向性取舍的实现，其 spec `design.md` 应回链对应 ADR。

## 4. 尊重既有约束，不擅自推翻

- 已在 ADR 中 `Accepted` 的决策是既定前提。若认为需要改变，**新增 ADR 走 supersede 流程**（见 [adr.md](adr.md)），禁止在实现中静默偏离已定结论。
- 已在 `docs/steering/` 中固化的规范不得为「省事」绕过；确需调整规范本身，应作为独立、显式的改动提出。
- 迁移/过渡期的临时例外必须在变更说明中记录**原因、范围与清理计划**，不得沉淀为默认结构（与 [ddd-architecture.md](ddd-architecture.md)「允许的例外」一致）。

## 5. 人类监督边界

- 高影响、难以逆转的操作（删除文件、批量重写、改动公共契约、变更安全边界、引入依赖）应显式说明影响面并留出评审点，不在无确认下一次性完成。
- Agent 的默认姿态是**保守 + 可中断**：宁可缩小范围、上报待决，也不越界自主扩张。

## 自检清单（改动前）

1. 我改的每个文件、每一处，都是达成当前目标**必需**的吗？有没有夹带无关改动？
2. 这次改动的规模，对应表 §2 的哪一档？该走的门（spec / ADR）走了吗？
3. 我是否在偏离某条已 `Accepted` 的 ADR 或某条 steering 规范？若是，是否已走显式流程？
4. 发现的范围外问题，是否已上报（TODO/issue/spec）而非顺手改掉？
5. 契约/结构/配置/工具有变化时，相关文档是否需要同步更新（见 [doc-sync.md](doc-sync.md)）？
</content>
