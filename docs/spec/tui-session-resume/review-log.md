# Review Log: TUI Session Resume

## 2026-06-14 Local Implementation Review

结论：通过。`/new` 已从破坏性清理改为仅切换当前 TUI `session_id`；新增 `/sessions`、`/resume <session_id>`、`/delete! <session_id>`；会话发现、恢复与删除通过 domain port、application runtime facade、infrastructure adapter 和 container composition root 分层实现。

检查项：

- 领域边界：`SessionMetadata`、`SessionIndexPort`、`SessionContextStorePort.exists()`、`ApprovalInterruptSummary`、`ApprovalStateStorePort.list_pending_by_session()` 均定义在 domain 层。
- 基础设施：本地文件与 Redis session index adapter 均已实现；Redis context/index 共用 `SESSION_REDIS_TTL_SECONDS`。
- 编排：`ChatServiceAdapter` 所有上下文保存路径统一同步 session index；显式删除同步删除 context、approval 与 index。
- CLI/TUI：`/new` 不再调用删除入口；恢复先查 index 再用 `exists()` 校验真实 context，不通过 `load()` 静默创建空会话。
- 兼容性：旧测试替身已补齐新增 port 方法；`_NoopApprovalStateStore` 已实现空摘要查询。

验证记录：

- `uv run --frozen pytest test/application/cli test/infrastructure/session test/infrastructure/agent test/infrastructure/chat test/application/test_container_config_backend_dispatch.py`
  - 结果：`563 passed`
- `uv run --frozen pytest`
  - 首次结果：`2589 passed, 2 skipped, 1 failed`
  - 失败说明：`test_npm_to_bun_migration_property.py::test_bun_install_does_not_modify_package_json` 因 `bun install` 30s 超时触发 Hypothesis flaky failure，非本需求改动路径。
- `uv run --frozen pytest test/integration/test_npm_to_bun_migration_property.py::test_bun_install_does_not_modify_package_json`
  - 结果：`1 passed`
- `uv run --frozen pytest`
  - 最终结果：`2590 passed, 2 skipped`

残余风险：无与本需求直接相关的已知失败。`bun install` 集成属性测试存在环境/网络相关耗时波动，已通过单测重跑与第二轮全量验证确认当前工作区通过。
