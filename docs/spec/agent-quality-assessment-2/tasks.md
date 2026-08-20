# 实现计划：Agent Quality Assessment 2 — P0 性能/正确性/数据一致性强化

## 概述

本任务计划对应 `requirement.md` 的三项 P0 改造，按底层到顶层顺序编排：

1. **配置与领域异常**（DDL 等价层）：新增 `SessionConflictError` 异常与 `SessionLockConfig` 配置，追加 `config.properties` 键值——这些是后续编码的基础。
2. **Session_Optimistic_Lock_Cycle**：端口扩展 + Redis CAS + LocalFile CAS 对等实现。
3. **Pairing_Aware_Trimming**：`SlidingWindowCompactionAdapter` 配对保护裁剪。
4. **Concurrent_Tool_Execution**：`ReActAgentAdapter` 四入口同轮并发改造。
5. **验证与检查点**：每个模块后紧跟单测/property-based 测试与编译检查。

所有文件路径相对于 `epsilon-boot/`；命令在该目录下执行。

## Tasks

- [x] 1. 配置与领域异常基础设施
  - [x] 1.1 新增 `SessionConflictError` 领域异常
    - 创建 `src/domain/chat/exceptions.py`
    - 定义 `SessionConflictError(BizException)` 类，`code=60040`，属性 `session_id: str` / `retry_count: int`
    - 中文 docstring 说明用途（CAS 重试耗尽时抛出）
    - 从 `common.exceptions.BizException` 继承
    - _需求: R3.5, R3.9, D11_
  - [x] 1.2 新增 `SessionLockConfig` 配置类
    - 创建 `src/infrastructure/session/session_lock_config.py`
    - 定义 `SessionLockConfig(PropertiesBaseSettings)`，`model_config = SettingsConfigDict(env_prefix="SESSION_REDIS_")`
    - 字段 `conflict_retry_max: int = 3`，`@model_validator` 校验 `< 0` 时抛 `ConfigurationError`
    - 模块级 `session_lock_config = create_config(SessionLockConfig)` 全局实例
    - 中文 docstring
    - _需求: R3.6, D13_
  - [x] 1.3 追加 `config.properties` 配置键
    - 修改 `config.properties`
    - 追加注释段 + `SESSION_REDIS_CONFLICT_RETRY_MAX=3`
    - _需求: R3.6, 已知约束 2_
  - [x] 1.4 验证：配置加载测试
    - 创建 `test/infrastructure/session/test_session_lock_config_unit.py`
    - `test_session_redis_conflict_retry_max_loaded_from_config_properties`：临时 config.properties 验证加载
    - `test_negative_value_raises_configuration_error`：`< 0` 启动校验失败
    - `test_default_value_is_three`：默认值 3
    - _需求: R3.10(e)_

- [x] 2. `SessionContextStorePort` 端口扩展
  - [x] 2.1 扩展 `SessionContextStorePort` Protocol
    - 修改 `src/domain/chat/ports.py`
    - 在 `SessionContextStorePort` 末尾追加 `compare_and_swap(self, session_id: str, mutator: Callable[[ConversationContext], Awaitable[T]]) -> T` 方法签名
    - 新增 `from collections.abc import Awaitable, Callable` 和 `from typing import TypeVar`；定义 `T = TypeVar("T")`
    - 中文 docstring 说明"读取-修改-提交"的 CAS 语义、mutator 幂等要求、`SessionConflictError` 抛出条件
    - 既有 `save` / `load` / `delete` 签名不变
    - _需求: R3.1, R3.2, R3.9_

- [x] 3. `RedisSessionContextAdapter` CAS 实现
  - [x] 3.1 扩展构造函数
    - 修改 `src/infrastructure/session/redis_session_context_adapter.py`
    - `__init__` 追加可选参数 `conflict_retry_max: int | None = None`
    - 内部取值逻辑：`self._conflict_retry_max = conflict_retry_max if conflict_retry_max is not None else 3`
    - 中文 docstring 更新
    - _需求: R3.1, D13_
  - [x] 3.2 实现 `compare_and_swap` 方法
    - 修改 `src/infrastructure/session/redis_session_context_adapter.py`
    - 使用 `self._redis.pipeline(transaction=True)` + `pipe.watch(key)` + `pipe.get(key)` + `pipe.multi()` + `pipe.set(key, data, ex=self._ttl_seconds)` + `pipe.execute()`
    - `execute()` 返回 `None`（`WatchError`）视为冲突，循环重试至 `_conflict_retry_max`
    - 重试期间 `logger.info` 记录 `session_id` / `retry_count` / `outcome="retry"`
    - 成功 `logger.info` `outcome="success"`
    - 耗尽 `logger.info` `outcome="give_up"` + `logger.error` 最少字段 + 抛 `SessionConflictError`
    - `aioredis.RedisError` 在任一阶段按既有 `logger.error` 范式透传
    - 导入 `from domain.chat.exceptions import SessionConflictError`
    - 中文 docstring 含决策 D8 / D14 背景说明
    - _需求: R3.1, R3.4, R3.5, R3.7, R3.8_
  - [x] 3.3 DI 容器注入 `conflict_retry_max`
    - 修改 `src/application/container_config.py` 中 `_create_session_store` Redis 分支
    - 导入 `from infrastructure.session.session_lock_config import session_lock_config`
    - 传入 `conflict_retry_max=session_lock_config.conflict_retry_max`
    - _需求: R3.6_
  - [x] 3.4 验证：Redis CAS 单元测试
    - 创建 `test/infrastructure/session/test_redis_session_context_cas_unit.py`
    - `test_compare_and_swap_single_writer_success`：fakeredis 单写者成功
    - `test_compare_and_swap_two_writers_no_lost_update`：`asyncio.gather` 双写者无丢更新
    - `test_compare_and_swap_retry_exhausted_raises_session_conflict_error`：mock `execute()` 持续 `None`，断言 `SessionConflictError`
    - `test_compare_and_swap_preserves_ttl`：SET 命令带 `EX=ttl_seconds`
    - `test_save_load_delete_unchanged`：既有方法签名与日志范式不变
    - _需求: R3.10(a-c), R3.7, R3.2, R3.8_
  - [x] 3.5 验证：Redis CAS property-based 测试
    - 创建 `test/infrastructure/session/test_redis_session_context_cas_property.py`
    - `test_property_concurrent_cas_linearizable`：hypothesis 生成 K 个并发 mutator，断言最终消息集合为并集（线性化）
    - _需求: R3.10(b), NFR-3, Property 12_

- [x] 4. `LocalFileSessionContextAdapter` CAS 对等实现
  - [x] 4.1 实现 `compare_and_swap` 方法
    - 修改 `src/infrastructure/session/local_file_session_context_adapter.py`
    - 在 `EXCLUSIVE` 锁内执行 read → mutator → atomic write 三步
    - 不抛 `SessionConflictError`（文件锁路径无冲突）
    - `OSError` 按既有 `logger.error` 范式透传
    - 中文 docstring 含决策 D12 背景说明
    - _需求: R3.3, R3.8_
  - [x] 4.2 验证：LocalFile CAS 单元测试
    - 创建 `test/infrastructure/session/test_local_file_session_context_cas_unit.py`
    - `test_cas_single_writer_success`：单写者成功
    - `test_cas_two_writers_no_lost_update`：`asyncio.gather` 双写者文件锁串行化
    - `test_cas_does_not_raise_session_conflict_error`：文件锁路径不抛该异常
    - `test_cas_os_error_logged_and_propagated`：底层 `OSError` 日志 + 透传
    - _需求: R3.3, R3.10(d), R3.8, Property 14_

- [x] 5. 检查点：Session 层编译与全量测试
  - [x] 5.1 执行 `uv run pytest test/infrastructure/session/ -v`，确认全部通过
  - [x] 5.2 执行 `uv run python -c "from domain.chat.ports import SessionContextStorePort"` 确认端口可导入
  - [x] 5.3 确认既有 `test/infrastructure/session/test_local_file_session_context_adapter_unit.py` 不受影响
  - _需求: NFR-6_

- [x] 6. `SlidingWindowCompactionAdapter` 配对保护裁剪
  - [x] 6.1 新增内部方法 `_trim_with_pairing`
    - 修改 `src/infrastructure/chat/sliding_window_compaction_adapter.py`
    - 实现决策 D6 的单次反向扫描算法：从尾部向头部遍历 `non_system_messages`
    - 维护 `pending_tools_by_id: dict[str, ToolMessage]` 待配对缓冲
    - `AssistantMessage(tool_calls)` 到达时检查全集匹配 + 配额，整组保留或丢弃
    - 扫描结束后 `pending_tools_by_id` 残留项整组丢弃
    - `logger.debug` 记录丢弃组数/消息数
    - 中文 docstring 含算法说明
    - 新增 `import logging`、`from domain.chat.context import AssistantMessage, ToolMessage` 按需
    - _需求: R2.2, R2.3, R2.4, R2.5, R2.6, D6, D7_
  - [x] 6.2 改写 `compact_messages`
    - 修改 `src/infrastructure/chat/sliding_window_compaction_adapter.py`
    - 空输入退化（R2.9）不变
    - 无 ToolMessage 退化到 v3 路径（R2.10）
    - 含 ToolMessage 时调用 `self._trim_with_pairing(non_system_messages)`
    - 最终返回 `system_messages + trimmed`
    - 端口签名 `compact` / `compact_messages` 不变（R2.7）
    - 中文 docstring 更新
    - _需求: R2.1, R2.7, R2.9, R2.10_
  - [x] 6.3 验证：配对保护单元测试
    - 创建 `test/infrastructure/chat/test_sliding_window_pairing_aware_unit.py`
    - `test_window_boundary_splits_pair_drops_whole_group`：窗口边界恰切 → 整组丢弃
    - `test_three_tool_calls_one_outside_window_drops_group`：3 id 1 缺失 → 整组丢弃
    - `test_chained_groups_recent_kept_older_dropped`：多组串联验证
    - `test_no_tool_messages_falls_back_to_v3_literal`：无工具退化等价
    - `test_system_messages_fully_preserved`：system 全保留
    - `test_empty_input_returns_empty`：空输入
    - `test_logger_debug_records_dropped_count`：`caplog` 验证 debug 日志
    - `test_compact_async_signature_unchanged`：`compact(...)` 返回 `ContextCompactionResult`
    - _需求: R2.11(a-e), R2.1, R2.9, R2.10, NFR-4_
  - [x] 6.4 验证：配对保护 property-based 测试
    - 创建 `test/infrastructure/chat/test_sliding_window_pairing_aware_property.py`
    - `test_property_each_tool_message_has_assistant`：Property 8
    - `test_property_each_assistant_tool_calls_fully_covered`：Property 9
    - `test_property_system_messages_fully_preserved`：Property 10
    - hypothesis 策略：随机 `Tool_Pair_Group` 序列 + 随机 `max_messages ∈ [1, 30]`
    - _需求: R2.11(d), NFR-2, Property 8/9/10_

- [x] 7. 检查点：Chat 层编译与测试
  - [x] 7.1 执行 `uv run pytest test/infrastructure/chat/ -v`，确认全部通过（含既有 compaction 测试）
  - [x] 7.2 确认 `LLMSummaryCompactionAdapter` 路径零侵入
  - _需求: R2.8, NFR-6_

- [x] 8. `ReActAgentAdapter` 同轮并发改造
  - [x] 8.1 新增辅助方法 `_execute_tool_call_with_events`
    - 修改 `src/infrastructure/agent/react_agent_adapter.py`
    - 签名 `async def _execute_tool_call_with_events(self, context, tool_call, config, round_num) -> tuple[ToolCallRequest, str, bool]`
    - 内部直接复用 `_execute_tool_call`，返回 `(tool_call, result, is_error)` 三元组
    - 中文 docstring
    - _需求: R1.3, R1.4, D3_
  - [x] 8.2 新增辅助方法 `_dispatch_concurrent_tool_calls`
    - 修改 `src/infrastructure/agent/react_agent_adapter.py`
    - 签名 `async def _dispatch_concurrent_tool_calls(self, context, tool_calls, config) -> None`
    - `len == 1` 直接 `await self._execute_tool_call(...)` (fast path, D2)
    - `len >= 2` 通过 `asyncio.gather(*tasks, return_exceptions=False)` 并发（D1）
    - 中文 docstring 含 D1/D2/D5 决策说明
    - _需求: R1.1, R1.5, R1.7, R1.9, R1.10_
  - [x] 8.3 新增辅助方法 `_stream_concurrent_tool_progress`
    - 修改 `src/infrastructure/agent/react_agent_adapter.py`
    - 签名 `async def _stream_concurrent_tool_progress(self, context, tool_calls, config, round_num) -> AsyncIterator[StreamingChunk]`
    - 每个 `tool_call` 包装为 `asyncio.create_task`，用 `asyncio.as_completed` 按完成顺序消费
    - 每个完成的 task 整段 yield `tool_progress(start)` + `tool_progress(end)` 两个 chunk
    - fast path: `len == 1` 串行 yield
    - 中文 docstring 含 D3 决策说明
    - _需求: R1.3, R1.10_
  - [x] 8.4 新增辅助方法 `_events_concurrent_tool_calls`
    - 修改 `src/infrastructure/agent/react_agent_adapter.py`
    - 签名 `async def _events_concurrent_tool_calls(self, context, tool_calls, config, round_num) -> AsyncIterator[AgentStreamEvent]`
    - 与 `_stream_concurrent_tool_progress` 同构，产出 `tool_start` + `tool_result`/`tool_error` 事件
    - fast path: `len == 1` 串行 yield
    - 中文 docstring
    - _需求: R1.4, R1.10_
  - [x] 8.5 替换 `run` 入口的串行循环
    - 修改 `src/infrastructure/agent/react_agent_adapter.py`（约第 727 行）
    - 将 `for tool_call in outcome.tool_calls: await self._execute_tool_call(...)` 替换为 `await self._dispatch_concurrent_tool_calls(context, outcome.tool_calls, config)`
    - _需求: R1.1_
  - [x] 8.6 替换 `resume` 入口的串行循环
    - 修改 `src/infrastructure/agent/react_agent_adapter.py`（约第 835 行）
    - 同 8.5 替换为 `_dispatch_concurrent_tool_calls`
    - `_apply_approval_decisions` 内部保持严格顺序不动
    - _需求: R1.2, NFR-5_
  - [x] 8.7 替换 `run_streaming` 入口的串行循环
    - 修改 `src/infrastructure/agent/react_agent_adapter.py`（约第 1052 行）
    - 将 `for tool_call ... yield start; await execute; yield end` 替换为 `async for chunk in self._stream_concurrent_tool_progress(...): yield chunk`
    - 保留 `yield self._heartbeat_chunk(outcome.round_num)` 在前
    - _需求: R1.3_
  - [x] 8.8 替换 `run_events` 入口的串行循环
    - 修改 `src/infrastructure/agent/react_agent_adapter.py`（约第 1147 行）
    - 将 `for tool_call ... yield tool_start; await execute; yield tool_result/error` 替换为 `async for event in self._events_concurrent_tool_calls(...): yield event`
    - _需求: R1.4_
  - [x] 8.9 验证：并发工具调用单元测试
    - 创建 `test/infrastructure/agent/test_react_agent_concurrent_tool_calls_unit.py`
    - `test_single_tool_call_fast_path_equivalence`：Property 1 / R1.10
    - `test_run_three_concurrent_tools_total_elapsed_under_threshold`：3 工具各 sleep(0.5)，elapsed < 1.2s；Property 2
    - `test_run_partial_failure_does_not_affect_others`：3 工具 (deny, raise, ok)，断言 3 条 ToolMessage + metadata；Property 4
    - `test_run_streaming_tool_progress_pair_adjacency_three_tools`：streaming 事件配对相邻；Property 3
    - `test_run_events_tool_start_result_pair_adjacency_three_tools`：events 事件配对相邻；Property 3
    - `test_concurrent_timeout_keeps_pair_semantics`：1 超时 + 2 正常；Property 7
    - `test_concurrent_tools_dont_share_arguments_state`：引用唯一性；R1.9
    - _需求: R1.11(a-d), R1.1, R1.5, R1.8, R1.9, R1.10_
  - [x] 8.10 验证：并发工具调用 property-based 测试
    - 创建 `test/infrastructure/agent/test_react_agent_concurrent_tool_calls_property.py`
    - `test_property_event_pair_adjacency`：hypothesis K=[1,8] 随机耗时，断言分组连续；Property 3
    - `test_property_message_set_equivalence`：hypothesis N 工具 + 任意成功/失败，断言 tool_call_id 集合等价；Property 6
    - _需求: R1.11(c-d), R1.7_
  - [x] 8.11 验证：resume 路径并发与 HITL 串行共存测试
    - 创建 `test/infrastructure/agent/test_react_agent_concurrent_resume_unit.py`
    - `test_resume_apply_decisions_serial_then_concurrent`：审批顺序处理 + 后续轮次并发；Property 5
    - _需求: R1.2, R1.6, R1.11(e), NFR-5_

- [x] 9. 既有测试矩阵兼容性改写
  - [x] 9.1 改写 `test/infrastructure/agent/test_react_agent_streaming_unit.py`
    - 中间轮次 `tool_progress` 断言改为"按 `tool_call_id` 分组相邻"语义等价断言
    - _需求: NFR-6_
  - [x] 9.2 改写 `test/infrastructure/agent/test_react_agent_events_unit.py`
    - `tool_start` / `tool_result` 断言同上
    - _需求: NFR-6_

- [x] 10. 检查点：Agent 层全量测试
  - [x] 10.1 执行 `uv run pytest test/infrastructure/agent/ -v`，确认全部通过
  - [x] 10.2 确认 `test_react_agent_hitl_unit.py` / `test_react_agent_tool_timeout_unit.py` / `test_react_agent_run_events_tool_failure_unit.py` 不受影响
  - _需求: NFR-5, NFR-6_

- [x] 11. 全局检查点
  - [x] 11.1 执行 `uv run pytest` 全量测试，确认零失败
  - [x] 11.2 执行 `uv run python -c "from application.container_config import register_all"` 确认 DI 装配可导入
  - [x] 11.3 确认 `config.properties` 包含 `SESSION_REDIS_CONFLICT_RETRY_MAX=3`
  - [x] 11.4 确认不引入新依赖（`pyproject.toml` / `uv.lock` 无非测试依赖变更）；若 hypothesis 未安装则 `uv add --dev hypothesis`
  - _需求: NFR-6, 已知约束 3_

## 备注

1. **依赖顺序**：Task 1-2 为基础设施前置（异常、配置、端口），Task 3-4 为 Session 层 CAS 实现，Task 6 为 Chat 层配对保护，Task 8 为 Agent 层并发改造。每层完成后有检查点任务确保不引入回归。

2. **测试框架**：所有测试使用 `pytest` + `pytest-asyncio`（`asyncio_mode="auto"`）+ `hypothesis`。Mock 使用 `unittest.mock.AsyncMock` / `MagicMock`，复用 `test/infrastructure/agent/_v3_stream_helpers.py` 中的 helpers。Redis 测试使用 `fakeredis[aioredis]`。

3. **配置源约束**：`SESSION_REDIS_CONFLICT_RETRY_MAX` 必须写入 `epsilon-boot/config.properties`（`docs/steering/config-source.md`），`.env` 仅作本地覆盖。

4. **DDD 边界**：`SessionConflictError` 位于 `domain/chat/exceptions.py`（领域语义），CAS 技术细节（WATCH/MULTI/EXEC、文件锁）封装在 `infrastructure/session/` 内部，不向 domain 层泄漏。

5. **事件配对约束（D3）**：`run_events` / `run_streaming` 中并发工具完成后，同一 `tool_call_id` 的起止事件必须作为整段 yield，不与其他 tool_call_id 交叉。实现使用 `asyncio.as_completed` 按完成顺序消费。

6. **单工具 fast path（D2）**：`len(outcome.tool_calls) == 1` 时不进入 `asyncio.gather`，直接 `await` 单次调用，与 v3 行为字面等价。

7. **hypothesis 依赖**：若 `pyproject.toml` 尚无 hypothesis，在 Task 11.4 中通过 `uv add --dev hypothesis` 安装。
