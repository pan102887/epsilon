# ruff: noqa: SIM102

"""Tool 抽象基类属性测试模块。

使用 Hypothesis 对 Tool ABC 的 cast_params、validate_params、to_schema 和 run 方法
进行属性测试，验证类型转换、参数校验、schema 生成和异常处理的正确性。

测试通过 FakeTool 具体子类驱动，配合 Hypothesis @st.composite 策略
生成随机 schema 和参数组合。
"""

import json
from typing import Any

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from domain.agent.exceptions import (
    ToolExecutionError,
    ToolParameterValidationError,
)
from domain.agent.tools import Tool
from domain.model_access.value_objects import ToolCallRequest

# ── JSON Schema type → Python 类型映射（与 tools.py 中的映射一致） ──

_SCHEMA_TYPE_TO_PYTHON: dict[str, type] = {
    "string": str,
    "integer": int,
    "number": float,
    "boolean": bool,
    "array": list,
    "object": dict,
}

# ── FakeTool：用于驱动属性测试的具体 Tool 子类 ──


class FakeTool(Tool):
    """用于属性测试的具体 Tool 实现。

    允许在构造时注入 name、description、parameters 和 execute 行为，
    以便 Hypothesis 生成随机配置的工具实例。
    """

    def __init__(
        self,
        tool_name: str = "fake_tool",
        tool_description: str = "A fake tool for testing",
        tool_parameters: dict[str, Any] | None = None,
        execute_fn: Any = None,
    ):
        """初始化 FakeTool。

        Args:
            tool_name: 工具名称。
            tool_description: 工具描述。
            tool_parameters: JSON Schema 格式的参数描述，默认为空 object schema。
            execute_fn: 可选的自定义执行函数，默认返回 "ok"。
        """
        self._name = tool_name
        self._description = tool_description
        self._parameters = tool_parameters or {
            "type": "object",
            "properties": {},
        }
        self._execute_fn = execute_fn

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def parameters(self) -> dict[str, Any]:
        return self._parameters

    async def execute(self, **kwargs: Any) -> str:
        """执行工具逻辑，委托给注入的 execute_fn 或返回默认值。"""
        if self._execute_fn is not None:
            return await self._execute_fn(**kwargs)
        return "ok"


# ── Hypothesis 策略 ──

# 可用的 JSON Schema 类型
_SCHEMA_TYPES = list(_SCHEMA_TYPE_TO_PYTHON.keys())

# 参数名称策略：生成合法的标识符风格名称
param_name_st = st.from_regex(r"[a-z][a-z0-9_]{0,15}", fullmatch=True)


@st.composite
def castable_param_st(draw: st.DrawFn) -> tuple[str, str, Any, type]:
    """生成一组可成功转换的 (参数名, schema_type, 原始值, 期望Python类型)。

    为每种 JSON Schema 类型生成一个可以被对应 Python 类型构造函数成功转换的值。

    Returns:
        (param_name, schema_type, raw_value, expected_python_type) 四元组。
    """
    schema_type = draw(st.sampled_from(_SCHEMA_TYPES))
    name = draw(param_name_st)
    expected_type = _SCHEMA_TYPE_TO_PYTHON[schema_type]

    if schema_type == "string":
        value = draw(st.one_of(st.integers(), st.floats(allow_nan=False), st.booleans()))
    elif schema_type == "integer":
        value = draw(
            st.one_of(
                st.from_regex(r"-?[1-9][0-9]{0,5}", fullmatch=True),
                st.just("0"),
            )
        )
    elif schema_type == "number":
        value = draw(
            st.one_of(
                st.from_regex(r"-?[0-9]{1,4}\.[0-9]{1,3}", fullmatch=True),
                st.integers(min_value=-9999, max_value=9999),
            )
        )
    elif schema_type == "boolean":
        value = draw(st.booleans())
    elif schema_type == "array":
        value = draw(
            st.one_of(
                st.lists(st.integers(), max_size=5),
                st.just("[1, 2]"),
            )
        )
    else:  # object
        value = draw(st.dictionaries(st.text(min_size=1, max_size=5), st.integers(), max_size=3))

    return (name, schema_type, value, expected_type)


@st.composite
def unconvertible_param_st(draw: st.DrawFn) -> tuple[str, str, Any]:
    """生成一组无法转换为目标类型的 (参数名, schema_type, 原始值)。

    为 integer/number 类型生成无法被 int()/float() 转换的值。

    Returns:
        (param_name, schema_type, unconvertible_value) 三元组。
    """
    schema_type = draw(st.sampled_from(["integer", "number"]))
    name = draw(param_name_st)
    value = draw(st.sampled_from(["not_a_number", "abc", "", "12.34.56", "NaN_str"]))
    return (name, schema_type, value)


@st.composite
def tool_schema_and_valid_params_st(
    draw: st.DrawFn,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """生成一组 (parameters_schema, valid_params)，参数值类型与 schema 声明一致。

    用于 validate_params 属性测试，确保生成的参数完全符合 schema 约束。

    Returns:
        (schema, params) 二元组，params 中所有 required 参数都存在且类型匹配。
    """
    num_props = draw(st.integers(min_value=1, max_value=4))
    names = draw(st.lists(param_name_st, min_size=num_props, max_size=num_props, unique=True))
    properties: dict[str, Any] = {}
    params: dict[str, Any] = {}

    for n in names:
        schema_type = draw(st.sampled_from(_SCHEMA_TYPES))
        properties[n] = {"type": schema_type}
        expected_type = _SCHEMA_TYPE_TO_PYTHON[schema_type]

        if expected_type is str:
            params[n] = draw(st.text(min_size=0, max_size=20))
        elif expected_type is int:
            params[n] = draw(st.integers(min_value=-1000, max_value=1000))
        elif expected_type is float:
            params[n] = draw(
                st.floats(allow_nan=False, allow_infinity=False, min_value=-1e6, max_value=1e6)
            )
        elif expected_type is bool:
            params[n] = draw(st.booleans())
        elif expected_type is list:
            params[n] = draw(st.lists(st.integers(), max_size=3))
        else:  # dict
            params[n] = draw(
                st.dictionaries(st.text(min_size=1, max_size=5), st.integers(), max_size=3)
            )

    required = draw(st.lists(st.sampled_from(names), unique=True))
    schema = {"type": "object", "properties": properties, "required": required}
    return (schema, params)


@st.composite
def tool_schema_and_invalid_params_st(
    draw: st.DrawFn,
) -> tuple[dict[str, Any], dict[str, Any], bool, bool]:
    """生成一组 (schema, params, has_missing, has_type_mismatch)，参数故意违反 schema。

    至少包含一种错误：缺失必填参数或类型不匹配。
    注意：类型不匹配的值必须在 cast_params 转换后仍然保持错误类型，
    因为 validate_params 在 cast_params 之后执行。

    Returns:
        (schema, params, has_missing, has_type_mismatch) 四元组。
    """
    num_props = draw(st.integers(min_value=1, max_value=4))
    names = draw(st.lists(param_name_st, min_size=num_props, max_size=num_props, unique=True))
    properties: dict[str, Any] = {}
    params: dict[str, Any] = {}

    for n in names:
        schema_type = draw(st.sampled_from(_SCHEMA_TYPES))
        properties[n] = {"type": schema_type}
        expected_type = _SCHEMA_TYPE_TO_PYTHON[schema_type]
        # 先填入正确类型的值
        if expected_type is str:
            params[n] = draw(st.text(min_size=0, max_size=10))
        elif expected_type is int:
            params[n] = draw(st.integers(min_value=-100, max_value=100))
        elif expected_type is float:
            params[n] = draw(
                st.floats(allow_nan=False, allow_infinity=False, min_value=-100, max_value=100)
            )
        elif expected_type is bool:
            params[n] = draw(st.booleans())
        elif expected_type is list:
            params[n] = draw(st.lists(st.integers(), max_size=2))
        else:
            params[n] = draw(
                st.dictionaries(st.text(min_size=1, max_size=5), st.integers(), max_size=2)
            )

    required = list(names)  # 所有参数都是必填
    schema = {"type": "object", "properties": properties, "required": required}

    # 决定注入哪种错误
    inject_missing = draw(st.booleans())
    inject_type_mismatch = draw(st.booleans())
    if not inject_missing and not inject_type_mismatch:
        inject_missing = True  # 至少注入一种错误

    has_missing = False
    has_type_mismatch = False

    if inject_missing and len(names) > 0:
        # 移除一个必填参数
        to_remove = draw(st.sampled_from(names))
        del params[to_remove]
        has_missing = True

    if inject_type_mismatch:
        # 找一个还存在的参数，注入错误类型
        # 注意：注入的值必须在 cast_params 转换后仍然保持错误类型
        # 例如 list/dict 无法被 int()/float()/str() 等简单构造函数正确转换
        remaining = [n for n in names if n in params]
        if remaining:
            target = draw(st.sampled_from(remaining))
            target_type = properties[target]["type"]
            # 使用 cast_params 无法转换为目标类型的值：
            # - 对 integer/number：传入 list（int([1,2]) 和 float([1,2]) 都会 TypeError）
            # - 对 boolean：传入 list（bool([]) 可转换，所以用 dict 不行，用特殊对象）
            # - 对 string：传入 list（str([1]) 会变成 "[1]" 即 str 类型，
            #   所以 string 无法制造不匹配）
            # - 对 array：传入 int（list(42) 会 TypeError，但 cast 失败保留 int 原值）
            # - 对 object：传入 int（dict(42) 会 TypeError，保留 int 原值）
            if target_type == "integer":
                params[target] = [1, 2]  # int([1,2]) → TypeError，保留 list
            elif target_type == "number":
                params[target] = [1, 2]  # float([1,2]) → TypeError，保留 list
            elif target_type == "boolean":
                # bool() 几乎能转换任何值，所以跳过 boolean 的类型不匹配注入
                has_type_mismatch = False
            elif target_type == "string":
                # str() 能转换任何值，所以跳过 string 的类型不匹配注入
                has_type_mismatch = False
            elif target_type == "array":
                params[target] = 42  # list(42) → TypeError，保留 int
            else:  # object
                params[target] = 42  # dict(42) → TypeError，保留 int
            if target_type in ("integer", "number", "array", "object"):
                has_type_mismatch = True

    # 确保至少有一种错误
    if not has_missing and not has_type_mismatch:
        # 回退到移除一个必填参数
        if len(names) > 0:
            remaining_keys = [n for n in names if n in params]
            if remaining_keys:
                to_remove = draw(st.sampled_from(remaining_keys))
                del params[to_remove]
                has_missing = True

    return (schema, params, has_missing, has_type_mismatch)


# ── Property 1: cast_params 类型转换正确性 ──
# Feature: tool-abstraction, Property 1: cast_params 类型转换正确性


@settings(max_examples=100)
@given(data=castable_param_st())
def test_cast_params_converts_to_correct_python_type(
    data: tuple[str, str, Any, type],
) -> None:
    """验证 cast_params 将参数值转换为 schema 声明的 Python 类型。

    对于任意可转换的参数值和 JSON Schema 类型声明，cast_params 返回的
    参数字典中对应值的 Python 类型应与 schema 声明一致。
    """
    param_name, schema_type, raw_value, expected_type = data
    tool = FakeTool(
        tool_parameters={
            "type": "object",
            "properties": {param_name: {"type": schema_type}},
        }
    )

    result = tool.cast_params({param_name: raw_value})

    if schema_type == "number":
        assert isinstance(result[param_name], int | float) and not isinstance(
            result[param_name], bool
        ), (
            "number 应转换为 JSON Schema 数值类型（int 或 float），"
            f"实际为 {type(result[param_name]).__name__}，原始值: {raw_value!r}"
        )
    else:
        assert isinstance(result[param_name], expected_type), (
            f"期望 {expected_type.__name__}，实际为 {type(result[param_name]).__name__}，"
            f"原始值: {raw_value!r}，schema_type: {schema_type}"
        )


# ── Property 2: cast_params 转换失败保留原值 ──
# Feature: tool-abstraction, Property 2: cast_params 转换失败保留原值


@settings(max_examples=100)
@given(data=unconvertible_param_st())
def test_cast_params_preserves_original_on_failure(
    data: tuple[str, str, Any],
) -> None:
    """验证 cast_params 在转换失败时保留参数的原始值。

    对于无法转换为目标类型的参数值，cast_params 应返回原始值不变。
    """
    param_name, schema_type, raw_value = data
    tool = FakeTool(
        tool_parameters={
            "type": "object",
            "properties": {param_name: {"type": schema_type}},
        }
    )

    result = tool.cast_params({param_name: raw_value})

    assert result[param_name] == raw_value, (
        f"转换失败时应保留原值 {raw_value!r}，实际为 {result[param_name]!r}"
    )


def test_cast_params_parses_boolean_strings_safely() -> None:
    """验证 boolean 字符串按语义转换，而不是使用 Python bool() 真值规则。"""
    tool = FakeTool(
        tool_parameters={
            "type": "object",
            "properties": {"recursive": {"type": "boolean"}},
        }
    )

    result = tool.cast_params({"recursive": "false"})

    assert result["recursive"] is False


def test_cast_params_preserves_ambiguous_boolean_string_for_validation() -> None:
    """验证无法明确解释的 boolean 字符串保持原值，交给校验阶段报错。"""
    tool = FakeTool(
        tool_parameters={
            "type": "object",
            "properties": {"recursive": {"type": "boolean"}},
        }
    )

    result = tool.cast_params({"recursive": "not sure"})

    assert result["recursive"] == "not sure"


def test_cast_params_does_not_split_string_as_array() -> None:
    """验证 array 不再通过 list(str) 把字符串拆成字符数组。"""
    tool = FakeTool(
        tool_parameters={
            "type": "object",
            "properties": {"items": {"type": "array"}},
        }
    )

    result = tool.cast_params({"items": "abc"})

    assert result["items"] == "abc"


def test_cast_params_parses_json_string_for_array_and_object() -> None:
    """验证 object/array 可从合法 JSON 字符串安全转换。"""
    tool = FakeTool(
        tool_parameters={
            "type": "object",
            "properties": {
                "headers": {"type": "object"},
                "items": {"type": "array"},
            },
        }
    )

    result = tool.cast_params({"headers": '{"x": "1"}', "items": "[1, 2]"})

    assert result == {"headers": {"x": "1"}, "items": [1, 2]}


def test_cast_params_recurses_into_nested_objects_and_arrays() -> None:
    """验证嵌套 object.properties 与 array.items 会递归执行安全 cast。"""
    tool = FakeTool(
        tool_parameters={
            "type": "object",
            "properties": {
                "requests": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "agent_name": {"type": "string"},
                            "priority": {"type": "integer"},
                            "input_data": {
                                "type": "object",
                                "properties": {"dry_run": {"type": "boolean"}},
                            },
                        },
                    },
                }
            },
        }
    )

    result = tool.cast_params(
        {
            "requests": [
                {
                    "agent_name": "worker",
                    "priority": "2",
                    "input_data": {"dry_run": "true"},
                }
            ]
        }
    )

    assert result["requests"][0]["priority"] == 2
    assert result["requests"][0]["input_data"]["dry_run"] is True


def test_cast_params_supports_nullable_type_lists() -> None:
    """验证 type 列表中的 null 分支不会阻止非空值按目标类型转换。"""
    tool = FakeTool(
        tool_parameters={
            "type": "object",
            "properties": {
                "limit": {"type": ["integer", "null"]},
                "offset": {"type": ["integer", "null"]},
            },
        }
    )

    result = tool.cast_params({"limit": "10", "offset": None})

    assert result == {"limit": 10, "offset": None}


def test_cast_params_uses_anyof_candidate_that_can_cast_and_validate() -> None:
    """验证 anyOf 可选择能完成 cast 且满足 schema 的候选分支。"""
    tool = FakeTool(
        tool_parameters={
            "type": "object",
            "properties": {
                "timeout": {
                    "anyOf": [
                        {"type": "integer", "minimum": 1},
                        {"type": "null"},
                    ]
                }
            },
        }
    )

    result = tool.cast_params({"timeout": "30"})

    assert result["timeout"] == 30


def test_cast_params_recurses_into_additional_properties_schema() -> None:
    """验证 additionalProperties 为 schema 时额外字段也会递归 cast。"""
    tool = FakeTool(
        tool_parameters={
            "type": "object",
            "properties": {
                "scores": {
                    "type": "object",
                    "additionalProperties": {"type": "integer"},
                }
            },
        }
    )

    result = tool.cast_params({"scores": {"a": "1", "b": "2"}})

    assert result["scores"] == {"a": 1, "b": 2}


def test_validate_params_uses_json_schema_constraints() -> None:
    """验证通用校验覆盖 enum、minItems、items.required 等 JSON Schema 约束。"""
    tool = FakeTool(
        tool_parameters={
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["GET", "POST"]},
                "requests": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 2,
                    "items": {
                        "type": "object",
                        "properties": {"agent_name": {"type": "string"}},
                        "required": ["agent_name"],
                    },
                },
            },
            "required": ["method", "requests"],
        }
    )

    errors = tool.validate_params({"method": "PATCH", "requests": [{}]})

    assert any("method" in error and "PATCH" in error for error in errors)
    assert any("requests[0]" in error and "agent_name" in error for error in errors)


# ── Property 3: cast_params 忽略 schema 外参数 ──
# Feature: tool-abstraction, Property 3: cast_params 忽略 schema 外参数


@settings(max_examples=100)
@given(
    extra_name=param_name_st,
    extra_value=st.one_of(st.integers(), st.text(max_size=10), st.booleans()),
)
def test_cast_params_preserves_extra_params_not_in_schema(
    extra_name: str,
    extra_value: Any,
) -> None:
    """验证 cast_params 对 schema 未声明的额外参数原样保留。

    传入包含 schema properties 中未声明的参数时，这些额外参数
    应在返回的字典中保持原始值不变。
    """
    # schema 中只声明了 "declared_param"，不包含 extra_name
    tool = FakeTool(
        tool_parameters={
            "type": "object",
            "properties": {"declared_param": {"type": "string"}},
        }
    )

    # 确保 extra_name 不与 schema 中声明的参数重名
    if extra_name == "declared_param":
        return  # 跳过重名情况

    result = tool.cast_params({extra_name: extra_value})

    assert result[extra_name] == extra_value, (
        f"schema 外参数应保留原值 {extra_value!r}，实际为 {result[extra_name]!r}"
    )


# ── Property 4: validate_params 检测缺失必填参数与类型不匹配 ──
# Feature: tool-abstraction, Property 4: validate_params 检测缺失必填参数与类型不匹配


@settings(max_examples=100)
@given(data=tool_schema_and_valid_params_st())
def test_validate_params_returns_empty_for_valid_params(
    data: tuple[dict[str, Any], dict[str, Any]],
) -> None:
    """验证 validate_params 对完全合法的参数返回空错误列表。

    当所有 required 参数都存在且类型匹配时，validate_params 应返回空列表。
    """
    schema, params = data
    tool = FakeTool(tool_parameters=schema)

    errors = tool.validate_params(params)

    assert errors == [], f"合法参数不应有校验错误，但得到: {errors}"


@settings(max_examples=100)
@given(data=tool_schema_and_invalid_params_st())
def test_validate_params_detects_missing_and_type_mismatch(
    data: tuple[dict[str, Any], dict[str, Any], bool, bool],
) -> None:
    """验证 validate_params 能检测缺失必填参数和类型不匹配。

    当参数中存在缺失的必填参数或类型不匹配时，validate_params
    应返回非空的错误列表。
    """
    schema, params, has_missing, has_type_mismatch = data
    tool = FakeTool(tool_parameters=schema)

    errors = tool.validate_params(params)

    assert len(errors) > 0, (
        f"非法参数应产生校验错误，has_missing={has_missing}, "
        f"has_type_mismatch={has_type_mismatch}, params={params}"
    )


# ── Property 5: to_schema 忠实反映 Tool 属性 ──
# Feature: tool-abstraction, Property 5: to_schema 忠实反映 Tool 属性


@settings(max_examples=100)
@given(
    tool_name=st.from_regex(r"[a-z][a-z0-9_]{0,20}", fullmatch=True),
    tool_desc=st.text(min_size=1, max_size=100),
    schema_type=st.sampled_from(_SCHEMA_TYPES),
)
def test_to_schema_reflects_tool_attributes(
    tool_name: str,
    tool_desc: str,
    schema_type: str,
) -> None:
    """验证 to_schema 输出结构忠实反映 Tool 的 name/description/parameters。

    对于任意 Tool 实例，to_schema() 返回的字典应满足：
    - schema["type"] == "function"
    - schema["function"]["name"] == tool.name
    - schema["function"]["description"] == tool.description
    - schema["function"]["parameters"] == tool.parameters
    """
    params_schema = {
        "type": "object",
        "properties": {"p": {"type": schema_type}},
    }
    tool = FakeTool(
        tool_name=tool_name,
        tool_description=tool_desc,
        tool_parameters=params_schema,
    )

    schema = tool.to_schema()

    assert schema["type"] == "function"
    assert schema["function"]["name"] == tool_name
    assert schema["function"]["description"] == tool_desc
    assert schema["function"]["parameters"] == params_schema


# ── Property 6: run 方法对非法输入抛出 ToolParameterValidationError ──
# Feature: tool-abstraction, Property 6: run 方法对非法输入抛出 ToolParameterValidationError


@settings(max_examples=100)
@given(bad_json=st.text(min_size=1, max_size=50).filter(lambda s: _is_invalid_json(s)))
@pytest.mark.asyncio
async def test_run_raises_on_invalid_json(bad_json: str) -> None:
    """验证 run 方法在 arguments 不是合法 JSON 时抛出 ToolParameterValidationError。

    对于任意非法 JSON 字符串，run 应抛出 ToolParameterValidationError，
    且 errors 列表中包含 JSON 解析错误信息。
    """
    tool = FakeTool(
        tool_parameters={
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }
    )
    request = ToolCallRequest(id="test-1", name="fake_tool", arguments=bad_json)

    with pytest.raises(ToolParameterValidationError) as exc_info:
        await tool.run(request)

    assert len(exc_info.value.errors) > 0
    assert exc_info.value.tool_name == "fake_tool"


@settings(max_examples=100)
@given(data=tool_schema_and_invalid_params_st())
@pytest.mark.asyncio
async def test_run_raises_on_validation_failure(
    data: tuple[dict[str, Any], dict[str, Any], bool, bool],
) -> None:
    """验证 run 方法在参数校验失败时抛出 ToolParameterValidationError。

    当 JSON 解析成功但参数不符合 schema 约束时，run 应抛出
    ToolParameterValidationError，且 errors 列表包含所有校验错误。
    """
    schema, params, _, _ = data
    tool = FakeTool(tool_parameters=schema)
    request = ToolCallRequest(
        id="test-2",
        name="fake_tool",
        arguments=json.dumps(params),
    )

    with pytest.raises(ToolParameterValidationError) as exc_info:
        await tool.run(request)

    assert len(exc_info.value.errors) > 0
    assert exc_info.value.tool_name == "fake_tool"


def _is_invalid_json(s: str) -> bool:
    """判断字符串是否为非法 JSON。"""
    try:
        json.loads(s)
        return False
    except (json.JSONDecodeError, ValueError):
        return True


# ── Property 7: run 方法包装非 ToolExecutionError 异常 ──
# Feature: tool-abstraction, Property 7: run 方法包装非 ToolExecutionError 异常


@settings(max_examples=100)
@given(
    exc_type=st.sampled_from([ValueError, RuntimeError, TypeError, KeyError]),
    exc_msg=st.text(min_size=1, max_size=50),
)
@pytest.mark.asyncio
async def test_run_wraps_non_tool_execution_errors(
    exc_type: type[Exception],
    exc_msg: str,
) -> None:
    """验证 run 方法将 execute 中抛出的非 ToolExecutionError 异常包装为 ToolExecutionError。

    构造 execute 抛出指定异常类型的 FakeTool，验证 run 将其包装为
    ToolExecutionError，且 tool_name 属性等于工具名称。
    """

    async def failing_execute(**kwargs: Any) -> str:
        raise exc_type(exc_msg)

    tool = FakeTool(
        tool_name="wrapping_tool",
        tool_parameters={
            "type": "object",
            "properties": {},
        },
        execute_fn=failing_execute,
    )
    request = ToolCallRequest(
        id="test-3",
        name="wrapping_tool",
        arguments="{}",
    )

    with pytest.raises(ToolExecutionError) as exc_info:
        await tool.run(request)

    assert exc_info.value.tool_name == "wrapping_tool"
    # 确保不是 ToolParameterValidationError（那是另一种子类）
    assert not isinstance(exc_info.value, ToolParameterValidationError)
