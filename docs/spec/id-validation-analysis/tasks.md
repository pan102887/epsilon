# 实现计划：ToolCallRequest id 校验失败链路分析与加固

## 概述

本计划把 `design.md` 落地为可执行任务清单。按 design 「修改文件清单」拆分为 8 个分组（A–H）：

- **Group A**：在 `domain/` 层新增两类领域异常（`InvalidToolCallIdError` / `InvalidApprovalActionError`）。
- **Group B**：审批前置校验（`PendingActionRequest` / `ApprovalDecision` 的 `__post_init__` 落地 D5）。
- **Group C**：同步 chat 链路加固（OpenAI 兼容适配器 `chat()` 的 tool_calls 解析点）。
- **Group D**：流式 finished 双侧修复（`_materialize_full_tool_calls` 上游归一化 + `_RoundStreamAccumulator.consume` 下游违约判定）。
- **Group E**：历史会话恢复（`BaseMessage.from_dict` 过滤 + WARN 日志，落地 D4）。
- **Group F**：配置基础设施 + 配置项落地（新增 `common/configuration/id_validation_config.py` settings 类与 `config.properties` 历史会话恢复策略开关，落地 D8）。
- **Group G**：测试覆盖（按 design §测试矩阵 19 条用例 + 回归保护用例 + `IdValidationConfig` 加载行为单测）。
- **Group H**：检查点（编译 / 静态检查 / 全量回归）。

每条 leaf 任务编辑 ≤ 1 个生产文件或 ≤ 1 个测试文件，验证任务紧跟该组实现任务后置。所有任务严格按依赖拓扑排序：异常类型先于使用方，配置开关先于读取它的模块，src 改造先于对应测试。

## Tasks

- [x] 1. Group A — 领域异常类型新增
  - [x] 1.1 在 `epsilon-boot/src/domain/model_access/exceptions.py` 末尾新增 `InvalidToolCallIdError` 类
    - 创建/修改 `/workspace/epsilon-boot/src/domain/model_access/exceptions.py`
    - 类继承自 `ModelAccessError`；错误码 `50007`
    - `__init__` 签名：`(self, source: str, raw_id_value: object, *, provider: str | None = None, model: str | None = None, tool_name: str | None = None, tool_call_index: int | None = None, extra: dict[str, Any] | None = None)`
    - `message` 模板：`f"工具调用 id 不合法（source={source}, raw_id_value={raw_id_value!r}）"`
    - `details` 必含键：`source / provider / model / tool_name / tool_call_index / raw_id_value`；`extra` 非空时 `details.update(extra)`
    - 类与 `__init__` 补中文 docstring，明确"统一诊断字段集 + 不省略键 + 不入 message 敏感字段"约束
    - 验收：`isinstance(InvalidToolCallIdError(...), ModelAccessError)` 为 `True`，`exc.code == 50007`，`set(exc.details.keys()) >= {"source","provider","model","tool_name","tool_call_index","raw_id_value"}`
    - _需求：R1.1 / R1.2 / R5.1 / R5.3 / R5.4 / R6.1 / R6.3_

  - [x] 1.2 在 `epsilon-boot/src/domain/agent/exceptions.py` 末尾新增 `InvalidApprovalActionError` 类
    - 创建/修改 `/workspace/epsilon-boot/src/domain/agent/exceptions.py`
    - 类继承自 `BizException`（与现有 `ApprovalNotFoundError` 等同段位）；错误码 `60040`
    - `__init__` 签名：`(self, value_object: str, field: str, raw_value: object, *, tool_name: str | None = None)`
    - `message` 模板：`f"{value_object}.{field} 不能为空（raw_value={raw_value!r}）"`
    - 实例属性：`value_object` / `field` / `raw_value` 暴露便于断言；`details` 含 `source="approval_resume" / provider=None / model=None / tool_name / tool_call_index=None / raw_id_value=raw_value / value_object / field`
    - 类与 `__init__` 补中文 docstring，明确"归属 agent 子域，不与 InvalidToolCallIdError 共享继承"
    - 验收：`isinstance(exc, BizException)` 为 `True`，`isinstance(exc, ModelAccessError)` 为 `False`，`exc.code == 60040`
    - _需求：R4.4 / R5.1 / R5.3 / R6.1 / R6.3_

  - [x] 1.3 [Validation] 为 Group A 新增异常类编写单元测试
    - 创建 `/workspace/epsilon-boot/test/domain/model_access/test_invalid_tool_call_id_error_unit.py`
    - 用例覆盖：默认字段填 `None`、`extra` 合并、`code == 50007`、message 含 `source` 与 `raw_id_value`、可被 `isinstance(exc, ModelAccessError)` 命中
    - _需求：R1.2 / R5.1 / R6.1_

  - [x] 1.4 [Validation] 为 `InvalidApprovalActionError` 编写单元测试
    - 创建 `/workspace/epsilon-boot/test/domain/agent/test_invalid_approval_action_error_unit.py`
    - 用例覆盖：`code == 60040`、`details["source"] == "approval_resume"`、`value_object` / `field` / `raw_value` 属性可读、`isinstance(exc, BizException) and not isinstance(exc, ModelAccessError)`
    - _需求：R4.4 / R5.3 / R6.1_

- [x] 2. Group B — 审批值对象前置校验
  - [x] 2.1 修改 `PendingActionRequest.__post_init__` 校验 `tool_call_id` 非空
    - 创建/修改 `/workspace/epsilon-boot/src/domain/agent/value_objects.py`
    - 在 `PendingActionRequest`（位于 `value_objects.py:170` 附近）追加 `__post_init__`：当 `not self.tool_call_id` 时抛 `InvalidApprovalActionError(value_object="PendingActionRequest", field="tool_call_id", raw_value=self.tool_call_id, tool_name=self.tool_name or None)`
    - 文件顶部按需新增导入：`from domain.agent.exceptions import InvalidApprovalActionError`
    - 类 docstring 追加 `Raises:` 段，与 design 模板一致
    - 不修改字段集与 `frozen=True` 约束
    - _需求：R4.1 / R4.4 / R6.1 / R6.3 / R6.6_

  - [x] 2.2 修改 `ApprovalDecision.__post_init__` 校验 `tool_call_id` 非空
    - 创建/修改 `/workspace/epsilon-boot/src/domain/agent/value_objects.py`
    - 在 `ApprovalDecision`（位于 `value_objects.py:202` 附近）追加 `__post_init__`：当 `not self.tool_call_id` 时抛 `InvalidApprovalActionError(value_object="ApprovalDecision", field="tool_call_id", raw_value=self.tool_call_id)`
    - 类 docstring 追加 `Raises:` 段
    - 不修改字段集与 `frozen=True` 约束
    - _需求：R4.2 / R4.4 / R6.1 / R6.3 / R6.6_

  - [x] 2.3 [Validation] 编写审批值对象前置校验单元测试
    - 创建 `/workspace/epsilon-boot/test/domain/agent/test_approval_value_objects_id_validation_unit.py`
    - 用例对应 design 测试矩阵 T12 / T13 / T14 / T15
      - T12：`PendingActionRequest(tool_call_id=None, ...)` 抛 `InvalidApprovalActionError`，断言 `exc.value_object == "PendingActionRequest"` 与 `exc.field == "tool_call_id"`
      - T13：`PendingActionRequest(tool_call_id="", ...)` 同上，`exc.raw_value == ""`
      - T14：`ApprovalDecision(type="approve", tool_call_id="")` 抛 `InvalidApprovalActionError(value_object="ApprovalDecision", ...)`
      - T15：合法 `tool_call_id="call_xxx"` 正常构造（回归保护）
    - _需求：R4.1 / R4.2 / R4.4_

  - [x] 2.4 [Validation] 编写审批恢复路径集成回归测试
    - 创建 `/workspace/epsilon-boot/test/application/test_approval_resume_id_validation_integration.py`
    - 用例对应 design 测试矩阵 T16：`ApprovalResume(decisions=(ApprovalDecision(tool_call_id="", ...),))` 在 `ApprovalDecision` 构造时即抛，错误不延迟到 `react_agent_adapter.py` 的 `ToolCallRequest(...)` 构造点
    - 注：design §修改文件清单未列 `react_agent_adapter.py`，本测试仅做"前置校验拦截在 application 入口"的黑盒断言
    - _需求：R4.3_

- [x] 3. Group C — 同步对话链路加固
  - [x] 3.1 修改 `OpenAICompatibleAdapter.chat()` 在构造 `ToolCallRequest` 前做 id 校验
    - 创建/修改 `/workspace/epsilon-boot/src/infrastructure/model_access/openai_compatible_adapter.py`
    - 改造点位于 `chat()`（`openai_compatible_adapter.py:131` 附近）的 tool_calls 解析循环
    - 解析每个 `tc` 时取出 `tc_id = getattr(tc, "id", None)` / `tc_name = getattr(tc.function, "name", None) if tc.function else None` / `tc_index = getattr(tc, "index", None)`
    - 当 `not tc_id` 时（`None` 或空串同等）：先 `logger.warning("OpenAI 兼容 Provider 返回的 tool_call.id 不合法，将抛出 InvalidToolCallIdError", extra=details)`；再 `raise InvalidToolCallIdError(source="chat_sync", raw_id_value=tc_id, provider=self._config.provider_name, model=completion.model, tool_name=tc_name, tool_call_index=tc_index)`
    - `details` dict 字段集与异常 `details` 完全一致
    - 文件顶部导入新增 `InvalidToolCallIdError`（合入既有 `from domain.model_access.exceptions import (...)` 块）
    - 不修改 `name` / `arguments` 现有非空兜底语义（design §同步链路改造说明）
    - _需求：R1.1 / R1.2 / R1.3 / R1.4 / R5.1 / R5.2 / R6.1_

  - [x] 3.2 [Validation] 编写同步 chat 链路 id 校验单元测试
    - 创建 `/workspace/epsilon-boot/test/infrastructure/model_access/test_openai_compatible_chat_id_validation_unit.py`
    - 用例对应 design 测试矩阵 T1 / T2 / T3：
      - T1：mock SDK 返回 `tool_calls[0].id=None`，`adapter.chat()` 抛 `InvalidToolCallIdError(source="chat_sync", raw_id_value=None, ...)`
      - T2：mock `tool_calls[0].id=""`，同上 `raw_id_value == ""`
      - T3：mock `tool_calls[0].id="call_xxx"`（合法），返回 `LLMResponse.tool_calls` 长度为 1，无 WARN 日志（回归保护）
    - 用 pytest `caplog` 断言 WARN 日志 `record.extra` 字段集包含 `source / provider / model / tool_name / tool_call_index / raw_id_value`
    - _需求：R1.1 / R1.2 / R1.3 / R1.4_

- [x] 4. Group D — 流式 finished 分片双侧修复
  - [x] 4.1 修改 `_materialize_full_tool_calls` 把空字符串归一化为 `None`
    - 创建/修改 `/workspace/epsilon-boot/src/infrastructure/model_access/openai_compatible_adapter.py`
    - 改造点位于 `_materialize_full_tool_calls`（`openai_compatible_adapter.py:327` 附近）
    - 把 `id=slot.get("id")` 改为 `id=slot.get("id") or None`；`name=slot.get("name")` 改为 `name=slot.get("name") or None`；把 `arguments_delta=slot.get("arguments") or ""` 改为 `arguments_delta=slot.get("arguments") or None`
    - 在方法 docstring 追加：「空字符串归一化为 `None`，让下游 `_RoundStreamAccumulator` 的违约判定一次到位（D3）」
    - _需求：R2.4 / R6.1_

  - [x] 4.2 修改 `_RoundStreamAccumulator.consume` finished 分支违约判定从 `is None` 扩展为「`None` 或空串」
    - 创建/修改 `/workspace/epsilon-boot/src/infrastructure/agent/round_stream_accumulator.py`
    - 改造点位于 `consume()`（`round_stream_accumulator.py:97-120` 附近）的 finished 分支
    - 文件顶部新增 `import logging` 与 `logger = logging.getLogger(__name__)`
    - 把 `if delta.id is None or delta.name is None or delta.arguments_delta is None` 改为 `if not delta.id or not delta.name or not delta.arguments_delta`
    - 命中违约时调用 `logger.warning("流式 finished 分片违约，回退到增量累积结果", extra={...})`，`extra` 字段：`source="stream_finished" / provider=None / model=self._model / tool_name=delta.name or None / tool_call_index=delta.index / raw_id_value=delta.id / violation_field=("id" if not delta.id else "name" if not delta.name else "arguments_delta")`
    - 增量分支（`else` 分支，`round_stream_accumulator.py:121-132` 附近）保持 `is not None` 判定不变（R2.3）
    - 类 docstring 追加：finished 违约判定从严说明
    - _需求：R2.1 / R2.2 / R2.3 / R5.1 / R5.2 / R6.1_

  - [x] 4.3 [Validation] 编写流式 finished 违约回退单元测试
    - 创建 `/workspace/epsilon-boot/test/infrastructure/agent/test_round_stream_accumulator_finished_violation_unit.py`
    - 用例对应 design 测试矩阵 T4 / T5 / T6 / T7：
      - T4：finished 分片 `delta.id=None`，`build_response().tool_calls` 与"增量累积三字段缺一即跳过"语义一致；`caplog` 断言 WARN extra 含 `violation_field="id"`
      - T5：finished 分片 `delta.id=""`，同 T4
      - T6：先发增量 `delta.id=""`（不报错，被累积进 slot），再发 finished 违约分片 → 回退至增量结果，但增量 id 仍是 `""` → `build_response().tool_calls` 为空列表（与"三字段缺一跳过"对齐）
      - T7：合法 finished（三字段全有）→ 优先取 finished 完整列表覆盖增量（回归保护）
    - 复用现有 `test/infrastructure/agent/_v3_stream_helpers.py` 工厂构造 `StreamingChunk` 序列
    - _需求：R2.1 / R2.2 / R2.3_

  - [x] 4.4 [Validation] 编写 `_materialize_full_tool_calls` 空串归一化单元测试
    - 创建 `/workspace/epsilon-boot/test/infrastructure/model_access/test_openai_compatible_materialize_normalize_unit.py`
    - 用例覆盖：
      - 累积态 `slot["id"]=""` → 产出的 `StreamingToolCallDelta.id is None`
      - 累积态 `slot["arguments"]=""` → 产出的 `arguments_delta is None`（与既有 `or ""` 行为相反，需更新断言）
      - 累积态合法（`id="x", name="y", arguments="{}"`）→ 三字段非 `None`（回归保护）
      - 空 `acc` → 返回 `None`（既有行为不变）
    - _需求：R2.4_

- [x] 5. Group E — 历史会话恢复兼容策略
  - [x] 5.1 修改 `BaseMessage.from_dict` 在 `role=assistant` 分支增加「过滤 + WARN 日志」逻辑
    - 创建/修改 `/workspace/epsilon-boot/src/domain/chat/context.py`
    - 改造点位于 `BaseMessage.from_dict`（`context.py:74-110` 附近）的 `elif role == "assistant"` 分支
    - 文件顶部新增 `import logging` 与 `logger = logging.getLogger(__name__)`；新增 `from domain.model_access.exceptions import InvalidToolCallIdError`；新增 `from common.configuration.id_validation_config import id_validation_config`（**禁止** `import os` 或自构 `_read_from_config_properties` 等绕过 settings 框架的实现，理由：避免 `domain/` 直接 import `pydantic_settings`，统一走 `common/configuration/` 已封装的 settings 加载链路 — 见 design §`BaseMessage.from_dict` 改造）
    - 模块级常量 `_HISTORY_RESTORE_STRATEGY = _load_history_restore_strategy()`，`_load_history_restore_strategy()` 直接读 `id_validation_config.history_restore_strategy`，对取值合法性做校验：仅允许 `"filter"` / `"raise"`，非法或缺失统一回退 `"filter"`（实现示例：`raw = id_validation_config.history_restore_strategy; return raw if raw in ("filter", "raise") else "filter"`）
    - 改造逻辑：遍历 `raw_tool_calls`，把 `not tc_id` 的项收集到 `skipped: list[dict]`，合法项构造 `ToolCallRequest`；`skipped` 非空时按 `_HISTORY_RESTORE_STRATEGY` 分支：
      - `"filter"` → `logger.warning("历史会话恢复发现 %d 项 tool_call 违约，已过滤", len(skipped), extra=details)`，继续返回 `AssistantMessage(tool_calls=过滤后)`
      - `"raise"` → `logger.warning(...)` 后抛 `InvalidToolCallIdError(source="history_restore", raw_id_value=skipped[0]["raw_id_value"], tool_name=skipped[0]["name"], tool_call_index=skipped[0]["index"], extra={"skipped_count": len(skipped), "session_id": ...})`
    - `details` 字段集与异常对齐：`source="history_restore" / provider=None / model=None / tool_name=skipped[0]["name"] / tool_call_index=skipped[0]["index"] / raw_id_value=skipped[0]["raw_id_value"] / skipped_count=len(skipped) / session_id=data.get("metadata", {}).get("session_id")`
    - 不修改 system / user / tool 三个 role 分支
    - 不修改 `to_dict` 输出格式（D3 边界）
    - _需求：R3.1 / R3.2 / R3.3 / R3.4 / R3.5 / R5.1 / R5.2 / R6.1 / R6.2 / R6.3_

  - [x] 5.2 [Validation] 编写历史会话恢复过滤策略单元测试
    - 创建 `/workspace/epsilon-boot/test/domain/chat/test_base_message_from_dict_id_validation_unit.py`
    - 用例对应 design 测试矩阵 T8 / T9 / T11：
      - T8：`tool_calls=[{"id":"","name":"x","arguments":"{}"}]` 默认 filter → `AssistantMessage.tool_calls == []`，`caplog` 含 `skipped_count=1`
      - T9：`tool_calls=[{"id":None,"name":"x","arguments":"{}"}, {"id":"ok","name":"y","arguments":"{}"}]` filter → 保留第 2 项
      - T11：合法历史快照（所有 id 非空） → 反序列化结果与现有完全一致（回归保护）
    - 用 `monkeypatch` 把模块级 `_HISTORY_RESTORE_STRATEGY` 强制为 `"filter"`，避免依赖配置文件
    - _需求：R3.1 / R3.2 / R3.4 / R3.5_

  - [x] 5.3 [Validation] 编写历史会话恢复 raise 策略单元测试
    - 创建 `/workspace/epsilon-boot/test/domain/chat/test_base_message_from_dict_raise_strategy_unit.py`
    - 用例对应 design 测试矩阵 T10：`monkeypatch` 把 `_HISTORY_RESTORE_STRATEGY` 设为 `"raise"`，输入与 T8 相同 → 抛 `InvalidToolCallIdError(source="history_restore", ...)`，`exc.details["skipped_count"] == 1`
    - _需求：R3.1 / R3.3_

- [x] 6. Group F — 配置基础设施 + 配置项落地
  - [x] 6.1 新增 `common/configuration/id_validation_config.py`，定义 `IdValidationConfig(PropertiesBaseSettings)` 与 `id_validation_config` 单例
    - 创建 `/workspace/epsilon-boot/src/common/configuration/id_validation_config.py`
    - 模块级 docstring 写明：「ID 校验相关运行期配置模块。承载历史会话恢复策略等 ID 校验链路的可调开关。所有配置项遵循 `config.properties` 的 `UPPER_SNAKE_CASE` 命名约定，前缀 `ID_VALIDATION_`」
    - 顶部导入：`from pydantic_settings import SettingsConfigDict` / `from common.configuration import PropertiesBaseSettings, create_config`
    - 类定义：`class IdValidationConfig(PropertiesBaseSettings):`，含中文 docstring 描述 `history_restore_strategy` 字段语义
    - 类内部：`model_config = SettingsConfigDict(env_prefix="ID_VALIDATION_")` 与字段 `history_restore_strategy: str = "filter"`
    - 模块末尾：`id_validation_config = create_config(IdValidationConfig)`，附中文 docstring 「全局 ID 校验配置实例，通过工厂函数创建（支持热更新）」
    - 落点理由：避免 `domain/` 直接 import `pydantic_settings`，`common/configuration/` 是仓库已确立的 settings 框架承载层（见 design §`BaseMessage.from_dict` 改造）
    - 验收：
      - `IdValidationConfig.model_config.get("env_prefix") == "ID_VALIDATION_"`
      - `IdValidationConfig().history_restore_strategy == "filter"`（默认值）
      - `id_validation_config` 由 `create_config(IdValidationConfig)` 返回，类型为既有 `ConfigProxy`（与 `chat_config` 等单例一致）
    - _需求：R3.1 / R6.1 / R6.4_

  - [x] 6.2 在 `epsilon-boot/config.properties` 新增历史会话恢复策略开关
    - 创建/修改 `/workspace/epsilon-boot/config.properties`
    - 在 「聊天服务 / Agent Loop 配置」段或文件末尾新增独立小段「ID 校验配置」，按设计原文写：
      ```properties
      # -------------------------------------------
      # ID 校验配置
      # -------------------------------------------
      # 历史会话恢复时遇到 tool_call.id 缺失/空时的兼容策略
      # - filter（默认）：过滤违约项，保留剩余合法 tool_calls，并通过 WARN 日志暴露脏数据
      # - raise：抛 InvalidToolCallIdError，由 application 层降级（仅在脏数据预期为 0 时启用）
      ID_VALIDATION_HISTORY_RESTORE_STRATEGY=filter
      ```
    - 键名遵循 `config.properties` 既有 `UPPER_SNAKE_CASE` 命名风格（与 `CHAT_MAX_TOOL_ROUNDS` / `HITL_ENABLED` 等并列），由 Task 6.1 的 `IdValidationConfig` 通过 `env_prefix="ID_VALIDATION_"` 自动匹配字段 `history_restore_strategy`
    - _需求：R3.1 / R6.4_

- [x] 7. Group G — 测试矩阵补齐与 schema/日志对齐
  - [x] 7.1 [Validation] 编写异常 `details` schema 对齐用例
    - 创建 `/workspace/epsilon-boot/test/domain/model_access/test_id_validation_details_schema_unit.py`
    - 用例对应 design 测试矩阵 T17：分别构造 `InvalidToolCallIdError(source="chat_sync", ...)` / `InvalidToolCallIdError(source="stream_finished", ...)` / `InvalidToolCallIdError(source="history_restore", ...)` / `InvalidApprovalActionError(...)`
    - 断言每个异常 `set(exc.details.keys()) >= {"source","provider","model","tool_name","tool_call_index","raw_id_value"}`，且不适用字段值为 `None`（**键存在**）
    - _需求：R5.1 / R5.4_

  - [x] 7.2 [Validation] 编写 `isinstance` 互斥用例
    - 创建 `/workspace/epsilon-boot/test/domain/agent/test_id_validation_isinstance_disjoint_unit.py`
    - 用例对应 design 测试矩阵 T18：
      - `InvalidToolCallIdError(...)` 实例：`isinstance(exc, ModelAccessError)` 与 `isinstance(exc, InvalidToolCallIdError)` 均为 `True`，`isinstance(exc, InvalidApprovalActionError)` 为 `False`
      - `InvalidApprovalActionError(...)` 实例：`isinstance(exc, BizException)` 与 `isinstance(exc, InvalidApprovalActionError)` 均为 `True`，`isinstance(exc, ModelAccessError)` 与 `isinstance(exc, InvalidToolCallIdError)` 均为 `False`
    - _需求：R5.3 / R6.1_

  - [x] 7.3 [Validation] 编写日志 extra 与异常 details 对齐用例
    - 创建 `/workspace/epsilon-boot/test/infrastructure/test_id_validation_log_extra_alignment_unit.py`
    - 用例对应 design 测试矩阵 T19：分别触发 chat_sync（T1）、stream_finished（T4）、history_restore（T8）三条链路，使用 `caplog`：
      - 抓取 WARN 记录的 `record.__dict__`（含 extra 字段）
      - 断言 extra 中的统一字段（`source / provider / model / tool_name / tool_call_index / raw_id_value`）与对应抛出的 / 跳过的异常 `details` 同名键值一致（chat_sync 为对应抛出的异常；stream_finished / history_restore 因不抛异常，与 design §统一诊断字段集 表对齐）
    - _需求：R1.3 / R2.2 / R3.4 / R5.2_

  - [x] 7.4 [Validation] 编写 `IdValidationConfig` 加载行为单元测试
    - 创建 `/workspace/epsilon-boot/test/common/configuration/test_id_validation_config_unit.py`
    - 用例覆盖 4 个 case：
      - **默认值**：清空相关 env 与 properties 覆盖后构造 `IdValidationConfig()`，断言 `history_restore_strategy == "filter"`
      - **环境变量覆盖**：`monkeypatch.setenv("ID_VALIDATION_HISTORY_RESTORE_STRATEGY", "raise")` 后重新加载 config，断言 `id_validation_config.history_restore_strategy == "raise"`
      - **properties 覆盖**：通过临时 `config.properties`（或 mock `PropertiesBaseSettings` 加载源）写入 `ID_VALIDATION_HISTORY_RESTORE_STRATEGY=raise`，断言生效
      - **非法值回退**：`monkeypatch.setenv("ID_VALIDATION_HISTORY_RESTORE_STRATEGY", "invalid_strategy")` 后调用 `domain.chat.context._load_history_restore_strategy()`，断言返回 `"filter"`（D8 / design §`BaseMessage.from_dict` 改造的非法值兜底契约）
    - 注：env 覆盖用例使用单下划线 `ID_VALIDATION_HISTORY_RESTORE_STRATEGY`（**不是**双下划线 `ID_VALIDATION__HISTORY_RESTORE__STRATEGY`），因为 `pydantic_settings` 默认 `env_nested_delimiter` 未启用，`env_prefix="ID_VALIDATION_"` 直接拼接字段名 `history_restore_strategy` 即可命中
    - _需求：R3.1 / R6.4_

- [x] 8. Group H — 检查点
  - [x] 8.1 [Checkpoint] 全量运行 Group A–G 新增/修改的单元测试
    - 在 `epsilon-boot/` 目录执行：`uv run pytest test/common/configuration test/domain/model_access test/domain/agent test/domain/chat test/infrastructure/model_access test/infrastructure/agent test/application -x`
    - 全部 PASS 才视为通过；任一 FAIL 退回到对应 Group 修复
    - _需求：R6.5（uv 包管理 + 测试用 `uv run`）_

  - [x] 8.2 [Checkpoint] 静态检查 — 验证 `domain/` 不引入 `infrastructure/` 反向依赖
    - 在 `epsilon-boot/` 目录执行 `uv run python -c "import domain.chat.context; import domain.agent.value_objects; import domain.model_access.exceptions; import domain.agent.exceptions"` 验证导入成功
    - 在 `epsilon-boot/` 目录执行 grep 校验：`grep -rE "from infrastructure\." src/domain/` 必须**无输出**
    - 复跑既有 `test/domain/chat/test_context_builder_import_boundaries.py`（若覆盖 context.py 导入边界）
    - _需求：R6.1 / R6.2_

  - [x] 8.3 [Checkpoint] 全量回归测试套件
    - 在 `epsilon-boot/` 目录执行：`uv run pytest test/ -x`
    - 全部 PASS 才视为本计划完成
    - _需求：R6.5（不引入回归）_

## 备注

### Implementation Readiness（实施前需确认的信息）

**无 OPEN QUESTION**：design.md §D8 / §配置开关 / §`BaseMessage.from_dict` 改造已锁定如下决策，所有命名风格冲突均已闭合：

- **配置键名**：`ID_VALIDATION_HISTORY_RESTORE_STRATEGY`（UPPER_SNAKE_CASE，与 `CHAT_MAX_TOOL_ROUNDS` / `HITL_ENABLED` 等并列）。
- **环境变量名**：`ID_VALIDATION_HISTORY_RESTORE_STRATEGY`（**单**下划线，由 `env_prefix="ID_VALIDATION_"` 与字段名 `history_restore_strategy` 直接拼接得到，不使用 `env_nested_delimiter` 双下划线写法）。
- **设计文件落点**：新增 `common/configuration/id_validation_config.py`（`IdValidationConfig(PropertiesBaseSettings)` + `id_validation_config = create_config(IdValidationConfig)` 单例）。
- **`domain/chat/context.py` 改造**：移除 `import os` / 自构 `_read_from_config_properties`，改为 `from common.configuration.id_validation_config import id_validation_config`，`_load_history_restore_strategy()` 直接读 `id_validation_config.history_restore_strategy` 并对取值合法性做兜底（仅允许 `"filter"` / `"raise"`，否则回退 `"filter"`）。
- **DDD 合规理由**：避免 `domain/` 直接 import `pydantic_settings`，`common/configuration/` 是仓库已确立的 settings 框架承载层。

**已锁定信息**（无需追问）：

- D1–D8 全部决策已由 `design.md` 锁定，本计划不再追问。
- 测试目录使用 `epsilon-boot/test/`（仓库实际为单数 `test/`，design.md 文档片段误写 `tests/`，本计划已矫正为单数）。
- `ApprovalResume` 值对象（`domain/chat/value_objects.py`）的字段名是 `decisions`（design.md 局部段落写作 `ApprovalResumeRequestVO`，本计划测试任务 2.4 直接以 `ApprovalResume` 为准）。
- 错误码 `50007` / `60040` 可用，与既有错误码段位无冲突（`50001-50006` / `60020-60030` 已对照）。
- 仓库测试框架为 `pytest`，命名风格 `test_*_unit.py` / `test_*_property.py`，本计划遵循。

### Out of Scope（不在本次实施范围）

- 不强化 `ToolCallRequest.name` / `ToolCallRequest.arguments` 的非空校验语义；两者的同质问题（design §同步链路改造说明的「二阶段加固空间」）在此计划之外（需求 1 范围明确不含）。
- 不重写 `ToolCallRequest` 数据结构，保持 `@dataclass(frozen=True)` 与三字段必填语义不变（R6.6）。
- 不修改 `epsilon-client/` 任何前端代码（需求范围 Out-of-Scope 第 1 项）。
- 不引入新的 LLM Provider、不改 Provider 鉴权与路由逻辑、不引入新可观测性后端（需求范围 Out-of-Scope 第 4–5 项）。
- 不扩展校验到其他值对象（如 `ChatRequest`、`StreamingChunk`）。
- **不**增量改造 `application/api/exception_handlers.py`：design §日志规范"审批侧抛出方约定"虽提到此处可承担"统一把 details 写入 extra 日志"职责，但 design §修改文件清单未列入此文件；本计划严格按修改文件清单执行，不在此扩展。如需该集中点改造，应回 `spec_designer` 补入修改清单后单独立项。
- 不在 `react_agent_adapter.py` 第 1154 / 1187 行处新增防御性校验（design §影响域明确"前置校验生效后此处永远不会再收到空 tool_call_id"）。
- 不引入新依赖；本计划无 `uv add` / `uv remove` 操作。

### Verification Commands（最终验证命令）

所有命令必须在 `epsilon-boot/` 目录下执行（`pyproject.toml` 所在目录），严格遵守 `docs/steering/uv-package-manager.md`：

```bash
cd /workspace/epsilon-boot

# 1. 单元测试 — Group A–G 新增/修改的全部测试
uv run pytest test/common/configuration test/domain/model_access test/domain/agent test/domain/chat \
              test/infrastructure/model_access test/infrastructure/agent \
              test/application \
              -x -q

# 2. 静态检查 — domain/ 不依赖 infrastructure/
grep -rE "from infrastructure\." src/domain/ && echo "FAIL: domain 反向依赖 infrastructure" || echo "OK"

# 3. 导入烟测 — 新增异常类、配置类与改造模块均可正常导入
uv run python -c "
from common.configuration.id_validation_config import IdValidationConfig, id_validation_config
from domain.model_access.exceptions import InvalidToolCallIdError
from domain.agent.exceptions import InvalidApprovalActionError
from domain.agent.value_objects import PendingActionRequest, ApprovalDecision
from domain.chat.context import BaseMessage
from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter
from infrastructure.agent.round_stream_accumulator import _RoundStreamAccumulator
assert id_validation_config.history_restore_strategy in ('filter', 'raise')
print('imports OK')
"

# 4. 全量回归（最终把关）
uv run pytest test/ -x -q
```

约定：`uv run pytest` 全部 PASS + 静态检查 `OK` + 导入烟测打印 `imports OK`，本计划方视为完成。
