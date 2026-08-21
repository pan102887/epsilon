"""上下文构建适配器属性测试。"""

from dataclasses import dataclass

from hypothesis import given, settings
from hypothesis import strategies as st

from domain.chat.context import (
    AssistantMessage,
    BaseMessage,
    ConversationContext,
    SystemMessage,
    ToolMessage,
    UserMessage,
)
from domain.chat.value_objects import ContextCompactionResult
from domain.model_access.ports import ModelAccessPort
from infrastructure.chat.context_builder_adapter import ContextBuilderAdapter

_ENVIRONMENT_TEXT = "<environment_context>safe</environment_context>"


@dataclass
class _FakeCompaction:
    """返回固定消息列表的上下文压缩 fake。"""

    messages: list[BaseMessage]

    async def compact(
        self,
        messages: list[BaseMessage],
        *,
        model_access: ModelAccessPort | None = None,
        model: str | None = None,
    ) -> ContextCompactionResult:
        """返回预设压缩结果，不修改调用方传入的消息。"""
        del model_access, model
        return ContextCompactionResult(
            messages=messages if self.messages is messages else self.messages,
            usage={},
            summary_created=False,
        )


class _FakeEnvironmentProvider:
    """返回固定环境上下文文本的 provider fake。"""

    def build(self) -> str:
        """返回安全环境上下文文本。"""
        return _ENVIRONMENT_TEXT


_safe_text = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",)),
    min_size=1,
    max_size=40,
).filter(
    lambda value: "<environment_context>" not in value and "context_kind=environment" not in value
)

_tool_name = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N")),
    min_size=1,
    max_size=20,
)
_tool_call_id = st.text(
    alphabet=st.characters(whitelist_categories=("L", "N", "P")),
    min_size=0,
    max_size=20,
)


@st.composite
def _message_strategy(draw: st.DrawFn) -> BaseMessage:
    """生成上下文构建支持的领域消息。"""
    role = draw(st.sampled_from(["system", "user", "assistant", "tool"]))
    content = draw(_safe_text)
    if role == "system":
        return SystemMessage(content=content)
    if role == "user":
        return UserMessage(content=content)
    if role == "assistant":
        return AssistantMessage(content=content)
    return ToolMessage(
        content=content,
        tool_name=draw(_tool_name),
        tool_call_id=draw(_tool_call_id),
    )


_message_list = st.lists(_message_strategy(), min_size=0, max_size=12)


def _fingerprint(message: BaseMessage) -> tuple[str, str, str]:
    """提取足以验证消息相对顺序的稳定标识。"""
    if isinstance(message, ToolMessage):
        return (message.role, message.content, message.tool_call_id)
    return (message.role, message.content, "")


def _result_message_fingerprint(message: BaseMessage) -> tuple[str, str, str]:
    """提取结果消息中的顺序标识。"""
    tool_call_id = getattr(message, "tool_call_id", "")
    return (message.role, message.content, str(tool_call_id) if tool_call_id else "")


@settings(max_examples=80)
@given(
    source_messages=_message_list,
    generated_messages=_message_list,
    return_source=st.booleans(),
)
async def test_environment_inserted_after_last_system_and_preserves_non_system_order(
    source_messages: list[BaseMessage],
    generated_messages: list[BaseMessage],
    return_source: bool,
) -> None:
    """Property 2：环境消息位于最后一条 system 后，非 system 相对顺序不变。"""
    compacted_messages = source_messages if return_source else generated_messages
    adapter = ContextBuilderAdapter(
        compaction=_FakeCompaction(compacted_messages),
        environment_provider=_FakeEnvironmentProvider(),
    )

    result = await adapter.build(source_messages)

    result_messages = result.messages
    environment_indexes = [
        index
        for index, message in enumerate(result_messages)
        if message.role == "system" and message.content == _ENVIRONMENT_TEXT
    ]
    assert environment_indexes == [
        max(
            (
                index + 1
                for index, message in enumerate(compacted_messages)
                if message.role == "system"
            ),
            default=0,
        )
    ]

    expected_non_system = [
        _fingerprint(message) for message in compacted_messages if message.role != "system"
    ]
    actual_non_system = [
        _result_message_fingerprint(message)
        for message in result_messages
        if message.role != "system"
    ]
    assert actual_non_system == expected_non_system


@settings(max_examples=80)
@given(messages=_message_list)
async def test_environment_context_never_persisted_in_conversation_context_dict(
    messages: list[BaseMessage],
) -> None:
    """Property 3：环境上下文不写入 ConversationContext.to_dict()。"""
    context = ConversationContext()
    context.replace_messages(messages)
    adapter = ContextBuilderAdapter(
        compaction=_FakeCompaction(context.get_messages()),
        environment_provider=_FakeEnvironmentProvider(),
    )

    result = await adapter.build(context.get_messages())
    persisted = str(context.to_dict())

    assert result.environment_injected is True
    assert any(
        message.role == "system" and message.content == _ENVIRONMENT_TEXT
        for message in result.messages
    )
    assert "<environment_context>" not in persisted
    assert "context_kind=environment" not in persisted
