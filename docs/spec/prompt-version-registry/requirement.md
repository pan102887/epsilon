# 需求文档：Prompt 资产目录与版本化注册（Prompt Version Registry）

## 简介

### 背景

当前 `epsilon-boot` 后端的系统提示词（system prompt）以两种方式散落在代码与配置中：

- **静态 Chat 提示词**：`epsilon-boot/src/infrastructure/chat/chat_config.py` 中 `ChatConfig.system_prompt: str = "你是一个有用的 AI 助手。"` 作为字段默认值存在，运行期可由 `CHAT_SYSTEM_PROMPT` 环境变量或 `config.properties` 覆盖；随后 `ChatConfig._append_workspace_path_guidance`（`@model_validator(mode="after")`）幂等地把工作区路径规范文案追加到末尾。容器装配时 `container_config.py` 把 `chat_config.system_prompt` 作为字符串传入 `ChatServiceAdapter`，再被塞进 `AgentConfig.system_prompt`。
- **动态 Task 提示词**：`epsilon-boot/src/infrastructure/task/task_agent_adapter.py` 的 `TaskAgentAdapter.build_system_prompt(task)` 把 `Task.goal`、`Task.input_data`、`Task.constraints`、`Task.output_format` 按固定模板拼装成 system prompt，非静态文本。

这种布局存在三个已暴露的运维问题：

1. **审计缺失**：prompt 内容变更等价于"字段默认值变更 + 配置文件变更"，git diff 上缺少"这是 prompt 第 N 版"的独立线索；回滚时只能通过 `git checkout` 配置/代码文件，颗粒度与其他改动耦合。
2. **指标与 prompt 变更无法关联**：模型 `latency_ms` / `usage` / 对话质量出现回归时，无法从日志或 trace 中直接查到"本次调用用的是哪一版 prompt"，因此无法区分是模型漂移还是 prompt 改动带来的影响。
3. **多 Agent 场景缺少复用单元**：`NamedAgentConfig.system_prompt` 当前是裸字符串，没有跨 Agent 复用、跨版本灰度的抽象单元。未来若新增多个命名 Agent（例如"代码审查 Agent"、"文案润色 Agent"），每个 Agent 的 prompt 都以裸字符串形式塞进代码组合根，难以独立演进。

### 动机

建立"Prompt 资产目录（文件化）+ 版本键配置（`config.properties` 驱动）+ prompt_id 追踪字段（值对象 + 可观测性）"三段式机制，把 prompt 从"配置默认值 / 代码字符串"这一类贫语义数据升级为**有版本、有审计、有追踪 ID** 的项目资产，使之满足：

- prompt 的每一次改动都对应一次 git 可追溯的文件新增（`prompts/<name>/v<N>.md`），历史版本**不被覆盖**，可与当时产出的指标长期并存；
- 服务端实际加载哪一版由 `config.properties` 的 `PROMPT_<NAME>_VERSION` 键决定，切换与回滚只需改一行配置并重启；
- 所有下游可观测性信号（结构化日志、OpenTelemetry span 属性、聊天 / 任务响应）都携带 `prompt_id`（形如 `chat-default@v3`），把模型指标与 prompt 版本绑死。

### 范围

**纳入（In Scope）**：

1. 在仓库内建立 **Prompt 资产目录** `Prompt_Asset_Directory`（`epsilon-boot/prompts/`，理由见需求 1），并约定命名、版本号格式、文件编码、换行与版本发布规则；
2. 定义 **Prompt 注册表端口** `Prompt_Registry_Port`（领域层）与 **文件系统实现适配器** `Filesystem_Prompt_Registry_Adapter`（基础设施层），负责在启动期加载目录下的所有静态 prompt 并返回 `Loaded_Prompt`（含 `prompt_id`、`content`）；
3. 引入 **Prompt 版本配置** `Prompt_Version_Config`（`env_prefix="PROMPT_"`，如 `PROMPT_CHAT_DEFAULT_VERSION=v3`），承担"从 prompt 名映射到目标版本号"的唯一配置职责；
4. 在领域值对象 `ChatConfig`（注意：即 `epsilon-boot/src/infrastructure/chat/chat_config.py` 中的配置类本身，本特性中归并为"Prompt 消费方"语义角色；而 `AgentConfig` 与 `NamedAgentConfig` 是领域值对象）与 `AgentConfig`、`NamedAgentConfig` 中**新增 `prompt_id` 字段**，作为 prompt 内容的伴随标识；`system_prompt` 字段继续保留（承载实际文本），二者在同一对象中由"内容"与"身份"两种视角并存；
5. 启动期校验：任一 `PROMPT_<NAME>_VERSION` 指向的文件必须存在、UTF-8 可解码、非空；任一校验失败以 `Startup_Failure` 语义拒绝启动，不静默降级；
6. 保留并明确既有 `ChatConfig._append_workspace_path_guidance` 的工作区路径规范追加语义，规定其与版本化 prompt 文件之间的组合顺序（见需求 6）；
7. 可观测性：把 `prompt_id` 作为**结构化日志字段**与 **OpenTelemetry span 属性** 写入聊天与任务两条链路；在聊天 HTTP 响应（`ChatResponseVO`）与任务 HTTP 响应（`TaskResult` 或响应模型）中附带 `prompt_id`，使前端 / 调用方可直接用于回放；
8. 向后兼容：对历史上通过 `CHAT_SYSTEM_PROMPT` 环境变量或 `config.properties` 键直接覆盖 prompt 文本的运维方，提供**显式迁移路径**与**冲突时的 fail-fast 行为**（见需求 8）；
9. `TaskAgentAdapter.build_system_prompt` 的动态模板处理策略（见需求 5）：纳入版本化机制，但以"模板版本"而非"prompt 文本版本"形式管理，且其运行期生成的实际文本不落盘；
10. 测试覆盖：对 `Filesystem_Prompt_Registry_Adapter`、`Prompt_Version_Config`、`prompt_id` 填充路径与错误分支提供 unit + property 测试；遵循 [docs/steering/uv-package-manager.md](../../steering/uv-package-manager.md)，测试依赖安装仅使用 `uv`。

**不在本期范围（Out of Scope）**：

- **Prompt 内容的 A/B 分流**：不提供"同名 prompt 同时并行加载多版并按流量切分"的能力；本期一次只选定一版；未来若需要，需在 `Prompt_Registry_Port` 之上单独建特性，不在本期承诺；
- **运行期热切换 prompt 版本**：不在运行期监听 `PROMPT_<NAME>_VERSION` 变化；切换需要重启服务（与 `ChatConfig` 当前的非热更新一致）；即便 `PropertiesBaseSettings.hot_reload` 被设为 True，`Prompt_Version_Config` 也**显式**不支持热切换（避免请求中途换 prompt 导致同一会话上下文下的 system 消息前后不一致）；
- **前端管理 UI**：不为 `epsilon-client/` 提供 prompt 编辑、版本对比等界面；
- **多租户 / 多环境的 prompt 隔离**：单一全局命名空间，`prod` / `staging` 共用同一份 `prompts/` 目录；
- **Prompt 模板引擎**：不引入 Jinja2、Handlebars 等模板引擎；`prompts/<name>/v<N>.md` 文件内容为**纯文本（Plain Markdown）**，不做变量插值；动态拼装仍由 `TaskAgentAdapter.build_system_prompt` 在代码里完成（见需求 5）；
- **Prompt 的多语言分支**：不为同一个 `name` 管理 `zh` / `en` 等多语言子目录，本期约定 prompt 文本语言与项目既有中文风格一致；
- **Prompt 内容的敏感信息扫描与 DLP**：不做密钥/Token 泄漏检测，沿用代码评审流程；
- **向外暴露 `Prompt_Registry_Port` HTTP API**：本期仅供容器装配内部使用，不注册对外路由。

## 术语表

| 业务术语 | 英文标识符 | 定义 |
| --- | --- | --- |
| Prompt 资产目录 | `Prompt_Asset_Directory` | 仓库内存放所有静态系统提示词文件的根目录，本期约定为 `epsilon-boot/prompts/`（与 `config.properties` 同级；位于后端包根内，而非仓库根 `/workspace/prompts/`）。目录结构固定为 `<name>/v<N>.md`。纳入 git 版本控制。 |
| Prompt 名称 | `Prompt_Name` | Prompt 资产的逻辑标识符，小写 + 连字符（如 `chat-default`、`task-template`）。在 `Prompt_Asset_Directory` 下表现为一级子目录名。一个 `Prompt_Name` 对应一组版本文件。 |
| Prompt 版本号 | `Prompt_Version_Tag` | 单调递增的版本号，格式固定为 `v<正整数>`（如 `v1`、`v2`、`v10`）；不使用语义版本（SemVer）。新版本**只新增文件**，不得原地编辑既有版本文件。删除历史版本需单独评审并更新所有引用。 |
| Prompt 文件 | `Prompt_Asset_File` | 单个 prompt 版本的物理载体，路径为 `prompts/<Prompt_Name>/<Prompt_Version_Tag>.md`，UTF-8 编码，LF 换行，内容为纯文本（Markdown 语法允许但不做渲染）。文件首行允许可选的 YAML front matter（后续扩展用，本期不强制）。 |
| Prompt 标识符 | `Prompt_Id` | 组合标识符，格式 `<Prompt_Name>@<Prompt_Version_Tag>`（如 `chat-default@v3`）。唯一、稳定，可写入日志 / trace / 响应体；是本特性引入的核心可观测性字段。 |
| 已加载 Prompt | `Loaded_Prompt` | 启动期从 `Prompt_Asset_File` 解析得到的不可变值对象，包含 `prompt_id: str`、`name: str`、`version: str`、`content: str` 四个字段。是 `Prompt_Registry_Port.get(name)` 的返回类型。 |
| Prompt 注册表端口 | `Prompt_Registry_Port` | 领域层 Port（`domain/prompt/ports.py`，使用 `Protocol` 定义），声明 `get(name: str) -> Loaded_Prompt` 与 `list_names() -> list[str]` 两个方法；不触发 I/O（I/O 在适配器构造时已完成）。 |
| 文件系统 Prompt 注册表适配器 | `Filesystem_Prompt_Registry_Adapter` | `Prompt_Registry_Port` 的基础设施实现，位于 `infrastructure/prompt/filesystem_prompt_registry_adapter.py`。在构造时一次性扫描 `Prompt_Asset_Directory` 并按 `Prompt_Version_Config` 决定的版本加载 `Loaded_Prompt` 进内存字典，构造完成后即对外只读。 |
| Prompt 版本配置 | `Prompt_Version_Config` | 基于 `PropertiesBaseSettings` 的配置类（`env_prefix="PROMPT_"`），字段名形如 `chat_default_version: str = "v1"`，对应配置键 `PROMPT_CHAT_DEFAULT_VERSION`。承载"从 `Prompt_Name` 到 `Prompt_Version_Tag` 的映射"这一唯一职责。 |
| 版本键命名规则 | `Prompt_Version_Key_Scheme` | `config.properties` / 环境变量中 prompt 版本键的命名规则：`PROMPT_<NAME_UPPER_SNAKE>_VERSION`，其中 `NAME_UPPER_SNAKE` 是 `Prompt_Name` 把连字符替换为下划线后全大写的形式。例：`chat-default` → `PROMPT_CHAT_DEFAULT_VERSION`。 |
| Workspace 路径规范追加 | `Workspace_Path_Guidance_Appender` | 既有 `ChatConfig._append_workspace_path_guidance` 的职责角色化命名：在 `system_prompt` 末尾幂等追加 `_WORKSPACE_PATH_GUIDANCE` 文案。本特性保留其存在，但其作用对象改为"从 `Loaded_Prompt.content` 得到的文本"（而非 `ChatConfig.system_prompt` 字段默认值）。 |
| Prompt 消费方 | `Prompt_Consumer` | 在本特性中承担"从 `Prompt_Registry_Port` 获取 prompt 并组装进 `AgentConfig` / `NamedAgentConfig`"的角色。本期已知的 `Prompt_Consumer`：`ChatServiceAdapter`（消费 `chat-default`）、`TaskAgentAdapter`（消费 `task-template`）以及未来注册命名 Agent 的组合根代码（消费对应 `Prompt_Name`）。 |
| Task 动态 Prompt 模板 | `Task_Dynamic_Prompt_Template` | `TaskAgentAdapter.build_system_prompt` 当前内置的拼装逻辑（`goal + ## 输入数据 + ## 约束条件 + ## 期望输出格式` 顺序），把 `Task` 结构化字段渲染成 system prompt 文本。本特性中把"模板骨架"纳入 `Prompt_Asset_File`（`prompts/task-template/v<N>.md`），但运行期的实际填充文本**不落盘**。 |
| Prompt 启动失败 | `Prompt_Startup_Failure` | 在 `Filesystem_Prompt_Registry_Adapter` 构造时因 `Prompt_Asset_Directory` 不存在、目标版本文件缺失、UTF-8 解码失败、内容为空、或 `Prompt_Version_Config` 引用了未注册的 `Prompt_Name` 等原因导致应用无法完成装配时，以 fail-fast 方式拒绝启动的语义。对应的异常应继承自 `ConfigurationError`（参考 `common/configuration/configuration_utils.py` 中的既有异常类型）。 |
| Prompt 冲突检测 | `Prompt_Conflict_Detection` | 当 `CHAT_SYSTEM_PROMPT`（或其他历史的"prompt 文本直写"型配置键）与 `Prompt_Version_Config` 同时出现时的处理机制：启动期检测并触发 `Prompt_Startup_Failure`，拒绝以"哪一个优先"的默默合并方式继续。 |
| Prompt Id 追踪 | `Prompt_Id_Propagation` | 把 `Loaded_Prompt.prompt_id` 从 `AgentConfig` / `NamedAgentConfig` / `ChatResponseVO` 向下传递到 `logger.info` 的结构化 `extra` 字段与 OpenTelemetry span 属性 `prompt.id` 的全过程。 |
| Prompt 回退语义 | `Prompt_Fallback_Semantics` | 对未配置 `PROMPT_<NAME>_VERSION` 键的 `Prompt_Name` 的处理：**不做隐式默认**，`Filesystem_Prompt_Registry_Adapter` 在 `Prompt_Consumer` 请求该 `Prompt_Name` 时抛出领域错误 `Prompt_Not_Configured_Error`；组合根代码负责要么配齐键要么不注册对应消费方。禁止把"找不到版本 → 用 v1"类的隐式回退落地。 |
| Prompt 未找到错误 | `Prompt_Not_Found_Error` | `Prompt_Registry_Port.get(name)` 在 `name` 未注册或版本未加载时抛出的领域错误。在代码路径中只应在存在编程错误时被触发（正常路径在启动期已校验）。 |
| Prompt 未配置错误 | `Prompt_Not_Configured_Error` | `Filesystem_Prompt_Registry_Adapter` 在构造时发现 `Prompt_Asset_Directory` 下存在 `<name>/` 子目录但 `Prompt_Version_Config` 中无对应字段（或字段为空字符串）时抛出的子类化 `ConfigurationError`，触发 `Prompt_Startup_Failure`。 |

### Prompt 资产目录位置选择（说明）

候选位置：

- **A. 仓库根 `/workspace/prompts/`**：对仓库根一级可见，跨子项目共享便利；缺点是与后端包的启动根（`epsilon-boot/`）脱钩，`Filesystem_Prompt_Registry_Adapter` 必须通过 `_find_file`（参考 `configuration_utils.py`）向上多层查找，存在 Docker 镜像构建时漏拷贝风险（镜像构建上下文通常就是 `epsilon-boot/`）。
- **B. 后端包内 `epsilon-boot/prompts/`**（**推荐**）：与 `config.properties` 同级，构建镜像时天然一并打包；与现有配置加载路径（`_PROPERTIES_FILE = _find_file("config.properties")`）语义对称；不跨子项目共享，但本特性 Out of Scope 已明确不做多项目共享。
- **C. `epsilon-boot/src/infrastructure/prompt/assets/`**：与代码贴得最近；缺点是把"资产"放进 `src/` 会模糊"可执行代码 / 资产数据"的边界，且 `uv` 打包、`pip install -e .` 等流程对 `src/` 下非 `.py` 文件的处理需要 `package-data` 特殊声明，运维复杂度不必要上升。

**本特性选定 B**：`epsilon-boot/prompts/`。理由：（1）与 `config.properties` 对称；（2）Docker 镜像构建上下文一致；（3）与 [docs/steering/config-source.md](../../steering/config-source.md) 关于"配置优先写入 `epsilon-boot/config.properties`"的现有习惯一致，prompt 资产视为"大颗粒、非键值对的配置数据"，与 `config.properties` 放在同一个后端根下便于统一运维。

## 需求

### 需求 1：Prompt 资产目录结构与命名、版本化规则

**用户故事：** 作为 prompt 运维者，我希望在一个有明确目录结构与版本化规则的资产目录中管理系统提示词，以便 git 历史能清晰呈现"第 N 版 prompt"的新增、切换与回滚。

#### 验收标准

1. THE `Prompt_Asset_Directory` SHALL 固定为 `epsilon-boot/prompts/`，与 `epsilon-boot/config.properties` 同级；其存在性由仓库内的 `.gitkeep` 文件或至少一份 `Prompt_Asset_File` 保证，不允许在运行期动态创建目录以绕过启动期校验。
2. FOR ALL 已注册的 `Prompt_Name`, THE `Prompt_Asset_Directory` SHALL 包含一个与之同名（小写+连字符）的一级子目录，目录下至少存在一个符合 `Prompt_Version_Tag` 格式的 `Prompt_Asset_File`。
3. THE `Prompt_Version_Tag` SHALL 固定为 `v<正整数>` 格式（大小写敏感，小写 `v` + 无前导零的十进制整数）；`v0` 视为无效版本号；不使用语义版本（`v1.0.0`、`v2-rc1` 等均视为无效）。
4. THE `Prompt_Asset_File` SHALL 使用 UTF-8 编码、LF 换行、`.md` 扩展名；文件内容为纯文本（Markdown 语法允许，本期不做模板变量插值）。
5. FOR ALL 已发布的 `Prompt_Asset_File`, THE 发布流程 SHALL 遵循"**只新增、不原地编辑**"规则：修改 prompt 即新增 `v<N+1>.md` 文件；`v<N>.md` 保留不动（除非有独立评审记录），以保证历史版本可被未来的调用方作为回放基线。
6. THE 仓库 SHALL 在本特性落地时至少包含两份 `Prompt_Asset_File`：`prompts/chat-default/v1.md`（Chat 默认系统提示词）与 `prompts/task-template/v1.md`（Task 动态模板骨架），分别对应既有 `CHAT_SYSTEM_PROMPT` 默认值迁移和 `TaskAgentAdapter.build_system_prompt` 的模板骨架外置。
7. FOR ALL 新增的 `Prompt_Name`, THE 添加流程 SHALL 同时新增目录 + `v1.md` + `Prompt_Version_Config` 对应字段的默认值或 `config.properties` 对应键，禁止仅新增文件而不配置版本键（否则 `Filesystem_Prompt_Registry_Adapter` 构造期会触发 `Prompt_Not_Configured_Error`）。

### 需求 2：Prompt 版本配置（`Prompt_Version_Config`）与 `config.properties` 集成

**用户故事：** 作为运维者，我希望通过修改 `config.properties` 的一行配置就能切换 prompt 版本，而无需触碰代码或 prompt 文件内容本身。

#### 验收标准

1. THE `Prompt_Version_Config` SHALL 作为 `PropertiesBaseSettings` 子类存在于 `infrastructure/prompt/prompt_version_config.py`，`model_config = SettingsConfigDict(env_prefix="PROMPT_")`；每个 `Prompt_Name` 对应一个 `<name_snake>_version: str` 字段，其中 `name_snake` 为 `Prompt_Name` 把连字符替换为下划线的形式。
2. THE `Prompt_Version_Config` SHALL 为当前已知的两个 `Prompt_Name` 提供以下字段：`chat_default_version: str` 与 `task_template_version: str`；字段默认值在代码中显式写为 `"v1"`。
3. FOR ALL `PROMPT_<NAME>_VERSION` 键, THE 配置加载 SHALL 遵循 `Prompt_Version_Key_Scheme`：`Prompt_Name` 为 `chat-default` 对应键 `PROMPT_CHAT_DEFAULT_VERSION`；`Prompt_Name` 为 `task-template` 对应键 `PROMPT_TASK_TEMPLATE_VERSION`。
4. WHEN 运维者在 `epsilon-boot/config.properties` 中将 `PROMPT_CHAT_DEFAULT_VERSION` 从 `v1` 改为 `v3`, THE 服务 SHALL 在下一次启动时加载 `prompts/chat-default/v3.md` 而非 `v1.md`；原 `v1.md` 文件不得被读取、不得被删除。
5. THE `Prompt_Version_Config` SHALL 不启用 `hot_reload`（保留 `PropertiesBaseSettings.hot_reload: ClassVar[bool] = False` 的默认值）；即便运维者在运行期修改 `config.properties`，`Filesystem_Prompt_Registry_Adapter` 也不得重新加载；切换必须通过重启服务生效。
6. IF `PROMPT_<NAME>_VERSION` 配置值不符合 `v<正整数>` 的 `Prompt_Version_Tag` 格式，THEN THE `Prompt_Version_Config` 加载 SHALL 触发 `Prompt_Startup_Failure`（具体抛出 `ConfigurationError` 的子类 `Invalid_Prompt_Version_Tag_Error`），错误消息中必须指明字段名、实际取值、期望格式示例。
7. FOR ALL `Prompt_Version_Config` 的新增字段, THE 配置编写者 SHALL 同时更新 `epsilon-boot/config.properties` 模板增加对应键与中文注释，遵循 [docs/steering/config-source.md](../../steering/config-source.md) "配置优先写入 `config.properties`" 原则。

### 需求 3：`Prompt_Registry_Port` 领域端口与 `Filesystem_Prompt_Registry_Adapter` 基础设施实现

**用户故事：** 作为后端开发者，我希望通过 DDD Port/Adapter 形式访问 prompt 资源，领域层与应用层不感知文件系统细节，为未来替换为 DB / 远端服务后端留出扩展位置。

#### 验收标准

1. THE `Prompt_Registry_Port` SHALL 以 `Protocol` 形式定义在 `epsilon-boot/src/domain/prompt/ports.py`，声明同步方法 `get(name: str) -> Loaded_Prompt` 与 `list_names() -> list[str]`；不声明任何异步方法、不声明 I/O 相关异常（I/O 在适配器构造阶段已完成）。
2. THE `Loaded_Prompt` SHALL 以 `frozen dataclass` 形式定义在 `epsilon-boot/src/domain/prompt/value_objects.py`，含字段 `prompt_id: str`、`name: str`、`version: str`、`content: str`；`__post_init__` 中校验 `content` 非空白、`prompt_id` 等于 `f"{name}@{version}"`。
3. THE `Filesystem_Prompt_Registry_Adapter` SHALL 位于 `epsilon-boot/src/infrastructure/prompt/filesystem_prompt_registry_adapter.py`，通过构造参数接收 `Prompt_Asset_Directory` 路径、`Prompt_Version_Config` 实例；构造阶段一次性扫描目录、加载每个已配置 `Prompt_Name` 的目标版本文件到内存字典。
4. WHEN `Filesystem_Prompt_Registry_Adapter` 构造完成后, THE 适配器 SHALL 对外仅提供只读访问；`get(name)` 方法 SHALL 直接返回内存字典中的 `Loaded_Prompt` 引用，不触发任何磁盘 I/O。
5. IF `Prompt_Registry_Port.get(name)` 在运行期被传入未注册的 `name`, THEN THE 适配器 SHALL 抛出 `Prompt_Not_Found_Error`（领域异常，继承自 `RuntimeError`），错误消息包含 `name` 与已注册的 `Prompt_Name` 列表。
6. THE `domain/prompt/` 目录 SHALL 不导入任何 `src/infrastructure/*` 模块、不导入 `pydantic-settings`、不导入文件系统 SDK；依赖方向严格遵循 [docs/steering/ddd-architecture.md](../../steering/ddd-architecture.md)（`domain/` 只允许依赖标准库与 `common/` 中与业务无关的共享抽象）。
7. THE `Filesystem_Prompt_Registry_Adapter` 与 `Prompt_Registry_Port` SHALL 在 `epsilon-boot/src/application/container_config.py` 中完成装配：作为应用启动阶段的组合根代码同时引用领域 Port 与基础设施 Adapter，这符合 [docs/steering/ddd-architecture.md](../../steering/ddd-architecture.md) "允许的例外" 第一条。

### 需求 4：`AgentConfig` / `NamedAgentConfig` / Chat 编排的 `prompt_id` 字段集成

**用户故事：** 作为后端开发者，我希望每一次 Agent Loop 的调用都携带可追溯的 `prompt_id`，使日志、trace、响应三处都能对齐到具体某一版 prompt。

#### 验收标准

1. THE `AgentConfig`（`epsilon-boot/src/domain/agent/value_objects.py`）SHALL 新增必填字段 `prompt_id: str`；`__post_init__` SHALL 校验 `prompt_id` 非空且形如 `<name>@v<正整数>`，否则抛出 `ValueError`。
2. THE `NamedAgentConfig`（同文件）SHALL 新增必填字段 `prompt_id: str`；校验规则同上。
3. FOR ALL `Prompt_Consumer` 在构造 `AgentConfig` / `NamedAgentConfig` 时, THE 构造代码 SHALL 通过 `Prompt_Registry_Port.get(name)` 一次性取回 `Loaded_Prompt`，同时把 `Loaded_Prompt.content` 写入 `system_prompt` 字段、把 `Loaded_Prompt.prompt_id` 写入 `prompt_id` 字段，二者来自同一次 `get` 调用，禁止分别来源于不同注册表查询。
4. THE `ChatServiceAdapter`（`epsilon-boot/src/infrastructure/chat/chat_service_adapter.py`）SHALL 通过构造参数接收 `Prompt_Registry_Port` 实例而非裸 `system_prompt: str`；原构造参数 `system_prompt: str` 替换为 `prompt_registry: Prompt_Registry_Port`；`_system_prompt: str` 内部字段替换为 `_loaded_prompt: Loaded_Prompt`，在构造时完成 `registry.get("chat-default")` 一次性解析并缓存。
5. THE `ChatResponseVO`（`epsilon-boot/src/domain/chat/value_objects.py`）SHALL 新增字段 `prompt_id: str`；`__post_init__` 校验同 `AgentConfig.prompt_id`；`ChatServiceAdapter` 构造 `ChatResponseVO` 时 SHALL 把当次调用使用的 `Loaded_Prompt.prompt_id` 写入该字段。
6. WHILE `ChatServiceAdapter` 处于流式响应路径（`stream=True`）, WHEN 最终 SSE 结束事件被发出, THE 适配器 SHALL 在结束事件的元数据或伴随的完成事件中一并携带 `prompt_id` 字段，使前端可与同步路径行为对齐。
7. THE `AgentConfig` / `NamedAgentConfig` 的既有字段（`system_prompt`、`tool_schemas`、`model`、`max_rounds`、`allowed_tool_names`、`name`、`description`、`tool_names`）SHALL 保持签名与语义不变；新增 `prompt_id` 属于纯追加变更，既有直接构造 `AgentConfig(system_prompt=..., tool_schemas=..., model=..., max_rounds=...)` 的调用点**必须**显式补齐 `prompt_id` 参数（不提供默认值，见需求 9 的迁移语义）。

### 需求 5：Task 动态 Prompt 模板 `Task_Dynamic_Prompt_Template` 的版本化策略

**用户故事：** 作为 Task 入口的维护者，我希望 `TaskAgentAdapter.build_system_prompt` 生成的 prompt 也能被追溯到"第 N 版 task 模板"，而不至于因模板骨架悄悄变化而难以复现历史任务行为。

#### 验收标准

1. THE `Task_Dynamic_Prompt_Template` SHALL 以 `prompts/task-template/v<N>.md` 的形式登记 `Prompt_Asset_File`；文件内容为"骨架文档"（说明本版模板如何组装 `goal / input_data / constraints / output_format` 四段、段落标题与顺序），仅供代码评审与审计阅读，**不用于运行期字符串替换**。
2. THE `TaskAgentAdapter` SHALL 通过构造参数接收 `Prompt_Registry_Port` 实例，在构造时调用 `registry.get("task-template")` 得到 `Loaded_Prompt`，把 `Loaded_Prompt.prompt_id` 记录为实例属性 `_task_template_prompt_id`。
3. THE `TaskAgentAdapter.build_system_prompt(task)` SHALL 保持现有纯函数行为（相同 `Task` 输入产出相同字符串），**但**该方法本身 SHALL 不依赖 `Loaded_Prompt.content`；`Loaded_Prompt` 仅用于提供 `prompt_id`。
4. WHEN `TaskAgentAdapter.execute` 构造 `AgentConfig` 时, THE 适配器 SHALL 将 `_task_template_prompt_id` 写入 `AgentConfig.prompt_id`（不是 `chat-default@v1`，不是裸字符串），确保任务执行的追踪线索指向 `task-template` 家族。
5. WHEN `Task_Dynamic_Prompt_Template` 的拼装规则（段落标题、顺序、字段集合）发生变化时, THE 变更流程 SHALL 同时：（a）新增 `prompts/task-template/v<N+1>.md` 记录新骨架文档；（b）修改 `TaskAgentAdapter.build_system_prompt` 代码；（c）在 `config.properties` 将 `PROMPT_TASK_TEMPLATE_VERSION` 更新为 `v<N+1>`；三者构成同一次提交的不可分割集合。
6. FOR ALL `TaskResult` 的返回值（`epsilon-boot/src/domain/task/value_objects.py`）, THE `TaskAgentAdapter.execute` SHALL 把 `_task_template_prompt_id` 透传到 `TaskResult` 的可观测性字段（新增 `prompt_id: str` 字段或在既有 trace / usage 载荷中附加），以便调用方从响应侧识别模板版本。
7. THE `TaskAgentAdapter.build_system_prompt` 的运行期生成文本 SHALL NOT 被写入 `Prompt_Asset_Directory` 或任何持久化位置；运行期生成物仅在内存中存活至本次 `Agent Loop` 结束。

### 需求 6：`Workspace_Path_Guidance_Appender` 与版本化 Prompt 的组合顺序

**用户故事：** 作为 Chat 调用方，我希望每次 LLM 调用仍然带有工作区路径规范约束（既有需求 7.3），同时 prompt 的版本化不会让这条约束消失或被重复堆叠。

#### 验收标准

1. THE `Workspace_Path_Guidance_Appender` SHALL 继续以 `@model_validator(mode="after")` 的形式存在于 `ChatConfig`，但其作用对象发生变化：不再追加到 `system_prompt: str` 字段默认值上，而是由 `Prompt_Consumer` 在构造 `AgentConfig.system_prompt` 时调用一个纯函数 `append_workspace_path_guidance(content: str) -> str`，对 `Loaded_Prompt.content` 做同样的幂等追加。
2. FOR ALL 经过 `Prompt_Consumer` 组装的 `AgentConfig.system_prompt`, THE 最终文本 SHALL 满足：以 `Loaded_Prompt.content` 开头，末尾追加 `_WORKSPACE_PATH_GUIDANCE`（若 `content` 末尾已包含该文案则跳过追加，保持幂等）。
3. WHEN 同一版本的 `Loaded_Prompt.content` 被多次组装进 `AgentConfig`（例如同一进程内多次请求）, THE 追加行为 SHALL 幂等：连续调用 `append_workspace_path_guidance` 不得产生多份叠加的路径规范文案。
4. THE `Loaded_Prompt.prompt_id` SHALL NOT 因 `append_workspace_path_guidance` 的追加动作而发生变化；`prompt_id` 始终反映原始 prompt 资产版本，路径规范文案属于进程级注入而非 prompt 资产版本的一部分。
5. IF 未来项目决定把 `_WORKSPACE_PATH_GUIDANCE` 也纳入版本化（作为独立 `Prompt_Name` 如 `workspace-guidance`）, THEN THE 变更 SHALL 作为独立特性处理，不在本期承诺。
6. FOR ALL 现有单元测试（如 `test/infrastructure/chat/test_chat_config_system_prompt_unit.py`）, THE 迁移 SHALL 把"对 `ChatConfig.system_prompt` 字段末尾包含 `_WORKSPACE_PATH_GUIDANCE`"的断言，迁移为对"`ChatServiceAdapter` 构造的 `AgentConfig.system_prompt` 末尾包含 `_WORKSPACE_PATH_GUIDANCE`"的断言，不得仅因迁移而降低覆盖粒度。

### 需求 7：`Prompt_Id_Propagation` 到结构化日志、OpenTelemetry、HTTP 响应

**用户故事：** 作为运维 / SRE，我希望在 Kibana / Jaeger 上按 `prompt_id` 过滤日志与 span，从而把 latency / usage 指标的回归与具体 prompt 变更直接关联。

#### 验收标准

1. FOR ALL `ChatServiceAdapter.chat` / `stream_chat` / `TaskAgentAdapter.execute` 生成的 `logger.info` 起止日志, THE 代码 SHALL 在 `extra` 字典中包含 `prompt_id` 字段（字符串，形如 `chat-default@v3`）；不得仅通过字符串格式化混入消息正文（否则结构化日志采集器无法作为独立字段索引）。
2. WHEN OpenTelemetry 已启用（`otel_config.enabled is True`）且当前调用路径包裹在一个 span 内, THE `Prompt_Consumer` SHALL 调用 `trace.get_current_span().set_attribute("prompt.id", prompt_id)`；属性名固定为 `prompt.id`（点号分隔，符合 OTel 语义约定的命名风格）。
3. THE `ChatResponseVO` SHALL 在 HTTP 响应序列化时包含 `prompt_id` 字段（字符串，非空）；对应 HTTP 路由层（`application/routers/chat.py` 或等效位置）的 Pydantic 响应模型必须同步新增该字段，确保 OpenAPI 文档反映变化。
4. THE `TaskResult` 对应的 HTTP 响应模型 SHALL 同步包含 `prompt_id` 字段，对齐需求 5.6。
5. FOR ALL `prompt_id` 值, THE 代码 SHALL NOT 把 `Loaded_Prompt.content` 的任何片段一并写入结构化日志或 span 属性；`prompt.id` 是唯一对外可观测字段，具体内容通过 git 与资产目录回查，避免 prompt 全文污染日志系统。
6. FOR ALL `AgentConfig.prompt_id` / `NamedAgentConfig.prompt_id` 在内部日志中被记录的场景, THE 记录调用 SHALL 使用同一 `extra` 键名 `prompt_id`（不得一处用 `prompt_id`、另一处用 `promptId` 或 `prompt-id`）；统一名字便于日志采集侧的 schema 约束。
7. THE 可观测性改动 SHALL NOT 引入新的 OpenTelemetry Instrumentation 包；仅使用项目已依赖的 `opentelemetry-api` / `opentelemetry-sdk`，依赖集的任何变化均须通过 `uv add` 执行并同步 `uv.lock`（遵循 [docs/steering/uv-package-manager.md](../../steering/uv-package-manager.md)）。

### 需求 8：向后兼容与 `CHAT_SYSTEM_PROMPT` 迁移路径

**用户故事：** 作为已把 `CHAT_SYSTEM_PROMPT` 写入生产 `config.properties` 或环境变量的运维者，我希望升级到本特性时有清晰的迁移指引与 fail-fast 保护，避免"新版 prompt 版本键被静默忽略"或"旧 prompt 文本被静默丢弃"。

#### 验收标准

1. THE `ChatConfig.system_prompt` 字段 SHALL 被移除；`Prompt_Consumer`（`ChatServiceAdapter`）不再从 `ChatConfig` 读取 prompt 文本。
2. WHEN 服务启动时检测到环境变量 `CHAT_SYSTEM_PROMPT` 被设置（非空）或 `config.properties` 中存在 `CHAT_SYSTEM_PROMPT` 键, THE `Prompt_Conflict_Detection` SHALL 触发 `Prompt_Startup_Failure`（抛出 `Conflicting_Legacy_Prompt_Config_Error`，继承自 `ConfigurationError`），错误消息中必须引导运维者：
   - 将原文本另存为 `prompts/chat-default/v<N+1>.md`；
   - 将 `PROMPT_CHAT_DEFAULT_VERSION` 设为新版本号；
   - 从 `config.properties` / 环境变量中删除 `CHAT_SYSTEM_PROMPT`。
3. THE 特性上线文档（不在本 requirement 内，但需求 8 在 design / tasks 阶段必须沉淀到 `epsilon-boot/config.properties` 的注释块、迁移说明文档中）SHALL 给出一步可复制的迁移示例。
4. THE 默认资产 `prompts/chat-default/v1.md` SHALL 以原 `ChatConfig.system_prompt` 字段的默认值 `"你是一个有用的 AI 助手。"` 作为初始内容（不含 `_WORKSPACE_PATH_GUIDANCE`——该文案继续由 `Workspace_Path_Guidance_Appender` 在运行期注入，见需求 6），保证默认行为与升级前完全等价。
5. IF 历史上存在 `CHAT_SYSTEM_PROMPT` 以外的"prompt 文本直写"型配置键（本期核查结论：无），THEN THE `Prompt_Conflict_Detection` SHALL 同样拒绝继续启动，错误消息列出所有冲突键名。
6. FOR ALL 既有依赖 `ChatConfig.system_prompt` 字段的生产代码与测试代码, THE 迁移 SHALL 一次性完成，不保留任何"兼容旧字段名"的隐式桥接。

### 需求 9：启动期校验、错误处理与 `Prompt_Startup_Failure` 语义

**用户故事：** 作为运维者，我希望 prompt 资产或版本配置出现任何问题时，服务在启动期就以明确的错误消息 fail-fast，而不是运行到第一次 LLM 调用才暴露。

#### 验收标准

1. WHEN `Filesystem_Prompt_Registry_Adapter` 构造时 `Prompt_Asset_Directory` 不存在或不是目录, THE 适配器 SHALL 抛出 `Prompt_Asset_Directory_Missing_Error`（继承自 `ConfigurationError`）触发 `Prompt_Startup_Failure`，错误消息包含期望路径。
2. FOR ALL `Prompt_Version_Config` 中配置的 `<Prompt_Name>@<Prompt_Version_Tag>` 组合, THE 适配器 SHALL 在构造期校验对应 `Prompt_Asset_File` 存在；任一缺失 SHALL 抛出 `Prompt_Asset_File_Missing_Error` 触发 `Prompt_Startup_Failure`，错误消息包含文件绝对路径与对应配置键名。
3. IF 目标 `Prompt_Asset_File` 存在但 UTF-8 解码失败, THEN THE 适配器 SHALL 抛出 `Prompt_Asset_Encoding_Error` 触发 `Prompt_Startup_Failure`，错误消息包含文件路径与底层 `UnicodeDecodeError` 的位置信息。
4. IF 目标 `Prompt_Asset_File` 内容仅包含空白字符（`.strip() == ""`）, THEN THE 适配器 SHALL 抛出 `Empty_Prompt_Asset_Error` 触发 `Prompt_Startup_Failure`，避免"加载成功但 LLM 收到空 system prompt"的隐式失败。
5. IF `Prompt_Asset_Directory` 下存在未在 `Prompt_Version_Config` 中配置的 `<name>/` 子目录, THEN THE 适配器 SHALL 允许其存在但跳过加载（该目录可能对应未来特性或历史归档）；但 SHALL 在启动日志（`logger.info`）中列出被跳过的目录名，便于审计。
6. IF `Prompt_Version_Config` 的字段引用了 `Prompt_Asset_Directory` 下不存在的 `Prompt_Name` 子目录（例如配置 `PROMPT_FOO_VERSION=v1` 但目录 `prompts/foo/` 不存在）, THEN THE 适配器 SHALL 抛出 `Prompt_Not_Configured_Error` 触发 `Prompt_Startup_Failure`。
7. FOR ALL `Prompt_Startup_Failure` 路径, THE 错误消息 SHALL 使用中文可读文案，不得在日志或异常文本中拼接敏感信息（与 [docs/steering/code-documentation.md](../../steering/code-documentation.md) 强调的中文一致性呼应，亦与 `docs/spec/local-file-persistence/requirement.md` 需求 1.7 的风格对齐）。

### 需求 10：代码文档、依赖管理与 DDD 边界约束

**用户故事：** 作为代码评审者，我希望本特性产出的代码符合项目既有的 Steering 规范（docstring、uv、DDD 方向），避免引入长期债务。

#### 验收标准

1. FOR ALL 本特性新增的模块、类、公开函数/方法（包括 `domain/prompt/ports.py`、`domain/prompt/value_objects.py`、`infrastructure/prompt/filesystem_prompt_registry_adapter.py`、`infrastructure/prompt/prompt_version_config.py` 等）, THE 源码 SHALL 包含中文 docstring，说明职责、参数、返回值、异常，遵循 [docs/steering/code-documentation.md](../../steering/code-documentation.md)。
2. FOR ALL 本特性引入的新依赖（预期为零；如需新增则须 justify）, THE 添加 SHALL 通过 `uv add` 完成并同步 `uv.lock`；禁止通过 `pip` / `poetry` / `pipenv` / `conda`（[docs/steering/uv-package-manager.md](../../steering/uv-package-manager.md)）。
3. THE `domain/prompt/` SHALL 不导入 `infrastructure/*`、不导入 FastAPI / Pydantic Settings / 文件系统 SDK；`Loaded_Prompt` 与 `Prompt_Registry_Port` 保持在领域层的存储无关性。
4. THE `infrastructure/prompt/` SHALL 实现 `Prompt_Registry_Port` 并可导入 `pydantic-settings`、`pathlib` 等基础设施依赖；按 [docs/steering/ddd-architecture.md](../../steering/ddd-architecture.md) 的 Adapter 归属规定。
5. THE `application/container_config.py` SHALL 在组合根位置完成 `Prompt_Registry_Port` 的装配；应用层其他位置（路由、服务编排）不得直接 `from infrastructure.prompt import ...`，只能依赖 Port 抽象。
6. FOR ALL 本特性的配置新增（`Prompt_Version_Config` 字段、`PROMPT_<NAME>_VERSION` 配置键）, THE 编写 SHALL 优先写入 `epsilon-boot/config.properties` 而非 `.env`，遵循 [docs/steering/config-source.md](../../steering/config-source.md)。
7. THE 本特性的实现 SHALL 不修改 `CLAUDE.md`、`docs/project-overview.md`、`docs/repository-map.md` 之外的文档索引；若需要补充 prompt 目录说明，SHALL 通过追加一份 `docs/prompts.md` 的方式进行，并在 `CLAUDE.md` 主题文档索引表中追加一行，避免侵入既有主题文档的内部结构。

### 需求 11：测试覆盖与回归保证

**用户故事：** 作为质量保证负责人，我希望本特性的每一条关键语义都有对应的自动化测试，使未来的改动能够通过测试失败第一时间发现回归。

#### 验收标准

1. FOR ALL `Filesystem_Prompt_Registry_Adapter` 的启动期错误分支（需求 9.1–9.6）, THE 测试套件 SHALL 在 `epsilon-boot/test/infrastructure/prompt/` 下提供对应的 unit test 覆盖；每个错误分支至少一个用例断言抛出的异常类型与错误消息片段。
2. FOR ALL `Prompt_Version_Config` 的字段解析, THE 测试套件 SHALL 覆盖合法 `v<N>`、非法格式、缺失键（回退到字段默认值）三种场景。
3. THE 测试套件 SHALL 在 `test/domain/agent/test_agent_value_objects_unit.py`（或对等文件）中为 `AgentConfig.prompt_id` / `NamedAgentConfig.prompt_id` 新增用例：合法 `chat-default@v3`、空字符串、格式非法 `foo`、格式非法 `chat-default@1` 四种场景。
4. THE 测试套件 SHALL 为 `ChatServiceAdapter` 提供集成型 unit test，断言在 `chat` / `stream_chat` 路径下：（a）`AgentConfig.system_prompt` 等于 `Loaded_Prompt.content + _WORKSPACE_PATH_GUIDANCE`；（b）`ChatResponseVO.prompt_id` 与当时加载的 `Loaded_Prompt.prompt_id` 一致。
5. THE 测试套件 SHALL 为 `TaskAgentAdapter` 提供 unit test，断言 `execute` 路径上构造的 `AgentConfig.prompt_id` 等于 `task-template@<配置版本>`；断言 `TaskResult.prompt_id` 同值。
6. FOR ALL `Prompt_Id_Propagation` 的可观测性语义（需求 7.1–7.2）, THE 测试 SHALL 通过 monkeypatch / fake logger / in-memory span exporter 断言 `extra["prompt_id"]` 与 `span.attributes["prompt.id"]` 被正确写入。
7. THE 既有 `test/infrastructure/chat/test_chat_config_system_prompt_unit.py` 等"针对旧字段 `ChatConfig.system_prompt`"的测试 SHALL 被迁移或重写为针对新 `Prompt_Consumer` 组装行为的等价测试，迁移后的覆盖面不低于原有。
