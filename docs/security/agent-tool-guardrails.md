# Agent 工具安全护栏

## 适用范围

本文档记录 Agent 高风险工具的硬规则，当前覆盖 `ShellExecTool` 与 `HttpRequestTool`。
这些规则用于降低 Prompt Injection 诱导模型调用工具时带来的本地命令、敏感文件、远程脚本和 SSRF 风险。

本文档中的策略标记供静态策略测试锁定：

- `Dangerous_Command_Fragment`
- `SSRF_Risk_Target`
- `Model_Controlled_Sensitive_Header`
- `Config_Primary_Source`

## ShellExecTool 硬规则

`ShellExecTool` 默认关闭，配置默认值必须保持 `SHELL_EXEC_ENABLED=false`。启用后仍必须同时受以下边界约束：

- Workspace 边界：命令工作目录只能通过工作区相对路径解析，不得越出 `WORKSPACE_ROOT`。
- ScopedToolRegistry 工具权限：未授权 Agent 不得调用 `shell_exec`。
- HITL 审批：启用人工审批时，Shell 执行仍须先经过 approve / reject。
- 工具自身参数校验：HITL 不是命令安全校验的替代边界。

`Dangerous_Command_Fragment` 至少包括以下类别和标记：

- destructive command execution：`rm -rf`、`mkfs`、`dd if=`
- sensitive file reads：`.env`、`/etc/shadow`、`~/.ssh/id_rsa`
- remote script download-and-execute patterns：`curl ... | sh`、`wget ... | bash`

命中上述片段时，`ShellExecTool` 必须在创建子进程前拒绝执行，并返回工具执行错误。错误内容只能说明阻断分类，不得暴露宿主绝对路径、环境变量值或敏感文件内容。

## HttpRequestTool 硬规则

`HttpRequestTool` 只允许 `http/https` URL。所有请求必须在发起网络请求前完成 URL 安全校验。

`SSRF_Risk_Target` 至少包括：

- metadata endpoint：`169.254.169.254`
- `localhost` 与 `localhost.localdomain`
- loopback 地址，例如 `127.0.0.1` 与 `::1`
- link-local 地址，例如 `169.254.0.0/16`
- RFC1918 private target，例如 `10.0.0.0/8`、`172.16.0.0/12`、`192.168.0.0/16`
- reserved、unspecified、multicast 与 `non-global` 地址

对 hostname URL，工具必须执行 DNS 解析并检查所有解析地址；任一地址命中 `SSRF_Risk_Target` 即拒绝整个请求。

`Model_Controlled_Sensitive_Header` 不允许出现在模型可控 `headers` 参数中，至少包括：

- `Authorization`
- `Cookie`
- `X-API-Key`
- `API-Key`
- `Proxy-Authorization`

敏感 Header 阻断只检查 Header 名，不读取、不记录、不输出 Header 值。

## 不作为安全边界的能力

以下能力有助于降低风险，但不能替代 Workspace、工具权限、参数校验、网络访问控制、命令沙箱或 OS 权限：

- HITL 审批
- Prompt 文案约束
- 模型自我拒答
- 日志脱敏
- 请求/响应体截断

即使 HITL 审批已开启，`ShellExecTool` 与 `HttpRequestTool` 也必须执行本文档规定的工具层硬校验。

## 未来 allowlist 变更要求

当前规则不引入 allowlist。未来如果确需允许特定内网目标、metadata 代理、敏感 Header 或远程脚本执行模式，必须另走 approved spec，明确风险、配置项、测试和回滚策略。

所有新增配置必须优先写入 `Config_Primary_Source`，即 `epsilon-boot/config.properties`；环境变量或 `.env` 只能用于覆盖主配置。
