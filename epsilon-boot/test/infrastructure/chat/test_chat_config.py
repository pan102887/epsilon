"""ChatConfig 配置校验单元测试。

验证 ChatConfig 中 Agent Loop 相关配置字段的默认值和校验逻辑：
- max_tool_rounds 字段默认值为 10
- tool_calling_enabled 默认值为 True
- max_tool_rounds ≤ 0 时归一化为"不限制"哨兵值
- max_tool_rounds 为正整数时直接使用该值
"""

import pytest

from common.configuration import ConfigurationError
from infrastructure.chat.chat_config import (
    UNLIMITED_MAX_TOOL_ROUNDS_SENTINEL,
    ChatConfig,
)


class TestChatConfigDefaults:
    """ChatConfig 默认值验证测试。"""

    def test_max_tool_rounds_field_default_is_10(self) -> None:
        """验证 max_tool_rounds 的字段默认值为 10（正数直接生效，不触发归一化）。"""
        config = ChatConfig(max_tool_rounds=10)
        assert config.max_tool_rounds == 10

    def test_tool_calling_enabled_default_is_true(self) -> None:
        """验证 tool_calling_enabled 的默认值为 True。"""
        config = ChatConfig()
        assert config.tool_calling_enabled is True

    def test_compaction_config_defaults(self) -> None:
        """摘要压缩配置默认值符合 config.properties。"""
        config = ChatConfig()

        assert config.compaction_trigger_tokens == 8000
        assert config.compaction_keep_recent_messages == 20
        assert config.compaction_encoding == "cl100k_base"


class TestChatConfigMaxToolRoundsValidation:
    """ChatConfig max_tool_rounds 校验逻辑测试。"""

    def test_max_tool_rounds_zero_normalizes_to_unlimited(self) -> None:
        """验证 max_tool_rounds=0（不限制）归一化为哨兵值。"""
        config = ChatConfig(max_tool_rounds=0)
        assert config.max_tool_rounds == UNLIMITED_MAX_TOOL_ROUNDS_SENTINEL

    def test_max_tool_rounds_negative_normalizes_to_unlimited(self) -> None:
        """验证 max_tool_rounds=-5（不限制）归一化为哨兵值。"""
        config = ChatConfig(max_tool_rounds=-5)
        assert config.max_tool_rounds == UNLIMITED_MAX_TOOL_ROUNDS_SENTINEL

    def test_max_tool_rounds_positive_value_accepted(self) -> None:
        """验证 max_tool_rounds=20 时直接使用该值。"""
        config = ChatConfig(max_tool_rounds=20)
        assert config.max_tool_rounds == 20


class TestChatConfigCompactionValidation:
    """ChatConfig 摘要压缩配置校验测试。"""

    @pytest.mark.parametrize("value", [0, -1])
    def test_invalid_compaction_trigger_tokens_rejected(self, value: int) -> None:
        """trigger token 数必须为正整数。"""
        with pytest.raises(ConfigurationError, match="CHAT_COMPACTION_TRIGGER_TOKENS"):
            ChatConfig(compaction_trigger_tokens=value)

    @pytest.mark.parametrize("value", [0, -1])
    def test_invalid_compaction_keep_recent_messages_rejected(
        self,
        value: int,
    ) -> None:
        """最近消息保留数必须为正整数。"""
        with pytest.raises(
            ConfigurationError,
            match="CHAT_COMPACTION_KEEP_RECENT_MESSAGES",
        ):
            ChatConfig(compaction_keep_recent_messages=value)

    def test_chat_config_fields_do_not_use_budget_name(self) -> None:
        """ChatConfig 字段不使用 budget 命名。"""
        field_names = set(ChatConfig.model_fields)

        assert not any("budget" in field_name for field_name in field_names)
