"""ChatResponseVO 值对象单元测试模块。

验证 ChatResponseVO 的 kw_only 语义、prompt_id 校验和 frozen 不可变性。

# Validates: Requirement 4.5
"""

from dataclasses import FrozenInstanceError

import pytest

from domain.chat.value_objects import ChatResponseVO


class TestChatResponseVO:
    """ChatResponseVO 值对象单元测试。"""

    def test_kw_only_rejects_positional_args(self) -> None:
        """位置参数调用触发 TypeError（kw_only=True 语义）。"""
        with pytest.raises(TypeError):
            ChatResponseVO("s1", "hi", "gpt-4", {}, "chat-default@v1")  # type: ignore[misc]

    def test_missing_prompt_id_raises_type_error(self) -> None:
        """缺省 prompt_id 关键字触发 TypeError。"""
        with pytest.raises(TypeError):
            ChatResponseVO(  # type: ignore[call-arg]
                session_id="s1",
                reply="hello",
                model="gpt-4",
                usage={},
            )

    @pytest.mark.parametrize("bad_id", ["", "foo", "chat-default@0", "UPPER@v1"])
    def test_invalid_prompt_id_raises_value_error(self, bad_id: str) -> None:
        """非法 prompt_id 格式抛出 ValueError。"""
        with pytest.raises(ValueError, match="prompt_id"):
            ChatResponseVO(
                session_id="s1",
                reply="hello",
                model="gpt-4",
                usage={},
                prompt_id=bad_id,
            )

    def test_valid_construction(self) -> None:
        """合法参数构造成功且字段只读。"""
        vo = ChatResponseVO(
            session_id="s1",
            reply="hello",
            model="gpt-4",
            usage={"total_tokens": 10},
            prompt_id="chat-default@v3",
        )
        assert vo.session_id == "s1"
        assert vo.reply == "hello"
        assert vo.model == "gpt-4"
        assert vo.usage == {"total_tokens": 10}
        assert vo.prompt_id == "chat-default@v3"

    def test_frozen_rejects_assignment(self) -> None:
        """frozen=True 赋值触发 FrozenInstanceError。"""
        vo = ChatResponseVO(
            session_id="s1",
            reply="hello",
            model="gpt-4",
            usage={},
            prompt_id="chat-default@v1",
        )
        with pytest.raises(FrozenInstanceError):
            vo.reply = "changed"  # type: ignore[misc]
