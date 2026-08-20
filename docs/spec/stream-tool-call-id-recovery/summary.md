# 完成总结：流式工具调用 ID 兼容恢复

## 结果

已完成 `stream-tool-call-id-recovery` spec 的全部任务。默认兼容策略下，OpenAI-compatible Provider 在流式工具调用 finished 阶段缺失 `tool_call.id` 时，适配层会在工具名和参数完整的前提下生成稳定的本地合成 id，避免进入领域层后触发 `InvalidToolCallIdError(source="stream_finished", raw_id_value=None)`。官方 OpenAI Provider 默认仍使用严格策略。

## 关键变更

- `epsilon-boot/src/infrastructure/model_access/provider_config.py`
  - 新增 `stream_tool_call_id_strategy` 配置字段，支持 `recover` 与 `raise`。
- `epsilon-boot/config.properties`
  - 为 `MODEL_CLIPROXY_`、`MODEL_ZHIPU_`、`MODEL_QWEN_`、`MODEL_OPENAI_` 增加 `STREAM_TOOL_CALL_ID_STRATEGY`。
- `epsilon-boot/src/infrastructure/model_access/openai_compatible_adapter.py`
  - 在流式 finished/usage-only 分支统一 materialize 工具调用。
  - 缺失 id 且工具调用完整时按策略生成 `call_synthetic_<request_nonce>_<index>`。
  - 恢复发生时记录结构化 WARN 日志，并在 final chunk metadata 中标记恢复信息。
  - `raise` 策略保持 fail-fast。
- `docs/agent.md`
  - 补充配置、行为、合成 id 前缀与排障日志字段说明。
- 新增/更新模型接入层、Agent 集成、属性测试和文档静态测试。

## 验证

在 `epsilon-boot/` 下执行并通过：

- `uv run --frozen pytest test/infrastructure/model_access/test_openai_compatible_stream_id_recovery_unit.py -q`：3 passed
- `uv run --frozen pytest test/infrastructure/model_access/test_openai_compatible_stream_id_validation_unit.py test/infrastructure/model_access/test_openai_compatible_stream_tool_calls_unit.py -q`：9 passed
- `uv run --frozen pytest test/infrastructure/agent/test_react_agent_stream_tool_call_id_recovery_unit.py -q`：3 passed
- `uv run --frozen pytest test -q`：1752 passed, 2 skipped

## 剩余风险

- 本次只处理流式响应中 finished/usage-only 汇总阶段缺失工具调用 id 的兼容偏差；同步 `chat()` 返回缺失 id 的场景仍保持原有 fail-fast 行为。
- 合成 id 只保证单次请求内稳定且唯一，不代表 Provider 原生追踪 id。
