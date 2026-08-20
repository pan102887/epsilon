# Spec 交付总结：Agent Quality Assessment 2 — P0 性能/正确性/数据一致性强化

## Feature

`agent-quality-assessment-2`：针对 ReAct Agent Loop 同轮工具调用并发、滑动窗口配对保护裁剪、Redis 会话 CAS 乐观锁三项 P0 缺口的生产级强化。

## 改动范围

### 新增文件

| 路径 | 用途 |
|------|------|
| `src/domain/chat/exceptions.py` | `SessionConflictError`（code 60040），CAS 重试耗尽时抛出 |
| `src/infrastructure/session/session_lock_config.py` | `SessionLockConfig`（`SESSION_REDIS_` 前缀 pydantic-settings） |
| `test/infrastructure/session/test_session_lock_config_unit.py` | 配置加载验证（3 用例） |
| `test/infrastructure/session/test_redis_session_context_cas_unit.py` | Redis CAS 单元测试（5 用例） |
| `test/infrastructure/session/test_redis_session_context_cas_property.py` | Redis CAS property-based 测试（hypothesis 并发线性化） |
| `test/infrastructure/session/test_local_file_session_context_cas_unit.py` | LocalFile CAS 对等验证（4 用例） |
| `test/infrastructure/chat/test_sliding_window_pairing_aware_unit.py` | 配对保护裁剪单元测试（8 用例） |
| `test/infrastructure/chat/test_sliding_window_pairing_aware_property.py` | 配对保护 property-based 测试（3 策略） |
| `test/infrastructure/agent/test_react_agent_concurrent_tool_calls_unit.py` | 并发工具调用单元测试（7 用例） |
| `test/infrastructure/agent/test_react_agent_concurrent_tool_calls_property.py` | 并发工具调用 property-based 测试（2 策略） |
| `test/infrastructure/agent/test_react_agent_concurrent_resume_unit.py` | resume 路径并发与 HITL 串行共存（1 用例） |

### 修改文件

| 路径 | 改动 |
|------|------|
| `config.properties` | 追加 `SESSION_REDIS_CONFLICT_RETRY_MAX=3` |
| `src/domain/chat/ports.py` | `SessionContextStorePort` 扩展 `compare_and_swap` 方法签名 |
| `src/infrastructure/session/redis_session_context_adapter.py` | 实现 `compare_and_swap`（WATCH/MULTI/EXEC 乐观锁循环） |
| `src/infrastructure/session/local_file_session_context_adapter.py` | 实现 `compare_and_swap`（文件锁内 read→mutator→write） |
| `src/application/container_config.py` | Redis 分支注入 `conflict_retry_max` |
| `src/infrastructure/chat/sliding_window_compaction_adapter.py` | 新增 `_trim_with_pairing` 反向扫描 + `compact_messages` 路由 |
| `src/infrastructure/agent/react_agent_adapter.py` | 新增 `_dispatch_concurrent_tool_calls` / `_stream_concurrent_tool_progress` / `_events_concurrent_tool_calls`；替换四入口串行循环 |
| `test/infrastructure/agent/test_react_agent_streaming_unit.py` | 适配并发语义（tool_progress 按 id 分组断言） |
| `test/infrastructure/agent/test_react_agent_events_unit.py` | 适配并发语义（tool_start/result 按 id 分组断言） |

## 测试结果

- 新增测试用例：**34 个**（含 property-based）
- 全量回归：**1644 passed / 1 failed（已知 web_search hypothesis 边界问题）/ 3 skipped**
- 编译检查：`container_config` / `ports` / 所有新增模块可正常导入

## 关键设计决策

1. **并发 fast path（D2）**：`len(tool_calls) == 1` 时不进入 `asyncio.gather`，直接 `await`，与 v3 字面等价。
2. **事件配对约束（D3）**：并发完成后同一 `tool_call_id` 的起止事件作为整段 yield，不与其他 id 交叉。使用 `asyncio.as_completed` 按完成顺序消费。
3. **配对保护反向扫描（D6）**：从尾部向头部遍历，维护 `pending_tools_by_id` 缓冲；`AssistantMessage(tool_calls)` 到达时检查全集匹配 + 配额，整组保留或整组丢弃。
4. **CAS 语义对等（D12）**：`LocalFileSessionContextAdapter` 在文件锁内执行 read→mutator→write，不抛 `SessionConflictError`（无冲突路径）；`RedisSessionContextAdapter` 通过 `WATCH` 实现等价保护。

## 已知遗留

1. `test_web_search_tool.py::test_format_completeness` — hypothesis 边界用例（content 含 `\n---\n` 分隔符），与本 spec 无关，已在 mcp-protocol-adapter summary 中记录。
2. `compare_and_swap` 端口为通用扩展，当前仅 `ReActAgentAdapter` 未直接调用（由业务层按需引入）；CAS 主要由内部 session 操作使用。

## 交付状态

✅ 所有 11 个任务组（含检查点）已完成。
