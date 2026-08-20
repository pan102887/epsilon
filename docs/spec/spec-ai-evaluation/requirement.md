# 需求文档：AI Agent 工作台系统性评估（spec-ai-evaluation）

## 简介

### 背景

本仓库包含通用 AI Agent 工作台的前后端：后端 `epsilon-boot` 采用 FastAPI + DDD 六边形架构，提供 ReAct Agent Loop、多模型路由、工具系统、会话存储、多 Agent 委派、上下文压缩与可观测性；前端 `epsilon-client` 为 Next.js 控制台，通过 rewrites 代理到后端。项目已经沉淀了较完整的 `docs/steering/` 强制规范与 `docs/` 主题文档，但尚未按业界公认的 AI Agent 应用标准做过一次横向自评，风险点与改进优先级对技术负责人、开发工程师与 QA/平台工程师均不透明。

### 动机

- 技术负责人需要一份可对管理层汇报的 Agent 能力与风险评估，覆盖架构、Agent 能力、模型与提示工程、安全、可靠性、可测试性与前端 UX 等七个维度。
- 开发工程师需要按优先级排序的改进清单与可复测的自动化度量脚本，避免凭感觉优化。
- QA/平台工程师需要一套可集成进回归流水的评测脚本，对 Agent 核心指标做持续度量。

### 范围内（In-Scope）

- 按七个评估维度生成一份 Markdown 评估报告，落地在 `docs/evaluation/` 下。
- 产出一套自动化评测脚本，落地在 `tests/evaluation/` 或 `scripts/evaluation/` 下，覆盖至少三项核心 Agent 指标，可本地一键运行并输出结构化结果（JSON 或表格）。
- 评估标准显式引用业界公认框架：OpenAI Assistants/Agents 最佳实践、Anthropic Agent 工程实践（tool use、context window、prompt caching、multi-agent）、LangChain/LangGraph Agent 模式、Google ADK/Vertex Agent Builder、AgentBench / Berkeley Function-Calling Leaderboard / τ-bench 等公开工作中的通用原则。
- 覆盖前端与后端两端的实现证据。
- 严格遵循 `docs/steering/` 下四份规范（DDD 架构、配置源、uv 包管理、中文 docstring）。

### 范围外（Out-of-Scope）

- 不修复评估中发现的业务代码问题；只给出建议，不改动 `epsilon-boot/src/` 与 `epsilon-client/src/` 中的业务代码。
- 不替换或重构现有架构、框架、Port/Adapter 组合。
- 不对生产环境执行压测、渗透测试、红蓝对抗。
- 不新增业务功能（后端路由/工具/模型接入/前端页面）。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| 评估报告 | Evaluation_Report | 由本特性产出的 Markdown 文档，位于 `docs/evaluation/` 下，覆盖七个评估维度、证据引用、评分与改进建议，不包含任何业务代码改动。 |
| 自动化评测脚本 | Evaluation_Script | 本特性产出的可独立执行脚本，位于 `tests/evaluation/` 或 `scripts/evaluation/` 下，输出结构化结果（JSON 或表格），度量 Agent 核心指标。 |
| 评估维度 | Evaluation_Dimension | 本特性定义的七个维度：架构与工程化、Agent 核心能力、模型与提示工程、安全与合规、可靠性与性能、可测试性与质量、前端/UX。 |
| 评估评分量表 | Evaluation_Rubric | 每个 Evaluation_Dimension 下使用的 1-5 级评分量表，显式引用业界公认框架中的具体条款作为判据。 |
| 证据引用 | Evidence_Reference | 评估报告中每条结论所附的证据，至少包含仓库内文件路径与行号，必要时附上下文摘录。 |
| 改进建议 | Improvement_Recommendation | 评估报告中针对每个 Evaluation_Dimension 列出的改进项，标注优先级（P0/P1/P2）、预期收益与实施难度。 |
| ReAct 循环 | ReAct_Loop | 后端 `ReActAgentAdapter` 实现的多轮推理-工具调用循环，由 `infrastructure/agent/react_agent_adapter.py` 提供。 |
| 工具注册表 | Tool_Registry | 后端 `ToolRegistry` 及其受限视图 `ScopedToolRegistry`，按 `allowed_tool_names` 暴露工具子集。 |
| 委派 | Agent_Delegation | 通过 `DelegateToAgentTool` + `DelegationAdapter` 将子任务派发给命名 Agent 的机制，`delegation_depth` 上限为 3。 |
| 上下文压缩 | Context_Compaction | `ContextCompactionPort` 的滑动窗口实现，保留全部 `SystemMessage` + 最后 N 条非 system 消息。 |
| 端口与适配器 | Port_Adapter | 六边形架构下 `domain/*/ports.py` 中的 Port 接口与 `infrastructure/` 中 Adapter 实现的对应关系。 |
| 业界框架 | Industry_Framework | 评估标准来源，包括 OpenAI Agents 最佳实践、Anthropic Agent 工程实践、LangChain/LangGraph Agent 模式、Google ADK/Vertex Agent Builder、AgentBench、Berkeley Function-Calling Leaderboard、τ-bench 等公开工作。 |
| 工具调用成功率 | Tool_Call_Success_Rate | Evaluation_Script 度量的核心指标之一：成功执行的工具调用次数占总工具调用次数的比例。 |
| 委派正确性 | Delegation_Correctness | Evaluation_Script 度量的核心指标之一：委派目标 Agent 正确、`delegation_depth` 不超限、返回结果被正确拼回父 Agent 的比例。 |
| 上下文压缩有效性 | Context_Compaction_Effectiveness | Evaluation_Script 度量的核心指标之一：压缩后 token 或消息条数下降比例与 SystemMessage 完整保留率。 |
| 回归评测 | Regression_Evaluation | QA/平台工程师通过 Evaluation_Script 周期性执行、对比历史结果判断 Agent 核心指标是否退化的流程。 |
| 工作区 | Workspace | `domain.workspace.Workspace` 抽象，所有文件系统工具通过它完成 I/O，解析后的路径不得越出 `WORKSPACE_ROOT`。 |
| 规范目录 | Steering_Docs | `docs/steering/` 下四份强制规范（`ddd-architecture.md`、`config-source.md`、`uv-package-manager.md`、`code-documentation.md`）。 |

## 需求

### 需求 1：多角色交付目标对齐

**用户故事：** 作为技术负责人、开发工程师与 QA/平台工程师，我希望本特性同时交付一份可汇报的 Evaluation_Report 与一套可复测的 Evaluation_Script，以便三类角色都能拿到与自己岗位匹配的产物。

#### 验收标准

1. THE Evaluation_Report SHALL 面向技术负责人给出"执行摘要"章节，包含整体结论、七个 Evaluation_Dimension 的评分汇总表、前三位高优先级 Improvement_Recommendation 与整体风险等级判定。
2. THE Evaluation_Report SHALL 面向开发工程师给出"改进清单"章节，列出全部 Improvement_Recommendation，并按 P0/P1/P2 优先级排序，每条包含：涉及文件、预期收益、实施难度、关联业界框架条款。
3. THE Evaluation_Script SHALL 面向 QA/平台工程师提供单命令入口，输出机器可读结果（JSON 或 CSV）与人类可读摘要（表格或 Markdown），并打印退出码（0 代表成功运行，非 0 代表脚本自身异常）。
4. FOR ALL 三类目标角色，THE Evaluation_Report SHALL 在开头"读者导览"小节标注各角色应优先阅读的章节锚点。

### 需求 2：评估维度全覆盖

**用户故事：** 作为技术负责人，我希望 Evaluation_Report 覆盖七个 Evaluation_Dimension，以便对 Agent 工作台做全景式诊断。

#### 验收标准

1. THE Evaluation_Report SHALL 至少包含以下 7 个独立章节，一一对应 Evaluation_Dimension：架构与工程化、Agent 核心能力、模型与提示工程、安全与合规、可靠性与性能、可测试性与质量、前端/UX。
2. FOR ALL Evaluation_Dimension，THE Evaluation_Report SHALL 在对应章节内给出该维度的 Evaluation_Rubric 定义与 1-5 级评分结果，并用自然语言说明评分判据。
3. FOR ALL Evaluation_Dimension，THE Evaluation_Report SHALL 明确列出该维度扫描的代码范围（后端目录 / 前端目录 / 配置文件），确保覆盖前端与后端两端实现。
4. WHEN 某个 Evaluation_Dimension 的代码范围仅存在于后端或前端一端，THE Evaluation_Report SHALL 在该章节以单独段落说明"为何另一端不适用"，而非留空。

### 需求 3：证据引用可追溯

**用户故事：** 作为开发工程师，我希望 Evaluation_Report 里每条结论都能追溯到代码位置，以便快速定位与复核。

#### 验收标准

1. FOR ALL Evaluation_Dimension，THE Evaluation_Report SHALL 至少提供 3 条 Evidence_Reference，每条包含：相对于仓库根的文件路径、起止行号、一句话证据描述。
2. FOR ALL Evidence_Reference，THE Evaluation_Report SHALL 使用格式 `path/to/file.py:Lstart-Lend` 或 `path/to/file.py:Lstart`，禁止仅写文件名或仅写目录。
3. WHEN Evidence_Reference 指向前端代码，THE Evaluation_Report SHALL 使用 `epsilon-client/src/...` 作为路径前缀；WHEN Evidence_Reference 指向后端代码，THE Evaluation_Report SHALL 使用 `epsilon-boot/src/...` 或 `epsilon-boot/config.properties` 等真实路径前缀。
4. IF 某条结论无法定位到具体代码行，THEN THE Evaluation_Report SHALL 显式标注"无直接代码证据"并给出推导路径（例如引用 `docs/` 主题文档），不得伪造行号。

### 需求 4：评分量表显式引用业界框架

**用户故事：** 作为技术负责人，我希望每个 Evaluation_Dimension 的评分判据能显式引用 Industry_Framework 条款，以便评估结论具备外部权威性。

#### 验收标准

1. FOR ALL Evaluation_Dimension，THE Evaluation_Rubric SHALL 在 1-5 级判据中至少引用 2 个不同 Industry_Framework 中的具体条款或最佳实践名称（例如"Anthropic — Tool use best practices"、"OpenAI — Agent design patterns"、"Berkeley Function-Calling Leaderboard — tool selection"）。
2. THE Evaluation_Report SHALL 在"评估方法"章节集中罗列本次引用的全部 Industry_Framework 来源条目，每条包含：框架名称、所引用的具体章节或条款标题、公开链接或出处说明。
3. WHEN 某条 Improvement_Recommendation 基于 Industry_Framework，THE Improvement_Recommendation SHALL 在条目中标注对应的框架来源。

### 需求 5：自动化评测脚本核心指标覆盖

**用户故事：** 作为 QA/平台工程师，我希望 Evaluation_Script 覆盖至少 3 项 Agent 核心指标，以便持续跟踪质量。

#### 验收标准

1. THE Evaluation_Script SHALL 至少度量以下三项指标：Tool_Call_Success_Rate、Delegation_Correctness、Context_Compaction_Effectiveness。
2. FOR ALL 三项核心指标，THE Evaluation_Script SHALL 在输出结果中包含：指标名称、分子/分母原始计数、最终比例或数值、本次运行样本数量。
3. THE Evaluation_Script SHALL 能独立运行，不要求真实 LLM Provider 可达——默认使用桩实现或录制样本驱动 ReAct_Loop、Agent_Delegation、Context_Compaction 等被测单元。
4. WHEN Evaluation_Script 运行结束，THE Evaluation_Script SHALL 输出一份 JSON 文件（默认写入 `tests/evaluation/reports/` 或 `scripts/evaluation/reports/` 下，文件名含时间戳）并在标准输出打印表格摘要。
5. IF Evaluation_Script 内部任一被测项抛出异常，THEN THE Evaluation_Script SHALL 将该项记为失败样本、继续运行后续样本，并在摘要中汇总失败数，禁止因单个样本异常中止整批执行。

### 需求 6：评测脚本的可执行规范与依赖管理

**用户故事：** 作为开发工程师，我希望 Evaluation_Script 遵循仓库的 `uv` 与配置规范，以便本地一键执行并纳入 CI。

#### 验收标准

1. THE Evaluation_Script 后端侧实现 SHALL 通过 `uv run` 驱动（例如 `uv run python -m tests.evaluation.run` 或 `uv run pytest tests/evaluation -m evaluation`），禁止在文档或脚本中使用 `pip` / `poetry` / `pipenv` / `conda` 命令。
2. THE Evaluation_Script 前端侧指标（如存在）SHALL 通过 `bun` 或 `npm` 脚本驱动，并在 `epsilon-client/package.json` 对应目录下单独声明，禁止影响既有 `dev` / `build` / `start` / `lint` 脚本。
3. IF Evaluation_Script 需要新增 Python 依赖，THEN THE Evaluation_Script SHALL 通过 `uv add --group evaluation <package>` 或等价可组命令登记，不得直接编辑 `pyproject.toml` 绕过锁文件。
4. THE Evaluation_Script SHALL 所有 Python 模块、类、公开函数与方法均提供中文 docstring，符合 Steering_Docs 中 `code-documentation.md` 规范。

### 需求 7：不改动业务代码的硬约束

**用户故事：** 作为技术负责人，我希望评估工作不带来业务代码变更风险，以便评估过程与业务迭代解耦。

#### 验收标准

1. THE Evaluation_Report 与 THE Evaluation_Script SHALL 只新增文件于以下三处：`docs/evaluation/`、`tests/evaluation/`、`scripts/evaluation/`；禁止新增或修改其他目录下的文件。
2. WHEN 本特性交付完成，THE 交付变更 SHALL 能通过 `git diff --name-only` 验证：所有变更路径以 `docs/evaluation/` / `tests/evaluation/` / `scripts/evaluation/` 之一开头。
3. IF 评估过程中发现 `epsilon-boot/src/` 或 `epsilon-client/src/` 内有问题，THEN THE Evaluation_Report SHALL 仅以 Improvement_Recommendation 形式记录，不得在本次交付中修改这些文件。
4. THE Evaluation_Script SHALL 在运行时禁止对 `epsilon-boot/src/`、`epsilon-client/src/`、`epsilon-boot/config.properties` 执行写操作；其桩/样本所需的临时文件必须写入本特性允许的三个目录之一或系统临时目录。

### 需求 8：Agent 核心能力评估条目

**用户故事：** 作为开发工程师，我希望 Evaluation_Report 的"Agent 核心能力"章节精确覆盖 ReAct_Loop、Tool_Registry、Agent_Delegation、Context_Compaction 与权限隔离，以便对应到具体改进动作。

#### 验收标准

1. THE Evaluation_Report "Agent 核心能力"章节 SHALL 对 ReAct_Loop 的以下要点分别给出结论与 Evidence_Reference：最大轮次、工具权限拒绝回写为 ToolMessage 的设计、tool_calls 序列化、异常处理路径。
2. THE Evaluation_Report "Agent 核心能力"章节 SHALL 对 Tool_Registry 给出结论与 Evidence_Reference，包含：内置工具清单、`ScopedToolRegistry.create_scoped_view` 受限暴露机制、Workspace 路径归一化。
3. THE Evaluation_Report "Agent 核心能力"章节 SHALL 对 Agent_Delegation 给出结论与 Evidence_Reference，包含：`delegation_depth` 上限校验、命名 Agent 注册、循环依赖解法（`DelegateToAgentTool` 后置注册形成 DAG）。
4. THE Evaluation_Report "Agent 核心能力"章节 SHALL 对 Context_Compaction 给出结论与 Evidence_Reference，说明 SystemMessage 是否被无损保留、窗口 N 如何由配置驱动。

### 需求 9：安全与合规评估条目

**用户故事：** 作为技术负责人，我希望安全与合规维度对工具沙箱、凭证隔离、Workspace 边界、注入防御分别给出结论，以便识别合规风险。

#### 验收标准

1. THE Evaluation_Report "安全与合规"章节 SHALL 针对 `ShellExecTool`、`PythonExecTool` 分别评估：环境变量脱敏（`API_KEY`/`PASSWORD`/`SECRET`/`TOKEN`/`CREDENTIAL` 剥离）、超时与输出截断、`cwd` 被 Workspace 锁定、启动期路径校验（`SHELL_EXEC_WORKING_DIR` / `PYTHON_EXEC_WORKING_DIR`）。
2. THE Evaluation_Report "安全与合规"章节 SHALL 针对 Workspace 给出结论：文件系统工具是否通过 `Workspace` 完成 I/O、`SymlinkGuard` / `IdentityGuard` 是否覆盖逃逸场景。
3. THE Evaluation_Report "安全与合规"章节 SHALL 评估凭证管理来源是否符合 Steering_Docs 中 `config-source.md` 的规则（优先 `config.properties`，`.env` 仅用于覆盖），并给出 Evidence_Reference。
4. THE Evaluation_Report "安全与合规"章节 SHALL 对 prompt 注入与工具滥用风险至少列出 2 个 Improvement_Recommendation，引用 Industry_Framework 中的对应条款。

### 需求 10：可靠性、性能与可测试性评估条目

**用户故事：** 作为开发工程师，我希望可靠性/性能与可测试性两个维度的结论基于当前测试代码与可观测性实现，以便定位具体改进方向。

#### 验收标准

1. THE Evaluation_Report "可靠性与性能"章节 SHALL 评估：SSE 流式响应错误恢复、ReAct_Loop 失败路径、模型 Provider Round-Robin 与热重载、延迟与 token 成本观测手段；每项给出 Evidence_Reference。
2. THE Evaluation_Report "可测试性与质量"章节 SHALL 基于 `epsilon-boot/test/` 目录结构与命名给出结论，说明单元、属性、集成测试的分布与缺口。
3. THE Evaluation_Report "可测试性与质量"章节 SHALL 明确说明本次交付的 Evaluation_Script 如何填补回归评测空白、未来如何接入 Regression_Evaluation 流水。
4. WHILE Evaluation_Script IN 回归执行场景，WHEN 历史结果 JSON 作为基线传入，THE Evaluation_Script SHALL 计算并输出每项核心指标相较基线的差值，并在差值超过阈值（默认 5 个百分点，可通过参数配置）时以非零退出码提示，供 CI 判定失败。

### 需求 11：前端/UX 评估与可追溯性

**用户故事：** 作为 QA/平台工程师，我希望前端 UX 维度的结论包括可追溯性（trace）与用户反馈，以便验证前端是否支撑观测与迭代。

#### 验收标准

1. THE Evaluation_Report "前端/UX"章节 SHALL 基于 `epsilon-client/src/` 给出结论，至少覆盖：`ChatPanel` 流式增量渲染、`TaskWorkspace` 结果展示、模型选择与会话管理、SSE `[DONE]` 协议处理、错误与中止（AbortController）交互。
2. THE Evaluation_Report "前端/UX"章节 SHALL 评估前端是否暴露 trace 或任务执行轨迹（如 `execution_trace`），如存在，给出 Evidence_Reference；如不存在，作为 Improvement_Recommendation 列出。
3. THE Evaluation_Report "前端/UX"章节 SHALL 评估用户反馈通道（如点赞/点踩/复制/重试入口），如缺失，作为 Improvement_Recommendation 列出，并引用 Industry_Framework 中关于人类反馈的条款。

### 需求 12：Steering 规范合规校验

**用户故事：** 作为开发工程师，我希望本特性交付物本身符合 Steering_Docs 规范，以便示范性地满足仓库约束。

#### 验收标准

1. THE Evaluation_Report 与 THE Evaluation_Script SHALL 不引入任何对 `domain/` 层的新增依赖，也不新增 `domain/` → `infrastructure/` 方向的导入。
2. THE Evaluation_Script 中所有新增 Python 源码 SHALL 位于 `tests/evaluation/` 或 `scripts/evaluation/` 下，禁止在 `epsilon-boot/src/domain/`、`epsilon-boot/src/application/`、`epsilon-boot/src/infrastructure/`、`epsilon-boot/src/common/` 目录下新增文件。
3. IF Evaluation_Script 需要读取运行时配置，THEN THE Evaluation_Script SHALL 通过读取 `epsilon-boot/config.properties` 或 `.env` 获取，不得硬编码凭证；且默认使用桩实现避免真实凭证进入样本。
4. THE Evaluation_Report 与 THE Evaluation_Script 文档 SHALL 使用中文撰写，与仓库既有文档风格保持一致。

### 需求 13：改进建议的优先级与可执行性

**用户故事：** 作为开发工程师，我希望每条 Improvement_Recommendation 可被转成后续需求卡片，以便快速转入实施。

#### 验收标准

1. FOR ALL Improvement_Recommendation，THE Evaluation_Report SHALL 提供：唯一编号、标题、优先级（P0/P1/P2）、问题描述、建议动作、预期收益、实施难度（S/M/L）、关联的 Evaluation_Dimension 与 Evidence_Reference。
2. THE Evaluation_Report SHALL 在"改进清单"章节尾部给出按优先级分组的合计计数（P0/P1/P2 各多少条）。
3. WHEN 同一问题横跨多个 Evaluation_Dimension，THE Improvement_Recommendation SHALL 在"关联的 Evaluation_Dimension"字段列出全部维度，并只在主维度章节详细展开，其他维度章节使用编号引用避免重复。

### 需求 14：交付目录与产物清单

**用户故事：** 作为技术负责人，我希望本特性交付后能在固定位置找到全部产物，以便归档与后续审阅。

#### 验收标准

1. THE Evaluation_Report SHALL 命名为 `docs/evaluation/report.md`（主报告）；如需要拆分子报告，子文件 SHALL 位于 `docs/evaluation/` 下并在主报告中用相对链接引用。
2. THE Evaluation_Script SHALL 至少提供入口模块/脚本（例如 `tests/evaluation/run.py` 或 `scripts/evaluation/run.sh`），入口在其 docstring 中说明用途、参数、输出路径。
3. THE 交付清单 SHALL 在 `docs/evaluation/report.md` "附录：交付物清单"章节列出：全部新增文件路径、对应 Evaluation_Dimension 或指标、可执行命令示例。
4. THE Evaluation_Script 产出的 JSON 报告目录（例如 `tests/evaluation/reports/` 或 `scripts/evaluation/reports/`）SHALL 在首次运行前由脚本自动创建，若该目录不存在也不得因此失败。
