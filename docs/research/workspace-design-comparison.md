# AI Coding Agent Workspace 设计对比研究

> 调研对象：OpenCode、Gemini CLI
> 调研日期：2026-04-20
> 目的：分析主流 AI coding agent 对 workspace 概念的设计思路，为后续功能设计提供参考

## 1. 核心问题

所有 AI coding agent 在 workspace 设计上都需要解决以下问题：

- **项目边界检测**：如何确定 agent 的工作范围（哪些文件可读写、哪些目录属于"项目"）
- **配置分层与合并**：全局配置、用户配置、项目配置之间的优先级和合并策略
- **上下文管理**：如何为 LLM 提供项目级指令和背景信息
- **多目录/多项目支持**：monorepo 或跨项目场景下的工作区管理
- **企业管控**：组织级别的配置强制和安全策略

---

## 2. OpenCode 的 Workspace 设计

### 2.1 项目根检测

OpenCode 启动时从当前工作目录（CWD）向上遍历目录树，找到最近的 `.git` 目录所在位置作为项目根。配置文件 `opencode.json` 也沿这个路径搜索。

本质上是**单项目模型**——一个 git repo 就是一个 workspace。

### 2.2 配置分层体系

优先级从低到高：

| 层级 | 位置 | 说明 |
|------|------|------|
| Remote config | `.well-known/opencode` 端点 | 组织级默认配置，认证后自动拉取 |
| Global config | `~/.config/opencode/opencode.json` | 用户全局偏好（provider、model、权限等） |
| Custom config | `OPENCODE_CONFIG` 环境变量指定路径 | 自定义覆盖 |
| Project config | 项目根的 `opencode.json` | 项目级配置，可提交到 Git |
| `.opencode` 目录 | `.opencode/agents/`、`.opencode/commands/` 等 | agents、commands、plugins |
| Inline config | `OPENCODE_CONFIG_CONTENT` 环境变量 | 运行时覆盖 |
| Managed config | `/Library/Application Support/opencode/`（macOS）等 | 管理员控制，不可被用户覆盖 |
| macOS MDM | `.mobileconfig` via MDM | 最高优先级，企业强制策略 |

合并策略：**配置文件是合并而非替换**。后加载的配置只覆盖冲突的 key，非冲突的设置会保留。

### 2.3 指令上下文（Rules）

- **项目级**：项目根放置 `AGENTS.md`，仅在该目录及子目录下生效
- **全局级**：`~/.config/opencode/AGENTS.md`，跨所有 session 生效
- **Claude Code 兼容**：支持 `CLAUDE.md` 和 `~/.claude/CLAUDE.md` 作为 fallback（可通过环境变量禁用）
- **额外指令引用**：在 `opencode.json` 的 `instructions` 字段中指定文件路径、glob 模式或远程 URL

```json
{
  "instructions": [
    "CONTRIBUTING.md",
    "docs/guidelines.md",
    ".cursor/rules/*.md",
    "https://raw.githubusercontent.com/my-org/shared-rules/main/style.md"
  ]
}
```

优先级规则：
1. 从当前目录向上遍历查找 `AGENTS.md`（或 `CLAUDE.md`）
2. 全局 `~/.config/opencode/AGENTS.md`
3. Claude Code 兼容 `~/.claude/CLAUDE.md`
4. 同类文件中先匹配到的生效（如同时有 `AGENTS.md` 和 `CLAUDE.md`，只用 `AGENTS.md`）

### 2.4 多目录支持

目前 OpenCode 对多 workspace / 多目录的支持较弱：

- VSCode 扩展的 multi-root workspace 支持仍在讨论中（GitHub Issue #15802、#15796）
- 没有类似 `--include-directories` 的机制
- Session 管理与项目根绑定，跨子目录的 session 聚合也是社区诉求（Issue #1877）

### 2.5 其他 Workspace 相关特性

- **Snapshot**：基于内部 git 仓库跟踪文件变更，支持 undo/revert。大型仓库可通过 `"snapshot": false` 禁用
- **Watcher**：文件监听器，支持 glob 模式的 ignore 配置
- **Formatter**：可配置代码格式化工具，支持自定义命令

---

## 3. Gemini CLI 的 Workspace 设计

### 3.1 项目根检测

Gemini CLI 通过 `.git` 目录或用户 home 目录来确定项目边界。上下文文件的向上遍历会在遇到 `.git` 目录或 home 目录时停止。

还支持通过 `context.memoryBoundaryMarkers` 配置自定义边界标记，控制向上遍历的范围。

### 3.2 配置四层体系

| 层级 | 位置 | 说明 |
|------|------|------|
| System defaults | `/etc/gemini-cli/system-defaults.json` | 系统级默认值，最低优先级 |
| User settings | `~/.gemini/settings.json` | 用户全局配置 |
| Project settings | `.gemini/settings.json` | 项目级配置 |
| System overrides | `/etc/gemini-cli/settings.json` | 管理员强制覆盖，高于所有 settings 文件 |
| 环境变量 | `.env` 文件或系统环境变量 | 覆盖 settings 文件 |
| 命令行参数 | `--model`、`--yolo` 等 | 最高优先级 |

`.env` 文件加载顺序：CWD → 向上遍历到 `.git` 根或 home → `~/.env`

Settings 文件中的字符串值支持环境变量引用：`$VAR_NAME`、`${VAR_NAME}`、`${VAR_NAME:-DEFAULT_VALUE}`

### 3.3 上下文层级发现（GEMINI.md）

这是 Gemini CLI 最有特色的设计——**多层级、多方向的上下文自动发现**：

#### 加载顺序

1. **Global**：`~/.gemini/GEMINI.md`，所有项目通用
2. **向上遍历**：从 CWD 向上到 `.git` 根或 home，沿途收集所有 `GEMINI.md`
3. **向下扫描**：扫描 CWD 下的子目录（默认最多 200 个，通过 `context.discoveryMaxDirs` 配置），收集子模块级上下文
4. **JIT（Just-In-Time）上下文**：当工具访问某个文件/目录时，自动扫描该目录及其祖先的 `GEMINI.md`，实现按需加载

所有找到的文件拼接后（带来源路径标注）作为 system prompt 的一部分发送给模型。

#### 上下文文件名可配置

```json
{
  "context": {
    "fileName": ["AGENTS.md", "CONTEXT.md", "GEMINI.md"]
  }
}
```

#### 模块化导入

支持在 `GEMINI.md` 中通过 `@path/to/file.md` 语法导入其他文件，支持相对路径和绝对路径。

### 3.4 多目录 Workspace 支持

Gemini CLI 通过以下机制支持多目录工作区：

- **命令行参数**：`--include-directories /path/to/project1,/path/to/project2`（最多 5 个）
- **配置文件**：`context.includeDirectories` 数组
- **上下文加载**：`context.loadMemoryFromIncludeDirectories` 控制是否从 include 目录加载 GEMINI.md
- Include 目录中缺失的目录会被跳过并给出警告

```json
{
  "context": {
    "includeDirectories": ["path/to/dir1", "~/path/to/dir2", "../path/to/dir3"],
    "loadMemoryFromIncludeDirectories": true
  }
}
```

### 3.5 安全与信任

- **Folder Trust**：`security.folderTrust.enabled` 机制，对不信任的目录有安全限制
- **Home 目录警告**：在 home 目录运行时显示警告（`ui.showHomeDirectoryWarning`）
- **环境变量脱敏**：自动识别并脱敏包含 TOKEN、SECRET、PASSWORD 等敏感词的环境变量
- **Sandbox**：支持 Docker/Podman/Seatbelt 等多种沙箱方案

### 3.6 其他 Workspace 相关特性

- **Shell History 隔离**：按项目路径 hash 存储 shell 历史（`~/.gemini/tmp/<project_hash>/shell_history`）
- **Session Retention**：自动清理过期 session（可配置 maxAge、maxCount）
- **Directory Tree**：`context.includeDirectoryTree` 控制是否将目录树包含在初始请求中
- **File Filtering**：支持 `.gitignore`、`.geminiignore`、自定义 ignore 文件

---

## 4. 核心设计差异对比

| 维度 | OpenCode | Gemini CLI |
|------|----------|------------|
| 项目根检测 | 向上找 `.git` | 向上找 `.git` 或 home，支持 boundary markers |
| 多目录支持 | 基本没有（社区诉求中） | `--include-directories`，最多 5 个额外目录 |
| 上下文发现方向 | 单文件 + 配置引用 | 向上 + 向下 + JIT 三方向自动发现 |
| 上下文文件名 | 固定 `AGENTS.md`（兼容 `CLAUDE.md`） | 可配置，支持多文件名 |
| 上下文模块化 | `instructions` 字段引用外部文件 | `@file.md` 导入语法 |
| 远程指令 | 支持（URL in instructions） | 不直接支持（但 settings 支持环境变量引用） |
| 企业管控 | Managed config + macOS MDM | System defaults + System overrides 双层 |
| 配置合并策略 | 合并，后者覆盖冲突 key | 分层覆盖 |
| 安全信任 | Permission 系统（allow/ask/deny） | Folder Trust + 环境变量脱敏 + Sandbox |
| Session 隔离 | 按项目根 | 按项目路径 hash |

---

## 5. 设计启示与参考价值

### 5.1 值得借鉴的设计

1. **Gemini CLI 的层级上下文发现**：向上遍历 + 向下扫描 + JIT 按需加载的三层机制，对 monorepo 场景非常友好。子目录可以有自己的 `GEMINI.md` 提供局部指令，不需要全部堆在根目录。

2. **Gemini CLI 的 Memory Boundary Markers**：允许用户自定义上下文发现的边界，避免在大型目录结构中无限遍历。

3. **OpenCode 的 Remote Config**：通过 `.well-known/opencode` 端点提供组织级默认配置，对企业部署很实用。

4. **OpenCode 的配置合并语义**：明确"合并而非替换"的策略，减少配置覆盖时的意外丢失。

5. **Gemini CLI 的 `--include-directories`**：简单直接地解决跨项目引用问题，虽然有数量限制但覆盖了大部分场景。

### 5.2 共同的设计模式

- 都以 `.git` 目录作为项目根的主要检测标志
- 都采用"全局 → 项目"的配置分层
- 都支持项目级指令文件（`AGENTS.md` / `GEMINI.md`）提交到版本控制
- 都有企业/管理员级别的强制配置机制
- 都支持 MCP server 配置

### 5.3 当前不足

- **OpenCode**：多目录支持缺失，对 monorepo 不够友好
- **Gemini CLI**：include-directories 最多 5 个的限制在复杂项目中可能不够
- **两者都**：对"workspace 内的子项目独立配置"支持有限，不如 VS Code 的 multi-root workspace 灵活

---

## 6. 参考链接

- [OpenCode 配置文档](https://opencode.ai/docs/config)
- [OpenCode Rules 文档](https://opencode.ai/docs/rules)
- [Gemini CLI 配置文档](https://geminicli.com/docs/reference/configuration)
- [Gemini CLI GEMINI.md 文档](https://geminicli.com/docs/cli/gemini-md/)
- [OpenCode Multi-Workspace Issue #15802](https://github.com/anomalyco/opencode/issues/15802)
- [Gemini CLI Multi-Directory Issue #1118](https://github.com/google-gemini/gemini-cli/issues/1118)

*Content was rephrased for compliance with licensing restrictions.*
