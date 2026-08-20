# 任务清单：model-access-protocol-encapsulation

> 顺序执行，每个任务完成后由 evaluator 审查，PASS 才能推进下一项。所有 `uv` 命令在 `epsilon-boot/` 目录下执行（`cd epsilon-boot && ...`）。

## 概述

本计划把"领域消息 → OpenAI Chat Completions 协议字典"以及"基于 tiktoken 的 token 估算"两类长期错位职责，从 `infrastructure/chat/` 与 `domain/model_access/value_objects.py` 归位到 `infrastructure/model_access/` 内每个具体 adapter，使 `domain/` 不再隐含 OpenAI 协议假设、未来引入 Anthropic / Bedrock / Gemini 等非 OpenAI 协议 adapter 时无需触动领域层。

按 design.md「改造分阶段建议」切分为 6 个原子任务（编号 1/2/3/4/6/7，跳过 5）：每个任务自包含、可独立编译可测，并且每个代码改动任务内同步迁移其直接对应的测试，避免中间态项目 import 失败。任务 2 合并了"协议转换归位"与"消除 4 个调用点"为一个原子 commit（删除 `message_serialization.py` 必然触发调用方 ImportError，不可拆分）。

## 任务清单

- [x] 任务 1：端口契约去 OpenAI 协议化（原子提交）
  - 对应需求：需求 1（全部）、需求 4（验收标准 1、2、3 的契约面）、需求 7（验收标准 1、2、3）
  - 对应设计：组件 1（`ChatRequest`）、组件 2（`ModelAccessPort.count_tokens` Protocol 声明）、组件 3（`ContextBuilderResult`）；改造分阶段建议 1
  - 改动文件
    - 修改 `epsilon-boot/src/domain/model_access/value_objects.py`
      - `ChatRequest.messages` 类型由 `list[dict[str, Any]]` 改为 `list[BaseMessage]`（用 `TYPE_CHECKING` 引 `from domain.chat.context import BaseMessage`）
      - `__post_init__` 校验改为：非空 + 逐元素 `isinstance(BaseMessage)`，错误消息含违规 index 与实际类型名；不再校验 `role` / `content` 字典键
      - 重写 `messages` 与 `tools` 字段的中文 docstring：删除所有 "OpenAI" / "Chat Completions" / 字典示例 / `role` 键名等措辞；`tools` docstring 改为"opaque tool schema 列表，由具体 adapter 翻译"
    - 修改 `epsilon-boot/src/domain/model_access/ports.py`
      - 在 `ModelAccessPort` Protocol 上新增同步方法 `def count_tokens(self, messages: "list[BaseMessage]") -> int: ...`，附中文 docstring（说明阈值用途、空列表返回 0、不同 Provider 由各自 adapter 决定 tokenizer）
    - 修改 `epsilon-boot/src/domain/chat/value_objects.py`
      - `ContextBuilderResult.serialized_messages` 字段重命名为 `messages`，类型改为 `list[BaseMessage]`
      - `__post_init__` 校验改为：`list` 且非空 + 逐元素 `isinstance(BaseMessage)`；保留 `usage` / `metadata` 既有校验
      - 同步重写中文 docstring 去 OpenAI 协议描述
    - 同步更新所有受字段重命名影响的生产代码引用（grep `serialized_messages` 全量定位）：
      - `epsilon-boot/src/infrastructure/chat/context_builder_adapter.py`（构造 `ContextBuilderResult` 的位置，仅改字段名 `serialized_messages=` → `messages=`，先暂保留对 `serialize_messages` 的调用，本任务只做"端口契约同步"）
      - `epsilon-boot/src/infrastructure/chat/chat_service_adapter.py`（约 235 / 385 / 445 行 3 处 `builder_result.serialized_messages` → `builder_result.messages`）
      - `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py`（约 751 / 1321 / 1384 行 3 处 `builder_result.serialized_messages` → `builder_result.messages`；本任务**不**删除 `_serialize_messages` 静态方法，仅同步字段名）
    - 同步更新所有 `ChatRequest(messages=...)` 与 `ContextBuilderResult(serialized_messages=...)` 测试 fixture：
      - 修改 `epsilon-boot/test/domain/model_access/` 下相关测试，把 `messages=[{"role": "user", "content": "hi"}]` 改为 `messages=[UserMessage(content="hi")]`
      - 修改 `epsilon-boot/test/domain/chat/` 下 `test_context_builder_result*.py` 等的字段名与断言（断言"必须为 BaseMessage 子类实例"取代"必须含 role / content"）
      - 在 grep `serialized_messages` 命中的测试文件中，**仅**替换字段名（`serialized_messages=` → `messages=`、`.serialized_messages` → `.messages`）；测试入参中"传入 OpenAI 字典"的 fixture 暂时保留为 OpenAI 字典形态——本任务一并改为 `BaseMessage` 实例（机械替换）
    - 注意：本任务结束后，`infrastructure/chat/message_serialization.py` / `token_counter.py` 仍存在，但 `ChatRequest` 已不接受 dict 形态。`ContextBuilderAdapter.build` 仍调用 `serialize_messages(...)`，但其结果会被立即赋给 `ContextBuilderResult.messages`，会触发 `__post_init__` 校验失败——这是预期的中间态破口，**任务 2 立即修复**。允许任务 1 在该范围内 commit/PR；任务 2 视为强依赖。
  - 验证
    - `cd epsilon-boot && uv run pytest test/domain/model_access/ -x`
    - `cd epsilon-boot && uv run pytest test/domain/chat/test_context_builder_result*.py -x`
    - `cd epsilon-boot && uv run ruff check src/domain/`
    - 进度自检：`cd epsilon-boot && grep -rn "serialized_messages" src/ test/` 应为空

- [x] 任务 2：`OpenAICompatibleAdapter` 内部承担协议转换 + 消除全部 `serialize_messages` 调用 + 删除旧文件
  - 对应需求：需求 2（全部）、需求 4（全部）、需求 5（验收标准 1、2、3、4、5）、需求 6（验收标准 1、3）、需求 7（验收标准 1、2、3）
  - 对应设计：组件 4.1（`_to_openai_messages`）、组件 4.2（`_build_params` 改造）、组件 5（`ContextBuilderAdapter`）、组件 6.3（`_build_summary_request`）、组件 7（`ReActAgentAdapter`）、组件 8（`ChatServiceAdapter`）、组件 9（删除 `message_serialization.py`）、Property 1、Property 4、Property 5、Property 6、Property 7；改造分阶段建议 2、3、5（`message_serialization` 删除部分）
  - 改动文件
    - 修改 `epsilon-boot/src/infrastructure/model_access/openai_compatible_adapter.py`
      - 新增私有静态方法 `@staticmethod def _to_openai_messages(messages: "list[BaseMessage]") -> list[dict[str, Any]]`，转换语义与原 `infrastructure.chat.message_serialization.serialize_messages` 字典级等价：`AssistantMessage` 携带 `tool_calls` 时输出 OpenAI 嵌套 `{"id","type":"function","function":{"name","arguments"}}`；`ToolMessage` 输出 `role` / `content` / `tool_call_id`；其他消息仅输出 `role` / `content`；附中文 docstring 说明转换规则与"信任已通过 `ToolCallRequest.__post_init__` 校验的领域消息"
      - `_build_params` 中 `params["messages"] = request.messages` 改为 `params["messages"] = self._to_openai_messages(request.messages)`；同步修正 docstring 中"messages 已是字典"假设
      - 顶部新增 `from domain.chat.context import BaseMessage, AssistantMessage, ToolMessage`（如尚未导入）
    - 修改 `epsilon-boot/src/infrastructure/chat/context_builder_adapter.py`
      - 顶部移除 `from infrastructure.chat.message_serialization import serialize_messages`
      - `build` 方法尾部不再调用 `serialize_messages(...)`；`ContextBuilderResult` 构造直接传 `messages=combined_messages`（领域消息列表）
      - 同步更新方法 docstring：移除"序列化为 OpenAI 协议字典"等措辞
    - 修改 `epsilon-boot/src/infrastructure/agent/react_agent_adapter.py`
      - 删除静态方法 `_serialize_messages`（约 217-236 行）
      - 顶部移除 `from infrastructure.chat.message_serialization import serialize_messages`
      - 3 处 `ChatRequest(messages=..., ...)` 入参改为传 `builder_result.messages`（任务 1 已完成字段重命名，此处去除 `_serialize_messages(...)` 包裹调用）
      - `tools=config.tool_schemas` 等其它字段不变
    - 修改 `epsilon-boot/src/infrastructure/chat/llm_summary_compaction_adapter.py`
      - `_build_summary_request`：移除 `serialize_messages` 调用，改用 `[m.to_dict() for m in messages]` + `json.dumps(..., ensure_ascii=False, indent=2)` 生成历史消息字符串；`ChatRequest.messages` 传入 `[SystemMessage(content=self._prompt.content), UserMessage(content=content)]`
      - 顶部移除 `from infrastructure.chat.message_serialization import serialize_messages`（如尚存）
    - 修改 `epsilon-boot/src/infrastructure/chat/chat_service_adapter.py`
      - 复核 3 处 `ChatRequest(messages=..., ...)` 入参均为领域消息列表（任务 1 已替换字段名，此处确保不再经 `serialize_messages`）
    - 删除 `epsilon-boot/src/infrastructure/chat/message_serialization.py`
    - 删除 `epsilon-boot/test/infrastructure/chat/test_message_serialization_unit.py`（其语义在新增测试文件中重建）
    - 新增 `epsilon-boot/test/infrastructure/model_access/test_openai_compatible_message_conversion_unit.py`
      - `test_convert_plain_system_user_assistant_messages`：仅含 `SystemMessage` / `UserMessage` 输出 `{"role","content"}` 字典
      - `test_convert_assistant_with_tool_calls_outputs_openai_nested_shape`：`AssistantMessage` 携 `tool_calls` 输出 OpenAI 嵌套结构
      - `test_convert_tool_message_includes_tool_call_id`：`ToolMessage` 输出 `tool_call_id`
      - `test_convert_empty_list_returns_empty_list`
      - `test_convert_does_not_mutate_input_messages`
      - 文件为模块、类、用例补充中文 docstring
    - 全仓 grep 兜底：`grep -rn "from infrastructure.chat.message_serialization\|serialize_messages\|_serialize_messages" src/ test/` 应无命中
  - 验证
    - `cd epsilon-boot && uv run pytest test/infrastructure/model_access/test_openai_compatible_message_conversion_unit.py -x`
    - `cd epsilon-boot && uv run pytest test/infrastructure/model_access/ test/infrastructure/chat/ test/infrastructure/agent/ -x`
    - `cd epsilon-boot && uv run ruff check src/infrastructure/`
    - 兜底命令：`cd epsilon-boot && grep -rn "message_serialization\|_serialize_messages\|serialize_messages" src/ test/` 应为空

- [x] 任务 3：`ModelAccessPort.count_tokens` 端口实现 + adapter 装配
  - 对应需求：需求 3（全部）、需求 5（验收标准 5）、需求 6（验收标准 2、3、5）、需求 7（验收标准 1、3、5）
  - 对应设计：组件 4.3（`count_tokens` 与 encoding 持有）、组件 6.1（`LLMSummaryCompactionAdapter` 构造签名改造）、组件 6.2（`compact` 内部对 token 计数的调用）、组件 9（删除 `token_counter.py`）、组件 10（组合根装配调整）、Property 3；改造分阶段建议 2（count_tokens 部分）、4、5（`token_counter.py` 删除部分）
  - 改动文件
    - 修改 `epsilon-boot/src/infrastructure/model_access/openai_compatible_adapter.py`
      - 构造签名新增 `tokenizer_encoding: str | None = None` kw-only 形参；构造期 `self._tokenizer = tiktoken.get_encoding(tokenizer_encoding or "cl100k_base")`，加载失败抛 `ConfigurationError("CHAT_COMPACTION_ENCODING 非法或不可用: ...")`
      - 新增方法 `def count_tokens(self, messages: "list[BaseMessage]") -> int`：内部沿用 `_to_openai_messages([message])[0]` + `json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",", ":"))` + `tiktoken` 编码 + 4 token 的 `_MESSAGE_OVERHEAD`，与原 `TokenCounter.count_messages` 语义字典级等价；空列表返回 0；附中文 docstring
    - 修改 `epsilon-boot/src/infrastructure/chat/llm_summary_compaction_adapter.py`
      - 构造签名移除 `token_counter` 形参；类 docstring 更新说明"token 计数职责已上升到 `ModelAccessPort.count_tokens`"
      - `compact(messages, *, model_access, model)` 入口：当 `model_access is None` 时直接降级到 `_fallback_with_warning(reason_class="ModelAccessMissing")`；否则用 `model_access.count_tokens(messages)` 与 `self._trigger_tokens` 比较；其余分支保持
      - 删除 `from infrastructure.chat.token_counter import TokenCounter` 导入
    - 修改 `epsilon-boot/src/application/container_config.py`
      - `_create_compaction_adapter`：移除 `from infrastructure.chat.token_counter import TokenCounter` 导入与 `TokenCounter(...)` 实例化，构造 `LLMSummaryCompactionAdapter` 时不再传 `token_counter`
      - 在 `OpenAICompatibleAdapter` 的全部装配点（grep `OpenAICompatibleAdapter(` 命中）注入 `tokenizer_encoding=chat_config.compaction_encoding`
    - 删除 `epsilon-boot/src/infrastructure/chat/token_counter.py`
    - 删除 `epsilon-boot/test/infrastructure/chat/test_token_counter_unit.py`（其语义在新增测试文件中重建）
    - 新增 `epsilon-boot/test/infrastructure/model_access/test_openai_compatible_count_tokens_unit.py`
      - `test_count_tokens_empty_list_returns_zero`
      - `test_count_tokens_pure_text_messages_is_positive_int`
      - `test_count_tokens_with_tool_calls_is_positive_int`
      - `test_count_tokens_invalid_encoding_raises_configuration_error`
      - `test_count_tokens_message_list_equals_sum_of_individual_messages`
      - 类、模块、用例补中文 docstring
    - 调整 `epsilon-boot/test/infrastructure/chat/test_llm_summary_compaction_adapter_unit.py` 与同目录 `test_llm_summary_compaction_properties.py`
      - 把 `TokenCounter` fixture 注入改为通过 `model_access`（FakeModelAccessAdapter，可在测试文件内 inline 定义）传入 `count_tokens`
      - 新增用例：`model_access is None` 时入口立即降级
    - 全仓 grep 兜底：`grep -rn "token_counter" src/ test/` 应无命中
  - 验证
    - `cd epsilon-boot && uv run pytest test/infrastructure/model_access/test_openai_compatible_count_tokens_unit.py -x`
    - `cd epsilon-boot && uv run pytest test/infrastructure/chat/test_llm_summary_compaction_adapter_unit.py test/infrastructure/chat/test_llm_summary_compaction_properties.py -x`
    - `cd epsilon-boot && uv run ruff check src/infrastructure/ src/application/`
    - 兜底：`cd epsilon-boot && grep -rn "TokenCounter\|token_counter" src/ test/` 应为空

- [x] 任务 4：测试套件批量迁移与端口级 fake adapter
  - 对应需求：需求 4（验收标准 3、4）、需求 5（验收标准 1、2、3、5）、需求 6（验收标准 4、5、6）、需求 7（验收标准 1、2、3）
  - 对应设计：组件 5（测试面）、组件 7（测试面）、组件 8（测试面）、测试策略 E（FakeModelAccessAdapter）、H、I、J、K、Property 6、Property 7；改造分阶段建议 6
  - 改动文件
    - 新增 `epsilon-boot/test/domain/model_access/_fake_adapter.py`
      - `FakeModelAccessAdapter` 类，实现 `ModelAccessPort` 协议最小子集：
        - `count_tokens(messages)`：默认 `sum(len(m.content) for m in messages)`，构造期可注入自定义 `count_fn`
        - `chat`、`stream`：默认返回 `pytest.fail` 占位或允许测试 monkeypatch
      - 中文 docstring 说明用途与限制
    - 同步迁移以下测试至传入 `BaseMessage` 列表（机械替换 fixture，覆盖范围以 grep 兜底为准）：
      - `epsilon-boot/test/infrastructure/chat/test_context_builder_adapter_unit.py`
      - `epsilon-boot/test/infrastructure/chat/test_context_builder_properties.py`
      - `epsilon-boot/test/infrastructure/chat/test_llm_summary_compaction_adapter_unit.py`（任务 3 已动 fixture，此处仅补 `_build_summary_request` 输出形态断言：第二条 `UserMessage.content` 为领域字典 JSON 而非含 `"type":"function"` 嵌套的 OpenAI 字典——对应 Property 6）
      - `epsilon-boot/test/infrastructure/agent/test_react_agent_*.py`（约 25 个文件，grep `_serialize_messages` 与 `serialize_messages` 残留命中处批量清理 fixture）
      - `epsilon-boot/test/infrastructure/model_access/test_openai_preservation_properties.py`：把 `_make_chat_request` fixture 切到领域消息（Property 1 / 4 / 5 锚点）
      - `epsilon-boot/test/infrastructure/model_access/test_openai_compatible_chat_id_validation_unit.py`：fixture 改为领域消息（commit `040695a` 加固语义不变，对应 Property 4）
      - `epsilon-boot/test/infrastructure/model_access/test_openai_compatible_stream_tool_calls_*.py`：fixture 改为领域消息（Property 5）
      - `epsilon-boot/test/infrastructure/chat/test_chat_service_*.py`：fixture 改为领域消息
    - 新增 / 扩展 `epsilon-boot/test/domain/model_access/test_chat_request_post_init_unit.py`（若不存在则新增；存在则扩展）
      - `test_chat_request_rejects_non_base_message_element`
      - `test_chat_request_accepts_all_concrete_subclasses`
      - 删除 / 改造既有"缺少 role 或 content"分支
    - 兜底命令（必须为空）：
      - `grep -rn "serialized_messages" src/ test/`
      - `grep -rn "message_serialization\|token_counter\|TokenCounter\|_serialize_messages" src/ test/`
      - `grep -rn "messages=\[\s*{\s*[\"']role[\"']" test/`（残留 OpenAI 字典 fixture）
  - 验证
    - `cd epsilon-boot && uv run pytest test/infrastructure/chat/ test/infrastructure/agent/ test/infrastructure/model_access/ test/domain/model_access/ -x`
    - `cd epsilon-boot && uv run ruff check src/ test/`
    - 兜底：`cd epsilon-boot && grep -rn "serialize_messages\|_serialize_messages\|serialized_messages" src/ test/` 应为空

- [x] 任务 6：DDD 合规自检与中文 docstring 终验
  - 对应需求：需求 7（全部）、需求 5（验收标准 1、2）
  - 对应设计：DDD 合规性自检表、中文 docstring 与配置规范执行清单
  - 改动文件（仅查核与补写 docstring，不引入业务行为变化）
    - 自检命令（应全部为空）：
      - `cd epsilon-boot && grep -rn "import openai\|from openai\|tiktoken" src/domain/`
      - `cd epsilon-boot && grep -rn "from infrastructure" src/domain/`
      - `cd epsilon-boot && grep -rn "OpenAI\|Chat Completions" src/domain/model_access/ src/domain/chat/`（剩余命中应仅为本次保留的 docstring 反向说明，逐个审阅）
    - 对本次新增 / 修改的所有公开类、公开函数、方法（`ChatRequest` / `ContextBuilderResult` / `ModelAccessPort.count_tokens` / `OpenAICompatibleAdapter._to_openai_messages` / `OpenAICompatibleAdapter.count_tokens` / `OpenAICompatibleAdapter.__init__` 改造点 / `LLMSummaryCompactionAdapter.__init__` / `LLMSummaryCompactionAdapter.compact` / `LLMSummaryCompactionAdapter._build_summary_request` / `ContextBuilderAdapter.build` / `FakeModelAccessAdapter`）补齐中文 docstring，复杂逻辑（协议转换、tokenizer 选择、降级语义、字符串化路径）补背景说明
    - 复核未引入 `pip` / `poetry` / `pipenv` / `conda` 命令，未新增配置键
  - 验证
    - `cd epsilon-boot && uv run pytest -x`
    - `cd epsilon-boot && uv run ruff check src/ test/`
    - 自检 grep 全部为空（命令同上）

- [x] 任务 7：文档同步
  - 对应需求：需求 7（验收标准 3）；本次治理总结收尾
  - 对应设计：改造分阶段建议 7
  - 改动文件
    - 修改 `docs/architecture.md`：把"上下文构建结果承载 OpenAI 协议字典 / Token 计数由 `infrastructure/chat/token_counter.py` 完成"等旧描述更新为"承载领域消息 + 协议转换在 `OpenAICompatibleAdapter` 内部完成 / `ModelAccessPort.count_tokens` 端口下沉"；补充对未来非 OpenAI 协议 adapter 的扩展说明
    - 修改 `docs/domain-model.md`：更新 `ChatRequest.messages` / `ChatRequest.tools` / `ContextBuilderResult.messages` 字段描述（去 OpenAI 协议措辞、字段名 `serialized_messages → messages`）
    - 修改 `docs/agent.md`：在 ReAct Loop / 摘要压缩相关章节标注"token 计数路径已上升到 `ModelAccessPort.count_tokens`，由具体 adapter 持有 tokenizer"（无需扩展配置说明，键名与默认值不变）
    - 复核 `CLAUDE.md` 主题文档索引无需新增条目（本次未新建主题文档）
  - 验证
    - 人工 grep 检查：`grep -rn "serialize_messages\|TokenCounter\|serialized_messages" docs/` 应为空
    - 人工 grep 检查：`grep -rn "OpenAI 协议\|Chat Completions" docs/` 命中处仅应作为历史/对比/扩展说明出现，逐个审阅措辞是否仍准确
    - `cd epsilon-boot && uv run pytest -x`（终验：与文档一致，整套测试通过）

## 备注

- 本次治理无新增配置键，无数据库改动，无 DDL / 数据回填脚本；故没有传统意义上的"migrations 前置 / 数据回填后置"任务。
- 任务 1 是不可拆分的原子任务：必须同时改 `ChatRequest`、`ContextBuilderResult`、`ModelAccessPort.count_tokens` Protocol 声明与三处生产代码字段名引用，否则 `__post_init__` 校验立即在 import 期/运行期爆出错误。任务 1 结束后允许存在"`ContextBuilderAdapter.build` 仍调用 `serialize_messages` 但其结果赋给 `ContextBuilderResult.messages` 触发校验"的中间态破口，由任务 2 立即修复。请勿在任务 1 与任务 2 之间触发任何 `ContextBuilderAdapter.build` 链路的运行期测试。
- 任务 2 合并了原始任务 4（"上游 4 调用点脱离 OpenAI 协议字典"）中的**生产代码**改动——原因：删除 `message_serialization.py` 必然导致 4 个调用点立即 `ImportError`，必须在同一原子 commit 内修复。原始任务 4 的**测试 fixture 批量迁移**职责下沉到新任务 4。
- 任务 2、3 顺序不能颠倒：`count_tokens` 实现内部沿用 `_to_openai_messages`，必须先有协议转换函数再有 token 计数实现。
- 任务 4（测试套件批量迁移 + FakeModelAccessAdapter）合并了原始任务 5 的全部范围。"端口级 fake adapter"不在任务 3 内引入是为了让任务 3 内单测可以直接 inline 一个最小 stub；任务 4 把它抽到共享文件避免重复。
- 所有 `uv` 命令必须在 `epsilon-boot/` 子目录执行（满足 `docs/steering/uv-package-manager.md`）；不得引入 `pip` / `poetry` / `pipenv` / `conda`。
- 文档主题更新（任务 7）放在最后，避免在代码改动尚未稳定时反复重写文档。
- 任务总数从 7 缩减为 6（原任务 4 生产代码部分并入任务 2，原任务 5 整体并入新任务 4）。编号保持 1/2/3/4/6/7 不做重编以避免引用混乱。
