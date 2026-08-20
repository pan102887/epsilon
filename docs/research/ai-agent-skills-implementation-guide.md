# AI 应用中 Skill 的主流实现方式

## 1. 什么是 Agent Skill

Agent Skill 是一种模块化、可复用的能力单元，封装了领域知识和过程性逻辑。与简单的事实检索（"know-that"）或单一工具调用（"do-this"）不同，Skill 运作在"know-how"层面——它包含排序逻辑、验证步骤、条件分支和输出格式化标准，并编码了领域判断力。

在软件工程的类比中，Skill 更接近一个 Service Object 或 Domain Module，而非单个函数调用。

### 1.1 Skill 解决的核心问题：上下文污染

传统做法是将所有指令预加载到 Agent 的系统提示中，这会导致：

- **Token 成本高**：用静态指令填满 200k 上下文窗口代价昂贵
- **推理延迟**：上下文越大，推理越慢
- **中间丢失综合征（Lost-in-the-middle）**：模型无法检索到埋在大量上下文中的指令
- **指令稀释**：指令密度增加时，对单条指令的遵循度下降

RAG 处理的是声明性知识，Function Calling 处理的是离散动作，但两者都无法解决编排问题——即执行需要判断和适应的复杂多步骤工作流的"how-to"。

### 1.2 行业采纳情况

Anthropic 于 2025 年 12 月 18 日将 Agent Skills 规范作为开放标准发布，48 小时内 Microsoft 和 OpenAI 即宣布支持。目前已被以下产品/框架采纳：

- Claude Code、VS Code、GitHub Copilot
- OpenAI Codex、Cursor
- Gemini CLI、Goose、OpenCode
- LangChain、Spring AI、Semantic Kernel
- Kiro

---

## 2. Skill 的标准文件结构

```
my-skill/
├── SKILL.md           # 必需：元数据 + 指令（YAML frontmatter + Markdown 正文）
├── scripts/           # 可选：可执行脚本（Python、Shell 等）
│   ├── process.py
│   └── util.sh
├── references/        # 可选：参考文档（API 规范、策略文档等）
│   └── api-spec.json
└── assets/            # 可选：模板、图片、数据文件
    └── template.docx
```

### 2.1 SKILL.md 的结构

SKILL.md 是 Agent 与能力之间的接口，由 YAML frontmatter（机器可读元数据）和 Markdown 正文（人机可读指令）组成：

```yaml
---
name: generate-receipt
description: >
  Generate a PDF receipt for a transaction.
  Use when asked to create a receipt or proof of purchase.
allowed-tools: python, read_file
version: "1.0.0"
---

# Generate Receipt

This skill creates a formatted PDF receipt based on transaction details.

## Procedure

1. **Extract Details**: Identify customer name, date, items, and total amount
2. **Validation**: Ensure all items sum to the total
3. **Generation**: Run the script to generate the PDF
   ```bash
   python3 scripts/create_pdf.py --customer "${CUSTOMER_NAME}" --items "${ITEMS_JSON}"
   ```
4. **Verification**: Check if the file was created successfully
5. **Output**: Return the path of the generated PDF
```

### 2.2 Frontmatter 字段说明

| 字段 | 必需 | 说明 | 约束 |
|------|------|------|------|
| `name` | 是 | 唯一标识符 | 最长 64 字符，小写，仅允许连字符 |
| `description` | 是 | 语义触发器，用于发现匹配 | 最长 1024 字符 |
| `allowed-tools` | 否 | 安全作用域，限制可用工具 | 逗号分隔的工具列表 |
| `version` | 否 | 版本控制 | 语义化版本号 |
| `license` | 否 | 许可证信息 | 标准许可证标识符 |
| `metadata` | 否 | 附加属性 | 自定义键值对 |

选择 Markdown 作为指令格式是架构层面的决策：LLM 在大量代码和文档仓库上训练，对 Markdown 格式的指令具有很高的解析能力。

---

## 3. 核心架构：渐进式披露（Progressive Disclosure）

渐进式披露是 Skill 架构中最关键的创新，其核心思想是：**只在严格必要时才加载信息**。

### 3.1 三层加载模型

```
┌─────────────────────────────────────────────────────────────────┐
│                    Level 1: Discovery                           │
│                    （发现阶段 — 仅元数据）                        │
│                                                                 │
│  启动时扫描所有 Skill 目录，只读取 YAML frontmatter             │
│  提取 name + description，注入系统提示                           │
│  每个 Skill 约消耗 30-50 tokens                                 │
│  100 个 Skill ≈ 5,000 tokens                                   │
│                                                                 │
│  注入格式示例：                                                  │
│  <available_skills>                                             │
│    <skill>                                                      │
│      <name>generate-receipt</name>                              │
│      <description>Generate a PDF receipt...</description>       │
│    </skill>                                                     │
│  </available_skills>                                            │
├─────────────────────────────────────────────────────────────────┤
│                    Level 2: Activation                          │
│                    （激活阶段 — 加载完整指令）                     │
│                                                                 │
│  当用户请求与某 Skill 的 description 语义匹配时触发              │
│  Agent 调用 Skill Tool，读取完整 SKILL.md 内容                  │
│  将指令注入当前活跃上下文                                        │
│  解决长对话中的"灾难性遗忘"问题                                  │
├─────────────────────────────────────────────────────────────────┤
│                    Level 3: Execution                           │
│                    （执行阶段 — 按需加载资源）                     │
│                                                                 │
│  Agent 按照指令执行工作流                                        │
│  仅在需要时读取 references/ 中的文档                             │
│  仅在需要时执行 scripts/ 中的脚本                                │
│  脚本代码本身不进入上下文，只有输出结果进入                       │
└─────────────────────────────────────────────────────────────────┘
```

### 3.2 动态上下文管理（水合/脱水）

在多步骤会话中，Skill 支持上下文的动态交换：

```
Step 1: 数据清洗 Skill 加载 → 执行 → 卸载
Step 2: 可视化 Skill 加载 → 执行 → 卸载
Step 3: 报告生成 Skill 加载 → 执行 → 卸载
```

每个阶段由聚焦的指令集管控，而非不断膨胀的提示。这种机制：
- 提高 token 使用效率
- 减少 token 压力
- 创建更清晰的推理边界

### 3.3 触发方式

- **显式调用**：用户直接说"运行 SEO 审计 Skill"
- **隐式调用**：Agent 基于用户输入与 Skill description 的语义相似度自动匹配（通常使用 embedding 相似度计算）

Skill 的 description 字段至关重要——它是路由钩子。描述越精确、关键词越丰富，Agent 正确选择的概率越高。

---

## 4. 客户端调用 Skill 的四种主流实现

### 4.1 Meta-Tool 模式（Anthropic / Claude Code 原生方式）

Skill 通过一个"元工具"实现——一个能修改 Agent 自身行为的工具。这是最原始也最直接的实现方式。

**工具定义：**

```yaml
name: Skill
description: >
  Use this tool to load a skill into your context.
  Available skills:
  <available_skills>
    <skill>
      <name>git-helper</name>
      <description>Manage git workflows...</description>
    </skill>
  </available_skills>
parameters:
  type: object
  properties:
    name:
      type: string
      description: The name of the skill to load.
```

**调用流程：**

当模型调用 `Skill(name="git-helper")` 时：

1. 运行时拦截该调用
2. 从磁盘读取对应的 `SKILL.md` 文件
3. 将文件内容作为"工具输出"返回给模型
4. 模型将这个输出作为新的系统指令处理，实现实时"自我重编程"

**特点：**
- 实现最简单，无需额外依赖
- Skill 目录结构即是全部配置
- 适合单机、本地开发场景

### 4.2 SDK 抽象模式（Agent Skills SDK — Microsoft 推出）

将 Skill 的存储（Provider）和消费（Integration）解耦，支持多种存储后端和多种 Agent 框架。

**核心架构：**

```
┌──────────────┐     ┌──────────────────┐     ┌──────────────────┐
│   Providers  │     │   SkillRegistry  │     │   Integrations   │
│              │     │                  │     │                  │
│ - Filesystem │────▶│ - 注册/验证      │────▶│ - LangChain      │
│ - HTTP/S3    │     │ - 生成 Catalog   │     │ - Agent Framework│
│ - Database   │     │ - 渐进式披露 API │     │ - MCP Server     │
│ - Custom     │     │                  │     │                  │
└──────────────┘     └──────────────────┘     └──────────────────┘
```

**代码示例：**

```python
from pathlib import Path
from agentskills_core import SkillRegistry
from agentskills_fs import LocalFileSystemSkillProvider

# 1. 注册 Provider（Skill 可以来自文件系统、HTTP、S3、数据库等）
provider = LocalFileSystemSkillProvider(Path("my-skills"))
registry = SkillRegistry()
await registry.register("incident-response", provider)

# 2. 生成框架原生工具（以 LangChain 为例）
from agentskills_langchain import get_tools, get_tools_usage_instructions

tools = get_tools(registry)
skills_catalog = await registry.get_skills_catalog(format="xml")
tool_usage_instructions = get_tools_usage_instructions()

# 3. 注入系统提示
system_prompt = (
    "You are an SRE assistant.\n\n"
    f"{skills_catalog}\n\n"
    f"{tool_usage_instructions}"
)

agent = create_agent(llm, tools, system_prompt=system_prompt)
```

**SDK 暴露的工具：**
- `get_skill_body`：获取 Skill 主体指令
- `get_skill_reference`：获取参考文档
- `get_skill_script`：获取脚本内容

**包结构：**

| 包名 | 职责 |
|------|------|
| `agentskills-core` | 注册、验证、Catalog 生成、渐进式披露 API |
| `agentskills-fs` | 本地文件系统 Provider |
| `agentskills-http` | HTTP/S3/CDN/Azure Blob Provider |
| `agentskills-langchain` | LangChain 集成 |
| `agentskills-agentframework` | Microsoft Agent Framework 集成 |
| `agentskills-mcp-server` | MCP Server，暴露 Skill 为 MCP 工具 |

**特点：**
- 存储与消费完全解耦
- 支持多种存储后端，可自定义 Provider
- 支持多种 Agent 框架
- 适合企业级、分布式部署场景

### 4.3 Spring AI 工具注册模式（Java 生态）

Spring AI 将 Skill 实现为一组协作的 Tool Bean，与 Spring Boot 生态无缝集成。

**代码示例：**

```java
@SpringBootApplication
public class Application {

    @Bean
    CommandLineRunner demo(ChatClient.Builder chatClientBuilder) {
        return args -> {
            ChatClient chatClient = chatClientBuilder
                // 注册 Skill 工具（扫描 .claude/skills 目录）
                .defaultToolCallbacks(SkillsTool.builder()
                    .addSkillsDirectory(".claude/skills")
                    .build())
                // 注册文件系统工具（用于读取 references/）
                .defaultTools(FileSystemTools.builder().build())
                // 注册 Shell 工具（用于执行 scripts/）
                .defaultTools(ShellTools.builder().build())
                .build();

            String response = chatClient.prompt()
                .user("Review this controller class for best practices")
                .call()
                .content();
        };
    }
}
```

**三个核心工具的协作：**

```
用户请求 → LLM 语义匹配 Skill description
                │
                ▼
         SkillsTool.Skill(name="code-reviewer")
                │
                ▼
         返回 SKILL.md 完整内容 + 基础目录路径
                │
                ▼
         LLM 按指令执行，按需调用：
         ├── FileSystemTools.Read("references/style-guide.md")
         └── ShellTools.Bash("python scripts/analyze.py src/Controller.java")
```

**特点：**
- 与 Spring Boot 生态无缝集成
- LLM 无关（支持 OpenAI、Anthropic、Gemini 等）
- 兼容所有现有 Claude Code Skills
- 支持从 classpath 加载（适合 JAR/WAR 部署）

### 4.4 LangChain Skills 模式

LangChain 将 Skill 实现为一个 `@tool` 装饰的函数，支持多种扩展模式。

**基础实现：**

```python
from langchain.tools import tool
from langchain.agents import create_agent

@tool
def load_skill(skill_name: str) -> str:
    """Load a specialized skill prompt.

    Available skills:
    - write_sql: SQL query writing expert
    - review_legal_doc: Legal document reviewer

    Returns the skill's prompt and context.
    """
    # 从文件/数据库加载 Skill 内容并返回
    skill_path = Path(f"skills/{skill_name}/SKILL.md")
    return skill_path.read_text()

agent = create_agent(
    model="gpt-4.1",
    tools=[load_skill],
    system_prompt=(
        "You are a helpful assistant. "
        "You have access to skills: write_sql, review_legal_doc. "
        "Use load_skill to access them when relevant."
    ),
)
```

**三种扩展模式：**

**a) 动态工具注册**

加载 Skill 时同时注册新工具。例如加载 "database_admin" Skill 可以同时注册 backup、restore、migrate 等数据库专用工具。

**b) 层级 Skill**

Skill 可以定义子 Skill，形成树状结构：

```
data_science/
├── SKILL.md                    # 顶层 Skill
├── sub_skills/
│   ├── pandas_expert/SKILL.md  # 子 Skill
│   ├── visualization/SKILL.md  # 子 Skill
│   └── statistics/SKILL.md     # 子 Skill
```

加载 `data_science` Skill 后，Agent 可以按需进一步加载子 Skill。

**c) 引用感知**

Skill 的 prompt 可以引用其他资源文件的位置，Agent 在需要时自行读取：

```markdown
## References
- Schema definition: `references/schema.sql` — load when writing queries
- Style guide: `references/sql-style.md` — load when reviewing code
```

---

## 5. Skill 中脚本的三种执行模式

### 5.1 模式 A：直接脚本执行

最简单的模式，Skill 指令中直接包含脚本调用命令：

```bash
# PDF 表单字段提取
python scripts/extract_form_field_info.py <input.pdf> <output.json>

# Excel 重新计算
python recalc.py <excel_file> [timeout_seconds]
```

Agent 使用 Shell 工具执行命令，获取输出结果。

### 5.2 模式 B：管道式执行（多脚本串联）

多个脚本按顺序链式执行，用于复杂工作流：

```
Step 1: 检查 PDF 是否有可填写字段
┌─────────────────────────────────────────┐
│ python scripts/check_fillable_fields.py │
│ <file.pdf>                              │
└─────────────────────────────────────────┘
            │
            ├── 有可填写字段 ─────────────────┐
            │                                 │
            └── 无可填写字段                   │
                     │                        │
    ┌────────────────▼──────────────┐  ┌─────▼───────────────────────┐
    │ 非可填写工作流：               │  │ 可填写工作流：               │
    │ 1. 转换 PDF 为 PNG            │  │ 1. 提取字段信息              │
    │ 2. 视觉分析                   │  │ 2. 转换 PDF 为 PNG           │
    │ 3. 创建 fields.json           │  │ 3. 分析用途                  │
    │ 4. 添加注释                   │  │ 4. 创建 field_values.json    │
    └───────────────────────────────┘  │ 5. 填充表单字段              │
                                       └───────────────────────────────┘
```

Agent 在每一步之间进行推理和决策，决定下一步执行哪个脚本。

### 5.3 模式 C：库导入模式

脚本提供类和函数，供 Agent 生成的代码导入使用：

```python
from skills.docx.scripts.document import Document

doc = Document('workspace/unpacked', author="Claude", initials="C")
node = doc["word/document.xml"].get_node(tag="w:del", attrs={"w:id": "1"})
doc.add_comment(start=node, end=node, text="Comment text")
doc.save()
```

**这种架构的核心价值：**
- LLM 提供推理能力（何时运行、如何组合）
- 脚本提供执行可靠性（数学正确性、精确解析、确定性操作）

---

## 6. Skill vs MCP vs Tool vs System Prompt

### 6.1 对比总览

| 维度 | Agent Skills | MCP | Tools | System Prompt |
|------|-------------|-----|-------|---------------|
| 类比 | 大脑 / 操作手册 | 双手 / API 适配器 | 工人 | 身份 / 人格 |
| 存储 | 文件系统（SKILL.md） | 服务进程（本地/远程） | 代码函数 | 提示文本 |
| 数据格式 | 非结构化（Markdown + 代码） | 结构化（JSON Schema） | JSON Schema | 自然语言 |
| 机制 | 上下文注入 | 远程过程调用（JSON-RPC） | 函数调用 | 始终在上下文中 |
| 生命周期 | 按需加载/卸载 | 持久连接 | 任务级调用 | 持久存在 |
| 用途 | 复杂工作流编排、最佳实践 | 数据获取、API 操作 | 原子操作 | 角色、语气、安全策略 |

### 6.2 Skill 与 MCP 的协作模式

MCP 解决连接问题（将 Agent 连接到数据源），Skill 解决编排问题（定义如何使用这些连接）。

**示例：**

一个 MCP Server 提供通用的 `query_database` 工具。一个"财务报告"Skill 包含如何使用该工具生成资产负债表的指令：

> "首先查询当前季度的交易表，然后对借方列求和，然后格式化结果..."

**"将 MCP 编译为 Skill"模式：**

| 方式 | Token 消耗 |
|------|-----------|
| 直接加载 Playwright MCP 工具定义 | 5,000 - 8,000 tokens |
| 用 Skill 包装（~150 tokens 的 Skill + 本地脚本处理 MCP 交互） | ~250 tokens |
| **节省** | **约 98%** |

### 6.3 何时使用哪种方案

- **使用 Skill**：复杂多步骤工作流、标准操作流程、企业知识管理、Token 效率优化、跨平台可移植行为
- **使用 MCP**：直接数据访问、实时连接、标准化工具接口
- **使用 Tool**：确定性的、外部的原子操作（如 API 调用）
- **生产系统**：三者结合使用——MCP 提供工具，Skill 编排使用，Tool 执行操作

---

## 7. 各平台 Skill 发现路径

| 平台 | 默认路径 | 说明 |
|------|---------|------|
| Claude Code | `~/.claude/skills` + `.claude/skills` | 全局 + 项目级 |
| OpenAI Codex | `~/.codex/skills` | 同构结构 |
| GitHub Copilot | `.github/skills` | 仓库级 |
| Goose | `~/.config/goose/skills` | 遵循开放标准 |
| OpenCode | `.opencode/skill` | 全局 + 项目级 |
| Kiro | `.kiro/skills` | 项目级 |

---

## 8. Skill 设计最佳实践

### 8.1 优化语义发现

Skill 的 description 是路由钩子，直接影响匹配准确率：

```yaml
# ❌ 差的描述
description: "Helps with writing."

# ✅ 好的描述
description: >
  Analyzes long-form technical blog posts and generates SEO optimization
  recommendations, including header restructuring and internal linking strategy.
  Use when asked to review, audit, or optimize content for search engines.
```

### 8.2 通过结构化提高确定性

LLM 是概率性的，刚性结构可以缩小解空间：

```markdown
## Procedure

1. Validate inputs
2. If missing fields → request clarification
3. Execute core workflow
4. Produce output in JSON schema:
   - `summary`: string
   - `risks`: array
   - `recommendations`: array
```

### 8.3 避免"上帝 Skill"反模式

不要让一个 Skill 试图解决太多松散相关的问题：

```
# ❌ 反模式：一个 Skill 处理所有事情
data-analysis/
└── SKILL.md  # 包含数据清洗 + 预测 + 可视化 + 报告生成

# ✅ 正确做法：拆分为可链式调用的小 Skill
data-cleaning/SKILL.md
forecasting/SKILL.md
visualization/SKILL.md
report-generation/SKILL.md
```

小 Skill 更容易测试、版本控制和调试。

### 8.4 安全考量

- **`allowed-tools` 字段**：限制 Skill 可访问的工具（如 `allowed-tools: Read, Grep` 会阻止 Write 和 Bash）
- **沙箱执行**：在容器中运行，限制网络访问
- **文件系统作用域**：限制访问范围为项目目录
- **权限模型**：敏感数据使用只读模式

---

## 9. Skill 性能评估

两个核心指标：

- **召回率（Recall）**：Skill 在应该触发时是否触发了？
  - 低召回率 → 改进 description 的关键词和语义覆盖
- **精确率（Precision）**：Skill 触发后是否正确执行了？
  - 低精确率 → 收紧指令结构，增加 few-shot 示例

可以使用 LLM-as-a-Judge 框架进行自动化评估：

```
生成输出 → 传递给评估模型 → 按评分标准打分 → 存储指标用于监控
```

---

## 10. 参考来源

- [Agent Skills 官方规范 — agentskills.io](https://agentskills.io)
- [Microsoft Agent Skills SDK — Tech Community](https://techcommunity.microsoft.com/blog/azuredevcommunityblog/giving-your-ai-agents-reliable-skills-with-the-agent-skills-sdk/4497074)
- [Claude Skills 技术深度解析 — avasdream.com](https://avasdream.com/blog/claude-skills-technical-guide)
- [Spring AI Agent Skills — spring.io](https://spring.io/blog/2026/01/13/spring-ai-generic-agent-skills)
- [LangChain Skills 文档](https://langchain-5e9cc07a.mintlify.app/oss/python/langchain/multi-agent/skills)
- [What Are Agent Skills — DataCamp](https://www.datacamp.com/blog/agent-skills)
- [Agentic Skills — Beyond Tool Use in LLM Agents — arXiv](https://arxiv.org/html/2602.20867v1)
