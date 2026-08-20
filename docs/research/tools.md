# 业界主流 Agent Tools 调研

本文整理主流 AI Agent / Coding Agent 中内置或常见 tools 的类型、能力边界和对本项目的启发。调研范围默认覆盖 Claude Code、OpenAI Codex / ChatGPT Agent、Google Gemini CLI、Cursor / Agent、OpenCode、LangChain / LangGraph / Deep Agents、AutoGen、CrewAI 等产品或框架。

> 说明：本次结论基于已完成的 deep-research 工作流。该工作流抓取 25 个来源，抽取 103 条 claim，验证 25 条主要 claim，确认 22 条，剔除 3 条。高置信证据主要覆盖 Claude Code、Gemini CLI、OpenCode、LangChain / LangGraph。OpenAI Codex / ChatGPT Agent、Cursor、AutoGen、CrewAI 的资料有来源，但具体“内置工具清单”在本轮验证中不够稳定，因此本文对它们更多放在框架 / 生态能力层面，不强行断言具体内置工具名。

## 一、总览结论

主流 AI Agent / Coding Agent 的 tools 大致可以归纳为 8 类：

1. 文件系统工具；
2. 代码检索与代码编辑工具；
3. Shell / 命令执行工具；
4. Web 搜索与网页抓取工具；
5. 任务管理、计划与用户交互工具；
6. Git / PR / 协作工具；
7. 外部系统连接工具；
8. 框架层工具调用协议与动态工具机制。

Claude Code、Gemini CLI、OpenCode 这类 coding agent 更强调“直接操作开发环境”：读取代码库、编辑文件、搜索代码、运行命令、访问网页信息，并在权限确认或沙箱边界内执行。LangChain / LangGraph 等框架则更关注工具抽象：tool 是具备明确输入输出的可调用函数，模型发出结构化 tool call，由运行时执行并把结果作为 ToolMessage 回传。

## 二、文件系统工具

文件系统工具是 coding agent 最基础的一类能力。

| 工具类型 | 常见名称 | 能力 |
| --- | --- | --- |
| 读文件 | `read_file`、`read`、`Read` | 读取指定文件内容，通常支持分页、行范围、截断 |
| 批量读文件 | `read_many_files` | 一次读取多个文件或匹配 glob 的文件 |
| 写文件 | `write_file`、`write`、`Write` | 创建或覆盖文件 |
| 编辑文件 | `edit`、`replace`、`Edit`、`MultiEdit` | 精确字符串替换、局部 patch、多处替换 |
| 列目录 | `list_directory`、`ls`、`LS` | 查看目录结构 |
| 删除文件 | 部分 agent 支持 | 删除文件或目录，通常属于高风险操作 |
| 文件元数据 | `stat` 类能力 | 查询大小、类型、mtime 等 |

### 主流产品表现

Gemini CLI 官方文档明确列出：

- `read_file`
- `read_many_files`
- `write_file`
- `replace`
- `list_directory`

OpenCode 官方文档列出：

- `read`
- `write`
- `edit`

Claude Code 官方概述确认其可读取代码库和编辑文件，但本轮 workflow 对 Claude Code 具体工具名没有保留为高置信结论，因此本文只确认能力，不硬断具体名称。

### 能力特征

成熟 agent 的文件系统工具通常具备：

- 工作区边界限制；
- 路径归一化；
- 大文件截断；
- 分页读取；
- 精确替换；
- patch 失败时返回可修复错误；
- 操作前权限确认；
- 对隐藏文件、敏感文件、`.env`、密钥文件做特殊处理。

## 三、代码检索与搜索工具

Coding agent 需要快速理解代码库，因此搜索工具非常核心。

| 工具类型 | 常见名称 | 能力 |
| --- | --- | --- |
| glob 查找 | `glob`、`Glob` | 按文件名模式查找文件 |
| 正则搜索 | `grep`、`grep_search`、`Grep` | 在文件内容中搜索正则或关键词 |
| 符号搜索 | IDE / LSP 工具 | 查找函数、类、变量、引用 |
| 语义搜索 | Cursor / IDE agent 常见 | 基于代码索引做自然语言检索 |
| 多文件读取 | `read_many_files` | 搜索后批量读上下文 |
| 目录树 | directory tree | 初始展示或按需展示项目结构 |

Gemini CLI 官方文档列出：

- `glob`
- `grep_search`
- `list_directory`
- `read_many_files`

OpenCode 官方文档列出：

- `grep`
- `glob`

Cursor 这类 IDE agent 通常强项在代码索引、符号理解、编辑器上下文，但本轮不对其具体工具名做确定性结论。

主流 coding agent 往往组合使用：

```text
glob 找文件 → grep 找位置 → read_file 读上下文 → edit / replace 修改
```

更高级的 IDE agent 会增加：

- LSP `go to definition`；
- find references；
- hover type info；
- workspace symbol；
- call hierarchy；
- semantic code search；
- diagnostics / lint 问题读取。

## 四、Shell / 命令执行工具

Shell 执行是 coding agent 从“读写代码”升级到“验证代码”的关键能力。

| 工具类型 | 常见名称 | 能力 |
| --- | --- | --- |
| shell 执行 | `bash`、`run_shell_command`、`shell_exec`、`execute` | 执行命令 |
| 后台进程 | background process | 启动 server、watcher、测试进程 |
| 命令输出读取 | task output | 读取 stdout / stderr |
| 终止进程 | stop task | 停止后台任务 |
| 测试 / 构建命令 | shell 上层能力 | 运行 test、lint、build、typecheck |

### 主流产品表现

Gemini CLI 明确提供：

- `run_shell_command`
- 支持后台进程
- 交互式 shell 需要设置和确认

OpenCode 提供：

- `bash`
- 受权限策略控制

Claude Code 官方概述确认其可运行命令，并且权限文档中体现 Bash / git 等命令可被允许或拒绝。

### 能力特征

成熟实现通常会有：

- 默认 ask / confirm；
- allowlist / denylist；
- 工作目录限制；
- 超时；
- 输出截断；
- 后台任务管理；
- 命令风险分级；
- sandbox / container / OS 权限隔离；
- 命令审计；
- 禁止或审批破坏性命令。

Shell 是风险最高的一类工具之一。业界趋势不是单靠黑名单，而是结合 sandbox、权限、审批、审计和最小权限运行。

## 五、Web 搜索与网页抓取工具

很多 agent 会访问外部信息，尤其是依赖文档、API、错误信息、包版本、issue 等。

| 工具类型 | 常见名称 | 能力 |
| --- | --- | --- |
| 网页抓取 | `web_fetch`、`WebFetch`、`webfetch` | 获取 URL 内容并转成模型可读文本 |
| Web 搜索 | `web_search`、`WebSearch`、`google_web_search` | 搜索网页 |
| 文档检索 | Context7、官方 docs connector | 查询库 / 框架最新文档 |
| URL 内容摘要 | fetch + summarize | 抓取后提炼要点 |
| 多源调研 | research workflow | 搜索、抓取、交叉验证、引用 |

### 主流产品表现

Claude Code 官方工具参考列出：

- `WebFetch`
- `WebSearch`

Gemini CLI 文档列出：

- `web_fetch`
- `google_web_search`

OpenCode 文档列出：

- `webfetch`
- `websearch`

### 能力特征

Web 工具常见边界：

- 可能受地区、provider、配置影响；
- 搜索结果可能不稳定；
- 网页内容可能有 prompt injection；
- 抓取内容进入模型上下文前需要过滤；
- 对私有 URL 或带凭据 URL 需要谨慎处理；
- 对官方文档应优先使用可信来源。

## 六、任务管理、计划与用户交互工具

长任务需要拆解、跟踪、审批和中途询问用户。

| 工具类型 | 常见名称 | 能力 |
| --- | --- | --- |
| todo 管理 | `todowrite`、task tracker | 创建、更新、完成任务 |
| 计划模式 | `enter_plan_mode`、`exit_plan_mode` | 只读规划，等待用户批准 |
| 用户提问 | `question`、`AskUserQuestion` | 在关键分歧点询问用户 |
| 技能加载 | `skill` | 加载专项能力文档 |
| 进度可视化 | tracker visualize | 展示任务状态 |
| 后台工作流 | workflow | 多 agent / 多阶段编排 |

### 主流产品表现

Gemini CLI 文档列出：

- `tracker_create_task`
- `tracker_update_task`
- `tracker_list_tasks`
- `tracker_visualize`
- `enter_plan_mode`
- `exit_plan_mode`

OpenCode 文档列出：

- `todowrite`
- `question`
- `skill`

Claude Code 在本运行环境中也具备任务管理、计划、技能和工作流工具，但本轮 web workflow 对 Claude Code 公开文档中的具体 Task / TodoWrite 名称没有保留为高置信外部结论，因此这里不作为外部调研结论展开。

### 能力特征

这类工具解决的是 agent 的执行可靠性：

- 避免长任务失控；
- 让用户审批高风险计划；
- 保持任务状态透明；
- 在需求不明确时主动停下；
- 为多阶段工作流提供状态边界。

## 七、Git / PR / 协作工具

Coding agent 的高阶能力是把代码变更纳入工程协作流程。

| 工具类型 | 能力 |
| --- | --- |
| git status / diff | 查看当前变更 |
| git add / commit | 暂存和提交 |
| branch 管理 | 创建、切换分支 |
| PR 创建 | 调用 GitHub / GitLab CLI 或 API |
| review | 读取 PR diff、评论、修复 |
| issue 集成 | 读取 issue、关联任务 |
| CI 集成 | 查看运行结果、失败日志 |

Claude Code 官方概述明确提到其可以协作处理 git 工作流，包括：

- staging changes；
- writing commit messages；
- creating branches；
- opening pull requests。

Claude Code 也可通过 MCP 连接外部工具，例如 Google Drive、Jira、Slack 和自定义工具。

Git / PR 工具通常不是单一工具，而是一组命令或外部连接器能力：

```text
读取 diff → 分析变更 → 运行测试 → 创建 commit → 开 PR → 处理 review
```

成熟实现会结合：

- 权限确认；
- 禁止自动 push，除非用户明确授权；
- PR body 模板；
- CI 状态读取；
- review comment 解析；
- 变更范围限制。

## 八、外部系统连接工具

Agent 要进入真实业务流程，必须连接外部系统。

| 系统 | 能力 |
| --- | --- |
| MCP server | 标准化外部工具与资源接入 |
| Jira / Linear | 读取需求、更新任务 |
| Slack / Lark / Teams | 发消息、查消息、协作 |
| Google Drive / Docs | 读写文档 |
| GitHub / GitLab | issue、PR、CI |
| 数据库 | 查询、分析、迁移辅助 |
| 浏览器 | 打开网页、操作页面、截图 |
| 云服务 | 部署、日志、监控、对象存储 |
| 知识库 / RAG | 检索内部知识 |

Claude Code 官方文档明确支持 MCP，用于连接外部数据源和工具。

LangChain / LangGraph 这类框架支持开发者把任意函数、API、数据库、搜索工具包装为 tool。

CrewAI 和 AutoGen 等框架也有工具生态，但本轮没有对其具体内置工具清单做强断言。

## 九、框架层工具调用协议

对 LangChain / LangGraph / AutoGen / CrewAI 这类框架来说，重点不只是“有哪些内置工具”，而是“如何定义工具”。

### LangChain / LangGraph 的典型机制

工具是具备清晰输入输出的 callable，例如：

- 天气 API；
- 计算器；
- Web 搜索；
- 数据库查询；
- 自定义业务函数。

模型输出结构化 tool call，运行时执行工具，然后把结果作为 `ToolMessage` 回传给模型。

典型流程：

```text
User message
  → model decides tool call
  → runtime executes tool
  → ToolMessage returned
  → model continues reasoning
```

### 常见框架能力

| 能力 | 说明 |
| --- | --- |
| tool schema | 根据函数签名、docstring、schema 定义工具参数 |
| structured tool call | 模型以结构化格式请求工具调用 |
| tool result message | 工具结果回传给模型 |
| dynamic tool selection | 运行时改变可用工具集合 |
| middleware | 工具前后处理、审批、日志、脱敏 |
| tool error handling | 错误返回给模型或触发重试 |
| parallel tool calls | 并行执行多个工具 |
| human-in-the-loop | 高风险工具审批 |
| tracing | 记录 tool call 链路 |
| sandbox backend | 将执行工具接到隔离环境 |

## 十、横向分类总结

### 10.1 按工具作用对象分类

| 类别 | 工具能力 |
| --- | --- |
| 文件 | 读、写、编辑、列目录、批量读取 |
| 代码 | glob、grep、符号搜索、LSP、语义搜索 |
| 命令 | shell、测试、构建、后台进程 |
| 网络 | web fetch、web search、HTTP 请求 |
| 任务 | todo、计划模式、问题澄清 |
| 协作 | git、PR、issue、CI |
| 外部系统 | MCP、SaaS、数据库、云服务 |
| 执行环境 | sandbox、容器、远程 devbox |
| 治理 | 权限、审批、审计、追踪、限流 |

### 10.2 按风险等级分类

| 风险等级 | 工具 |
| --- | --- |
| 低风险 | list、read、grep、glob |
| 中风险 | web fetch、web search、read_many_files |
| 高风险 | write、edit、git commit、HTTP mutation |
| critical | shell、python execute、delete、deploy、credentials、external side effects |

### 10.3 按成熟度分类

| 成熟度 | 能力 |
| --- | --- |
| 基础 agent | 文件读写、grep、shell |
| 工程 agent | 测试、构建、git、PR |
| 产品化 agent | 权限、HITL、审计、任务跟踪 |
| 企业 agent | MCP、SSO、策略、日志、配额 |
| 安全 coding agent | sandbox、egress policy、secrets 外置、artifact 审查 |

## 十一、对本项目的启发

### 11.1 本项目已有或接近已有

结合当前项目文档和实现，本项目已有或接近已有：

- 文件读取：`read_file`
- 文件写入：`write_file`
- 文件编辑：`edit_file`
- 目录列表：`list_dir`
- Shell 执行：`shell_exec`，默认关闭
- Python 执行：`python_exec`，默认关闭
- HTTP / Web：`http_request`、`web_fetch`、`web_search`
- 多 Agent：`delegate_to_agent`、`handoff_to_agent`、`delegate_parallel`
- 工具权限隔离：`ScopedToolRegistry`
- HITL 审批配置
- Workspace confinement

### 11.2 本项目相对主流还可补齐

1. `glob` 文件名匹配工具；
2. `grep` 内容搜索工具；
3. 批量读取 `read_many_files`；
4. LSP / symbol search 工具；
5. Git 工具封装，而不是只依赖 shell；
6. PR / issue / CI 集成工具；
7. 浏览器 / 页面操作工具；
8. 结构化 artifact 工具；
9. Sandbox backend 工具；
10. 动态工具选择与工具治理策略；
11. 工具审计与风险分级 UI；
12. 外部 MCP / SaaS connector 更完整生态。

### 11.3 建议优先补齐的 tools

如果目标是追赶主流 coding agent，建议按以下优先级补齐：

1. **`glob`**：按路径模式找文件，成本低、收益高；
2. **`grep`**：正则搜索代码内容，是理解代码库的核心工具；
3. **`read_many_files`**：批量读取候选上下文，配合 glob / grep 使用；
4. **`git_status` / `git_diff`**：让 agent 不必通过 shell 获取变更，更安全、更结构化；
5. **`git_apply_patch` 或结构化 patch 工具**：替代大范围 shell 修改，便于审计；
6. **LSP 工具**：支持 `go_to_definition`、`find_references`、`workspace_symbol`、`hover` 等；
7. **任务计划工具**：类似 todo / task tracker，可接入后台 Run 和 workflow；
8. **sandbox execute backend**：替代当前本地 shell / python 子进程，对安全提升最大；
9. **artifact 工具**：生成报告、patch、测试日志、构建产物，供用户审查和下载；
10. **MCP connector 管理工具**：动态发现外部工具、授权、调用和审计。

## 十二、来源

本轮调研使用 deep-research 工作流，抓取并验证多个来源。高置信来源包括：

- Claude Code overview：<https://code.claude.com/docs/en/overview>
- Claude Code tools reference：<https://code.claude.com/docs/en/tools-reference>
- Claude Code permissions：<https://code.claude.com/docs/en/permissions>
- Claude Code MCP：<https://code.claude.com/docs/en/mcp>
- Gemini CLI tools docs：<https://google-gemini.github.io/gemini-cli/docs/tools/>
- Gemini CLI reference tools：<https://github.com/google-gemini/gemini-cli/blob/main/docs/reference/tools.md>
- Gemini CLI shell tool：<https://google-gemini.github.io/gemini-cli/docs/tools/shell.html>
- Gemini CLI plan mode：<https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/plan-mode.md>
- OpenCode tools：<https://opencode.ai/docs/tools/>
- OpenCode permissions：<https://opencode.ai/docs/permissions>
- LangChain tools：<https://docs.langchain.com/oss/python/langchain/tools>
- LangChain tool calling：<https://docs.langchain.com/oss/python/langchain/frontend/tool-calling>
- LangChain messages：<https://docs.langchain.com/oss/python/langchain/messages>
- LangChain models：<https://docs.langchain.com/oss/python/langchain/models>
- LangGraph dynamic tool calling announcement：<https://changelog.langchain.com/announcements/dynamic-tool-calling-in-langgraph-agents>
- MCP security best practices：<https://modelcontextprotocol.io/docs/tutorials/security/security_best_practices>

## 十三、注意事项

- 工具清单是时间敏感信息，不同产品版本可能变动较快；
- 官方文档描述的工具能力可能受到配置、权限、地区、企业策略、provider 或 sandbox 策略影响；
- “框架支持某类 tool”不等于“产品默认内置某个 tool”；
- Shell / Python / deploy / credentials / external side effects 等工具必须被视为高风险或 critical 风险；
- Web fetch / search 结果可能包含 prompt injection，进入模型上下文前应做过滤和可信源优先级控制。
