
"""JSON Schema 参数转换与校验工具模块。

本模块提供面向工具调用参数的轻量安全转换能力，并委托 ``jsonschema``
执行标准 JSON Schema 校验。转换逻辑只处理无歧义场景：数字字符串转数字、
明确布尔词转布尔、JSON 字符串转对象/数组，以及递归处理 schema 声明的
对象属性和数组元素。无法安全转换的值会保持原样，由校验阶段报告错误。
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from typing import Any, cast

from jsonschema import Draft202012Validator, exceptions

_TRUE_STRINGS = {"true", "1", "yes", "y", "on"}
_FALSE_STRINGS = {"false", "0", "no", "n", "off"}


def cast_json_schema_value(value: Any, schema: Mapping[str, Any]) -> Any:
    """按 JSON Schema 对单个值执行安全递归转换。

    Args:
        value: 待转换的参数值。
        schema: 描述该值的 JSON Schema 片段。

    Returns:
        转换后的值。若无法无歧义转换，则返回原值。
    """
    if value is None:
        return None

    for keyword in ("anyOf", "oneOf"):
        candidates = schema.get(keyword)
        if isinstance(candidates, list):
            resolved = _cast_with_candidates(value, cast("list[Any]", candidates))
            if resolved is not _UNRESOLVED:
                return resolved

    schema_type = schema.get("type")
    if isinstance(schema_type, list):
        resolved = _cast_with_type_candidates(value, schema, cast("list[Any]", schema_type))
        if resolved is not _UNRESOLVED:
            return resolved
        return value

    if schema_type == "object":
        return _cast_object(value, schema)
    if schema_type == "array":
        return _cast_array(value, schema)
    if schema_type == "integer":
        return _cast_integer(value)
    if schema_type == "number":
        return _cast_number(value)
    if schema_type == "boolean":
        return _cast_boolean(value)
    if schema_type == "string":
        return _cast_string(value)
    return value


def cast_json_schema_params(params: Mapping[str, Any], schema: Mapping[str, Any]) -> dict[str, Any]:
    """按工具参数 schema 转换顶层参数字典。

    Args:
        params: 工具调用参数。
        schema: 工具 ``parameters`` JSON Schema。

    Returns:
        转换后的新参数字典，不修改输入对象。
    """
    original = dict(params)
    casted = cast_json_schema_value(original, schema)
    if isinstance(casted, dict):
        return cast("dict[str, Any]", casted)
    return original


def validate_json_schema_params(params: Mapping[str, Any], schema: Mapping[str, Any]) -> list[str]:
    """使用 ``jsonschema`` 校验工具参数。

    Args:
        params: 已完成安全转换的工具参数。
        schema: 工具 ``parameters`` JSON Schema。

    Returns:
        校验错误信息列表，为空表示校验通过。schema 自身非法时返回 schema
        错误信息，避免调用链因校验器异常崩溃。
    """
    try:
        Draft202012Validator.check_schema(schema)
        validator = cast("Any", Draft202012Validator(schema))
        raw_errors = cast("Iterable[exceptions.ValidationError]", validator.iter_errors(params))
        errors = sorted(raw_errors, key=lambda err: list(err.path))
    except exceptions.SchemaError as exc:
        return [f"工具参数 schema 无效: {exc.message}"]

    return [_format_validation_error(error) for error in errors]


class _Unresolved:
    """候选转换未能确定时使用的内部哨兵。"""


_UNRESOLVED = _Unresolved()


def _cast_with_candidates(value: Any, candidates: list[Any]) -> Any:
    """从 anyOf/oneOf 候选中选择能转换并通过校验的分支。"""
    for candidate in candidates:
        if not isinstance(candidate, Mapping):
            continue
        candidate_schema = cast("Mapping[str, Any]", candidate)
        casted = cast_json_schema_value(value, candidate_schema)
        if _is_valid(casted, candidate_schema):
            return casted
    return _UNRESOLVED


def _cast_with_type_candidates(value: Any, schema: Mapping[str, Any], types: list[Any]) -> Any:
    """处理 ``type`` 为列表的常见 nullable / 多类型 schema。"""
    for schema_type in types:
        if schema_type == "null":
            continue
        candidate = dict(schema)
        candidate["type"] = schema_type
        casted = cast_json_schema_value(value, candidate)
        if _is_valid(casted, candidate):
            return casted
    return _UNRESOLVED


def _cast_object(value: Any, schema: Mapping[str, Any]) -> Any:
    """转换对象并递归处理 properties / additionalProperties。"""
    parsed_obj = _parse_json_string(value, dict)
    if parsed_obj is _UNRESOLVED:
        if not isinstance(value, dict):
            return value
        obj = cast("dict[str, Any]", value)
    else:
        obj = cast("dict[str, Any]", parsed_obj)

    result: dict[str, Any] = dict(obj)
    raw_properties = schema.get("properties")
    properties: Mapping[str, Any] | None = (
        cast("Mapping[str, Any]", raw_properties) if isinstance(raw_properties, Mapping) else None
    )
    if properties is not None:
        for key, child_schema in properties.items():
            if key in result and isinstance(child_schema, Mapping):
                child = cast("Mapping[str, Any]", child_schema)
                result[key] = cast_json_schema_value(result[key], child)

    raw_additional = schema.get("additionalProperties", True)
    if isinstance(raw_additional, Mapping):
        additional = cast("Mapping[str, Any]", raw_additional)
        declared: set[str] = set(properties.keys()) if properties is not None else set()
        for key, child_value in list(result.items()):
            if key not in declared:
                result[key] = cast_json_schema_value(child_value, additional)
    return result


def _cast_array(value: Any, schema: Mapping[str, Any]) -> Any:
    """转换数组并递归处理 items。"""
    parsed_arr = _parse_json_string(value, list)
    if parsed_arr is _UNRESOLVED:
        if not isinstance(value, list):
            return value
        arr = cast("list[Any]", value)
    else:
        arr = cast("list[Any]", parsed_arr)

    raw_items_schema = schema.get("items")
    if isinstance(raw_items_schema, Mapping):
        items_schema = cast("Mapping[str, Any]", raw_items_schema)
        return [cast_json_schema_value(item, items_schema) for item in arr]
    if isinstance(raw_items_schema, list):
        items_schema = cast("list[Any]", raw_items_schema)
        result = list(arr)
        for index, child_schema in enumerate(items_schema):
            if index < len(result) and isinstance(child_schema, Mapping):
                child = cast("Mapping[str, Any]", child_schema)
                result[index] = cast_json_schema_value(result[index], child)
        return result
    return list(arr)


def _cast_integer(value: Any) -> Any:
    """安全转换 integer，不截断小数。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value) if value.is_integer() else value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped and (stripped.isdigit() or (stripped[0] in "+-" and stripped[1:].isdigit())):
            try:
                return int(stripped)
            except ValueError:
                return value
    return value


def _cast_number(value: Any) -> Any:
    """安全转换 number。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return value
        try:
            return float(stripped)
        except ValueError:
            return value
    return value


def _cast_boolean(value: Any) -> Any:
    """安全转换 boolean，避免 ``bool('false')`` 之类的真值陷阱。"""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in _TRUE_STRINGS:
            return True
        if normalized in _FALSE_STRINGS:
            return False
    return value


def _cast_string(value: Any) -> Any:
    """安全转换 string，避免把复杂结构隐式压扁成字符串。"""
    if isinstance(value, str):
        return value
    if isinstance(value, int | float | bool):
        return str(value)
    return value


def _parse_json_string(value: Any, expected_type: type) -> Any:
    """若 value 为 JSON 字符串且解析类型符合预期，则返回解析值。"""
    if not isinstance(value, str):
        return _UNRESOLVED
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return _UNRESOLVED
    return parsed if isinstance(parsed, expected_type) else _UNRESOLVED


def _is_valid(value: Any, schema: Mapping[str, Any]) -> bool:
    """判断值是否满足指定 schema，schema 非法时视为候选不可用。"""
    try:
        validator = cast("Any", Draft202012Validator(schema))
        return bool(validator.is_valid(value))
    except exceptions.SchemaError:
        return False


def _format_validation_error(error: exceptions.ValidationError) -> str:
    """将 ``jsonschema`` 错误格式化为稳定、可读的中文错误文本。"""
    path = _format_path(error.path)
    if path:
        return f"参数 {path} 校验失败: {error.message}"
    return f"参数校验失败: {error.message}"


def _format_path(path: Iterable[Any]) -> str:
    """将 jsonschema path 转成 ``a[0].b`` 形式。"""
    parts: list[str] = []
    for item in path:
        if isinstance(item, int):
            if parts:
                parts[-1] = f"{parts[-1]}[{item}]"
            else:
                parts.append(f"[{item}]")
        else:
            parts.append(str(item))
    return ".".join(parts)
