"""Agent 值对象单元测试模块。

对 AgentConfig 和 AgentResult 值对象进行边界情况的单元测试，验证：
- AgentConfig 基本构造与字段赋值
- AgentConfig max_rounds 非法值校验（0 和 -1）
- AgentConfig kw_only 语义与 prompt_id 校验
- AgentResult 基本构造与默认值正确性

与属性测试互补，覆盖具体示例和边界情况。
"""

import pytest

from domain.agent.value_objects import AgentConfig, AgentResult, NamedAgentConfig


class TestAgentConfig:
    """AgentConfig 值对象单元测试。"""

    def test_basic_construction_with_valid_params(self) -> None:
        """验证 AgentConfig 使用合法参数构造成功，各字段值正确。

        **Validates: Requirements 1.1**
        """
        config = AgentConfig(
            system_prompt="你是一个助手",
            tool_schemas=[{"name": "read_file", "type": "function"}],
            model="gpt-4",
            max_rounds=5,
            prompt_id="chat-default@v1",
        )

        assert config.system_prompt == "你是一个助手"
        assert config.tool_schemas == [{"name": "read_file", "type": "function"}]
        assert config.model == "gpt-4"
        assert config.max_rounds == 5
        assert config.prompt_id == "chat-default@v1"

    def test_max_rounds_zero_raises_value_error(self) -> None:
        """验证 max_rounds=0 时构造抛出 ValueError。

        **Validates: Requirements 1.2**
        """
        with pytest.raises(ValueError, match="max_rounds"):
            AgentConfig(
                system_prompt="test",
                tool_schemas=[],
                model=None,
                max_rounds=0,
                prompt_id="chat-default@v1",
            )

    def test_max_rounds_negative_raises_value_error(self) -> None:
        """验证 max_rounds=-1 时构造抛出 ValueError。

        **Validates: Requirements 1.2**
        """
        with pytest.raises(ValueError, match="max_rounds"):
            AgentConfig(
                system_prompt="test",
                tool_schemas=[],
                model=None,
                max_rounds=-1,
                prompt_id="chat-default@v1",
            )

    # ── prompt_id 校验用例（Validates: Requirement 4.1, 4.7）──

    def test_kw_only_rejects_positional_args(self) -> None:
        """位置参数调用触发 TypeError（kw_only=True 语义）。

        # Validates: Requirement 4.1 / 4.7
        """
        with pytest.raises(TypeError):
            AgentConfig("prompt", [], None, 1, "chat-default@v1")  # type: ignore[misc]

    def test_missing_prompt_id_raises_type_error(self) -> None:
        """缺省 prompt_id 关键字触发 TypeError。

        # Validates: Requirement 4.1
        """
        with pytest.raises(TypeError):
            AgentConfig(
                system_prompt="test",
                tool_schemas=[],
                model=None,
                max_rounds=1,
            )  # type: ignore[call-arg]

    def test_valid_prompt_id_construction(self) -> None:
        """prompt_id='chat-default@v3' 关键字调用构造成功。

        # Validates: Requirement 4.1
        """
        config = AgentConfig(
            system_prompt="test",
            tool_schemas=[],
            model=None,
            max_rounds=1,
            prompt_id="chat-default@v3",
        )
        assert config.prompt_id == "chat-default@v3"

    @pytest.mark.parametrize("bad_id", ["foo", "chat-default@1", ""])
    def test_invalid_prompt_id_raises_value_error(self, bad_id: str) -> None:
        """非法 prompt_id 格式抛出 ValueError。

        # Validates: Requirement 4.1 / 4.7
        """
        with pytest.raises(ValueError, match="prompt_id"):
            AgentConfig(
                system_prompt="test",
                tool_schemas=[],
                model=None,
                max_rounds=1,
                prompt_id=bad_id,
            )


class TestNamedAgentConfigPromptId:
    """NamedAgentConfig prompt_id 字段单元测试。

    # Validates: Requirement 4.2 / 4.7
    """

    def test_empty_prompt_id_raises_value_error(self) -> None:
        """prompt_id='' 抛出 ValueError。"""
        with pytest.raises(ValueError, match="prompt_id"):
            NamedAgentConfig(
                name="test-agent",
                description="测试",
                system_prompt="你好",
                prompt_id="",
            )

    def test_invalid_format_raises_value_error(self) -> None:
        """prompt_id='bar' 抛出 ValueError。"""
        with pytest.raises(ValueError, match="prompt_id"):
            NamedAgentConfig(
                name="test-agent",
                description="测试",
                system_prompt="你好",
                prompt_id="bar",
            )

    def test_valid_prompt_id_construction(self) -> None:
        """合法 prompt_id='foo@v2' 构造成功。"""
        config = NamedAgentConfig(
            name="test-agent",
            description="测试",
            system_prompt="你好",
            prompt_id="foo@v2",
        )
        assert config.prompt_id == "foo@v2"


class TestAgentResult:
    """AgentResult 值对象单元测试。"""

    def test_basic_construction_with_all_fields(self) -> None:
        """验证 AgentResult 使用全部字段构造成功，各字段值正确。

        **Validates: Requirements 3.1**
        """
        result = AgentResult(
            content="回复内容",
            model="gpt-4",
            usage={"prompt_tokens": 100, "completion_tokens": 50},
            latency_ms=123.45,
        )

        assert result.content == "回复内容"
        assert result.model == "gpt-4"
        assert result.usage == {"prompt_tokens": 100, "completion_tokens": 50}
        assert result.latency_ms == 123.45

    def test_default_values_are_correct(self) -> None:
        """验证 AgentResult 仅传必填字段时，默认值正确。

        usage 默认为空 dict，latency_ms 默认为 0.0。

        **Validates: Requirements 3.1**
        """
        result = AgentResult(content="hello", model="gpt-4")

        assert result.usage == {}
        assert result.latency_ms == 0.0
