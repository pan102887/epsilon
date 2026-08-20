"""聊天对话值对象属性测试。

使用 Hypothesis 对 ChatRequest_VO 的字段验证行为进行属性测试，
验证空白消息和空 session_id 始终被拒绝。
"""

import hypothesis.strategies as st
from hypothesis import given, settings

from domain.chat.value_objects import ChatRequestVO

# ── Hypothesis 生成策略 ──

# 纯空白字符策略：生成仅由空白字符组成的字符串（包括空字符串）
whitespace_only_st = st.from_regex(r"^[\s]*$", fullmatch=True).filter(
    lambda s: len(s) == 0 or not s.strip()
)

# 有效 session_id 策略：非空字符串
valid_session_id_st = st.text(min_size=1, max_size=50).filter(lambda s: s.strip() != "")


# ── Property 1: 空白消息拒绝 ──
# Feature: chat-chat-api, Property 1: 空白消息拒绝


@settings(max_examples=100)
@given(message=whitespace_only_st, session_id=valid_session_id_st)
def test_blank_message_raises_value_error(message: str, session_id: str) -> None:
    """验证纯空白消息始终被拒绝。

    对于任意仅由空白字符组成的字符串（空字符串、空格、制表符、换行符等），
    构造 ChatRequest_VO 时必须抛出 ValueError，不产生有效的值对象实例。

    验证: 需求 1.3
    """
    try:
        ChatRequestVO(session_id=session_id, message=message)
    except ValueError:
        pass
    else:
        raise AssertionError(
            f"应当拒绝空白消息，但成功构造了 ChatRequest_VO: message={message!r}"
        )


@settings(max_examples=100)
@given(message=valid_session_id_st)
def test_empty_session_id_raises_value_error(message: str) -> None:
    """验证空 session_id 始终被拒绝。

    当 session_id 为空字符串时，构造 ChatRequest_VO 必须抛出 ValueError。

    验证: 需求 1.4
    """
    try:
        ChatRequestVO(session_id="", message=message)
    except ValueError:
        pass
    else:
        raise AssertionError("应当拒绝空 session_id，但成功构造了 ChatRequest_VO")
