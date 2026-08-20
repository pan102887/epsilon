# 实施评审日志：model-access-protocol-encapsulation

> 本文档为 generator/evaluator 的追加式审计记录。每次评估、跳过或 FAIL 都追加一条；不删除/修改历史。

## 任务 1：端口契约去 OpenAI 协议化（原子提交）

- 实施模式：自动模式（用户已批准 b）
- 评估状态：实施完成，等待 spec-evaluator 审查（generator 自检验证全部通过）

### 改动文件清单

生产代码：

- `epsilon-boot/src/domain/model_access/value_objects.py`
  - `ChatRequest.messages` 类型由 `list[dict[str, Any]]` 改为 `list[BaseMessage]`（`TYPE_CHECKING` 引入）
  - `__post_init__` 改为非空 + 逐元素 `isinstance(BaseMessage)` 校验，错误消息含 index 与实际类型名
  - `messages` / `tools` 字段中文 docstring 重写：移除 OpenAI / Chat Completions / 字典示例 / role 键名等措辞
- `epsilon-boot/src/domain/model_access/ports.py`
  - 新增 `ModelAccessPort.count_tokens(messages: list[BaseMessage]) -> int` Protocol 声明，带中文 docstring 描述实现要求
  - 类 docstring Usage 示例去 OpenAI 协议字典化
- `epsilon-boot/src/domain/chat/value_objects.py`
  - `ContextBuilderResult.serialized_messages` 字段重命名为 `messages`，类型 `list[BaseMessage]`
  - `__post_init__` 改为校验 `BaseMessage` 子类实例，保留 usage / metadata 校验
  - 中文 docstring 重写：移除 OpenAI 协议描述
- `epsilon-boot/src/infrastructure/chat/context_builder_adapter.py`
  - `ContextBuilderResult` 构造参数由 `serialized_messages=` 改为 `messages=`（仅字段名同步，本任务 *不* 删除 `serialize_messages` 调用，留待任务 2）
- `epsilon-boot/src/infrastructure/chat/chat_service_adapter.py`
  - 3 处 `builder_result.serialized_messages` 改为 `builder_result.messages`
- `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py`
  - 3 处 `builder_result.serialized_messages` 改为 `builder_result.messages`，docstring 内的对应字面也同步
  - **不**删除 `_serialize_messages` 静态方法（任务 4 删除）

测试代码：

- `epsilon-boot/test/domain/chat/test_context_builder_result_unit.py`：完整重写，断言 `BaseMessage` 子类实例
- `epsilon-boot/test/domain/model_access/test_value_objects.py`：fixture 改为 `UserMessage(content=...)`，新增 BaseMessage 校验断言
- `epsilon-boot/test/domain/model_access/test_chat_request_tools_unit.py`：`_MESSAGES` 替换为 `_make_messages()` 返回 `list[BaseMessage]`
- `epsilon-boot/test/domain/model_access/test_chat_request_tools_property.py`：`_messages_st` 改为生成 `BaseMessage` 实例列表，类型注解同步
- 全仓批量替换字段名（38 个测试文件）：`serialized_messages=` → `messages=`、`.serialized_messages` → `.messages`、局部变量 `serialized_messages` → `messages_payload`，覆盖 `test/domain/chat/test_compaction_unit.py`、`test/infrastructure/chat/`、`test/infrastructure/agent/`、`test/infrastructure/prompt/` 下的所有命中文件

### 验证结果

- `uv run pytest test/domain/model_access/ -x` → 39 passed
- `uv run pytest test/domain/chat/test_context_builder_result*.py -x` → 11 passed
- `uv run ruff check src/domain/` → All checks passed
- `grep -rn "serialized_messages" src/ test/` → 0 命中（全仓干净）

### 已知中间态破口（设计预期）

- `ContextBuilderAdapter.build` 仍调用 `serialize_messages(...)` 生成字典列表后赋给 `ContextBuilderResult.messages`，会触发 `__post_init__` 校验失败 → 此破口由任务 2 修复（本任务不跑 `test/infrastructure/chat/` 与 `test/infrastructure/agent/` 的运行期测试，验证范围已限定）。
- `infrastructure/chat/message_serialization.py` 与 `token_counter.py` 仍保留，待任务 2/3 删除。
- `ReActAgentAdapter._serialize_messages` 仍保留，待任务 4 删除。
- `OpenAICompatibleAdapter._build_params` 仍直接透传 `request.messages`（现为 `list[BaseMessage]`），SDK 调用会失败 → 由任务 2 修复（adapter 内 `_to_openai_messages` 转换）。

### 残留 CLARIFICATION

无。设计与代码现状一致，机械替换可控。
