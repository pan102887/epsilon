# 实施计划：上下文压缩策略（Context Compaction Strategy）

## 概述

将 `ConversationContext.get_messages()` 中内嵌的滑动窗口裁剪逻辑抽取为独立的 Port/Adapter 模式。实施按"领域层端口定义 → 值对象简化 → 适配器实现 → 编排层集成 → DI 注册 → 测试"的顺序递进，每步构建在前一步基础上，确保增量可验证。

## Tasks

- [x] 1. 定义 ContextCompactionPort 并简化 ConversationContext
  - [x] 1.1 在 `domain/chat/ports.py` 中新增 `ContextCompactionPort` Protocol
    - 导入 `Message` 类型（TYPE_CHECKING 下）
    - 定义 `compact(self, messages: list[Message]) -> list[Message]` 方法
    - 添加中文 docstring，说明端口职责和方法语义
    - _需求: 1.1, 1.2, 1.3, 1.4_

  - [x] 1.2 重构 `domain/chat/context.py` 中的 `ConversationContext`
    - 移除 `__init__` 的 `max_messages` 参数和 `_max_messages` 属性
    - 修改 `get_messages()` 返回 `list[Message]`（移除裁剪逻辑和序列化，移除 `max_tokens` 参数）
    - 修改 `to_dict()` 不再包含 `max_messages` 字段
    - 修改 `from_dict()` 兼容包含和不包含 `max_messages` 的字典数据（忽略该字段）
    - 更新模块级 docstring，移除"窗口裁剪策略"相关描述
    - _需求: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [x] 1.3 编写属性测试：get_messages 返回完整 Message 列表
    - **Property 2: get_messages 返回完整的 Message 列表**
    - 生成随机消息序列（混合 system/user/assistant/tool 角色），验证 `get_messages()` 返回 `list[Message]`，长度等于添加的消息总数
    - 测试文件：`test/domain/chat/test_compaction_properties.py`
    - **验证: 需求 2.1, 2.3**

  - [x] 1.4 编写属性测试：ConversationContext 序列化往返一致性
    - **Property 3: ConversationContext 序列化往返一致性**
    - 生成随机 ConversationContext，验证 `to_dict()` → `from_dict()` 往返一致性，且 `to_dict()` 输出不包含 `max_messages` 键；同时验证包含 `max_messages` 的旧格式字典也能正常 `from_dict()`
    - 测试文件：`test/domain/chat/test_compaction_properties.py`
    - **验证: 需求 2.4, 2.5, 2.6**

- [x] 2. 实现 SlidingWindowCompactionAdapter 和 ChatConfig 变更
  - [x] 2.1 在 `infrastructure/chat/chat_config.py` 的 `ChatConfig` 中新增 `max_messages` 字段
    - 添加 `max_messages: int = 50`，对应环境变量 `CHAT_MAX_MESSAGES`
    - _需求: 5.3, 5.4_

  - [x] 2.2 创建 `infrastructure/chat/sliding_window_compaction_adapter.py`
    - 实现 `SlidingWindowCompactionAdapter` 类
    - 构造函数接收 `max_messages: int`，校验 ≤ 0 时抛出 `ValueError`
    - `compact()` 方法：保留所有 system 消息 + 最近 `max_messages` 条非 system 消息，system 在前、非 system 在后
    - 空列表输入返回空列表
    - 添加中文模块级 docstring 和类/方法 docstring
    - _需求: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 1.4_

  - [x] 2.3 编写属性测试：compact 输出是输入的子集
    - **Property 1: compact 输出是输入的子集**
    - 生成随机 Message 列表和 SlidingWindowCompactionAdapter，验证输出中每个 Message 在输入中存在，且输出长度 ≤ 输入长度
    - 测试文件：`test/domain/chat/test_compaction_properties.py`
    - **验证: 需求 1.3**

  - [x] 2.4 编写属性测试：滑动窗口压缩行为
    - **Property 4: 滑动窗口压缩保留所有 system 消息并裁剪非 system 消息**
    - 生成随机 Message 列表 + 随机正整数 max_messages，验证：(a) 所有 system 消息保留；(b) 非 system 消息数 ≤ max_messages；(c) 超出时保留最后 N 条；(d) system 在前、非 system 在后
    - 测试文件：`test/domain/chat/test_compaction_properties.py`
    - **验证: 需求 3.3, 3.4, 3.5**

  - [x] 2.5 编写属性测试：Message 序列化为模型调用格式
    - **Property 5: Message 序列化为模型调用格式**
    - 生成随机 Message 对象，验证序列化为 `{"role": ..., "content": ...}` 格式，恰好包含两个键
    - 测试文件：`test/domain/chat/test_compaction_properties.py`
    - **验证: 需求 6.1, 6.2, 6.3**

- [x] 3. Checkpoint - 确保领域层和适配器测试通过
  - 确保所有测试通过，ask the user if questions arise.

- [x] 4. 集成 ChatServiceAdapter 和 DI 容器
  - [x] 4.1 重构 `infrastructure/chat/chat_service_adapter.py`
    - 构造函数新增 `compaction: ContextCompactionPort` 参数
    - 新增 `_serialize_messages(messages: list[Message]) -> list[dict[str, str]]` 静态方法
    - 修改 `_ensure_system_prompt()`：适配 `get_messages()` 返回 `list[Message]`，通过 `Message.role` 属性判断
    - 修改 `chat()` 方法：调用 `context.get_messages()` 获取完整列表 → `compaction.compact()` 压缩 → `_serialize_messages()` 序列化 → 传给 `ChatRequest`
    - 修改 `stream_chat()` 方法：同上压缩和序列化流程
    - 确保保存到 `SessionContextStorePort` 的是完整上下文（未压缩）
    - _需求: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 6.1, 6.2_

  - [x] 4.2 修改 `application/container_config.py` 注册压缩策略
    - 导入 `ContextCompactionPort` 和 `SlidingWindowCompactionAdapter`
    - 新增 `_create_compaction_adapter()` 工厂函数，从 `chat_config` 读取 `max_messages` 创建适配器
    - 在 `configure_container()` 中注册 `ContextCompactionPort → SlidingWindowCompactionAdapter` 绑定
    - 修改 `_create_chat_service()` 从容器解析 `ContextCompactionPort` 并注入 `ChatServiceAdapter`
    - _需求: 5.1, 5.2, 5.3, 5.4_

  - [x] 4.3 编写属性测试：重构后行为等价性
    - **Property 6: 重构后行为等价性**
    - 生成随机 Message 列表 + 随机正整数 max_messages，验证新流程（compact → serialize）与旧流程（重构前 `get_messages()`）输出完全一致
    - 测试文件：`test/domain/chat/test_compaction_properties.py`
    - **验证: 需求 7.1**

  - [x] 4.4 编写单元测试：边界条件和集成验证
    - 测试文件：`test/domain/chat/test_compaction_unit.py`
    - 边界条件：compact 空列表返回空列表（需求 1.4）；仅 system 消息原样返回（需求 3.6）；非 system 为 0 时仅返回 system（需求 7.3）；tool 消息视为非 system 参与裁剪（需求 7.4）
    - 配置测试：默认 max_messages 为 50（需求 5.3）；自定义值生效（需求 5.4）；max_messages ≤ 0 抛出 ValueError
    - 集成测试（mock）：ChatServiceAdapter.chat() 调用 compact 后传压缩结果给 ModelAccessPort（需求 4.2, 4.4）；stream_chat() 同理（需求 4.3, 4.4）；保存完整历史到 SessionContextStorePort（需求 4.5）
    - _需求: 1.4, 3.6, 4.2, 4.3, 4.4, 4.5, 5.3, 5.4, 7.3, 7.4_

- [x] 5. Final checkpoint - 确保所有测试通过
  - 确保所有测试通过，ask the user if questions arise.

## 备注

- 标记 `*` 的任务为可选，可跳过以加速 MVP 交付
- 每个任务引用了具体的需求编号，确保可追溯性
- 属性测试验证通用正确性属性，单元测试验证具体示例和边界情况
- 测试运行命令：`cd epsilon-boot && uv run pytest test/domain/chat/test_compaction_properties.py test/domain/chat/test_compaction_unit.py -v`
- Checkpoint 任务确保增量验证
