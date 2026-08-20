# AI Agent Skill 实现方案业界调研

> 调研主题：业界主流 Agent 应用中的 skill / action / tool / connector 实现方案，覆盖通用 Agent 与 Coding Agent。
>
> 调研范围：Claude Agent SDK / Managed Agents、Devin、MCP Agent Skills、OpenAI GPT Actions、Amazon Bedrock Agents、Microsoft Copilot Studio、Cursor Agent、ToolRegistry 等。
>
> 说明：本报告基于已完成的 deep-research 工作流。该工作流分解 5 个研究角度，抓取 23 个来源，抽取 104 条候选 claim，验证 Top 25 条主要 claim，确认 24 条、剔除 1 条。结论优先采用官方文档和经对抗验证的高置信来源。

## 1. 总体结论

业界主流 Agent 的 skill / 能力扩展实现，大致可分为三类：

1. **文件系统 / 仓库内的可移植指令包**：以 `SKILL.md` 为核心，将任务流程、触发条件、示例、参考资料打包成可版本化制品。
2. **API / Tool 调用编排**：以 JSON Schema、OpenAPI、Action Group、Tool Registry 等方式，把自然语言意图映射为结构化工具调用。
3. **企业平台的 Connector / Knowledge / Workflow 建模**：将 Agent 能力拆成连接器、知识源、业务流程、权限和审计等平台能力。

对 **Coding Agent** 来说，最值得借鉴的是第一类和第二类的组合：

```text
Skill = 如何完成某类任务的专业流程与上下文
Tool  = Agent 可执行的具体动作
Policy = 何时允许执行、是否需要确认、如何审计
```

也就是说，成熟 Coding Agent 不应只做“工具调用”，而应将工程方法沉淀成可发现、可版本化、可按需加载的 skill，同时用工具系统提供文件读写、代码搜索、终端执行、测试验证、Web 检索和外部系统集成。

## 2. 主流实现范式对比

| 范式 | 代表产品 / 框架 | 核心机制 | 适用场景 |
|---|---|---|---|
| 文件型 Skill 指令包 | Claude Agent SDK、Devin、MCP Agent Skills | `SKILL.md`、frontmatter、Markdown 指令、`references/` 支撑材料、按需加载 | Coding Agent、项目流程复用、团队规范沉淀 |
| API / Action Schema | OpenAI GPT Actions、Amazon Bedrock Agents | OpenAPI / JSON Schema / Action Group，将自然语言请求转成 API 调用 | 通用 Agent、SaaS 集成、业务动作自动化 |
| 工具基础设施层 | Cursor Agent、ToolRegistry、LangChain / LangGraph 工具生态 | Tool registry、schema generation、执行后端、权限策略、可观测性 | 自建 Agent 平台、跨模型工具生态 |
| 企业 Connector 平台 | Microsoft Copilot Studio、Microsoft 365 declarative agents | Topics、Tools、Knowledge sources、Connectors、Declarative Agents | 企业知识问答、流程自动化、权限治理 |

## 3. 文件型 Skill：Coding Agent 的主流方向

### 3.1 共同形态

Claude Agent SDK、Devin、MCP Agent Skills 都采用或支持以 `SKILL.md` 为核心的文件型 skill：

```text
skills/
  code-review/
    SKILL.md
    references/
      checklist.md
      examples.md
  mcp-server-dev/
    SKILL.md
    references/
      auth-flows.md
      tool-design.md
      manifest-schema.md
```

典型 `SKILL.md` 结构如下：

```markdown
---
name: code-review
description: Use when reviewing code changes for correctness, security, and maintainability.
---

# Code Review Skill

## Workflow

1. Identify changed files.
2. Understand intent.
3. Review correctness first.
4. Verify each finding against code evidence.
5. Report actionable findings only.
```

这种设计的关键价值在于：

- **可版本化**：随代码仓库提交、审查、回滚。
- **可移植**：不同 Agent 或不同项目可复用同一类工作流。
- **上下文节省**：常驻 metadata，完整内容按需加载。
- **更贴近工程实践**：适合沉淀 TDD、代码评审、发布、迁移、MCP 开发等流程。

### 3.2 Claude Agent SDK

Claude Agent SDK 文档明确说明，Skills 是文件系统制品，而不是纯编程式注册 API。Skill 通常是包含 `SKILL.md` 的目录，`SKILL.md` 使用 YAML frontmatter 和 Markdown 正文描述 skill 的名称、触发语义和执行流程。

其设计重点包括：

- 启动时发现 skill metadata；
- 只在触发时加载完整 skill 内容；
- 可通过配置控制启用哪些 skill；
- 适合将复杂任务流程从主系统提示词中拆出来。

这说明 Claude 生态中的 skill 更接近“可发现的专业指令包”，而不是简单函数调用。

### 3.3 Devin Skills

Devin 官方文档同样推荐将 skill 提交到仓库中，典型路径为：

```text
.agents/skills/<skill-name>/SKILL.md
```

Devin 支持自动激活相关 skill，也支持用户显式指定 skill。其设计反映了 Coding Agent 的一个重要趋势：

> Skill 应跟随项目和团队流程一起版本化，而不是只存在于某个产品的全局配置里。

这对本项目很有参考价值：如果要沉淀“本仓库如何新增工具”“如何遵循 DDD 规范”“如何做 Agent Trace 调试”等经验，仓库内 skill 比纯口头约定更稳定。

### 3.4 MCP Agent Skills

MCP 官方的 Agent Skills 文档将 skills 定义为可移植 instruction sets。MCP server development 的参考 skill 会编码：

- deployment model；
- tool patterns；
- authentication flows；
- scaffold 决策；
- widget templates；
- manifest schemas。

这说明 skill 不只是“提示词片段”，也可以是复合工程流程的封装：先询问用例，再选择部署模型，再生成脚手架，再补充认证和测试。

## 4. API / Action Schema：通用 Agent 的主流方案

### 4.1 OpenAI GPT Actions

OpenAI GPT Actions 是通用 Agent API 扩展的典型代表。其核心机制是：

```text
用户自然语言请求
  → 选择相关 Action
  → 生成 JSON 参数
  → 调用 REST API
  → 将 API 结果返回给模型继续生成
```

GPT Actions 更关注“外部 API 调用”，适合：

- 查询 CRM；
- 创建工单；
- 调用内部 REST 服务；
- 操作 SaaS 系统；
- 将自然语言转成结构化业务动作。

与文件型 skill 相比，Action Schema 更像是工具能力描述，不承担复杂工程流程沉淀。

### 4.2 Amazon Bedrock Agents Action Groups

Amazon Bedrock Agents 使用 **Action Group** 抽象 Agent 可执行动作。Action Group 定义 Agent 可以帮助用户执行的 actions，并决定：

- Agent 如何收集用户参数；
- 参数和信息交给哪里处理；
- 执行后端是 Lambda、Return control、用户确认还是其他路径；
- Agent 如何围绕这些动作完成任务。

Bedrock 的实现更偏向云平台中的可执行动作编排，而不是仓库内文件型 skill。

## 5. 企业平台：Connector + Knowledge + Workflow

Microsoft Copilot Studio 代表企业 Agent 平台范式。它不会把所有能力都叫作 skill，而是拆成多个平台概念：

| 能力 | 作用 |
|---|---|
| Topics | 对话流程和业务主题 |
| Tools | 可执行动作 |
| Knowledge sources | 知识源和 grounding 数据 |
| Connectors | 外部系统、企业数据源、API 连接 |
| Other agents | Agent 间协作或委派 |
| Declarative agents | Microsoft 365 Copilot 中的声明式 Agent 配置 |

这种方案的重点是企业治理：

- 企业系统接入；
- 权限隔离；
- 数据源 grounding；
- 审计和合规；
- 多租户和组织级发布。

对自研平台的启发是：如果 Agent 面向企业业务，不应把所有扩展能力都塞进 prompt 或 skill 文件；连接器、知识源、权限和工作流应作为平台级对象单独建模。

## 6. Coding Agent 的 Skill 与 Tool 边界

Coding Agent 与通用对话 Agent 的关键区别是：工具不是附加能力，而是核心执行原语。

Cursor 官方文档将 tools 称为 Agent 的 building blocks。典型 Coding Agent 工具包括：

- 文件读取；
- 文件编辑；
- 正则 / glob 搜索；
- 语义代码搜索；
- 终端命令执行；
- 测试 / lint / build；
- Web 搜索；
- Git / PR / Issue 协作。

因此，Coding Agent 的 skill 更适合表达“如何使用这些工具完成某类工程任务”，而不是直接替代工具。

例如，`code-review` skill 可以规定：

```text
1. 先读取 diff 和相关上下文。
2. 按 correctness / security / reliability 分类审查。
3. 对每个发现做反证检查。
4. 只报告有文件和行号证据的问题。
5. 不报告纯风格建议，除非影响可维护性或正确性。
```

而真正的文件读取、搜索、测试运行仍由工具系统完成。

## 7. ToolRegistry 与工具基础设施趋势

ToolRegistry 体现了独立工具基础设施层的趋势。它将工具注册、schema 生成、并发执行、权限、集成和可观测性封装成面向 Agent 开发者的基础设施。

其能力覆盖：

- tool registration；
- schema generation；
- concurrent execution；
- message building；
- namespaces；
- permission policies；
- executor backends；
- MCP / OpenAPI / LangChain / provider integrations；
- discovery；
- observability；
- admin tooling。

这说明 skill / tool 能力正在从单个产品内部实现，逐渐演变为跨模型、跨协议、可治理的平台能力。

## 8. Skill、Tool、Action、Connector 的概念边界

为了避免架构概念混淆，建议在自研 Agent 平台中明确区分以下对象：

| 概念 | 本质 | 典型内容 | 是否直接执行外部动作 |
|---|---|---|---|
| Skill | 任务专用指令包 / 专业流程 | 步骤、规则、示例、检查清单、参考资料 | 不一定 |
| Tool | 可执行能力 | 读文件、写文件、搜索、调用函数、运行测试 | 是 |
| Action | 面向 API 的工具调用 | OpenAPI schema、REST endpoint、参数定义 | 是 |
| Connector | 企业系统连接层 | SaaS、数据库、知识库、权限映射 | 通常是 |
| Workflow | 多步骤业务编排 | 条件、审批、任务流、自动化 | 通常是 |
| Policy | 权限与治理规则 | allow / deny / confirm、审计、作用域 | 间接控制 |

推荐建模关系：

```text
Agent
  ├── Skills：如何完成任务
  ├── Tools：能执行哪些动作
  ├── Connectors：能访问哪些系统
  ├── Policies：哪些动作允许自动执行
  └── Memory / Knowledge：能参考哪些长期上下文
```

## 9. 对本项目的推荐实现方案

结合本项目是 FastAPI + DDD + Agent Loop + Tool Registry / Session / Workspace 架构，建议未来如要实现 skill 体系，可采用以下分层设计。

### 9.1 Skill Package

```text
skills/
  <skill-name>/
    SKILL.md
    references/
      *.md
    examples/
      *
    evals/
      *.json
```

`SKILL.md` 建议包含：

```yaml
---
name: code-review
description: Use when reviewing code changes for correctness and maintainability.
version: 1
triggers:
  - review diff
  - review PR
  - code review
required_tools:
  - read
  - grep
  - bash
permissions:
  bash: confirm
---
```

正文包含：

- 任务背景；
- 使用条件；
- 不适用条件；
- 工作流步骤；
- 工具使用策略；
- 输出格式；
- 验证标准；
- 常见失败模式。

### 9.2 Skill Metadata Index

启动或热加载时，只读取 skill metadata，构建轻量索引：

```text
SkillMetadata
  - name
  - description
  - triggers
  - version
  - required_tools
  - path
```

Agent Loop 中先基于用户请求和当前任务上下文选择候选 skill，再按需读取完整 `SKILL.md` 和 references。

### 9.3 Skill Loader Port

按照项目 DDD 规范，可在 domain 层定义 Port，在 infrastructure 层实现文件系统 Adapter：

```text
domain/agent/ports.py
  SkillRepository Protocol

infrastructure/skills/filesystem_skill_repository.py
  FileSystemSkillRepository
```

领域层只关心 skill 的抽象模型和读取接口，不直接依赖文件系统细节。

### 9.4 Tool / Skill 绑定

Skill 不应直接执行动作，而应声明推荐或必需工具：

```text
Skill.required_tools = ["read", "grep", "bash"]
```

运行时由 Tool Registry 与权限策略决定：

- 工具是否存在；
- 当前 workspace 是否允许使用；
- 是否需要用户确认；
- 输出如何进入上下文；
- 是否记录审计日志。

### 9.5 Skill 触发策略

推荐采用多级触发：

1. **显式触发**：用户指定 `/skill-name` 或 `@skill`。
2. **规则触发**：根据 `triggers`、关键词、任务类型匹配。
3. **模型选择**：将候选 skill metadata 给模型，由模型选择是否加载。
4. **人工约束**：高风险 skill 或高权限工具需要确认。

冲突策略建议：

- 显式触发优先于自动触发；
- 多个 skill 同时匹配时，允许组合，但必须限制总上下文；
- 互斥 skill 需要在 metadata 中声明；
- 默认不加载完整正文，只加载被选中的 skill。

## 10. 风险与治理

### 10.1 Skill Injection

如果 skill 文件或 references 可被不可信输入修改，Agent 可能被持久化 prompt injection 劫持。需要区分：

- 可信仓库内 skill；
- 用户上传 skill；
- 网页 / 文档中提到的伪 skill；
- 模型运行中生成的临时指令。

建议：只有明确注册且通过权限边界的 skill 才能作为系统级或开发者级指令加载。

### 10.2 权限提升

Skill 不应自动授予工具权限。例如，一个 `release` skill 可以说明发布流程，但不能绕过发布工具的确认策略。权限应绑定到 tool / connector / workspace policy，而不是绑定到 skill 文本。

### 10.3 上下文膨胀

将所有 skill 全量放入系统提示词会导致上下文膨胀、缓存失效和模型注意力下降。应采用：

- metadata 常驻；
- 正文按需加载；
- references 二次按需读取；
- 长 references 通过摘要或检索加载。

### 10.4 版本漂移

长任务中如果 skill 文件变更，可能导致同一 session 前后行为不一致。建议：

- session 启动时固定 skill version；
- trace 中记录加载的 skill 名称、版本、路径和摘要；
- 重要任务保留 skill snapshot。

### 10.5 评测缺失

关键 skill 需要配套 eval，例如：

- 是否在应触发时触发；
- 是否在不应触发时过度触发；
- 是否遗漏权限确认；
- 是否能产出符合格式的结果；
- 是否会调用不必要的高风险工具。

## 11. 建议的最小落地路径

如果本项目后续要引入 skill 机制，建议分三阶段推进。

### 阶段一：文档型 Skill

目标是低风险沉淀流程：

- 新增 `skills/` 或 `docs/skills/` 目录；
- 定义 `SKILL.md` 格式；
- 先沉淀 2-3 个内部流程 skill：
  - `code-review`；
  - `tool-development`；
  - `ddd-feature-implementation`。

此阶段可先不接入 Agent Loop，只作为人工和 Agent 共同参考的规范文档。

### 阶段二：Skill Metadata 与按需加载

目标是让 Agent Loop 能识别和加载 skill：

- 增加 SkillRepository Port；
- 文件系统 Adapter 读取 `SKILL.md` metadata；
- 在 Agent Loop prompt 构建阶段注入候选 skill metadata；
- 被选中后再加载完整正文。

### 阶段三：Skill + Tool + Policy 联动

目标是形成生产级能力：

- skill 声明 required / optional tools；
- Tool Registry 校验工具可用性；
- 权限策略决定是否自动执行；
- Trace 记录 skill 加载和工具使用链路；
- 引入 skill eval，持续评估触发和执行质量。

## 12. 后续开放问题

1. 不同平台的 skill 触发策略在自动触发、用户显式指定、工具选择之间如何排序和冲突消解？
2. `SKILL.md` 是否会在多 Agent、多仓库、大型 monorepo 场景下形成事实上的开放互操作格式？
3. 企业 Connector、Knowledge grounding、Action execution 在权限审计、数据泄露防护、租户隔离方面如何横向比较？
4. Coding Agent 的 tool-call 数量、上下文加载、长任务续跑和 skill 生命周期管理会如何影响可靠性？
5. Skill 与 MCP、Tool Registry、Managed Agents 的边界是否会逐渐融合？

## 13. 参考来源

- [Claude Agent SDK Skills](https://code.claude.com/docs/en/agent-sdk/skills)
- [Claude Managed Agents Skills](https://platform.claude.com/docs/en/managed-agents/skills.md)
- [Claude Managed Agents Tools & Skills](https://platform.claude.com/docs/en/managed-agents/tools.md)
- [Devin Skills](https://docs.devin.ai/product-guides/skills)
- [MCP Agent Skills](https://modelcontextprotocol.io/docs/develop/build-with-agent-skills)
- [MCP Server Development Skills Plugin](https://claude.com/plugins/mcp-server-dev)
- [OpenAI GPT Actions Introduction](https://platform.openai.com/docs/actions/introduction)
- [OpenAI GPT Actions Getting Started](https://platform.openai.com/docs/actions/getting-started)
- [OpenAI Help: Configuring Actions in GPTs](https://help.openai.com/en/articles/9442513-configuring-actions-in-gpts)
- [Amazon Bedrock Agents Action Groups](https://docs.aws.amazon.com/bedrock/latest/userguide/agents-action-create.html)
- [Amazon Bedrock Action Handling](https://docs.aws.amazon.com/bedrock/latest/userguide/action-handle.html)
- [Cursor Agent Tools](https://docs.cursor.com/en/agent/tools)
- [Cursor Search Tools](https://cursor.com/docs/agent/tools/search)
- [Microsoft Copilot Studio Connectors](https://learn.microsoft.com/en-us/microsoft-copilot-studio/copilot-connectors-in-copilot-studio)
- [Microsoft 365 Copilot Declarative Agents](https://learn.microsoft.com/en-us/microsoft-365-copilot/extensibility/overview-declarative-agent)
- [ToolRegistry Architecture](https://toolregistry.readthedocs.io/en/latest/architecture/overview/)
- [ToolRegistry GitHub Repository](https://github.com/Oaklight/ToolRegistry)
