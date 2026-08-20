# 实现计划：流式工具调用 ID 兼容恢复

## 概述

本计划只修改模型接入适配层和相关测试，保持领域层非空 id 契约不变。任务按配置模型、适配器恢复逻辑、Agent 集成验证、文档与全量回归顺序推进；实现前需确认 `requirement.md` 与 `design.md` 已获认可。

## Tasks

- [x] 1. 配置模型与默认配置
  - [x] 1.1 修改 Provider 配置模型
    - 在 `epsilon-boot/src/infrastructure/model_access/provider_config.py` 中为 `ProviderConfig` 增加字段 `stream_tool_call_id_strategy: str = "recover"`。
    - 增加中文 docstring/字段说明，说明允许值为 `recover`、`raise`。
    - 不引入新依赖，不把配置类移动到 domain。
    - _需求: 3_
  - [x] 1.2 修改 `config.properties`
    - 在 `epsilon-boot/config.properties` 的模型 Provider 配置段为 `MODEL_QWEN_`、`MODEL_ZHIPU_`、`MODEL_CLIPROXY_`、`MODEL_OPENAI_` 补充 `STREAM_TOOL_CALL_ID_STRATEGY` 注释与默认值。
    - 兼容 Provider 默认 `recover`；官方 OpenAI 默认 `raise`。
    - _需求: 3, 4_
  - [x] 1.3 编写配置测试
    - 在 `epsilon-boot/test/infrastructure/model_access/test_provider_config_stream_tool_call_id_strategy_unit.py` 中覆盖字段默认值、properties/env 覆盖、非法值后续由 adapter fail-fast。
    - 使用 pytest，不新增配置读取框架。
    - **验证: 需求 3**

- [x] 2. 实现流式工具调用 id 恢复核心逻辑
  - [x] 2.1 修改 `OpenAICompatibleAdapter` 辅助类型与策略读取
    - 在 `epsilon-boot/src/infrastructure/model_access/openai_compatible_adapter.py` 中新增 `StreamToolCallIdRecoveryMode = Literal["recover", "raise"]`。
    - 新增 `_StreamToolCallIdRecovery` frozen dataclass，字段 `recovered_count: int = 0`、`synthetic_ids: tuple[str, ...] = ()`、属性 `occurred -> bool`。
    - 新增实例方法 `_stream_tool_call_id_strategy(self) -> StreamToolCallIdRecoveryMode`，非法配置抛 `ConfigurationError`。
    - _需求: 1, 3, 4_
  - [x] 2.2 实现合成 id 生成与恢复日志
    - 在 `openai_compatible_adapter.py` 中新增静态方法 `_synthetic_tool_call_id(request_nonce: str, index: int) -> str`，格式 `call_synthetic_{request_nonce}_{index}`。
    - 新增 `_log_stream_tool_call_id_recovery(...) -> None`，输出 WARN，`extra` 包含 `source/provider/model/tool_name/tool_call_index/raw_id_value/synthetic_id/recovery_strategy`。
    - 日志不得包含完整 message、prompt、API key、tool arguments。
    - _需求: 1, 4, 5_
  - [x] 2.3 改造 `_materialize_full_tool_calls`
    - 将 `OpenAICompatibleAdapter._materialize_full_tool_calls` 从 `@staticmethod` 改为实例方法，签名改为 `def _materialize_full_tool_calls(self, acc: dict[int, dict[str, Any]], params: dict[str, Any], *, request_nonce: str) -> tuple[list[StreamingToolCallDelta] | None, _StreamToolCallIdRecovery]`。
    - 对空 `acc` 返回 `(None, _StreamToolCallIdRecovery())`。
    - 原始 id 非空时保留；id 缺失且 name/arguments 完整、策略 `recover` 时生成合成 id 并写回 `slot["id"]`；策略 `raise` 时抛 `InvalidToolCallIdError`。
    - id 缺失且 name/arguments 不完整时不恢复，保留 `None` 供现有累积器违约回退。
    - _需求: 1, 2, 3, 5_
  - [x] 2.4 改造 `stream()` finished 与 usage-only 分支
    - 在 `OpenAICompatibleAdapter.stream(...)` 开始处创建 `request_nonce = uuid.uuid4().hex[:12]`。
    - 将 finished 分支和 choices 为空但含 usage 的分支改为接收 `(tool_calls, recovery)`。
    - 删除或收窄 `_validate_tool_call_ids(...)` 在 recover 默认路径上的调用；严格模式由 `_materialize_full_tool_calls` 内部处理。
    - 在发生恢复的 finished chunk 中写入 metadata：`tool_call_id_recovered=True`、`synthetic_tool_call_count=recovery.recovered_count`。
    - 确保 usage-only 分支不为同一 `index` 二次生成不同 id。
    - _需求: 1, 2, 3, 4, 5_

- [x] 3. 更新模型接入层测试
  - [x] 3.1 新增恢复单元测试
    - 新建 `epsilon-boot/test/infrastructure/model_access/test_openai_compatible_stream_id_recovery_unit.py`。
    - 覆盖 `id=None`、`id=""` 的 finished 分支恢复；断言 final chunk `tool_calls[0].id.startswith("call_synthetic_")`。
    - 覆盖 usage-only 末尾分支恢复；断言前一 finished 与 usage-only 不产生不同 id。
    - 覆盖 WARN `caplog` 字段和 finished metadata。
    - **验证: 需求 1, 4, 5, 6**
  - [x] 3.2 修改既有 id validation 测试
    - 修改 `epsilon-boot/test/infrastructure/model_access/test_openai_compatible_stream_id_validation_unit.py`。
    - 默认配置下缺失 id 的用例改为恢复成功。
    - 新增/保留策略为 `raise` 时抛 `InvalidToolCallIdError(source="stream_finished")` 的断言。
    - **验证: 需求 3, 6**
  - [x] 3.3 修改 materialize normalize 测试
    - 修改 `epsilon-boot/test/infrastructure/model_access/test_openai_compatible_materialize_normalize_unit.py`。
    - 适配 `_materialize_full_tool_calls(...)` 新签名与 tuple 返回。
    - 覆盖空 `acc`、原始 id 保留、缺失 id 恢复、不完整槽位不恢复。
    - **验证: 需求 1, 2, 5, 6**
  - [x] 3.4 增加属性测试
    - 在 `epsilon-boot/test/infrastructure/model_access/test_openai_compatible_stream_id_recovery_property.py` 中使用 Hypothesis 生成 1-8 个不同 `index` 的完整槽位。
    - 验证 recover 下合成 id 非空、唯一、ASCII 安全，且已有原始 id 不被覆盖。
    - **验证: 需求 1, 5, 6**

- [x] 4. Agent 链路集成验证
  - [x] 4.1 新增 ReAct Agent 恢复集成测试
    - 新建 `epsilon-boot/test/infrastructure/agent/test_react_agent_stream_tool_call_id_recovery_unit.py`。
    - 构造 Fake `ModelAccessPort.stream(...)`，第一轮返回缺失 id 但完整 `name/arguments` 的工具调用分片，第二轮返回最终文本。
    - 使用 fake `ToolRegistry.execute(...)` 记录入参 `ToolCallRequest.id`。
    - 断言 `AssistantMessage.tool_calls[0].id`、工具执行入参 id、`ToolMessage.tool_call_id` 三者一致且非空。
    - **验证: 需求 2, 6**
  - [x] 4.2 覆盖事件/流式 metadata 不回归
    - 在现有 `epsilon-boot/test/infrastructure/agent/test_react_agent_streaming_unit.py` 或新增同文件测试中覆盖 `run_streaming(...)` 能透传恢复后 finished metadata，且 tool_progress metadata 使用合成 id。
    - **验证: 需求 2, 4, 6**

- [x] 5. 文档与兼容说明
  - [x] 5.1 更新运行时后端或 Agent 文档
    - 在 `docs/operations/runtime-backends.md` 或 `docs/agent.md` 中补充 `MODEL_*_STREAM_TOOL_CALL_ID_STRATEGY` 的用途、默认值、`recover/raise` 行为和排障日志字段。
    - 明确官方协议仍要求 Provider 返回 id，合成 id 仅用于兼容 OpenAI-compatible 偏差。
    - _需求: 3, 4, 5_
  - [x] 5.2 增加文档静态测试
    - 在 `epsilon-boot/test/application/test_stream_tool_call_id_recovery_docs_static.py` 中读取 `config.properties` 和文档，断言配置键、策略名、`call_synthetic_` 前缀说明存在。
    - **验证: 需求 3, 4**

- [x] 6. 检查点 — 模型接入与 Agent 回归
  - 在 `epsilon-boot/` 目录运行：
    - `uv run --frozen pytest test/infrastructure/model_access/test_openai_compatible_stream_id_recovery_unit.py -q`
    - `uv run --frozen pytest test/infrastructure/model_access/test_openai_compatible_stream_id_validation_unit.py test/infrastructure/model_access/test_openai_compatible_stream_tool_calls_unit.py -q`
    - `uv run --frozen pytest test/infrastructure/agent/test_react_agent_stream_tool_call_id_recovery_unit.py -q`
    - `uv run --frozen pytest test -q`
  - 若全量测试出现非本特性相关失败，记录失败命令和首个错误，再由用户决定是否扩大修复范围。

## 备注

- 本计划默认不修改同步 `chat()` 空 id fail-fast 行为；若后续确认同一 Provider 的非流式响应也缺失 id，应另起需求或扩展本 spec。
- 本计划不迁移模型协议，也不依赖 OpenAI/Anthropic SDK 之外的新库。
- 实现完成后需要 evaluator review 通过后再勾选任务。
