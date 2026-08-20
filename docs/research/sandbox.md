# Agent Sandbox 主流实现方案调研

## 摘要

在 AI Agent 应用中，sandbox 的目标不是单纯“把代码跑起来”，而是为不可信或半可信的工具调用、代码执行、浏览器自动化、文件操作和网络访问建立可控边界。业界主流方案大致可分为五类：

1. **普通容器与增强型容器隔离**：以 Docker/Kubernetes 为基础，通过 namespace、cgroups、seccomp、AppArmor/SELinux、rootless 等机制隔离；进一步可使用 gVisor、Kata Containers 增强边界。
2. **硬件虚拟化 microVM**：以 Firecracker 为代表，基于 KVM/硬件虚拟化提供比共享内核容器更强的租户边界。
3. **托管式 Agent Sandbox 平台**：以 E2B、Modal Sandboxes、Daytona 为代表，产品化封装 sandbox 创建、镜像/快照、暂停恢复、文件系统、网络、GPU/Windows/Android 等能力。
4. **轻量临时代码执行环境**：以 Replit Code Execution API 类方案为代表，偏向短生命周期、无状态、一次性代码片段执行。
5. **浏览器/桌面自动化隔离环境**：以 BrowserPane、remote browser isolation、托管 Playwright/CDP 浏览器为代表，适合网页浏览、表单操作、数据采集和 GUI 自动化类 agent。

总体判断：如果 agent 只调用内部可信工具，普通容器加严格权限控制通常够用；如果执行用户代码或 LLM 生成代码，应至少使用 gVisor/Kata 等增强容器隔离；如果是多租户、高风险、不可信代码执行，Firecracker/microVM 或托管 microVM 平台更稳妥；如果需要快速产品化长期编程 agent，可优先评估 E2B、Modal、Daytona 这类托管平台。

---

## 1. 背景：Agent 场景为什么需要 Sandbox

AI Agent 与传统后端任务执行不同，常见风险来自以下几个方面：

- **LLM 生成代码不可完全信任**：模型可能生成删除文件、访问敏感路径、无限循环、网络扫描、泄露凭证等行为。
- **用户输入不可完全信任**：多租户 SaaS 场景下，用户可能主动提交恶意代码或诱导 agent 执行危险命令。
- **网页和文档内容不可完全信任**：浏览器 agent 会接触网页、PDF、邮件、工单等非可信内容，可能遭遇 prompt injection。
- **工具权限通常高于模型权限**：模型本身只输出文本，但工具可以读写文件、访问网络、执行 shell、调用内部 API。
- **长期任务需要生命周期管理**：agent 可能需要持续运行、暂停、恢复、保留文件、复用依赖环境，而不只是一次性执行代码。

因此，一个完整的 agent sandbox 设计通常包括：

```text
Agent Orchestrator
  |
  |-- Policy Engine
  |     |-- 工具权限
  |     |-- 网络权限
  |     |-- 文件权限
  |     |-- 人类确认策略
  |
  |-- Sandbox Manager
  |     |-- create / start / pause / resume / destroy
  |     |-- snapshot / template / image
  |     |-- resource quota
  |
  |-- Execution Adapter
  |     |-- Docker / gVisor
  |     |-- Kata / Firecracker
  |     |-- E2B / Modal / Daytona
  |     |-- Remote Browser
  |
  |-- Observability
        |-- command log
        |-- stdout/stderr
        |-- file diff
        |-- network log
        |-- audit event
```

需要强调：**sandbox 只能解决执行环境隔离的一部分问题**。真正安全的 agent 系统还需要权限策略、凭证管理、网络边界、审计日志、资源限制和人工确认机制。

---

## 2. 普通容器与增强型容器隔离

### 2.1 普通 Docker / Kubernetes 容器

普通容器是最常见、最低门槛的 sandbox 方式。它通常依赖 Linux 内核能力实现隔离：

- PID namespace：隔离进程视图；
- mount namespace：隔离文件系统挂载；
- network namespace：隔离网络栈；
- user namespace：隔离用户和权限映射；
- cgroups：限制 CPU、内存、磁盘 I/O、进程数量等资源；
- seccomp：限制系统调用；
- AppArmor/SELinux：限制进程访问能力；
- capabilities：移除不必要的 Linux capability。

#### 优点

- 工程成熟，生态完善；
- 与 Docker、Kubernetes、CI/CD、镜像仓库集成简单；
- 冷启动快，资源开销低；
- 适合内部可信或半可信任务。

#### 缺点

- 普通容器与宿主机共享内核；
- 默认 Docker 配置并不等于强安全沙箱；
- 如果挂载了 Docker socket、宿主敏感目录或使用 privileged 模式，隔离会被显著削弱；
- 面对恶意用户代码或内核漏洞时，容器逃逸风险需要认真评估。

#### 适合场景

- 内部工具调用型 agent；
- 预定义脚本执行；
- 半可信数据处理任务；
- 非多租户或低风险环境。

#### 不适合场景

- 用户任意代码执行；
- LLM 生成 shell 命令直接执行；
- 多租户 SaaS agent 平台；
- 需要强租户隔离的场景。

---

### 2.2 gVisor：用户态 application kernel

gVisor 是 Google 开源的容器 sandbox runtime。它不是传统虚拟机，也不是单纯的 syscall filter，而是在用户态实现一个类似 Linux kernel 的隔离层。gVisor 的核心组件 Sentry 会拦截并处理应用的系统调用和 page fault，从而减少容器进程直接暴露给宿主 Linux kernel 的攻击面。

gVisor 通过 `runsc` 作为 OCI runtime，可集成 Docker、Kubernetes/containerd 等容器生态。

#### 优点

- 可保留 Docker/Kubernetes 的使用体验；
- 隔离强度通常高于普通容器；
- 资源占用通常低于完整虚拟机；
- 对现有容器化 agent 改造成本较低；
- 适合作为不可信容器 workload 的增强运行时。

#### 缺点

- Linux syscall 兼容性不是 100%；
- syscall 密集型、I/O 密集型负载可能性能下降；
- 某些底层内核特性、特殊文件系统操作或调试能力可能不兼容；
- 隔离边界通常仍弱于硬件虚拟化 microVM。

#### 适合场景

- 已有 Docker/Kubernetes 基础设施；
- 想增强容器化 agent worker 的隔离；
- 执行半可信或部分不可信代码；
- 希望在隔离强度、性能和工程复杂度之间折中。

#### 参考资料

- [gVisor Documentation](https://gvisor.dev/docs/)
- [gVisor Architecture Guide](https://gvisor.dev/docs/architecture_guide/intro/)
- [gVisor Docker Quick Start](https://gvisor.dev/docs/user_guide/quick_start/docker/)
- [gVisor Kubernetes Quick Start](https://gvisor.dev/docs/user_guide/quick_start/kubernetes/)
- [gVisor Performance Guide](https://gvisor.dev/docs/architecture_guide/performance/)
- [google/gvisor](https://github.com/google/gvisor)

---

### 2.3 Kata Containers：用轻量 VM 做容器边界

Kata Containers 的思路是：对开发者暴露容器接口，但底层通过轻量虚拟机提供隔离。它可以与 Kubernetes 集成，也可以使用不同 hypervisor，其中包括 Firecracker。

#### 优点

- 容器接口体验接近传统容器；
- 隔离边界强于共享内核容器；
- 适合 Kubernetes 多租户 workload；
- 可以作为容器与 microVM 之间的折中方案。

#### 缺点

- 冷启动和资源占用通常高于普通容器；
- 网络、存储、镜像、调试链路更复杂；
- 某些高级容器能力受底层 hypervisor 限制；
- 运维复杂度高于 Docker/gVisor。

#### 适合场景

- Kubernetes 多租户环境；
- SaaS agent 平台运行不同租户任务；
- 需要 VM 级边界，但又想保留容器编排体验。

#### 参考资料

- [Kata Containers](https://katacontainers.io/software/)
- [Kata Containers Virtualization Design](https://github.com/kata-containers/kata-containers/blob/main/docs/design/virtualization.md)

---

## 3. 硬件虚拟化 MicroVM

### 3.1 Firecracker

Firecracker 是 AWS 开源的 microVM 技术，基于 KVM/硬件虚拟化运行轻量虚拟机，目标是为容器和函数类多租户 workload 提供安全、快速、低开销的执行环境。AWS Lambda、AWS Fargate 等场景使用了 Firecracker 相关技术路线。

Firecracker 通过极简 VMM 设计减少不必要设备模型和 guest-facing 功能，从而降低内存占用和攻击面。其安全模型还包括 defense-in-depth：线程级 seccomp 过滤、Jailer 进程、cgroups、chroot、mount namespace、降权等机制。

#### 优点

- 基于硬件虚拟化，隔离边界强于共享内核容器；
- 攻击面小于传统通用虚拟机；
- 比完整 VM 更轻量，适合高密度多租户；
- 适合 serverless sandbox、不可信代码执行和多租户 agent worker。

#### 缺点

- 工程复杂度高；
- 需要处理 kernel、rootfs、网络、快照、镜像构建；
- 与 Docker/Kubernetes 的直接开发体验不如普通容器；
- 通常需要平台团队封装后再提供给业务使用；
- 存储、热插拔、VFIO、virtio-fs 等能力可能存在限制或额外复杂度。

#### 适合场景

- 多租户 SaaS agent 平台；
- 用户提交代码执行；
- LLM 生成代码或 shell 命令执行；
- 高安全要求的数据处理；
- 需要比容器更强租户边界的 agent 执行环境。

#### 参考资料

- [Firecracker GitHub](https://github.com/firecracker-microvm/firecracker)
- [Firecracker Official Site](https://firecracker-microvm.github.io/)
- [AWS Firecracker Open Source Blog](https://aws.amazon.com/blogs/opensource/firecracker-open-source-secure-fast-microvm-serverless/)
- [Firecracker seccomp](https://github.com/firecracker-microvm/firecracker/blob/main/docs/seccomp.md)
- [Firecracker Jailer](https://github.com/firecracker-microvm/firecracker/blob/main/docs/jailer.md)
- [Firecracker NSDI Paper](https://www.usenix.org/system/files/nsdi20-paper-agache.pdf)

---

### 3.2 InstaVM

InstaVM 是面向 AI agent 场景的托管 real-VM/microVM sandbox 产品。其公开文档明确主张使用 Firecracker microVM 和 KVM 级隔离，而不是容器隔离。

#### 优点

- 面向不可信代码执行和 agent sandbox 场景；
- 托管化封装，降低自建 Firecracker 平台成本；
- 强调每个 sandbox 具有独立虚拟机边界；
- 适合接入 OpenAI Agents SDK 等 agent 框架。

#### 缺点

- 证据主要来自厂商一手文档和博客；
- 是否有第三方安全审计、生产规模案例、CVE 响应记录，需要单独确认；
- 成本、区域、性能、镜像生态和 API 成熟度需要进一步评估。

#### 参考资料

- [InstaVM](https://instavm.io/)
- [InstaVM How It Works](https://instavm.io/docs/getting-started/how-it-works)
- [InstaVM Sandboxes](https://instavm.io/docs/concepts/sandboxes)
- [InstaVM with OpenAI Agents SDK](https://instavm.io/blog/instavm-works-with-openai-agents-sdk-sandboxes)

---

## 4. 托管式 Agent Sandbox 平台

托管式平台直接把 sandbox 生命周期、文件系统、模板、快照、网络、代码执行 API、SDK 等能力封装好。相比自建 Docker/gVisor/Firecracker，它们更适合快速落地产品，但安全边界、默认网络策略、套餐限制和厂商锁定需要额外评估。

---

### 4.1 E2B

E2B 是 AI agent sandbox 领域较典型的托管平台。它支持为 agent 创建 cloud sandbox，并提供文件系统、进程执行、模板环境、timeout、pause/resume 等能力。

调研中确认的关键点：E2B sandbox 可按配置持续运行到超时，超时后可配置自动 pause；pause 会保存文件系统和内存状态，resume 后可恢复进程、变量和数据。连续运行时间受套餐限制；同时需要注意，部分文档显示默认 timeout 行为可能是 kill，pause 需要显式配置。

#### 优点

- 上手快，SDK 友好；
- 适合编程 agent、数据分析 agent、长期任务 agent；
- 支持 pause/resume，适合多轮任务；
- 支持保留文件系统和内存状态；
- 不需要团队自建底层隔离平台。

#### 缺点

- 平台锁定；
- 安全边界依赖厂商实现；
- 长运行时间、并发、资源规格、pause/resume 受套餐限制；
- 默认网络出站、凭证注入、文件持久化策略需要仔细核对。

#### 适合场景

- 编程 agent；
- 数据分析 agent；
- 需要保留 workspace 的长期任务；
- 希望快速验证 agent sandbox 产品形态的团队。

#### 参考资料

- [E2B Legacy Sandbox Overview](https://e2b.dev/docs/legacy/sandbox/overview)
- [E2B Auto Resume](https://e2b.dev/docs/sandbox/auto-resume)
- [E2B Persistence](https://e2b.dev/docs/sandbox/persistence)
- [E2B Billing](https://e2b.dev/docs/billing)

---

### 4.2 Modal Sandboxes

Modal Sandboxes 是 Modal 提供的运行时安全容器，用于执行不可信用户代码或 AI/LLM 生成代码。官方文档明确列出执行语言模型生成代码作为用例，并区分 Sandboxes 的 process/container-like 接口与 Restricted Functions 的 function-like 接口。

#### 优点

- 与 Modal 的镜像、函数、GPU、存储生态结合紧密；
- 适合 Python、ML、数据处理和 LLM 代码执行；
- 资源规格、依赖环境、云端执行体验较完整；
- 比自己维护 worker 集群简单。

#### 缺点

- 更偏 Modal 生态；
- 是否满足强不可信代码执行，需要结合其隔离、网络、权限策略进一步确认；
- 运行成本、区域、并发和资源限制依赖平台配置。

#### 适合场景

- LLM 生成 Python/数据分析代码；
- ML/AI workload；
- 需要 GPU 的 agent 任务；
- 已经使用 Modal 的团队。

#### 参考资料

- [Modal Sandboxes](https://modal.com/docs/guide/sandboxes)
- [Modal Restricted Access](https://modal.com/docs/guide/restricted-access)
- [Modal Sandbox Networking](https://modal.com/docs/guide/sandbox-networking)

---

### 4.3 Daytona

Daytona 面向 agent sandbox 和 development environment 编排。其文档展示了从默认环境、snapshot、image 创建 sandbox 的能力；snapshot 可复用预配置环境；还提供 GPU、Windows、Android emulator + Linux 控制 sandbox 等专用变体，以及 linked sandbox 内网拓扑。

#### 优点

- 环境编排能力强；
- snapshot/image 对复杂依赖和预装环境很有价值；
- 支持多 runtime 类型，包括 GPU、Windows、Android emulator；
- linked sandbox 适合多服务集成测试、agent 控制多个环境。

#### 缺点

- 安全隔离强度需要结合实际底层实现确认；
- linked sandbox 拓扑增加网络策略复杂度；
- 较新平台需要关注成熟度、审计、生产案例和故障恢复能力。

#### 适合场景

- 编程 agent / dev environment agent；
- 需要复用复杂依赖环境的任务；
- Windows、Android、GPU 等多环境 agent；
- 多服务联调或自动化测试 agent。

#### 参考资料

- [Daytona Sandboxes](https://www.daytona.io/docs/en/sandboxes/)
- [Daytona Snapshots](https://www.daytona.io/docs/en/snapshots/)
- [Daytona Getting Started](https://www.daytona.io/docs/getting-started)
- [Daytona Docs Source](https://github.com/daytonaio/daytona/blob/main/apps/docs/src/content/docs/en/sandboxes.mdx)

---

## 5. 轻量临时代码执行环境

### 5.1 Replit Code Execution API

Replit 曾提供面向 AI agent 的 self-serve code execution API。其公开资料显示，该方案是部署在 Autoscale Deployments 上的 stateless API container server，每次请求使用 omegajail unprivileged container sandbox。

#### 优点

- 适合轻量、短时、无状态代码执行；
- 接入形态简单；
- 适合教学、演示、简单计算和一次性代码片段运行。

#### 缺点

- 不适合长期 autonomous agent；
- 不适合多轮依赖安装和复杂 workspace；
- 不适合高隔离要求场景；
- 曾探索的 stateful Repl sandbox 已被官方说明不再支持；
- 相关 GitHub repo 已归档，不宜假设仍活跃维护。

#### 适合场景

- 单次代码片段执行；
- 无状态计算；
- 低风险 demo；
- 不需要保留文件、进程和依赖环境的 agent 工具调用。

#### 参考资料

- [Replit AI Agents Code Execution](https://replit.com/blog/ai-agents-code-execution)
- [replit-code-exec](https://github.com/replit/replit-code-exec)
- [omegajail](https://github.com/omegaup/omegajail)
- [Replit Deployments](https://docs.replit.com/cloud-services/deployments/about-deployments)

---

## 6. 浏览器 / 桌面自动化 Sandbox

### 6.1 Remote Browser Isolation / BrowserPane

浏览器自动化 agent 的风险与代码执行 agent 不完全相同。浏览器 agent 会访问不可信网页、加载第三方脚本、处理登录态、下载文件，并把网页内容传给 LLM，因此同时面临浏览器攻击面和 prompt injection 风险。

BrowserPane 将自己定位为 open-source remote browser isolation / remote browser stack。其公开资料称，每个浏览器 session 会获得 fresh Linux container，会话结束后销毁，并支持 CDP/Playwright 场景。

#### 优点

- 每会话临时环境，便于清理浏览器状态；
- 可隔离 cookie、profile、下载文件和网页脚本；
- 与 Playwright/CDP 接入自然；
- 适合网页浏览、表单填写、Web 数据采集、后台系统自动化。

#### 缺点

- 浏览器本身攻击面大；
- 如果底层只是容器隔离，仍需关注共享内核风险；
- 登录态、下载目录、剪贴板、内网访问需要严格控制；
- BrowserPane 的隔离强度证据主要来自厂商自述，置信度低于 gVisor/Firecracker 这类成熟基础设施。

#### 适合场景

- Web browsing agent；
- Playwright 自动化；
- 表单填写；
- 数据采集；
- 访问不可信网页但不希望污染主应用环境的任务。

#### 参考资料

- [BrowserPane](https://browserpane.io/)
- [BrowserPane: Remote Browser Isolation vs VDI](https://browserpane.io/blog/remote-browser-isolation-vs-vdi.html)
- [BrowserPane GitHub](https://github.com/ITmedes/browserpane)

---

## 7. 横向对比

| 方案 | 隔离强度 | 启动速度 | 工程复杂度 | 生命周期能力 | 适合场景 |
|---|---:|---:|---:|---:|---|
| 普通 Docker 容器 | 中低 | 快 | 低 | 一般 | 可信/半可信工具执行 |
| Docker + seccomp/AppArmor/rootless | 中 | 快 | 中 | 一般 | 内部 agent worker |
| gVisor | 中高 | 较快 | 中 | 容器级 | 容器化不可信代码执行 |
| Kata Containers | 高 | 中 | 中高 | Pod/容器级 | K8s 多租户隔离 |
| Firecracker microVM | 高 | 中 | 高 | 需自建 | 强隔离、多租户、serverless sandbox |
| E2B | 取决于厂商实现 | 快 | 低 | 强，支持 pause/resume | 编程 agent、长期任务 |
| Modal Sandboxes | 取决于厂商实现 | 快 | 低 | 容器进程级 | LLM 代码执行、ML/数据任务 |
| Daytona | 取决于厂商实现 | 中 | 低到中 | snapshot/image/linked sandbox | 多环境 agent、开发环境 |
| Replit Code Exec | 中 | 快 | 低 | 弱，无状态 | 临时代码片段执行 |
| BrowserPane / RBI | 中 | 中 | 低到中 | 会话级临时环境 | 浏览器自动化 agent |

---

## 8. 按 Agent 场景的选型建议

### 8.1 内部工具调用型 Agent

典型任务：

- 查询数据库；
- 调用内部 API；
- 读取受控文件；
- 执行预定义脚本。

建议：

- 不一定需要完整 VM sandbox；
- 使用权限最小化的 tool registry；
- 对每个工具做参数校验和审计；
- 脚本执行使用普通容器、rootless、seccomp/AppArmor；
- 网络和文件权限采用白名单。

推荐路线：

> 普通容器 / rootless Docker / Kubernetes Job + 严格权限控制。

---

### 8.2 LLM 生成代码执行

典型任务：

- “写一段 Python 并运行”；
- 数据分析 agent；
- notebook agent；
- 编程助手运行测试。

建议：

- 最少使用增强容器；
- 用户代码不可信时，优先 gVisor、Kata 或 microVM；
- 需要持久化和多轮执行时考虑 E2B/Daytona；
- 需要 GPU/ML 环境时考虑 Modal/Daytona。

推荐路线：

> 快速产品化：E2B / Modal / Daytona。  
> 自建平台：gVisor 起步，高风险多租户使用 Firecracker/Kata。

---

### 8.3 多租户 SaaS Agent 平台

典型任务：

- 每个用户可以让 agent 执行 shell；
- 支持安装依赖；
- 支持上传文件；
- 支持访问互联网；
- 任务可能长时间运行。

建议：

- 不建议只使用普通 Docker；
- 使用 microVM，或至少使用 gVisor/Kata；
- 每租户独立网络 namespace；
- 默认禁止访问内网和 metadata service；
- 出站网络 egress policy；
- 凭证按任务临时注入；
- 任务结束销毁或 snapshot；
- 审计所有命令、网络、文件操作。

推荐路线：

> Firecracker / Kata + Firecracker / 托管 microVM 平台 / E2B 类托管 sandbox。

---

### 8.4 浏览器自动化 Agent

典型任务：

- agent 打开网页；
- 自动登录；
- 操作后台系统；
- 读取网页内容；
- 使用 Playwright/CDP。

建议：

- 浏览器放到远程隔离环境；
- 每会话独立 profile；
- 下载目录隔离；
- 限制内网访问；
- 对网页文本进入 LLM 的链路做 prompt injection 防护；
- session 结束销毁容器或 VM。

推荐路线：

> Remote Browser Isolation / BrowserPane 类方案 / 自建 Playwright worker + 容器或 microVM。

---

### 8.5 长期 Autonomous Agent

典型任务：

- agent 连续工作数小时；
- 中间暂停；
- 需要保留文件、进程、变量状态；
- 可恢复任务。

建议：

- 需要生命周期管理，而不仅是隔离；
- pause/resume、snapshot、timeout、checkpoint 很关键；
- 明确文件系统状态、内存状态、进程状态是否保留；
- 设计成本控制、资源回收和超时策略。

推荐路线：

> E2B pause/resume / Daytona snapshot / 自建 snapshot + checkpoint 机制。

---

## 9. 安全设计清单

无论选择哪种 sandbox，都建议至少覆盖以下安全设计点。

### 9.1 网络隔离

- 默认禁止访问内网；
- 默认禁止访问云厂商 metadata service；
- 出站域名/IP 白名单；
- 防 SSRF；
- 按任务开放最小网络权限；
- 对网络访问做日志记录。

### 9.2 文件系统隔离

- 只挂载必要目录；
- 默认只读 rootfs；
- 临时目录任务结束清理；
- 上传文件和生成文件隔离；
- 禁止挂载 Docker socket；
- 禁止挂载宿主敏感路径；
- 限制磁盘容量和 inode 数量。

### 9.3 凭证隔离

- 不把长期密钥写入 sandbox 镜像；
- 使用短期 token；
- 任务级注入；
- 执行完成立即吊销；
- 日志脱敏；
- agent 不应默认读取全局环境变量。

### 9.4 资源限制

- CPU 限制；
- 内存限制；
- 磁盘限制；
- 进程数限制；
- 文件描述符限制；
- 超时强制终止；
- 防 fork bomb；
- 防无限网络请求。

### 9.5 系统调用和能力限制

- seccomp；
- AppArmor/SELinux；
- drop Linux capabilities；
- 禁止 privileged；
- 禁止 host PID/network；
- 优先 rootless 或 user namespace；
- 禁止加载内核模块。

### 9.6 可观测性和审计

- 记录命令；
- 记录参数；
- 记录 stdout/stderr；
- 记录文件变更；
- 记录网络访问；
- 记录工具调用；
- 保留退出码和失败原因；
- 高风险操作需要人类确认。

### 9.7 Prompt Injection 防护

- 不把网页/文件中的指令直接当系统指令；
- 区分 trusted instruction 与 untrusted content；
- 工具调用前做 policy check；
- 对外部网页、邮件、PDF、issue、PR 评论进入 LLM 的链路做隔离；
- 浏览器 agent 尤其需要防网页诱导泄露凭证或执行越权动作。

---

## 10. 结论与推荐

### 10.1 快速选型

- **编程 / 代码执行 agent**：优先评估 E2B、Modal Sandboxes、Daytona。
- **已有 K8s / Docker 基础设施**：先评估 gVisor，再评估 Kata Containers。
- **强不可信、多租户、高安全要求**：优先评估 Firecracker microVM 或托管 microVM 平台。
- **浏览器 agent**：使用远程浏览器隔离，每会话独立容器/VM。
- **简单一次性代码片段执行**：轻量 stateless code runner 可以，但不要把它当长期 agent workspace。

### 10.2 推荐落地路线

对大多数 agent 产品，建议按风险逐步演进：

1. **第一阶段：容器化执行 + 最小权限**
   - 适合内部工具和低风险任务；
   - 加上资源限制、网络白名单、文件系统隔离和审计。

2. **第二阶段：增强容器隔离**
   - 引入 gVisor 或 Kata；
   - 用于 LLM 生成代码、半可信用户任务、多租户初期场景。

3. **第三阶段：microVM 或托管 sandbox**
   - 高风险多租户使用 Firecracker/Kata + Firecracker；
   - 快速产品化可使用 E2B、Modal、Daytona 等托管方案。

4. **第四阶段：完整安全治理**
   - 建立 policy engine；
   - 建立凭证生命周期管理；
   - 建立网络 egress policy；
   - 建立审计与回放；
   - 对高风险动作加入人类确认。

最终建议：**不要只问“用哪种 sandbox”，而应先定义威胁模型**：代码是否不可信、是否多租户、是否能访问内网、是否持有凭证、是否长期运行、是否需要恢复状态。sandbox 技术选型应服务于这些边界，而不是替代它们。

---

## 11. 调研限制与待验证问题

本报告主要基于公开文档、官方博客、GitHub 仓库和厂商资料。需要注意：

- 多个结论来自厂商文档，能说明其公开定位和设计接口，但不等同于独立安全审计结论；
- BrowserPane 的隔离强度证据主要来自官网和博客，置信度低于 gVisor/Firecracker 等成熟基础设施；
- E2B、Modal、Daytona、Replit、InstaVM 等托管平台的套餐限制、默认生命周期、区域资源、API 名称和支持状态具有时间敏感性；
- Replit 相关 repo 已归档，不宜假设其库仍活跃维护；
- “secure”“full isolation”等表述应理解为产品或架构定位，不应视为无条件安全保证。

后续如果要进入技术选型或落地设计，建议继续验证：

1. gVisor、Firecracker/microVM、普通容器和托管平台在相同 agent 负载下的冷启动、I/O、syscall、内存占用基准；
2. 托管平台对网络出站、凭证注入、文件持久化、跨租户隔离和审计日志的默认策略；
3. 长期 autonomous agent 场景下，pause/resume、snapshot、ephemeral one-shot、linked topology 的成本和恢复语义；
4. BrowserPane、InstaVM、Daytona 等较新产品是否有第三方安全审计、CVE 响应记录或生产规模案例。
