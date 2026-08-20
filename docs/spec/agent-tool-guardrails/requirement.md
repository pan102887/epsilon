# 需求文档：Agent Tool Guardrails

## 简介

本需求从 `docs/plan2.md` 的 “Task 3: Prompt Injection 与高风险工具参数防御分层” 独立拆出，目标是在不扩大业务能力面的前提下，为 Agent 高风险工具建立最小、可测试、可审计的参数安全护栏。

背景风险来自历史评估 `docs/evaluation/report.md` 中的 P0 安全项：Prompt Injection 可能诱导模型调用 `ShellExecTool` 或 `HttpRequestTool` 执行破坏性命令、读取敏感文件、下载并执行远程脚本，或访问 metadata、localhost、私网目标并携带敏感 Header。当前 `ShellExecTool` 已具备 Workspace cwd 锁定、环境变量清理、超时与输出截断，但缺少命令级危险片段阻断；`HttpRequestTool` 已具备 http/https scheme 校验、DNS 解析和非公网 IP 阻断，但仍需要把 metadata、localhost、private target 以及模型可控敏感 Header 的策略明确为工具层硬规则。

本需求范围包括：

- 新增 Agent 工具安全护栏文档，沉淀 Shell / HTTP 工具的硬规则。
- 新增静态策略测试，确保关键防护策略不会在后续重构中被移除。
- 在 `ShellExecTool` 中补充最小命令危险片段阻断。
- 在 `HttpRequestTool` 中明确 metadata / localhost / private target 与模型可控敏感 Header 防护策略，并保持既有 SSRF 公网校验能力。
- 保持现有 ReAct 工具执行节点、工具权限、guardrail、HITL 审批链路的语义，不把本需求扩展为新的运行时检测系统。

明确不在本需求范围内：

- 不实现 plan2 Task 4 的工具滥用检测、同工具高频调用检测、异常参数模式检测或 OpenTelemetry 事件。
- 不修改前端，不新增 UI 或 API 业务功能。
- 不引入新依赖，不改变依赖管理方式。
- 不扩展 Agent 业务功能面，不新增工具，不改变工具注册范围。
- 不重构 `ReActAgentAdapter` 的主循环；该文件仅作为现有 guardrail / HITL 集成上下文，不作为本需求的主要实现目标。
- 不把 HITL 审批当作替代 Workspace、工具权限、工具参数校验、网络访问控制、命令沙箱或操作系统隔离的安全边界。

本 spec-dev 阶段默认 approval-gated：本阶段只产出 `requirement.md`，经用户确认后才能进入 designer 阶段。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| Agent 工具安全护栏 | Agent_Tool_Guardrails | 面向 Agent 高风险工具参数的文档、测试和工具层阻断规则集合，用于降低 Prompt Injection 诱导工具误用的风险。 |
| Prompt Injection | Prompt_Injection | 用户输入或外部内容诱导模型忽略系统约束、调用不安全工具或传入危险参数的攻击方式。 |
| 高风险工具 | High_Risk_Tool | 具备本地命令执行、网络访问、文件修改或委派等高影响能力的 Agent 工具；本需求只覆盖 ShellExecTool 和 HttpRequestTool。 |
| Shell 执行工具 | ShellExecTool | `epsilon-boot/src/infrastructure/tools/shell_exec/shell_exec_tool.py` 中的 `shell_exec` 工具，执行由模型传入的 Shell 命令。 |
| HTTP 请求工具 | HttpRequestTool | `epsilon-boot/src/infrastructure/tools/http_request/http_request_tool.py` 中的 `http_request` 工具，按模型传入参数发起 HTTP 请求。 |
| 危险命令片段 | Dangerous_Command_Fragment | 可能造成破坏性操作、敏感文件读取或远程脚本直接执行的 Shell 命令片段，例如 `rm -rf /`、`mkfs`、`dd if=`、`:(){ :|:& };:`、`curl ... | sh`、`wget ... | bash`、`.env`、`/etc/shadow`、`~/.ssh/id_rsa`。 |
| SSRF 风险目标 | SSRF_Risk_Target | metadata endpoint、localhost、loopback、link-local、RFC1918 私网、reserved、unspecified、multicast 或非公网目标等不应由 Agent 直接访问的 URL host / IP。 |
| Metadata Endpoint | Metadata_Endpoint | 云厂商或运行环境暴露实例凭证和元数据的地址，至少包括 `169.254.169.254`。 |
| 模型可控敏感 Header | Model_Controlled_Sensitive_Header | 由模型工具参数直接传入且可能携带凭证的 HTTP Header，至少包括 `Authorization`、`Cookie`、`X-API-Key`、`API-Key`、`Proxy-Authorization`。 |
| Workspace 边界 | Workspace_Boundary | 由项目 Workspace 抽象和本地物化能力提供的工作区路径限制，确保本地文件与命令工作目录不越出 `WORKSPACE_ROOT`。 |
| HITL 审批 | HITL_Approval | Human-in-the-loop 工具审批链路，发生在 ReAct Loop 中模型返回工具调用之后、工具执行之前，用于人工 approve / edit / reject 高风险工具调用。 |
| ReAct Agent 适配器 | ReActAgentAdapter | `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py` 中现有 ReAct 工具执行与 guardrail / HITL 集成位置；本需求仅把它作为上下文，不要求重构主循环。 |
| 静态策略测试 | Static_Policy_Test | 位于 `epsilon-boot/test/static/test_tool_guardrail_policy.py` 的 pytest 测试，通过扫描文档和工具实现中的策略标记，防止安全护栏被移除或绕过。 |
| 工具执行错误 | ToolExecutionError | 工具参数命中安全阻断或执行失败时向调用方返回的领域工具错误类型。 |
| 安全护栏文档 | Security_Guardrail_Document | `docs/security/agent-tool-guardrails.md`，记录本需求要求的 ShellExecTool 与 HttpRequestTool 硬规则。 |
| 配置主源 | Config_Primary_Source | 项目 steering 规定的配置主来源 `epsilon-boot/config.properties`；本需求默认不新增配置项，如确需配置必须优先写入该文件。 |
| DNS 解析 | DNS_Resolution | HttpRequestTool 对 hostname URL 发起请求前执行的主机名解析流程，用于发现所有目标 IP 并应用 SSRF_Risk_Target 校验。 |
| 后端测试套件 | Backend_Test_Suite | `epsilon-boot` 中通过 pytest 执行的后端测试集合，本需求的完整回归期望为 `uv run pytest -m "not benchmark"`。 |
| 实现交付说明 | Implementation_Handoff | downstream generator 或实现阶段完成后向用户报告的验证结果、失败测试和剩余风险说明。 |
| UV 包管理规则 | UV_Package_Management_Rule | `docs/steering/uv-package-manager.md` 中规定的后端依赖和命令执行方式，验证命令需通过 `uv run` 执行。 |

## 需求

### 需求 1：沉淀 Agent 工具安全护栏文档

**用户故事：** 作为安全维护者，我希望把 Shell 与 HTTP 高风险工具的硬规则写入仓库文档，以便后续实现、评审和回归测试都有统一依据。

#### 验收标准

1. THE Security_Guardrail_Document SHALL document Agent_Tool_Guardrails for ShellExecTool and HttpRequestTool.
2. THE Security_Guardrail_Document SHALL state that ShellExecTool is disabled by default and SHALL remain subject to Workspace_Boundary and HITL_Approval when enabled.
3. THE Security_Guardrail_Document SHALL list Dangerous_Command_Fragment categories for destructive command execution, sensitive file reads, and remote script download-and-execute patterns.
4. THE Security_Guardrail_Document SHALL state that HttpRequestTool only accepts http and https URL schemes.
5. THE Security_Guardrail_Document SHALL state that HttpRequestTool blocks SSRF_Risk_Target including Metadata_Endpoint, localhost, loopback, link-local, RFC1918 private targets, and non-global addresses unless a future explicit allowlist is approved in a separate requirement.
6. THE Security_Guardrail_Document SHALL state that Model_Controlled_Sensitive_Header is not allowed in model-controlled HttpRequestTool parameters.
7. IF Config_Primary_Source IS required for a future allowlist, THEN THE Security_Guardrail_Document SHALL state that the configuration must be introduced through a later approved spec and written to Config_Primary_Source first.

### 需求 2：为工具安全策略建立静态回归守卫

**用户故事：** 作为质量维护者，我希望用静态策略测试锁定安全护栏关键标记，以便后续重构不会无声移除高风险工具防护。

#### 验收标准

1. THE Static_Policy_Test SHALL exist at `epsilon-boot/test/static/test_tool_guardrail_policy.py`.
2. THE Static_Policy_Test SHALL verify that Security_Guardrail_Document exists and mentions ShellExecTool, HttpRequestTool, Dangerous_Command_Fragment, SSRF_Risk_Target, and Model_Controlled_Sensitive_Header rules.
3. THE Static_Policy_Test SHALL verify that ShellExecTool implementation contains guardrail policy markers for `rm -rf`, `mkfs`, `dd if=`, remote script execution, sensitive file reads, and blocked-command behavior.
4. THE Static_Policy_Test SHALL verify that HttpRequestTool implementation contains guardrail policy markers for `169.254.169.254`, `localhost`, private or non-global network blocking, and sensitive header rejection.
5. WHEN `uv run pytest test/static/test_tool_guardrail_policy.py -v` IS run from `epsilon-boot`, THE Static_Policy_Test SHALL pass after Agent_Tool_Guardrails implementation is complete.
6. WHILE Static_Policy_Test IS maintained, WHEN a required guardrail marker is removed from ShellExecTool, HttpRequestTool, or Security_Guardrail_Document, THE Static_Policy_Test SHALL fail.

### 需求 3：阻断 ShellExecTool 危险命令参数

**用户故事：** 作为平台维护者，我希望 Shell 执行工具在创建子进程前拒绝危险命令片段，以便 Prompt Injection 无法仅通过模型参数触发破坏性命令或敏感文件读取。

#### 验收标准

1. WHEN ShellExecTool receives a command containing Dangerous_Command_Fragment, THE ShellExecTool SHALL raise ToolExecutionError before creating any subprocess.
2. THE ShellExecTool SHALL preserve existing Workspace_Boundary enforcement for `working_dir`.
3. THE ShellExecTool SHALL preserve existing environment variable sanitization, timeout, and output truncation behavior.
4. THE ShellExecTool SHALL classify blocked command attempts as blocked-command behavior in implementation text or error metadata that Static_Policy_Test can detect.
5. FOR ALL Dangerous_Command_Fragment entries defined by Agent_Tool_Guardrails, THE ShellExecTool SHALL apply case-insensitive matching where the fragment is textual and command-shell independent enough to match safely.
6. IF ShellExecTool blocks a command, THEN THE ShellExecTool SHALL return ToolExecutionError without exposing host absolute paths or sanitized environment values.
7. THE ShellExecTool SHALL not weaken HITL_Approval, tool permission checks, or High_Risk_Tool risk classification.

### 需求 4：强化 HttpRequestTool SSRF 与敏感 Header 策略

**用户故事：** 作为平台维护者，我希望 HTTP 请求工具在发起网络请求前拒绝内网目标和模型可控敏感 Header，以便 Prompt Injection 无法利用 Agent 访问内部服务或转发凭证。

#### 验收标准

1. WHEN HttpRequestTool receives a URL whose host is Metadata_Endpoint, THE HttpRequestTool SHALL raise ToolExecutionError before sending any network request.
2. WHEN HttpRequestTool receives a URL whose host resolves to SSRF_Risk_Target, THE HttpRequestTool SHALL raise ToolExecutionError before sending any network request.
3. WHEN HttpRequestTool receives a URL with localhost, loopback, link-local, RFC1918 private, reserved, unspecified, multicast, or non-global target semantics, THE HttpRequestTool SHALL raise ToolExecutionError.
4. WHEN HttpRequestTool receives Model_Controlled_Sensitive_Header in `headers`, THE HttpRequestTool SHALL raise ToolExecutionError before sending any network request.
5. THE HttpRequestTool SHALL preserve existing http and https scheme validation.
6. THE HttpRequestTool SHALL preserve existing DNS_Resolution based SSRF validation for hostname URLs.
7. IF DNS_Resolution for HttpRequestTool returns multiple addresses, THEN THE HttpRequestTool SHALL reject the URL when any resolved address is an SSRF_Risk_Target.
8. THE HttpRequestTool SHALL not add new dependencies or require external services for SSRF_Risk_Target and Model_Controlled_Sensitive_Header validation.
9. THE HttpRequestTool SHALL not weaken HITL_Approval, tool permission checks, or High_Risk_Tool risk classification.

### 需求 5：保持 ReAct、HITL 与实现范围稳定

**用户故事：** 作为 Agent 运行时维护者，我希望本需求只补齐工具参数最小阻断，不改变 ReAct 主循环或扩展新能力，以便 task3 可以独立落地且不与后续 task4 混淆。

#### 验收标准

1. THE Agent_Tool_Guardrails SHALL treat ReActAgentAdapter as existing context for guardrail and HITL_Approval integration, not as a required primary modification target.
2. THE Agent_Tool_Guardrails SHALL not implement task4-style tool abuse detection, repeated tool call detection, anomaly detection, or OpenTelemetry events.
3. THE Agent_Tool_Guardrails SHALL not modify frontend code.
4. THE Agent_Tool_Guardrails SHALL not introduce new dependencies.
5. THE Agent_Tool_Guardrails SHALL not add new High_Risk_Tool registrations or expand business capabilities.
6. THE Agent_Tool_Guardrails SHALL follow repository steering: infrastructure tool behavior stays in `src/infrastructure/tools/`, configuration changes if any use Config_Primary_Source, and public modules/classes/functions keep Chinese docstrings.
7. WHEN downstream designer or tasker derives work from this requirement, THE Agent_Tool_Guardrails SHALL remain approval-gated until the user explicitly approves the current major artifact.

### 需求 6：验证命令与回归范围

**用户故事：** 作为交付负责人，我希望本需求有明确的验证命令和回归边界，以便后续实现完成后能用项目既有测试方式确认安全护栏有效。

#### 验收标准

1. WHEN Agent_Tool_Guardrails implementation is complete, THE Static_Policy_Test SHALL pass with `uv run pytest test/static/test_tool_guardrail_policy.py -v` from `epsilon-boot`.
2. WHEN Agent_Tool_Guardrails implementation is complete, THE Agent_Tool_Guardrails SHALL preserve the Backend_Test_Suite expectation `uv run pytest -m "not benchmark"` from `epsilon-boot`.
3. IF Backend_Test_Suite fails for reasons unrelated to Agent_Tool_Guardrails, THEN THE Implementation_Handoff SHALL report the failing tests without masking the Static_Policy_Test result.
4. THE Agent_Tool_Guardrails SHALL not require network access during Static_Policy_Test execution.
5. THE Agent_Tool_Guardrails SHALL keep validation compatible with UV_Package_Management_Rule.
