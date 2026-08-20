"""Prompt 领域异常单元测试。

覆盖 :class:`domain.prompt.exceptions.PromptNotFoundError` 的：

- 继承关系（继承自 ``RuntimeError``，不涉及 ``ConfigurationError``）；
- ``name`` 与 ``registered`` 字段暴露正确；
- ``str(err)`` 含中文提示 ``"Prompt 未注册"`` 与已注册列表文本。
"""

# Validates: Requirement 3.5

from __future__ import annotations

from domain.prompt.exceptions import PromptNotFoundError


def test_prompt_not_found_error_is_runtime_error() -> None:
    """PromptNotFoundError 必须继承自 RuntimeError（与 ConfigurationError 隔离）。

    Validates: Requirement 3.5
    """
    err = PromptNotFoundError("missing", ["chat-default", "task-template"])

    assert isinstance(err, RuntimeError)


def test_prompt_not_found_error_exposes_name_and_registered_fields() -> None:
    """``name`` / ``registered`` 字段应被原样暴露在实例上。

    Validates: Requirement 3.5
    """
    registered = ["chat-default", "task-template"]

    err = PromptNotFoundError("missing", registered)

    assert err.name == "missing"
    assert err.registered == ["chat-default", "task-template"]


def test_prompt_not_found_error_registered_is_defensive_copy() -> None:
    """``registered`` 字段应为独立列表拷贝，外部修改不得影响实例状态。

    Validates: Requirement 3.5
    """
    registered = ["chat-default"]
    err = PromptNotFoundError("missing", registered)

    registered.append("evil")

    assert err.registered == ["chat-default"]


def test_prompt_not_found_error_message_includes_chinese_prefix_and_list() -> None:
    """``str(err)`` 必须同时包含中文前缀与已注册列表文本（需求 3.5）。

    Validates: Requirement 3.5
    """
    err = PromptNotFoundError("missing", ["chat-default", "task-template"])

    message = str(err)

    assert "Prompt 未注册" in message
    assert "chat-default" in message
    assert "task-template" in message
    assert "missing" in message
