"""ChatRequest tools 字段单元测试。

覆盖 ChatRequest.tools 字段的具体示例和边界情况，
以及 OpenAICompatibleAdapter._build_params() 对 tools 的传递逻辑。
与属性测试互补，提供可读性更强的回归用例。
"""

from typing import Any

from domain.chat.context import BaseMessage, SystemMessage, UserMessage
from domain.model_access.value_objects import ChatRequest
from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter
from infrastructure.model_access.provider_config import ProviderConfig


def _make_messages() -> list[BaseMessage]:
    """构造测试用的领域消息列表。

    每个用例独立持有列表实例，避免 frozen dataclass 之间共享可变引用。
    """
    return [UserMessage(content="hello")]


_SAMPLE_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取文件内容",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        },
    }
]


# ---------------------------------------------------------------------------
# 值对象测试
# ---------------------------------------------------------------------------


class TestChatRequestTools:
    """ChatRequest tools 字段单元测试。"""

    def test_default_tools_is_none(self) -> None:
        """不传 tools 时默认为 None。"""
        request = ChatRequest(messages=_make_messages())
        assert request.tools is None

    def test_empty_list_preserved(self) -> None:
        """tools=[] 时保留空列表。"""
        request = ChatRequest(messages=_make_messages(), tools=[])
        assert request.tools == []

    def test_non_empty_list_preserved(self) -> None:
        """传入具体 schema 列表时值一致。"""
        request = ChatRequest(messages=_make_messages(), tools=_SAMPLE_TOOLS)
        assert request.tools == _SAMPLE_TOOLS

    def test_backward_compatible(self) -> None:
        """不传 tools 的 ChatRequest 行为与变更前一致。"""
        messages = _make_messages()
        request = ChatRequest(
            messages=messages,
            model="gpt-4",
            temperature=0.5,
            max_tokens=100,
        )
        assert request.messages == messages
        assert request.model == "gpt-4"
        assert request.temperature == 0.5
        assert request.max_tokens == 100
        assert request.tools is None
        assert request.extra_params is None


# ---------------------------------------------------------------------------
# _build_params 单元测试
# ---------------------------------------------------------------------------


def _make_adapter() -> OpenAICompatibleAdapter:
    """构造测试用 OpenAICompatibleAdapter 实例。"""
    config = ProviderConfig(
        provider_name="test",
        api_base="https://test.example.com/v1",
        api_key="test-key",
        default_model="test-model",
    )
    return OpenAICompatibleAdapter(config)


def _make_official_openai_adapter(default_model: str = "gpt-5.4") -> OpenAICompatibleAdapter:
    """构造指向 OpenAI 官方 API 的测试 adapter。"""
    config = ProviderConfig(
        provider_name="openai",
        api_base="https://api.openai.com/v1",
        api_key="test-key",
        default_model=default_model,
        max_tokens=2048,
    )
    return OpenAICompatibleAdapter(config)


class TestBuildParamsTools:
    """_build_params 对 tools 参数的传递逻辑单元测试。"""

    def test_tools_none_not_in_params(self) -> None:
        """tools=None 时返回的 dict 无 "tools" 键。"""
        adapter = _make_adapter()
        request = ChatRequest(messages=_make_messages(), tools=None)
        params = adapter._build_params(request, stream=False)
        assert "tools" not in params

    def test_tools_empty_list_not_in_params(self) -> None:
        """tools=[] 时返回的 dict 无 "tools" 键。"""
        adapter = _make_adapter()
        request = ChatRequest(messages=_make_messages(), tools=[])
        params = adapter._build_params(request, stream=False)
        assert "tools" not in params

    def test_tools_non_empty_in_params(self) -> None:
        """tools 非空时返回的 dict 含 "tools" 键且值正确。"""
        adapter = _make_adapter()
        request = ChatRequest(messages=_make_messages(), tools=_SAMPLE_TOOLS)
        params = adapter._build_params(request, stream=False)
        assert "tools" in params
        assert params["tools"] == _SAMPLE_TOOLS


class TestBuildParamsOpenAIStandards:
    """OpenAI 官方最新 Chat Completions 规范相关参数测试。"""

    def test_official_openai_uses_max_completion_tokens(self) -> None:
        """官方 OpenAI Provider 使用 max_completion_tokens 而非 max_tokens。"""
        adapter = _make_official_openai_adapter()
        request = ChatRequest(messages=_make_messages())

        params = adapter._build_params(request, stream=False)

        assert params["max_completion_tokens"] == 2048
        assert "max_tokens" not in params

    def test_compatible_provider_keeps_max_tokens(self) -> None:
        """第三方 OpenAI-compatible Provider 仍使用 max_tokens。"""
        adapter = _make_adapter()
        request = ChatRequest(messages=_make_messages())

        params = adapter._build_params(request, stream=False)

        assert params["max_tokens"] == 4096
        assert "max_completion_tokens" not in params

    def test_official_openai_normalizes_extra_params_max_tokens(self) -> None:
        """官方 OpenAI Provider 会把 extra_params 中的 max_tokens 迁移为新字段。"""
        adapter = _make_official_openai_adapter()
        request = ChatRequest(
            messages=_make_messages(),
            extra_params={"max_tokens": 128, "top_p": 0.9},
        )

        params = adapter._build_params(request, stream=False)

        assert params["max_completion_tokens"] == 128
        assert params["top_p"] == 0.9
        assert "max_tokens" not in params

    def test_official_openai_gpt5_maps_system_to_developer(self) -> None:
        """官方 GPT-5 系列把领域 SystemMessage 映射为 developer role。"""
        adapter = _make_official_openai_adapter(default_model="gpt-5.4")
        request = ChatRequest(
            messages=[SystemMessage(content="follow policy"), UserMessage(content="hi")]
        )

        params = adapter._build_params(request, stream=False)

        assert params["messages"][0] == {
            "role": "developer",
            "content": "follow policy",
        }
        assert params["messages"][1] == {"role": "user", "content": "hi"}

    def test_compatible_provider_keeps_system_role(self) -> None:
        """第三方兼容 Provider 不自动改写 system role。"""
        adapter = _make_adapter()
        request = ChatRequest(
            messages=[SystemMessage(content="follow policy"), UserMessage(content="hi")]
        )

        params = adapter._build_params(request, stream=False)

        assert params["messages"][0] == {
            "role": "system",
            "content": "follow policy",
        }
