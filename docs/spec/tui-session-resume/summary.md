# Summary: TUI Session Resume

## 状态

已完成实现与验证。

## 交付内容

- `/new` 现在只切换到新的 TUI session，不删除旧对话上下文或 approval state。
- 新增 `/sessions`、`/resume <session_id>`、`/delete! <session_id>`，并更新帮助文本与 Textual 回归测试。
- 新增 session index domain port 与 `SessionMetadata`，并提供本地文件和 Redis adapter。
- 新增 `SessionContextStorePort.exists()`，恢复会话时避免用 `load()` 把缺失会话误当成空会话。
- 新增 approval pending 摘要查询，用于恢复会话时提示待处理 approval。
- `ChatServiceAdapter` 所有上下文保存路径同步更新 session index，显式删除同步清理 context、approval 与 index。
- Redis session context 与 index 共用 `SESSION_REDIS_TTL_SECONDS`，配置已写入 `config.properties`。
- `container_config.py` 已注册 `SessionIndexPort` 并注入 `ChatServiceAdapter` 与 CLI runtime 解析链路。

## 验证

- 需求相关回归：`563 passed`
- 单个 flaky 重跑：`1 passed`
- 最终全量回归：`2590 passed, 2 skipped`
