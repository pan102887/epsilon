# Sandbox 能力评估与追赶路线

本文记录本项目在 Agent 工具执行与文件系统隔离方面的现状，并对照 LangChain Deep Agents Sandboxes 与业界主流 AI coding agent sandbox 方案，梳理能力差距和后续优先工作。

核心结论：本项目当前已经具备较完整的 **Workspace confinement（工作区边界约束）**，并对本地 Shell / Python 执行工具加入了多层软件护栏；但当前能力不等同于 LangChain 文档或业界主流所说的 **sandbox backend（远程 / 容器 / OS 级隔离执行环境）**。在启用高风险执行工具前，必须明确这一安全边界。

## 一、本项目当前情况

### 1.1 当前定位

本项目当前实现的是：

```text
Workspace confinement + local subprocess execution
```

也就是说，Agent 的文件读写和本地执行工具被约束在配置的工作区根目录内；但命令执行仍然发生在服务进程所在宿主环境 / 容器环境中，并没有额外创建远程 sandbox、容器 sandbox、microVM 或独立 devbox。

当前不应把本能力描述为强 sandbox。更准确的术语是：

- 工作区边界：`Workspace` / `LocalFilesystemWorkspace`
- 受控本地执行：`ShellExecTool` / `PythonExecTool`
- 工具级安全护栏：默认关闭、路径约束、环境变量清理、超时、输出截断、HITL 审批

### 1.2 Workspace 抽象

项目在领域层定义了 `Workspace` Port：

- 位置：`epsilon-boot/src/domain/workspace/ports.py`
- 当前 Adapter：`epsilon-boot/src/infrastructure/workspace/local_filesystem/local_workspace.py`
- 配置：`epsilon-boot/src/infrastructure/workspace/workspace_config.py`
- 配置项：`epsilon-boot/config.properties` 中的 `WORKSPACE_*`

`Workspace` 提供受控文件系统操作，包括：

- `resolve_path`
- `exists`
- `stat`
- `read`
- `write`
- `edit`
- `list_dir`
- `delete`
- `capabilities`
- `display_root_hint`

其中，工具层不应直接使用宿主 `os` / `pathlib` 做文件 I/O，而是通过注入的 `Workspace` 完成。这个设计符合项目 DDD + 六边形架构：领域层定义 Port，基础设施层实现 Adapter，应用层通过 DI 容器装配。

### 1.3 路径归一化与越界防护

`WorkspacePolicy` 负责把 LLM / 工具调用方输入的原始路径规范化为 `WorkspacePath`，并拒绝越界路径。

当前路径策略包括：

- 空串、`.`、`/` 统一映射到工作区根；
- 拒绝 NUL 字节；
- 拒绝 Windows 盘符路径；
- 拒绝 UNC 路径；
- 拒绝反斜杠；
- 归一化 `.` / `..` / 重复 `/`；
- 归一化后越过根目录时抛出 `WorkspaceConfinementViolation`。

本地后端还通过以下守卫做二次防护：

- `SymlinkGuard`：阻断符号链接逃逸；
- `IdentityGuard`：阻断大小写折叠、跨设备等导致的路径身份漂移。

这使当前工作区边界强于简单的字符串前缀判断。

### 1.4 文件工具现状

项目内置文件系统工具包括：

- `ReadFileTool` / `read_file`
- `WriteFileTool` / `write_file`
- `EditFileTool` / `edit_file`
- `ListDirTool` / `list_dir`

这些工具通过 `Workspace` 完成 I/O，路径语义是工作区相对 POSIX 路径。`edit` 还在本地后端中使用 POSIX `fcntl.flock` 与 inode 一致性校验，降低并发编辑造成覆盖的风险。

### 1.5 本地 Shell 执行工具现状

`ShellExecTool` 位于：

```text
epsilon-boot/src/infrastructure/tools/shell_exec/shell_exec_tool.py
```

配置项：

```properties
SHELL_EXEC_ENABLED=false
SHELL_EXEC_TIMEOUT=30
SHELL_EXEC_MAX_OUTPUT_SIZE=51200
```

当前默认关闭 Shell 执行，这是合理的安全默认值。

启用后，Shell 工具提供以下软件护栏：

- 子进程 `cwd` 锁定到工作区内；
- 执行前检查 `Workspace.capabilities().local_materialization`；
- 剥离名称包含 `KEY` / `SECRET` / `PASSWORD` / `TOKEN` / `CREDENTIAL` 的环境变量；
- 超时强制终止；
- 输出大小截断；
- 阻断若干危险命令模式，如 `rm -rf`、`mkfs`、`dd if=`、`curl | sh`、fork bomb、读取 `.env` / `/etc/shadow` / `~/.ssh/id_rsa` 等。

需要强调：Shell 命令黑名单只能降低误操作风险，不能构成完整安全边界。Shell 语言和系统工具组合空间极大，启发式黑名单不能覆盖所有绕过方式。

### 1.6 本地 Python 执行工具现状

`PythonExecTool` 位于：

```text
epsilon-boot/src/infrastructure/tools/python_exec/python_exec_tool.py
```

配置项：

```properties
PYTHON_EXEC_ENABLED=false
PYTHON_EXEC_TIMEOUT=30
PYTHON_EXEC_MAX_OUTPUT_SIZE=51200
PYTHON_EXEC_MAX_MEMORY_MB=256
PYTHON_EXEC_ALLOWED_MODULES=
```

当前默认关闭 Python 执行，同样是合理的安全默认值。

启用后，Python 工具提供以下软件护栏：

- AST 静态分析；
- import 白名单；
- 禁止相对导入；
- 禁止 `exec` / `eval` / `compile` / `__import__` / `open` 等高风险调用；
- 子进程 `cwd` 锁定到工作区根；
- 临时 `.py` 文件落在工作区内；
- 环境变量清理；
- 超时控制；
- Linux / macOS 上通过 `resource.RLIMIT_AS` 限制内存；
- 输出大小截断。

需要强调：Python AST 静态分析是有价值的 guardrail，但不是强 sandbox。Python 语言具备复杂的反射、对象模型和生态依赖能力，不能仅靠 AST 黑名单承载不可信代码执行。

### 1.7 HITL 与工具权限隔离

项目已有 `ScopedToolRegistry` 和 HITL 配置能力：

```properties
HITL_ENABLED=false
HITL_INTERRUPT_ON=
HITL_STATE_TTL_SECONDS=3600
```

工具权限隔离通过 Agent 配置或任务配置限定可见工具集合；HITL 可以在工具执行前对高风险工具触发审批。文档中已明确：HITL 不能替代 Workspace 边界、工具权限、参数 schema 校验、网络访问控制、命令沙箱或 OS 权限。

### 1.8 当前边界总结

当前能力可以防止一部分路径逃逸、误写宿主文件、简单危险命令和低风险脚本滥用，但不能防止所有恶意执行行为。

当前尚不具备：

- 独立容器 / microVM / 远程 devbox；
- OS namespace / cgroup / seccomp 隔离；
- per-thread / per-assistant sandbox 生命周期；
- sandbox TTL / 自动销毁；
- upload / download 文件传输边界；
- egress 网络策略；
- snapshot / rollback；
- provider adapter；
- 多租户强隔离；
- secrets 外置代理注入机制。

## 二、业界前沿实现的发展情况

### 2.1 从 workspace 到 sandbox backend

AI coding agent 的执行环境大致经历了以下演进：

```text
无约束本地工具
  → 本地 workspace confinement
  → 本地容器 sandbox
  → 远程 sandbox / devbox provider
  → microVM / gVisor / 多租户强隔离平台
```

早期 agent 常直接使用宿主文件系统和本地 shell。随着 coding agent 能力增强，执行任意命令、安装依赖、运行测试、处理用户文件和访问网络成为常态，安全边界逐步从“应用层路径检查”演化到“独立执行环境”。

### 2.2 LangChain Deep Agents Sandboxes 模式

LangChain Deep Agents 的 sandbox 文档提供了一种代表性抽象：把 agent 的文件操作与命令执行放到隔离 backend 中，而不是直接访问宿主机。

典型能力包括：

- 文件工具：`ls`、`read_file`、`write_file`、`edit_file`、`glob`、`grep`；
- 命令工具：`execute`；
- 应用层文件传输：`upload_files`、`download_files`；
- provider backend：Daytona、E2B、Modal、Runloop、Vercel Sandbox、LangSmith sandbox；
- 生命周期管理：创建、复用、停止、删除；
- 状态作用域：thread-scoped、assistant-scoped；
- TTL / auto delete；
- human-in-the-loop 审批作为补充控制。

这类设计把 sandbox 作为独立 backend，而不是把执行能力分散在文件工具和 shell 工具中。

### 2.3 Sandbox as tool

主流推荐模式之一是 **Sandbox as tool**：

```text
Agent Runtime
  → tool call
  → Sandbox Backend Adapter
  → Remote Sandbox / Container / Devbox
```

agent 主逻辑运行在可信服务端，只有执行命令和读写文件时才调用 sandbox backend。

优点：

- 模型 API key 可以留在 sandbox 外；
- agent 状态不依赖 sandbox 存活；
- sandbox 故障不一定导致整个 agent 会话丢失；
- 可以按任务创建多个 sandbox；
- provider 可替换。

缺点：

- 每次工具调用存在网络延迟；
- 需要设计文件同步、artifact 下载和生命周期管理。

### 2.4 Agent in sandbox

另一种模式是 **Agent in sandbox**：agent 本身运行在 sandbox 内。

优点：

- 更接近真实远程开发环境；
- agent、依赖、项目代码和测试工具都在同一环境中；
- 适合长时间 coding session。

缺点：

- 模型 API key 或代理凭据可能进入 sandbox；
- agent 逻辑更新需要重建镜像或更新运行环境；
- 需要额外通信层；
- sandbox 崩溃可能影响 agent 运行状态。

多租户产品通常更倾向以 Sandbox as tool 起步，把 agent 控制面留在可信服务中。

### 2.5 生命周期作用域

业界主流 sandbox 通常会提供多种生命周期作用域：

| 作用域 | 说明 | 适用场景 |
| --- | --- | --- |
| run-scoped | 每次任务创建一个 sandbox，结束即销毁 | 高安全、一次性任务、批处理 |
| thread-scoped | 每个会话一个 sandbox，多轮复用 | 聊天式 coding agent、数据分析会话 |
| assistant-scoped | 一个 assistant 长期复用一个 sandbox | 长期项目助手、固定仓库维护 |
| user-scoped | 每个用户一个或多个 sandbox | 个人云开发环境 |

前沿实现通常支持 label、metadata、TTL、idle timeout、auto delete、snapshot 等机制来控制生命周期。

### 2.6 文件与 artifact 管理

成熟 sandbox 不只提供 agent 侧文件工具，还会提供应用层文件传输 API：

- 上传项目源码；
- 上传测试数据；
- 上传配置；
- 下载报告；
- 下载构建产物；
- 下载 patch / diff；
- 将结果转为平台内 artifact。

这使 sandbox 文件系统成为一个明确边界，而不是服务进程本地目录的一部分。

### 2.7 隔离技术栈

业界常见隔离层包括：

- Docker / Podman container；
- Linux namespaces；
- cgroups；
- seccomp；
- AppArmor / SELinux；
- gVisor；
- Firecracker microVM；
- nsjail / bubblewrap；
- macOS Seatbelt；
- 远程 devbox / cloud sandbox provider。

隔离层通常会组合使用，而不是只依赖单一机制。例如容器负责文件系统和进程隔离，cgroup 负责资源限制，seccomp 负责系统调用限制，网络策略负责 egress 控制。

### 2.8 网络与 secrets 策略

前沿 sandbox 设计通常遵循以下原则：

- 默认不把长期 secrets 放进 sandbox；
- 需要鉴权时由 sandbox 外部可信服务代理请求；
- 必须注入凭据时使用短生命周期、最小权限 token；
- 不需要网络时禁用网络；
- 需要网络时配置 egress allowlist；
- 对网络请求做审计；
- 对工具输出做脱敏；
- 将 sandbox 产物视为不可信输入。

这与 LangChain 文档中的安全提示一致：sandbox 可以隔离宿主机，但不能防止 prompt injection / context injection。

### 2.9 Snapshot、rollback 与 diff

成熟 coding agent sandbox 通常需要：

- 从仓库快照初始化；
- 运行过程中记录变更；
- 支持 diff 导出；
- 支持失败后丢弃；
- 支持回滚到初始状态；
- 支持保留可审查 artifact。

这类能力不仅是安全能力，也是产品体验能力。它让 agent 可以大胆执行测试和修改文件，同时让用户在接受变更前进行审查。

## 三、本项目与业界主流之间的差异

### 3.1 总体差异

本项目当前位于“本地 workspace confinement”阶段，尚未进入“容器 / 远程 sandbox backend”阶段。

| 维度 | 本项目当前情况 | 业界主流 / LangChain sandboxes |
| --- | --- | --- |
| 抽象核心 | `Workspace` Port + 本地工具 | `SandboxBackend` / provider adapter |
| 后端类型 | 仅 `local_filesystem` | Daytona / E2B / Modal / Runloop / Vercel / Docker 等 |
| 执行位置 | 服务环境中的本地子进程 | 远程 sandbox、容器、devbox、microVM |
| 文件系统 | 本地 `WORKSPACE_ROOT` | 独立 sandbox 文件系统 |
| 生命周期 | 进程级 / 服务级 workspace | run / thread / assistant scoped |
| TTL 清理 | 无 sandbox TTL | idle timeout / auto delete |
| upload/download | 无独立传输 API | 常见核心能力 |
| artifact | 无统一 artifact 边界 | 报告、patch、构建产物可下载审查 |
| 网络隔离 | 未见明确 egress policy | 可禁网或 allowlist |
| OS 资源隔离 | 主要依赖应用层限制 | cgroup / namespace / seccomp / microVM |
| secrets 管理 | 环境变量敏感键剥离 | secrets 外置、代理注入、短期 token |
| 多租户隔离 | 不完整 | per-user / per-thread sandbox |
| snapshot/rollback | 无 | coding sandbox 常见能力 |
| 安全边界 | 软件护栏 | 运行时隔离 + 软件策略 |

### 3.2 抽象层差异

本项目的 `Workspace` 抽象主要解决“文件路径与 I/O 边界”问题。它不是完整 sandbox 抽象，因为它不拥有：

- `execute` 作为 backend 能力；
- 文件上传下载边界；
- sandbox 生命周期；
- provider ID；
- TTL；
- 资源限制；
- 网络策略；
- artifact 管理。

当前执行能力属于具体工具：`ShellExecTool` 和 `PythonExecTool`。这导致未来接入 E2B / Daytona / Docker 时，需要重新梳理执行能力归属。

### 3.3 安全边界差异

本项目当前安全边界主要是应用层逻辑：

- 路径归一化；
- symlink / identity guard；
- 命令黑名单；
- Python AST 检查；
- 环境变量清理；
- timeout；
- 输出截断；
- HITL。

业界主流 sandbox 还会增加运行时隔离：

- 独立 rootfs；
- 独立进程 namespace；
- 独立网络 namespace；
- CPU / memory / pids / fd quota；
- 系统调用限制；
- 容器或 microVM 自动销毁。

应用层 guardrail 应作为补充，而不是唯一边界。

### 3.4 生命周期差异

当前项目使用全局 `WORKSPACE_ROOT`，默认甚至可能落在进程当前工作目录。这适合单机开发态，但不适合多用户、多会话、多任务隔离。

主流 sandbox 会把生命周期绑定到：

- run；
- thread；
- assistant；
- user；
- project。

并通过 TTL、label、snapshot 和 cleanup 控制资源成本与状态污染。

### 3.5 文件产物差异

当前文件工具直接操作 workspace 内文件。它没有区分：

- agent 内部读写；
- 应用向 sandbox 上传；
- 应用从 sandbox 下载；
- 用户可审查 artifact；
- 临时文件与最终产物。

主流方案通常会把 artifact 作为产品边界，让用户能够审查、下载、接受或丢弃。

### 3.6 网络与 secrets 差异

当前环境变量清理可以降低凭据泄露概率，但不能覆盖所有 secrets 来源，也不能防止 sandbox 内部通过网络外传数据。

主流方案更强调：

- secrets 不进入 sandbox；
- sandbox 内请求通过外部代理完成鉴权；
- egress 默认关闭或 allowlist；
- 网络访问审计；
- 工具输出脱敏；
- sandbox 产物视为不可信输入。

### 3.7 当前主要风险

1. **术语风险**：把当前 Workspace 误称为 sandbox，会高估安全边界。
2. **Shell 风险**：黑名单规则可被绕过，启用后风险显著上升。
3. **Python 风险**：AST 黑名单不是强隔离，不能运行不可信 Python。
4. **网络风险**：缺少 egress policy，数据可能被外传。
5. **状态污染风险**：全局 workspace 容易跨会话、跨用户污染。
6. **路径泄露风险**：`display_root_hint()` 当前可能向 LLM 暴露宿主绝对路径。
7. **资源耗尽风险**：缺少完整 CPU / pids / fd / disk quota。
8. **产物信任风险**：sandbox 生成内容尚无统一审查和 artifact 流程。

## 四、追赶主流所需的 10 项最高优先级工作

### 1. 明确术语与安全边界

**目标：** 防止团队和用户把当前 Workspace confinement 误解为强 sandbox。

**主要改动：**

- 在 `docs/tools.md`、`docs/architecture.md` 和相关配置注释中明确：当前 `Workspace` 只提供本地文件边界，不构成 OS 级 sandbox；
- 对 `SHELL_EXEC_ENABLED` / `PYTHON_EXEC_ENABLED` 增加生产启用风险说明；
- 将“安全沙箱环境”等容易误导的工具描述改为“受控本地执行环境”或“工作区受控执行”。

**验收标准：**

- 文档中明确区分 `Workspace confinement` 与 `Sandbox backend`；
- 高风险执行工具说明不再暗示其具备容器 / OS 级隔离；
- 安全评审者能从文档判断当前边界。

### 2. 新增 `SandboxPort` 领域抽象

**目标：** 建立 provider-neutral sandbox 能力边界，避免继续把执行能力分散在工具实现中。

**主要改动：**

- 在 domain 层定义 `SandboxPort`；
- 定义 `execute`、`upload_files`、`download_files`、`stop` 等接口；
- 定义 `SandboxCapabilities`、`SandboxExecuteResult`、`SandboxFileUploadResult`、`SandboxFileDownloadResult` 等值对象；
- 保持 domain 不依赖任何 provider SDK。

**验收标准：**

- `ShellExecTool` / `PythonExecTool` 可通过 `SandboxPort` 执行，而不是直接本地 `subprocess`；
- 新增后端不需要修改 domain；
- 接口可覆盖 LangChain 文档中的 execute / upload / download 语义。

### 3. 引入 sandbox 生命周期与作用域配置

**目标：** 支持 run-scoped、thread-scoped、assistant-scoped 等生命周期，为多会话隔离打基础。

**主要改动：**

- 新增配置，例如：

  ```properties
  SANDBOX_BACKEND=disabled
  SANDBOX_SCOPE=thread
  SANDBOX_TTL_SECONDS=3600
  SANDBOX_AUTO_DELETE=true
  ```

- 在 application 层引入 sandbox session manager；
- 通过 run_id / thread_id / assistant_id 建立 sandbox label；
- 支持创建、查找、复用、销毁。

**验收标准：**

- 不同 thread 默认使用不同 sandbox；
- 同一 thread 可复用 sandbox 状态；
- 空闲 sandbox 能自动清理；
- lifecycle 逻辑不泄漏到领域层。

### 4. 实现 Docker / Podman 本地容器 sandbox 后端

**目标：** 先达到主流 Level 2：本地容器隔离。

**主要改动：**

- 新增 `infrastructure/sandbox/local_container/`；
- 使用 Docker 或 Podman SDK / CLI 创建短生命周期容器；
- 支持 bind mount 或文件上传初始化；
- 配置 CPU、memory、pids、read-only rootfs、tmpfs、cap-drop、no-new-privileges；
- 默认关闭网络或提供 allowlist；
- 任务结束自动清理。

**验收标准：**

- `shell_exec` / `python_exec` 可在容器内执行；
- 宿主文件系统不直接暴露给 agent；
- 容器资源超限会失败并返回可解释错误；
- 容器退出后无残留进程。

### 5. 实现文件 upload / download 与 artifact API

**目标：** 对齐 LangChain sandbox 的应用层文件传输模型。

**主要改动：**

- 在 `SandboxPort` 中实现批量 `upload_files` / `download_files`；
- 区分 agent 内部文件工具与应用层文件传输；
- 定义 artifact 元数据：路径、大小、mime、来源 run、创建时间；
- 支持下载报告、patch、构建产物和测试日志。

**验收标准：**

- 应用可以在运行前上传源码 / 数据；
- 运行后可以下载指定产物；
- 下载失败按文件粒度返回错误；
- artifact 不依赖宿主绝对路径。

### 6. 增加网络 egress policy

**目标：** 防止 prompt injection 诱导 agent 通过网络外传数据。

**主要改动：**

- 新增配置，例如：

  ```properties
  SANDBOX_NETWORK_ENABLED=false
  SANDBOX_EGRESS_ALLOWLIST=
  ```

- 容器后端默认禁网；
- 如需网络，支持域名 / IP / 端口 allowlist；
- 对网络访问记录审计事件；
- 对 shell 中高风险下载执行链继续保留应用层拦截。

**验收标准：**

- 默认 sandbox 无法访问外网；
- allowlist 外目的地无法访问；
- 网络访问有审计日志；
- 配置变更需重启或经过明确热更新策略。

### 7. 建立 secrets 外置与代理注入机制

**目标：** 避免长期凭据进入 sandbox，同时支持必要的鉴权访问。

**主要改动：**

- 明确禁止把模型 API key、数据库密码、云服务长期 token 注入 sandbox；
- 需要访问外部服务时，通过宿主侧代理完成鉴权；
- 支持短生命周期、最小权限 token；
- 工具输出和日志做敏感信息脱敏；
- HITL 可对需要凭据的动作触发审批。

**验收标准：**

- sandbox 环境变量中不含长期 secrets；
- 凭据注入路径有审计；
- 代理只允许预期 API；
- 泄露面能通过测试或审计用例验证。

### 8. 引入 snapshot / rollback / diff 工作流

**目标：** 让 agent 可以安全修改代码，并让用户审查后接受或丢弃。

**主要改动：**

- sandbox 初始化时记录 baseline；
- 运行过程中记录文件变更；
- 支持导出 diff；
- 支持 rollback；
- 支持失败自动丢弃；
- 与 artifact API 关联。

**验收标准：**

- 用户可以查看本次 agent 改动列表；
- 可以只下载 patch，不直接写回主工作区；
- 失败任务不污染后续任务；
- 接受变更前不修改宿主源码。

### 9. 接入至少一个远程 sandbox provider

**目标：** 达到主流 Level 3：远程 sandbox / devbox provider。

**主要改动：**

- 在 `infrastructure/sandbox/` 下实现一个 provider adapter；
- 候选优先级可按产品目标选择：
  - E2B：代码执行与数据分析；
  - Daytona：远程开发环境；
  - Modal：弹性 Python 执行；
  - Runloop：devbox；
  - Vercel Sandbox：Node / 前端生态；
- provider API key 从 `config.properties` 留空，通过环境变量或部署 secret 注入；
- 实现 create / execute / upload / download / stop。

**验收标准：**

- 同一 `SandboxPort` 可切换 local container 与远程 provider；
- provider 故障有清晰错误映射；
- sandbox 生命周期和 TTL 生效；
- 不影响现有 Workspace 文件工具的默认行为。

### 10. 建立多租户 quota、审计与清理策略

**目标：** 让 sandbox 能力具备生产多用户运行条件。

**主要改动：**

- 按 user / thread / run 维度统计 sandbox 数量；
- 限制 CPU、内存、磁盘、运行时长、并发数；
- 记录 execute、upload、download、network、approval 事件；
- 定时清理过期 sandbox 和 artifact；
- 对异常退出、泄露资源、失败清理设置补偿任务。

**验收标准：**

- 单用户无法无限创建 sandbox；
- 过期资源可自动清理；
- 安全审计能追踪每次高风险动作；
- 清理失败会进入告警或重试队列。

## 结语

本项目当前在本地 Workspace 边界上已有扎实基础，尤其是路径归一化、符号链接防护、文件写入原子性、执行工具默认关闭、HITL 等机制，为后续演进到 sandbox backend 提供了良好起点。

下一阶段的关键不是继续强化 Shell / Python 黑名单，而是把执行环境从“本地子进程”提升到“可替换、可销毁、可审计、可限制资源和网络的独立 sandbox backend”。只有完成 `SandboxPort`、生命周期管理、容器 / 远程 provider、网络与 secrets 策略后，本项目才接近 LangChain Deep Agents Sandboxes 和业界主流 coding agent 的安全执行模型。
