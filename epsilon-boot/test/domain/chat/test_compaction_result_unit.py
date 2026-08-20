"""上下文压缩结果值对象单元测试。"""

import pytest

from domain.chat.context import UserMessage
from domain.chat.value_objects import ContextCompactionResult


def test_context_compaction_result_accepts_valid_values() -> None:
    """合法消息列表和 usage 可构造压缩结果。"""
    messages = [UserMessage(content="hello")]

    result = ContextCompactionResult(
        messages=messages,
        usage={"prompt_tokens": 3, "total_tokens": 5},
        summary_created=True,
    )

    assert result.messages == messages
    assert result.usage == {"prompt_tokens": 3, "total_tokens": 5}
    assert result.summary_created is True


def test_context_compaction_result_uses_safe_defaults() -> None:
    """默认 usage 为空字典且未创建摘要。"""
    result = ContextCompactionResult(messages=[])

    assert result.usage == {}
    assert result.summary_created is False


def test_context_compaction_result_rejects_negative_usage() -> None:
    """usage 中的负数值会被拒绝。"""
    with pytest.raises(ValueError, match="非负"):
        ContextCompactionResult(
            messages=[],
            usage={"summary_tokens": -1},
        )


def test_context_compaction_result_rejects_non_int_usage() -> None:
    """usage 中的非 int 值会被拒绝。"""
    with pytest.raises(ValueError, match="int"):
        ContextCompactionResult(
            messages=[],
            usage={"summary_tokens": 1.5},  # type: ignore[dict-item]
        )


def test_context_compaction_result_rejects_non_list_messages() -> None:
    """messages 必须保持 list 类型。"""
    with pytest.raises(ValueError, match="messages"):
        ContextCompactionResult(messages=())  # type: ignore[arg-type]
