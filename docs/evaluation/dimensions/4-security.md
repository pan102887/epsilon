# 维度 4：安全与合规

## 评估结论

**评分：4 / 5**。`ShellExecTool` / `PythonExecTool` 具备环境变量脱敏、超时与输出截断、cwd 被 Workspace 锁定以及启动期二次校验；`SymlinkGuard` / `IdentityGuard` 覆盖符号链接与跨设备身份两类逃逸；`AGENT_MAX_DELEGATION_DEPTH` 对委派递归有硬上限。距离 5 分的主要差距是：**缺少 Prompt Injection 防御分层**（系统提示隔离、工具输入白名单、输出过滤）、**缺少工具滥用检测与告警**，以及**缺少凭证轮转 / 红蓝对抗演练**。

## 证据与分析

- [`epsilon-boot/src/infrastructure/tools/shell_exec/shell_exec_tool.py:L59-L98`](../../../epsilon-boot/src/infrastructure/tools/shell_exec/shell_exec_tool.py)
  `_SENSITIVE_KEYWORDS = ["KEY", "SECRET", "PASSWORD", "TOKEN", "CREDENTIAL"]`，`sanitize_env` 对非保留变量按不区分大小写的子串匹配剔除敏感键；保留变量白名单按 Unix / Windows 分别维护（`PATH` / `HOME` / `Path` / `USERPROFILE` 等）。子进程 `env=clean_env` 传入，确保子进程看不到宿主的 API Key。
- [`epsilon-boot/src/infrastructure/tools/shell_exec/shell_exec_tool.py:L217-L249`](../../../epsilon-boot/src/infrastructure/tools/shell_exec/shell_exec_tool.py)
  执行开头做 `local_materialization` 能力守卫（非 LocalFilesystemWorkspace 后端直接拒绝运行）；随后 `Workspace.resolve_path(requested_working_dir)` 归一化工作区相对路径，`materialize_cwd` 再把它转成宿主绝对路径作为子进程 `cwd`。越界抛 `WorkspaceConfinementViolation` → `ToolExecutionError`，不会泄露宿主绝对路径到 LLM 消息。
- [`epsilon-boot/src/infrastructure/tools/python_exec/python_exec_tool.py:L42-L50`](../../../epsilon-boot/src/infrastructure/tools/python_exec/python_exec_tool.py)
  `BLOCKED_CALLS = frozenset({"exec", "eval", "compile", "__import__", "globals", "locals", "getattr", "setattr", "delattr", "open", "breakpoint", "exit", "quit"})`，配合 AST 静态分析（同文件 `analyze_code`）在代码 **执行前** 拦截危险调用与非白名单 import；`sanitize_env` 与 `ShellExecTool` 共用。
- [`epsilon-boot/src/infrastructure/workspace/local_filesystem/_guards.py:L31-L151`](../../../epsilon-boot/src/infrastructure/workspace/local_filesystem/_guards.py)
  `SymlinkGuard` 在严格模式（`follow_symlinks=False`）下逐段 `os.lstat`，任一已存在祖先是符号链接立即抛 `WorkspaceConfinementViolation(SYMLINK_ESCAPE)`；宽松模式用 `resolve(strict=False)` + `commonpath` 做前缀判断，跨驱动器异常同样判定越界。
- [`epsilon-boot/src/infrastructure/workspace/local_filesystem/_guards.py:L154-L215`](../../../epsilon-boot/src/infrastructure/workspace/local_filesystem/_guards.py)
  `IdentityGuard` 在构造时缓存 `root.st_dev`，每次 I/O 前回溯到最近存在祖先做 `st_dev` 比对，不一致抛 `CROSS_DEVICE`；这是针对 macOS HFS+ 大小写折叠与 bind mount 的补充防御。
- [`epsilon-boot/src/application/container_config.py:L212-L275`](../../../epsilon-boot/src/application/container_config.py)
  `_validate_exec_working_dir` 在启动期对 `SHELL_EXEC_WORKING_DIR` / `PYTHON_EXEC_WORKING_DIR` 做二次校验：先判断宿主绝对路径是否落在 `WORKSPACE_ROOT` 之下，再用 `ws.resolve_path` 做字符级归一化；任一失败抛 `ConfigurationError` 触发容器启动回滚。
- [`epsilon-boot/config.properties:AGENT_MAX_DELEGATION_DEPTH`](../../../epsilon-boot/config.properties)
  `AGENT_MAX_DELEGATION_DEPTH=3` / `AGENT_DELEGATE_TOOL_ENABLED=true` 固化默认递归深度与总开关，既在运行期被 `DelegateToAgentTool` 校验，也在评测样本的"depth_exceeded"场景被覆盖。

## 业界框架对照

- **OWASP LLM Top 10 — LLM01 Prompt Injection / LLM02 Insecure Output Handling / LLM05 Supply Chain / LLM09 Overreliance**（<https://owasp.org/www-project-top-10-for-large-language-model-applications/>）：
  - LLM01 **Prompt Injection**：项目缺少"系统提示 vs 用户提示"的结构化隔离（现状只把 `system_prompt` 字符串塞进 messages），也缺少用户输入的工具参数白名单过滤；与 OWASP 明确的"Constrain agent privileges / separate prompt regions"要求有差距。
  - LLM02 **Insecure Output Handling**：Shell/Python 输出只做长度截断，没有"敏感模式"过滤（如泄漏 `Bearer xxx`、私钥片段），输出仍直接进 ToolMessage 回给模型。
- **Anthropic — Safety best practices / Building effective agents**：建议"Defense in depth: tool use + deliberation + review"。项目实现了 tool use 侧的沙箱与工作区受控，但缺少 LLM 自我审查层（例如子 Agent 用于安全审查的二段确认）。
- **Google ADK — Agent Development Kit（Safety & guardrails）**：建议工具输入设 JSON Schema 约束 + 结构化错误，同时对敏感工具设 allow-list；项目已有 JSON Schema + 允许工具集，但 allow-list 的治理（谁能改 `allowed_tool_names`）无审计链路。

## 改进建议

1. **P0 — Prompt Injection 防御分层**：系统提示拆成不可被用户覆盖的 `system` 段与可由用户影响的 `user` 段；对工具参数（特别是 `ShellExecTool.command`、`HttpRequestTool.url`）做白名单正则 + 黑名单关键词双层校验；引用 OWASP LLM01 与 Anthropic "Safety best practices" 作为执行清单。
2. **P0 — 工具调用滥用检测**：在 `ReActAgentAdapter.run` 的工具执行节点加入"同工具高频调用"与"异常参数模式"探测（例如一轮内 ≥ 5 次 ShellExec、Python 代码命中 `BLOCKED_CALLS` 次数），命中即告警并短路；把事件落 OpenTelemetry event。引用 OWASP LLM09 / Google ADK "Safety & guardrails"。
3. **P1 — 凭证轮转手册与启动期校验**：`config.properties` 目前把 `MODEL_*_API_KEY` 明文存入仓库（从本次 review 的 L76-L110 可见）；应把样例值替换为 `REPLACE_ME`，引入 Provider 凭证轮转 runbook（配合 `docs/steering/config-source.md`），并在容器启动时校验 `API_KEY` 长度与前缀。
4. **P2 — 启动时打印实际生效的 WORKSPACE_ROOT / WORKSPACE_FOLLOW_SYMLINKS**，方便运维确认守卫策略；避免"默认严格 = 实际宽松"的隐性风险。

<!-- AUTO-START: aggregate_scores -->
### 自动生成区块

**评分**：4 / 5，**权重**：0.16，**加权得分**：0.640

**人工打分理由**：`ShellExecTool` / `PythonExecTool` 具备环境变量脱敏（`API_KEY` / `PASSWORD` / `SECRET` / `TOKEN` / `CREDENTIAL` 前缀）、超时与输出截断、cwd 被 Workspace 锁定 以及启动期 `_validate_exec_working_dir` 二次校验；`SymlinkGuard` / `IdentityGuard` 覆盖符号链接与跨设备身份两类逃逸；`AGENT_MAX_DELEGATION_DEPTH=3` 在运行期与评测 样本两侧被同时验证。这与 OpenAI "Function calling best practices"、Google ADK "Safety & guardrails"、Anthropic "Building effective agents" 建议的沙箱基线完全 一致。距离 5 分差距：缺少 Prompt Injection 防御分层、工具滥用检测告警、凭证轮转 runbook，这些都在 OWASP LLM Top 10（LLM01 / LLM02 / LLM09）中有明确要求。

#### 全局自动化指标（供维度交叉参考）

| 指标 | 分子 | 分母 | 比率 | 样本数 |
| --- | --- | --- | --- | --- |
| `tool_call_success_rate` | 6 | 20 | 0.3000 | 20 |
| `delegation_correctness` | 6 | 15 | 0.4000 | 15 |
| `context_compaction_effectiveness` | 36 | 36 | 1.0000 | 36 |

#### 证据清单（来自 `tests/evaluation/evidence/catalog.py`）

- `epsilon-boot/src/infrastructure/tools/shell_exec/shell_exec_tool.py:59-98`
- `epsilon-boot/src/infrastructure/tools/shell_exec/shell_exec_tool.py:217-249`
- `epsilon-boot/src/infrastructure/tools/python_exec/python_exec_tool.py:42-50`
- `epsilon-boot/src/infrastructure/workspace/local_filesystem/_guards.py:31-151`
- `epsilon-boot/src/infrastructure/workspace/local_filesystem/_guards.py:154-215`
- `epsilon-boot/src/application/container_config.py:212-275`
- `epsilon-boot/config.properties:AGENT_MAX_DELEGATION_DEPTH`

<!-- AUTO-END: aggregate_scores -->
