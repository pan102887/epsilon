# 实现计划：Structured Agent Trace — 结构化 Agent 追踪

## 概述

按底层到顶层顺序编排：值对象 → 端口 → 配置 → adapter → Agent 集成 → DI 装配 → 测试 → 全量回归。

所有文件路径相对于 `epsilon-boot/`；命令在该目录下执行。

## Tasks

- [x] 1. 领域层 Trace 值对象
  - [x] 1.1 创建 `src/domain/agent/trace_value_objects.py`
    - 定义模块级常量 `ARGUMENTS_SUMMARY_MAX_LEN = 128`、`RESULT_SUMMARY_MAX_LEN = 256`、`ERROR_MESSAGE_MAX_LEN = 512`
    - 定义 `ModelCallTrace` frozen dataclass，`kind: Literal["model_call"]` 为 `field(default="model_call", init=False)`
    - 定义 `ToolCallTrace` frozen dataclass，`kind: Literal["tool_call"]`
    - 定义 `ApprovalTrace` frozen dataclass，`kind: Literal["approval"]`
    - 定义 `ErrorTrace` frozen dataclass，`kind: Literal["error"]`
    - 定义 type alias `AgentStepTrace = ModelCallTrace | ToolCallTrace | ApprovalTrace | ErrorTrace`
    - 定义 `SessionTrace` frozen dataclass
    - 中文 docstring
    - _需求: R1.1-R1.9_

- [x] 2. TraceStorePort 端口
  - [x] 2.1 在 `src/domain/agent/ports.py` 追加 `TraceStorePort` Protocol
    - `async def append_step(self, session_id: str, step: "AgentStepTrace") -> None`
    - `async def get_session_trace(self, session_id: str) -> "SessionTrace | None"`
    - `async def list_traces(self, limit: int = 20) -> list["SessionTrace"]`
    - 使用 `TYPE_CHECKING` 保护导入
    - 中文 docstring
    - _需求: R2.1-R2.5_

- [x] 3. 配置
  - [x] 3.1 创建 `src/infrastructure/trace/__init__.py`（空文件）
  - [x] 3.2 创建 `src/infrastructure/trace/trace_config.py`
    - `TraceConfig(PropertiesBaseSettings)` + `SettingsConfigDict(env_prefix="TRACE_")`
    - 字段 `enabled: bool = True`、`store_dir: str = ".epsilon/traces"`
    - 模块级 `trace_config = create_config(TraceConfig)` 全局实例
    - _需求: R6.2_
  - [x] 3.3 在 `config.properties` 追加 `TRACE_ENABLED=true` 和 `TRACE_STORE_DIR=.epsilon/traces`
    - _需求: R6.1_

- [x] 4. 本地文件 Adapter
  - [x] 4.1 创建 `src/infrastructure/trace/local_file_trace_store_adapter.py`
    - 实现 `TraceStorePort` 三个方法
    - `append_step`：JSON 序列化 + `asyncio.to_thread` 包裹 append-only 写入
    - `get_session_trace`：读取 jsonl 反序列化，路径不存在返回 None
    - `list_traces`：扫描目录按 mtime 倒序，只读首行 + 行数统计
    - `_step_to_dict`：`dataclasses.asdict` 序列化
    - `_dict_to_step`：按 `kind` 字段分发反序列化
    - 目录不存在时自动创建
    - 异常不向上抛出（内部 try/except + logger.warning）
    - _需求: R4.1-R4.7_

- [x] 5. ReActAgentAdapter 追踪集成
  - [x] 5.1 修改 `__init__` 追加 `trace_store` 参数
    - `trace_store: "TraceStorePort | None" = None`
    - `self._trace_store = trace_store`
    - _需求: R3.1_
  - [x] 5.2 新增 `_record_trace` 私有方法
    - `async def _record_trace(self, session_id: str | None, step: Any) -> None`
    - `trace_store` 为 None 或 `session_id` 为 None 时直接 return
    - try/except 全覆盖，logger.warning 记录失败
    - _需求: R3.6_
  - [x] 5.3 新增 `_truncate` 静态方法
    - `@staticmethod def _truncate(text: str, max_len: int) -> str`
    - _需求: R1.9, D2_
  - [x] 5.4 在 `run` 入口的模型调用完成后记录 `ModelCallTrace`
    - 从 `RoundOutcome` 或 `LLMResponse` 获取 usage、latency_ms
    - _需求: R3.2, R3.7_
  - [x] 5.5 在 `_dispatch_concurrent_tool_calls` 内记录 `ToolCallTrace`
    - 在 `_execute_tool_call` 前后计时
    - _需求: R3.3, R3.7_
  - [x] 5.6 在审批中断产生时记录 `ApprovalTrace`
    - _需求: R3.4_
  - [x] 5.7 在 Agent Loop 异常时记录 `ErrorTrace`
    - _需求: R3.5_

- [x] 6. DI 容器装配
  - [x] 6.1 在 `container_config.py` 新增 `_create_trace_store` 工厂方法
    - 读取 `trace_config.enabled`，disabled 时返回 None
    - enabled 时构造 `LocalFileTraceStoreAdapter(store_dir=trace_config.store_dir)`
    - _需求: R5.1, R5.2_
  - [x] 6.2 修改 `_create_agent` 注入 `trace_store`
    - `trace_store = await _create_trace_store()` 或从容器 resolve
    - 传入 `ReActAgentAdapter(..., trace_store=trace_store)`
    - _需求: R5.3, R5.4_

- [x] 7. 验证：值对象单元测试
  - [x] 7.1 创建 `test/domain/agent/test_trace_value_objects_unit.py`
    - `test_model_call_trace_kind_field` — kind 为 "model_call"
    - `test_tool_call_trace_frozen` — 赋值抛 FrozenInstanceError
    - `test_session_trace_construction` — steps 列表可混合类型
    - `test_all_traces_have_kind` — 遍历 Union 类型验证 kind 字段
    - _需求: R1.1-R1.8_

- [x] 8. 验证：本地文件 Adapter 单元测试
  - [x] 8.1 创建 `test/infrastructure/trace/test_local_file_trace_store_unit.py`
    - `test_append_step_creates_file_and_writes_jsonl` — tmp_path 验证
    - `test_get_session_trace_returns_none_for_missing` — 路径不存在返回 None
    - `test_get_session_trace_reads_all_steps` — 多步骤序列化/反序列化
    - `test_list_traces_returns_sorted_by_mtime` — 多文件按时间倒序
    - `test_append_step_auto_creates_directory` — 目录不存在自动创建
    - `test_malformed_line_skipped` — 损坏行跳过不崩溃
    - _需求: R4.1-R4.7, NFR-5_

- [x] 9. 验证：Agent trace 集成测试
  - [x] 9.1 创建 `test/infrastructure/agent/test_react_agent_trace_unit.py`
    - `test_trace_records_model_call_after_llm_response` — mock trace_store 验证 append_step 被调用
    - `test_trace_records_tool_call_with_correct_fields` — tool_name、success、latency
    - `test_trace_store_none_no_error` — trace_store 为 None 时正常运行无异常
    - `test_trace_store_exception_does_not_affect_agent_result` — 故障隔离验证
    - _需求: R3.1-R3.7, NFR-2_

- [x] 10. 全局检查点
  - [x] 10.1 执行 `uv run pytest` 全量测试，确认零新增失败
  - [x] 10.2 确认 `config.properties` 包含 `TRACE_ENABLED=true` 和 `TRACE_STORE_DIR=.epsilon/traces`
  - [x] 10.3 确认 `uv run python -c "from domain.agent.trace_value_objects import AgentStepTrace"` 可导入
  - [x] 10.4 确认 `uv run python -c "from application.container_config import register_all"` DI 装配正常
  - _需求: NFR-4, NFR-6_

## 备注

1. **依赖顺序**：Task 1-2 为领域层前置，Task 3-4 为基础设施层，Task 5-6 为集成与装配。每层完成后有测试检查点。
2. **测试框架**：使用 pytest + pytest-asyncio（asyncio_mode="auto"）。Mock 使用 `unittest.mock.AsyncMock`。使用 `tmp_path` fixture 避免 trace 文件残留。
3. **不引入新依赖**：仅使用标准库 json / os / pathlib / asyncio + 已有 pydantic-settings。
4. **session_id 传递**：从 `context.session_id`（`str | None`）获取；为 None 时 `_record_trace` 静默跳过。
