"""``OpenAICompatibleAdapter._to_openai_messages`` 协议转换单元测试。

本测试模块覆盖 model-access 协议封装边界归位治理（任务 2）的核心契约：
``OpenAICompatibleAdapter`` 在 SDK 调用前，通过 adapter 内部私有静态方法
``_to_openai_messages`` 把领域消息列表（``list[BaseMessage]``）转换为
OpenAI Chat Completions API 所需的字典列表。

转换规则与 commit ``040695a`` 加固后既有协议转换函数完全等价：

- ``AssistantMessage`` 携带非空 ``tool_calls`` 时输出 OpenAI ``tool_calls``
  嵌套结构 ``{"id", "type": "function", "function": {"name", "arguments"}}``；
- ``ToolMessage`` 输出 ``role`` / ``content`` / ``tool_call_id``；
- 其他消息（``SystemMessage`` / ``UserMessage`` / 不携带 ``tool_calls``
  的 ``AssistantMessage``）仅输出 ``role`` 与 ``content``。

对应需求验收：需求 2.1 / 2.5；需求 6.1。
"""

from __future__ import annotations

from copy import deepcopy

from domain.chat.context import (
    AssistantMessage,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from domain.model_access.value_objects import ToolCallRequest
from infrastructure.model_access.openai_compatible_adapter import (
    OpenAICompatibleAdapter,
)


def test_convert_plain_system_user_assistant_messages() -> None:
    """普通 system / user / assistant 消息只输出 role 与 content。

    覆盖最常见的纯文本对话形态：``SystemMessage`` / ``UserMessage`` 与
    不携带 ``tool_calls`` 的 ``AssistantMessage`` 均仅暴露 ``role`` 与
    ``content`` 两个键，不出现任何 OpenAI 协议特化字段（``tool_calls`` /
    ``tool_call_id`` 等）。
    """
    messages = [
        SystemMessage(content="system"),
        UserMessage(content="user"),
        AssistantMessage(content="assistant"),
    ]

    assert OpenAICompatibleAdapter._to_openai_messages(messages) == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user"},
        {"role": "assistant", "content": "assistant"},
    ]


def test_convert_assistant_with_tool_calls_outputs_openai_nested_shape() -> None:
    """携带 tool_calls 的 assistant 消息输出 OpenAI 嵌套结构。

    OpenAI Chat Completions API 对 ``tool_calls`` 的形态有严格契约：
    每个元素必须含 ``id`` / ``type``（恒为 ``"function"``）/
    ``function``（嵌套含 ``name`` / ``arguments``）三个字段。本用例
    断言 adapter 内的协议转换严格按该形态产出，与 commit ``040695a``
    加固后既有协议转换函数 dict-equal。
    """
    message = AssistantMessage(
        content="",
        tool_calls=[
            ToolCallRequest(id="call-1", name="search", arguments='{"q":"x"}'),
        ],
    )

    assert OpenAICompatibleAdapter._to_openai_messages([message]) == [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "search", "arguments": '{"q":"x"}'},
                }
            ],
        }
    ]


def test_convert_tool_message_includes_tool_call_id() -> None:
    """tool 消息输出 tool_call_id。

    ``ToolMessage`` 是工具执行结果消息，OpenAI 协议要求附 ``tool_call_id``
    字段以便 LLM 把结果与上一轮请求中的某个 ``tool_calls[i]`` 关联起来。
    """
    message = ToolMessage(
        content="result",
        tool_name="search",
        tool_call_id="call-1",
    )

    assert OpenAICompatibleAdapter._to_openai_messages([message]) == [
        {"role": "tool", "content": "result", "tool_call_id": "call-1"}
    ]


def test_convert_empty_list_returns_empty_list() -> None:
    """空消息列表返回空字典列表（边界）。

    虽然 ``ChatRequest.__post_init__`` 已禁止空 ``messages``，但
    ``_to_openai_messages`` 作为底层 helper 应优雅处理空输入，便于在
    单元测试与未来潜在的旁路调用中复用。
    """
    assert OpenAICompatibleAdapter._to_openai_messages([]) == []


def test_convert_does_not_mutate_input_messages() -> None:
    """转换不会修改输入消息列表或其元素。

    协议转换 helper 必须保持纯函数语义，否则上游持有的同一份
    ``BaseMessage`` 列表会在多次转换间被污染（典型场景：``ChatRequest``
    构造后被 retry 装饰器多次调用 ``_build_params``）。
    """
    messages = [
        SystemMessage(content="system"),
        UserMessage(content="hi"),
        AssistantMessage(
            content="",
            tool_calls=[
                ToolCallRequest(id="call-1", name="search", arguments='{"q":"x"}'),
            ],
        ),
        ToolMessage(content="result", tool_name="search", tool_call_id="call-1"),
    ]
    snapshot = deepcopy(messages)

    OpenAICompatibleAdapter._to_openai_messages(messages)

    assert len(messages) == len(snapshot)
    for actual, expected in zip(messages, snapshot, strict=True):
        assert type(actual) is type(expected)
        assert actual.role == expected.role
        assert actual.content == expected.content
        assert actual.metadata == expected.metadata
        if isinstance(actual, AssistantMessage):
            assert isinstance(expected, AssistantMessage)
            assert len(actual.tool_calls) == len(expected.tool_calls)
            for actual_tc, expected_tc in zip(actual.tool_calls, expected.tool_calls, strict=True):
                assert actual_tc.id == expected_tc.id
                assert actual_tc.name == expected_tc.name
                assert actual_tc.arguments == expected_tc.arguments
        if isinstance(actual, ToolMessage):
            assert isinstance(expected, ToolMessage)
            assert actual.tool_name == expected.tool_name
            assert actual.tool_call_id == expected.tool_call_id
