"""消息类型层次结构单元测试模块。

验证 BaseMessage 及其子类的边界情况和具体示例，包括：
- BaseMessage 不可直接实例化
- ToolMessage 必须提供 tool_name
- metadata 默认值
- from_dict 缺少必要键时的错误处理
- Message 兼容别名可用性
"""

import pytest

from domain.chat.context import (
    AssistantMessage,
    BaseMessage,
    Message,
    SystemMessage,
    ToolMessage,
    UserMessage,
)


class TestBaseMessageAbstract:
    """验证 BaseMessage 作为抽象基类的行为。"""

    def test_cannot_instantiate_directly(self) -> None:
        """验证 BaseMessage 不可直接实例化，应抛出 TypeError。

        BaseMessage 定义了 role 抽象属性，直接实例化会因 ABC 机制抛出 TypeError。
        """
        with pytest.raises(TypeError):
            BaseMessage(content="test")  # type: ignore[abstract]


class TestToolMessageRequiresToolName:
    """验证 ToolMessage 的 tool_name 必填约束。"""

    def test_missing_tool_name_raises_error(self) -> None:
        """验证构造 ToolMessage 时缺少 tool_name 会抛出 TypeError。

        ToolMessage 的 tool_name 为必填字段（无默认值），
        使用 kw_only=True 时缺少该参数会导致 TypeError。
        """
        with pytest.raises(TypeError):
            ToolMessage(content="result")  # type: ignore[call-arg]


class TestMetadataDefault:
    """验证 metadata 字段的默认值行为。"""

    def test_system_message_metadata_defaults_to_empty_dict(self) -> None:
        """验证 SystemMessage 不传 metadata 时默认为空字典。"""
        msg = SystemMessage(content="hello")
        assert msg.metadata == {}

    def test_user_message_metadata_defaults_to_empty_dict(self) -> None:
        """验证 UserMessage 不传 metadata 时默认为空字典。"""
        msg = UserMessage(content="hello")
        assert msg.metadata == {}

    def test_assistant_message_metadata_defaults_to_empty_dict(self) -> None:
        """验证 AssistantMessage 不传 metadata 时默认为空字典。"""
        msg = AssistantMessage(content="hello")
        assert msg.metadata == {}

    def test_tool_message_metadata_defaults_to_empty_dict(self) -> None:
        """验证 ToolMessage 不传 metadata 时默认为空字典。"""
        msg = ToolMessage(content="result", tool_name="search")
        assert msg.metadata == {}


class TestFromDictMissingKeys:
    """验证 from_dict 缺少必要键时的错误处理。"""

    def test_missing_role_raises_key_error(self) -> None:
        """验证 from_dict 缺少 role 键时抛出 KeyError。"""
        with pytest.raises(KeyError):
            BaseMessage.from_dict({"content": "hello"})

    def test_missing_content_raises_key_error(self) -> None:
        """验证 from_dict 缺少 content 键时抛出 KeyError。"""
        with pytest.raises(KeyError):
            BaseMessage.from_dict({"role": "user"})


class TestMessageAlias:
    """验证 Message 兼容别名的可用性。"""

    def test_message_alias_is_base_message(self) -> None:
        """验证 Message 别名指向 BaseMessage。"""
        assert Message is BaseMessage

    def test_message_from_dict_works(self) -> None:
        """验证通过 Message 别名调用 from_dict 能正确分派到子类。"""
        data = {"role": "user", "content": "你好"}
        msg = Message.from_dict(data)
        assert isinstance(msg, UserMessage)
        assert msg.content == "你好"
        assert msg.role == "user"

    def test_subclass_is_instance_of_message(self) -> None:
        """验证所有子类实例都是 Message（即 BaseMessage）的实例。"""
        assert isinstance(SystemMessage(content="sys"), Message)
        assert isinstance(UserMessage(content="usr"), Message)
        assert isinstance(AssistantMessage(content="ast"), Message)
        assert isinstance(ToolMessage(content="tool", tool_name="t"), Message)
