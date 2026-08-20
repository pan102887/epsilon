---
status: Accepted
date: 2026-07-05
deciders: [spec-designer, 平台安全负责人]
supersedes:
superseded-by:
---

# ADR-0006：多租户可见性机制与会话主状态 USER tier 默认路径的安全边界

## 背景与问题（Context）

ADR-0002 将会话主状态归属 `USER` tier，并在 `LOCAL_PERSISTENCE_ROOT` 未显式配置时把本地默认路径迁移到 `~/.epsilon/persistence/<project-hash>/`。这带来两个必须明确的方向级问题：

1. **多租户可见性**：云端多用户对产物的可见性如何隔离？若误以为「不同 tier / 不同目录」就是租户隔离，会引入越权风险。
2. **USER tier 本地默认路径的安全边界**：`~` 本地路径能否被当作多实例 / 多租户生产的持久化后端？`<project-hash>` 如何生成？既有安全禁令与启动校验是否弱化？

## 决策（Decision）

我们将确立以下边界（本 spec 仅记录方向、不实现云端）：

1. **多租户可见性由 `TenantVisibilityPolicy`（SSO / 权限校验）在对应 adapter 层保证，绝不依赖文件系统路径隔离**。`TENANT` 仅作为 `StorageTier` 预留枚举取值，本特性不实现 `TenantVisibilityPolicy` 与云端 adapter。
2. **`~/.epsilon/persistence/<project-hash>/` 仅为本地文件 adapter 对 `USER` tier 的默认单机实现**，明确「仅保证单主机单实例协同」，**禁止**用于多实例 / 多租户生产。云端 / 多实例生产必须走 `SESSION_STORE_BACKEND=redis` 或（未来的）对象存储 adapter。
3. **`<project-hash>` 生成方式（全仓库唯一生成点）**：对 PROJECT 基点（`WORKSPACE_ROOT`，空则 CWD）的**规范化绝对路径**取 `hashlib.sha256`，取前 16 位十六进制，实现落在 `LocalFileTierResolver.project_hash()`。确定性、跨会话稳定、天然满足跨平台文件名约束；不含原始路径明文，避免把宿主目录结构泄露进文件名。**会话主状态默认路径与 USER tier 运行产物（日志，ADR-0005 决策 2b）共同复用此单一生成点**，保证二者落在同一 `<project-hash>` 分区键下。
4. **保留既有安全禁令与启动校验不弱化**：`config.properties` 中 NFS/SMB/OSS FUSE、多容器共享 volume 的禁止注释保留并补充说明默认路径迁移；`_validate_local_persistence_root` 的 7 步校验（含与 `WORKSPACE_ROOT` 的相互包含冲突检测）继续对迁移后的 USER tier 默认路径生效——USER tier 默认落在 `~`（HOME），与 `WORKSPACE_ROOT=cwd` 天然不相互包含，冲突检测自然通过。
5. **`config.properties` 显式行改留空 + 首次启动一次性提示（决策 1a）**：既有显式行 `LOCAL_PERSISTENCE_ROOT=../.local_persistence/epsilon-boot` 改为留空/注释以启用默认迁移；`_init_local_persistence` 在解析出 USER tier 默认路径后，若检测到旧默认目录存在（非空）且新默认目录为空，`logger.info` 输出一次性中文迁移提示（含旧/新路径与手动迁移/显式保留两个选项），**不自动搬运数据**，检测失败静默跳过。

## 后果（Consequences）

- **正面**：租户隔离责任明确落在鉴权层而非文件系统，避免路径伪隔离的越权；本地默认路径迁移不弱化既有安全防线；`<project-hash>` 确定性可复现、不泄露路径明文。
- **负面 / 代价**：`~/.epsilon/persistence/` 跨项目聚合在用户目录，须依赖 `<project-hash>` 分区避免不同项目会话串档；迁移后旧 `../.local_persistence/...` 数据不会自动搬迁，需迁移说明。
- **后续影响**：`docs/configuration.md` 须记录默认路径迁移、显式配置 / redis 不受影响、多租户由鉴权层保证；`TenantVisibilityPolicy` 与云端 adapter 由后续 spec 实现。

## 备选方案（Alternatives）

- **方案 A：用文件系统路径 / 目录权限做多租户隔离** —— 未采纳：容器 / 共享盘下路径隔离不可靠，易越权；隔离必须由 SSO / 权限校验保证。
- **方案 B：`<project-hash>` 用原始路径明文或 basename** —— 未采纳：明文泄露宿主目录结构、basename 易碰撞（不同项目同名目录串档）。
- **方案 C：USER tier 默认仍用 `../.local_persistence/...`（不迁移）** —— 未采纳：与 tier 语义（USER=跨项目单用户）不一致，且 `../` 相对路径在不同 CWD 下语义漂移；`~` 更符合「用户级」语义。显式配置者仍可保留旧路径（尊重显式配置）。
