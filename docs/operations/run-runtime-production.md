# Run Runtime 生产配置

本文档记录 Run runtime 在多副本生产环境中的配置基线。

## 后端选择

- 多副本生产必须使用 `SESSION_STORE_BACKEND=redis`。
- 本地文件 Run store 只适合单主机单实例；不适合多 Pod 共享 volume、NFS、SMB
  或对象存储 FUSE。
- 生产 Redis 应由平台侧提供高可用、持久化、监控和备份能力。

## Worker 与容量

- 每个 Pod 使用单 uvicorn worker，通过 K8S replica 扩容。
- `RUN_WORKER_COUNT` 控制每个 Pod 内后台 worker 数，默认值适合低并发环境。
- `RUN_MAX_RUNNING_RUNS` 应按 Provider 限流、工具风险预算和外部依赖容量设置。
- 调高 worker 或并发上限前，应确认 Provider 429、工具失败率、Run lost 比例和
  Redis 延迟没有恶化。

## 恢复语义

- Run runtime 不承诺外部副作用 exactly-once。
- checkpoint ledger 用于 bounded recovery：进程重启、worker 丢失或租约过期后，
  系统尽力从最近安全点继续。
- 外部工具、审批、消息发送、文件写入等副作用必须由对应 adapter 或业务流程承担
  幂等约束。

## 配置检查清单

- `SESSION_STORE_BACKEND=redis` 已通过 `config.properties`、环境变量或 Secret
  注入生效。
- Redis 连接配置和凭证不写入仓库默认明文配置。
- `RUN_WORKER_COUNT` 与 `RUN_MAX_RUNNING_RUNS` 已按目标 Provider 配额压测确认。
- `/readiness` 已纳入发布门禁，并能反映 Redis Run store 依赖状态。
- `/prometheus` 已接入监控系统，至少覆盖 Run 状态、API 5xx、Provider 错误率和
  Redis 可用性告警。
