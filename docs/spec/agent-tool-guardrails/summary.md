# 交付总结：Agent Tool Guardrails

## Feature Slug

`agent-tool-guardrails`

## 最终产物

- `docs/spec/agent-tool-guardrails/requirement.md`
- `docs/spec/agent-tool-guardrails/design.md`
- `docs/spec/agent-tool-guardrails/tasks.md`
- `docs/spec/agent-tool-guardrails/review-log.md`
- `docs/security/agent-tool-guardrails.md`
- `epsilon-boot/src/infrastructure/tools/shell_exec/shell_exec_tool.py`
- `epsilon-boot/src/infrastructure/tools/http_request/http_request_tool.py`
- `epsilon-boot/test/infrastructure/tools/shell_exec/test_shell_exec_tool_unit.py`
- `epsilon-boot/test/infrastructure/tools/http_request/test_http_request_tool.py`
- `epsilon-boot/test/static/test_tool_guardrail_policy.py`

## 设计决策

- Shell 命令阻断落在 `ShellExecTool.execute()` 读取 `command` 后的最前置位置，早于 Workspace 物化和子进程创建。
- HTTP 敏感 Header 阻断早于 URL 校验、DNS 解析和网络请求，只检查 Header 名，不读取或输出 Header 值。
- HTTP SSRF 防护保留既有 scheme 校验和 DNS 多地址逐一校验，并新增 metadata、localhost、IP literal 的 host 语义前置阻断。
- 不新增配置、依赖、工具注册、前端改动或 ReAct 主循环改动；HITL 继续作为审批链路，不替代工具层硬校验。
- 安全规则通过 `docs/security/agent-tool-guardrails.md` 沉淀，并由静态策略测试锁定关键标记。

## 测试覆盖

- `uv run pytest test/static/test_tool_guardrail_policy.py -v`：3 passed。
- `uv run pytest test/infrastructure/tools/shell_exec/test_shell_exec_tool_unit.py test/infrastructure/tools/http_request/test_http_request_tool.py -v`：74 passed。
- `uv run pytest -m "not benchmark"`：2630 passed, 2 skipped。

## 后续事项

- 本 spec 未实现 plan2 Task 4 的工具滥用检测、同工具高频检测、异常参数模式检测或 OpenTelemetry event；这些能力应在独立 spec 中推进。
- 当前未引入 allowlist。未来如需放开特定内网目标或敏感 Header，必须另走 approved spec，并优先写入 `epsilon-boot/config.properties`。
