"""上下文构建结果值对象单元测试。"""

import pytest

from domain.chat.context import SystemMessage, UserMessage
from domain.chat.value_objects import ContextBuilderResult


def test_context_builder_result_accepts_valid_values() -> None:
    """合法领域消息列表、usage 和元数据可构造上下文构建结果。"""
    messages = [
        SystemMessage(content="follow instructions"),
        UserMessage(content="hello"),
    ]
    metadata = {"message_count": 2}

    result = ContextBuilderResult(
        messages=messages,
        usage={"prompt_tokens": 3, "total_tokens": 5},
        summary_created=True,
        environment_injected=True,
        metadata=metadata,
    )

    assert result.messages == messages
    assert result.usage == {"prompt_tokens": 3, "total_tokens": 5}
    assert result.summary_created is True
    assert result.environment_injected is True
    assert result.metadata == metadata


def test_context_builder_result_uses_safe_defaults() -> None:
    """默认 usage 和 metadata 为空字典，摘要与环境注入标记为 False。"""
    result = ContextBuilderResult(messages=[UserMessage(content="hello")])

    assert result.usage == {}
    assert result.summary_created is False
    assert result.environment_injected is False
    assert result.metadata == {}


def test_context_builder_result_rejects_empty_messages() -> None:
    """messages 不能为空。"""
    with pytest.raises(ValueError, match="messages 不能为空"):
        ContextBuilderResult(messages=[])


@pytest.mark.parametrize(
    "invalid_message",
    [
        {"role": "user", "content": "hello"},
        "plain string",
        42,
    ],
)
def test_context_builder_result_rejects_non_base_message_element(
    invalid_message: object,
) -> None:
    """messages 列表中每个元素必须为 BaseMessage 子类实例。"""
    with pytest.raises(
        ValueError,
        match=r"messages\[0\] 必须为 BaseMessage 子类实例",
    ):
        ContextBuilderResult(messages=[invalid_message])  # type: ignore[list-item]


def test_context_builder_result_rejects_non_str_usage_key() -> None:
    """usage key 必须为 str。"""
    with pytest.raises(ValueError, match="usage key 必须为 str"):
        ContextBuilderResult(
            messages=[UserMessage(content="hello")],
            usage={1: 3},  # type: ignore[dict-item]
        )


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        ({"prompt_tokens": 1.5}, "int"),
        ({"prompt_tokens": True}, "int"),
        ({"prompt_tokens": -1}, "非负"),
    ],
)
def test_context_builder_result_rejects_invalid_usage_value(
    usage: dict[str, int],
    expected: str,
) -> None:
    """usage value 必须为非负 int，bool 不按 int 接受。"""
    with pytest.raises(ValueError, match=expected):
        ContextBuilderResult(
            messages=[UserMessage(content="hello")],
            usage=usage,
        )


def test_context_builder_result_rejects_non_dict_metadata() -> None:
    """metadata 必须保持 dict 类型。"""
    with pytest.raises(ValueError, match="metadata 必须为 dict"):
        ContextBuilderResult(
            messages=[UserMessage(content="hello")],
            metadata=(),  # type: ignore[arg-type]
        )
