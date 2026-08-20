# 工具开发规范（Tool Authoring）

「新增/修改工具」是本 coding-agent 平台最高频的迭代动作。工具是 LLM 与外部世界的接口，一个边界不清、描述含糊或缺乏可观测性的工具，会让 agent 长期误用、越权或产生不可恢复的副作用。本规范把工具的**契约、安全边界、面向 LLM 的描述、可观测性与恢复语义**固化为强制约定。

对标业界最新实践：Anthropic《Writing effective tools for agents》、OpenAI function calling 最佳实践、Model Context Protocol（MCP）工具约定；并**绑定本仓库真实的 `Tool` ABC**（`src/domain/agent/tools.py`）。与 [ddd-architecture.md](ddd-architecture.md)、[srp-principle.md](srp-principle.md)、[pydantic-model.md](pydantic-model.md)、[python-typing-lint.md](python-typing-lint.md) 及 [../security/agent-tool-guardrails.md](../security/agent-tool-guardrails.md) 联合生效。

## 1. 归属与分层

- 工具是**基础设施适配器**：实现放在 `src/infrastructure/tools/<tool_name>/`，一个工具一个子包。
- 工具继承的 `Tool` ABC 属于**领域层**（`src/domain/agent/tools.py`）；工具通过领域 Port（如 `domain.workspace.ports.Workspace`）访问外部资源，**不得**在工具内直接持有对其它 Adapter 具体类型的依赖。
- 禁止在 `domain/` 或 `application/` 中实现工具业务逻辑；应用层只负责在组合根装配注册。
- 一个工具只做一件事（SRP）：`name` 应能准确概括其唯一职责，避免 `xxx_and_yyy` 式复合工具。

## 2. 必须实现的契约

每个工具必须实现 `Tool` 的抽象成员：

- `name: str`：全局唯一、`snake_case`、语义明确的动词短语（如 `read_file`、`shell_exec`）。
- `description: str`：面向 LLM 的英文描述（见 §4）。
- `parameters: dict`：合法 JSON Schema，含 `"type": "object"`、`"properties"`，必填项列入 `"required"`。
- `async execute(**kwargs) -> ToolExecutionResult`：执行逻辑，返回结构化结果值对象（见下）。

参数在进入 `execute` 前已由基类流水线 `run()` 完成 **JSON 解析 → `cast_params` 类型转换 → `validate_params` schema 校验**；`execute` 内**不要**重复做类型/必填校验，只做业务级校验（如安全阻断、资源存在性）。

### 2.1 `ToolExecutionResult` 返回契约

`Tool.execute()`（以及 `Tool.run()` / `ToolRegistry.execute()` / `ScopedToolRegistry.execute()`）统一返回领域层 frozen 值对象 `ToolExecutionResult`（定义在 `src/domain/agent/tools.py`，与 `Tool` ABC 同模块）：

- `content: str`：回灌给 LLM 的完整文本，语义等价于原先 `execute()` 直接返回的 `str`——LLM 可见内容不因结构化返回值而改变（`ToolMessage.content` 与 checkpoint 均只取 `.content`）。大输出仍须在 `content` 内截断并注明「已截断」（见 §4）。
- `metadata: dict[str, Any]`：工具类型特有的结构化元数据，默认空 dict，仅供 trace 记录（透传到 `ToolCallTrace.metadata`），不回灌 LLM、不是对外 API 契约。`Any` 值类型是因为不同工具产出的键值天然异构（int/str/bool/list 等）；每个工具须在 `execute()` 的中文 docstring 中逐键说明 `metadata` 的含义与类型。

`metadata` 约定（对齐 `docs/spec/structured-tool-result/design.md` §3.1）：

- 所有键使用 `snake_case`。
- 字符串值须截断：命令/代码摘要（`command_summary` / `code_summary`）≤ 128 字符，URL（`url`）≤ 256 字符。适配器侧还会用 `_truncate_metadata` 把单条 metadata 的序列化体积限制在 ≈2KB，超限时丢弃剩余键并写入 `_truncated` 标记。
- **不得包含宿主绝对路径**：路径类字段（如 `working_dir` / `logical_path`）只记录工作区相对 POSIX 路径，遵守 §5 Workspace 边界红线。
- **敏感内容脱敏或不记录**：URL 须剥离认证凭证与敏感查询参数（`_summarize_url`），命令/代码摘要不暴露环境变量传递的凭证（敏感值已由 `sanitize_env` 在执行期剥离）。

失败路径不由工具自行构造 `ToolExecutionResult`：`execute()` / `run()` 遇业务或未知异常仍抛领域异常（见 §6），由 `ReActAgentAdapter` 在异常分支统一构造 `ToolExecutionResult(content=str(exc), metadata={"error_class": ...})` 并落 `ToolCallTrace.error_class` / `error_message`。

## 3. 必须显式声明的安全与恢复语义

`Tool` 基类为下列属性提供了**保守默认值**（未知工具按高风险、外部写入、需人工确认处理）。**任何新工具都必须显式复核并按真实语义覆盖**，不得沉默沿用默认而与实际行为不符：

| 成员 | 取值（`src/domain/...`） | 声明要求 |
|---|---|---|
| `risk_level` | `ToolRiskLevel`：`LOW`/`MEDIUM`/`HIGH`/`CRITICAL` | 只读工具 `LOW`；写工作区 `MEDIUM`~`HIGH`；执行命令/代码 `CRITICAL`。 |
| `side_effect_level` | `ToolSideEffectLevel`：`NONE`/`LOCAL_WRITE`/`EXTERNAL_WRITE`/`IRREVERSIBLE` | 按真实副作用声明，决定恢复期的保守判定。 |
| `replay_policy` | `ToolReplayPolicy`：`REPLAY_RESULT`/`REQUIRE_IDEMPOTENCY_KEY`/`MANUAL_REVIEW`/`NEVER_REPLAY` | 纯读可 `REPLAY_RESULT`；有外部副作用倾向 `MANUAL_REVIEW` 或 `REQUIRE_IDEMPOTENCY_KEY`。 |
| `idempotency_key(request, execution_key)` | `str \| None` | 仅当工具具备外部幂等能力时返回稳定键，配合 `REQUIRE_IDEMPOTENCY_KEY`。 |
| `timeout_seconds` | `float \| None` | 需要独立超时上限时覆盖；`None` 沿用 `AgentConfig.tool_timeout_seconds`。 |

> 这些语义直接影响**中断—恢复**的正确性：错误地把一个 `EXTERNAL_WRITE` 工具标成可重放，会在 resume 时重复产生副作用。宁可保守。

## 4. 面向 LLM 的描述与参数（可用性关键）

description / parameters 是 LLM 正确选择与调用工具的唯一依据，按业界最佳实践：

- **描述用英文书写**（与既有工具一致，利于模型对齐），说明「做什么、何时用、返回什么、有何边界」。docstring 与注释仍遵循 [code-documentation.md](code-documentation.md) 用中文。
- 明确**触发条件与不适用场景**，减少误用；对有约束的参数在 `description` 里写清（如「workspace-relative POSIX path」「timeout in seconds」）。
- 每个参数都要有 `description`；用 `enum`、`required`、类型约束收窄取值空间，别把校验推给模型。
- 运行期动态信息（如工作区根）可拼入 description 引导模型（参见 `ShellExecTool.description` 用 `Workspace.display_root_hint()`）。
- 返回内容应**对 LLM 友好且可控**：结构清晰、体积有上限。大输出必须截断并注明「已截断」，禁止无界返回撑爆上下文（参见 shell/http 工具的 `max_output_size`）。

## 5. Workspace 边界与红线

- 涉及文件/命令/代码执行的工具，**必须**通过构造注入的 `Workspace` 完成路径解析与物化，**不得**直接使用 `os`/`pathlib` 拼宿主绝对路径；越界须抛 `ToolExecutionError`。
- 物化得到的宿主绝对路径**只能**用于底层调用（如子进程 `cwd`），**禁止**回填到对 LLM 可见的参数、成功消息或 trace 中。
- 环境变量传递必须剥离敏感键（`KEY`/`SECRET`/`PASSWORD`/`TOKEN`/`CREDENTIAL`）；错误信息不得泄露宿主路径、环境变量值或敏感文件内容。
- 高风险工具（shell/python/http 等）的硬规则以 [../security/agent-tool-guardrails.md](../security/agent-tool-guardrails.md) 为准，新增此类工具须在该文档补充策略标记与静态策略测试。

## 6. 错误处理

- 业务/安全失败统一抛领域异常 `ToolExecutionError`（及其子类，见 `src/domain/agent/exceptions.py`）；参数问题走 `ToolParameterValidationError`。
- 遵循 [python-typing-lint.md](python-typing-lint.md)：禁裸 `except`、异常链 `raise ... from err`、禁 `print`、全量类型标注、禁裸 `Any`。
- 安全阻断必须发生在**产生副作用之前**（如创建子进程前拒绝危险命令）。

## 7. 权限与审批集成

- 工具是否对某 Agent 可见，由 `ScopedToolRegistry`（`Task.tool_names` / `AgentConfig.allowed_tool_names`）控制；工具自身不做「谁能调我」的判断，但要保证声明的 `risk_level` 正确，供权限与审批策略消费。
- 是否触发 HITL 审批由 `HITL_INTERRUPT_ON` 配置与默认策略决定（见 [../tools.md](../tools.md) §HITL）。**HITL、Workspace 边界、schema 校验、权限隔离是相互独立的多层防线，任何一层都不能替代另一层。**

## 8. 配置开关

- 高风险工具**默认关闭**（如 `SHELL_EXEC_ENABLED=false`）。功能开关用 `PropertiesBaseSettings` 新增配置类，配置源遵循 [config-source.md](config-source.md)：优先写 `config.properties`。
- 与工作区/执行相关的路径类配置应在容器启动阶段 fail-fast 校验（越界即拒绝启动），不要留到运行期才报错。

## 9. 注册

在 `application/container_config.py` 的 `_create_tool_registry()` 中按开关**条件注册**工具实例。组合根是唯一允许同时引用 domain Port 与 infrastructure 工具实现的位置。

## 10. 测试

- 每个工具必须有单元测试，覆盖：正常路径、参数校验失败、安全阻断/边界越界、超时与输出截断。
- 测试**离线确定性**：禁止真实网络/模型/宿主危险命令调用，用 stub/临时工作区。
- 高风险工具须有静态策略测试锁定安全标记（对齐 guardrails 文档）。

## 11. 文档同步

新增或修改工具后，**必须同步更新 [../tools.md](../tools.md)**（工具清单、参数、配置键、默认开关、HITL 策略、注册条件）。工具契约或安全模型发生方向性变化时，评估是否需要一条 ADR（见 [adr.md](adr.md)）。

## 新增工具检查清单

1. `infrastructure/tools/<tool_name>/` 下继承 `Tool`，单一职责。
2. 实现 `name` / `description`（英文，含边界）/ `parameters`（JSON Schema，每参数有 description）/ `async execute`（返回 `ToolExecutionResult`，并在 docstring 逐键说明 `metadata`）。
3. 显式复核并覆盖 `risk_level` / `side_effect_level` / `replay_policy` /（如适用）`idempotency_key` / `timeout_seconds`。
4. 文件/执行类工具注入 `Workspace`，不碰宿主绝对路径；输出有上限并截断。
5. 领域异常处理，安全阻断前置，错误信息不泄露敏感内容。
6. 需要开关则新增 `PropertiesBaseSettings` 配置类，高风险默认关闭，写入 `config.properties`。
7. 在 `_create_tool_registry()` 条件注册。
8. 补齐离线确定性单测（+ 高风险工具静态策略测试）。
9. 更新 `docs/tools.md`；方向性变更评估 ADR。
</content>
