# Token 语义摘要上下文压缩实现总结

## 完成范围

- 领域层新增 `ContextCompactionResult`，并将 `ContextCompactionPort.compact` 改为异步结构化返回。
- 基础设施新增消息序列化、usage 合并、token 计数工具，并将 `tiktoken>=0.12.0` 纳入直接依赖。
- 新增 `context-summary@v1` Prompt，接入 Prompt 版本配置、`config.properties` 与 `ChatConfig`。
- 新增 `LLMSummaryCompactionAdapter`：按 token 阈值触发摘要，保留 system 消息与最近非 system 消息；异常、空摘要、tool_calls、缺少模型访问时降级到滑动窗口。
- 容器默认装配切换为摘要压缩策略，滑动窗口保留为 fallback。
- `ChatServiceAdapter` 与 `ReActAgentAdapter` 的同步、流式、事件流路径均接入 async 压缩，并合并摘要调用 usage。
- 评测与静态防回归测试已迁移，覆盖 Prompt 不硬编码和 compaction 不引入 budget 命名。

## 关键文件

- `epsilon-boot/src/domain/chat/value_objects.py`
- `epsilon-boot/src/domain/chat/ports.py`
- `epsilon-boot/src/infrastructure/chat/llm_summary_compaction_adapter.py`
- `epsilon-boot/src/infrastructure/chat/sliding_window_compaction_adapter.py`
- `epsilon-boot/src/infrastructure/chat/message_serialization.py`
- `epsilon-boot/src/infrastructure/chat/token_counter.py`
- `epsilon-boot/src/infrastructure/chat/usage.py`
- `epsilon-boot/src/infrastructure/chat/chat_service_adapter.py`
- `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py`
- `epsilon-boot/prompts/context-summary/v1.md`
- `epsilon-boot/config.properties`

## 验证记录

- `UV_CACHE_DIR=../.uv-cache uv run pytest test/application/test_prompt_registry_boot_regression_unit.py -q`：3 passed。
- `PYTHONPATH=..:src UV_CACHE_DIR=../.uv-cache uv run pytest ../tests/evaluation/metrics/test_context_compaction_effectiveness.py ../tests/evaluation/metrics/test_meta_context_compaction_effectiveness.py -q`：37 passed。
- `UV_CACHE_DIR=../.uv-cache uv run python -m compileall src`：通过。
- 已完成 focused checks：领域端口和值对象、消息序列化、usage、TokenCounter、Prompt/ChatConfig、LLM summary adapter、container、ChatService、Agent/Task 相关测试均通过。

## 注意事项

- 用户提供的全量日志原结果为 `2 failed, 1328 passed, 2 skipped`；两个失败均来自 boot regression 测试未创建 `context-summary@v1`，已修复并复跑通过。
- 本轮新开的 broad/full pytest session 长时间无新增输出且 stdin 不可交互，未作为最终 PASS 证据。
- 任务 11 的广域 budget 扫描命中了既有 `ThinkingConfig.budget_tokens`。该字段属于模型 thinking 配置，不是本次 compaction 命名；compaction 相关生产文件扫描无命中。
- 工作区存在与本任务无关的已暂存新增文件 `epsilon-boot/src/domain/context_builder/__init__.pt.py` 与 `epsilon-boot/src/domain/context_builder/port.py`，本实现未修改其内容。
