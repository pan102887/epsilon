# 任务清单：OpenAICompatibleAdapter 风险项修复

## 概述

基于 `design.md` 中识别的 3 项风险，按优先级拆解为可执行的实现任务。

---

## 任务列表

### Task 1: 流式迭代阶段异常映射（中优先级）

- [x] 1.1 在 `stream()` 方法的 `async for chunk in response:` 循环外包裹 try-except，捕获 `APITimeoutError`、`APIError`、`httpx.ReadTimeout`、`httpx.RemoteProtocolError`、`httpx.ReadError`，映射为对应领域异常
- [x] 1.2 异常 `request_info` 中增加 `"phase": "stream_iteration"` 字段，与握手阶段区分
- [x] 1.3 编写单元测试 `test/infrastructure/model_access/test_openai_compatible_stream_iteration_error_unit.py`，覆盖迭代期间 5 种异常类型的映射验证

**修改文件**：
- `src/infrastructure/model_access/openai_compatible_adapter.py`
- `test/infrastructure/model_access/test_openai_compatible_stream_iteration_error_unit.py`（新增）

**验证命令**：
```bash
cd epsilon-boot && uv run pytest test/infrastructure/model_access/test_openai_compatible_stream_iteration_error_unit.py -v
```

---

### Task 2: 流式 tool_call.id 空值校验（低优先级）

- [x] 2.1 在 `stream()` 方法的 finished 分支中，`_materialize_full_tool_calls(acc)` 调用之后，添加对返回列表逐项 id 校验的逻辑：`if not delta.id: raise InvalidToolCallIdError(source="stream_finished", ...)`
- [x] 2.2 在 `stream()` 方法的 usage-only 分支中，同样在 `_materialize_full_tool_calls(acc)` 调用之后添加相同的 id 校验逻辑
- [x] 2.3 编写单元测试 `test/infrastructure/model_access/test_openai_compatible_stream_id_validation_unit.py`，验证流式 finished 分支和 usage-only 分支中 id 为空时抛出 `InvalidToolCallIdError`（携带 provider/model/tool_name/tool_call_index/raw_id_value 诊断字段）
- [x] 2.4 确认既有 `test_openai_compatible_materialize_normalize_unit.py` 5 个用例仍全部通过（`_materialize_full_tool_calls` 行为不变）

**注意**：不修改 `_materialize_full_tool_calls()` 方法签名和行为。校验逻辑在调用方（`stream()` 方法体内）完成。

**修改文件**：
- `src/infrastructure/model_access/openai_compatible_adapter.py`
- `test/infrastructure/model_access/test_openai_compatible_stream_id_validation_unit.py`（新增）

**验证命令**：
```bash
cd epsilon-boot && uv run pytest test/infrastructure/model_access/test_openai_compatible_stream_id_validation_unit.py test/infrastructure/model_access/test_openai_compatible_materialize_normalize_unit.py -v
```

---

### Task 3: ProviderConfig 增加 safety_identifier 字段（低优先级）

- [x] 3.1 在 `ProviderConfig` 中新增 `safety_identifier: str = ""` 可选字段
- [x] 3.2 在 `_build_params()` 中，当 `self._config.safety_identifier` 非空时设置 `params["user"]`，确保在 `extra_params.update` 之前
- [x] 3.3 编写单元测试 `test/infrastructure/model_access/test_openai_compatible_safety_identifier_unit.py`，验证：非空时传递 `user` 参数、空时不传递、`extra_params` 可覆盖

**修改文件**：
- `src/infrastructure/model_access/provider_config.py`
- `src/infrastructure/model_access/openai_compatible_adapter.py`
- `test/infrastructure/model_access/test_openai_compatible_safety_identifier_unit.py`（新增）

**验证命令**：
```bash
cd epsilon-boot && uv run pytest test/infrastructure/model_access/test_openai_compatible_safety_identifier_unit.py -v
```

---

### Task 4: 回归验证

- [x] 4.1 运行已有 model_access 全量测试，确保无回归

**验证命令**：
```bash
cd epsilon-boot && uv run pytest test/infrastructure/model_access/ -v
```
