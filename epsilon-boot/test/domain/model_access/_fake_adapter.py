"""端口级 FakeModelAccessAdapter，用于不依赖真实 Provider 的单元/属性测试。"""

from collections.abc import AsyncIterator, Callable

from domain.chat.context import BaseMessage
from domain.model_access.value_objects import ChatRequest, LLMResponse, StreamingChunk


def _default_count(messages: list[BaseMessage]) -> int:
    return sum(len(message.content or "") for message in messages)


class FakeModelAccessAdapter:
    """实现 ModelAccessPort 协议最小子集的测试 fake。

    - ``count_tokens``：默认按消息 content 字符长度累加，可通过 ``count_fn`` 注入自定义逻辑。
    - ``chat`` / ``stream``：默认抛出 AssertionError 提示测试未配置，可通过 monkeypatch 覆盖。
    """

    def __init__(
        self,
        *,
        count_fn: Callable[[list[BaseMessage]], int] | None = None,
    ) -> None:
        self._count_fn: Callable[[list[BaseMessage]], int] = count_fn or _default_count

    def count_tokens(self, messages: list[BaseMessage]) -> int:
        """估算 token 数，默认按字符长度。"""
        return self._count_fn(messages)

    async def chat(self, request: ChatRequest) -> LLMResponse:
        """未配置时抛出，需测试侧 monkeypatch。"""
        raise AssertionError("FakeModelAccessAdapter.chat 未配置 — 请 monkeypatch")

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        """未配置时抛出，需测试侧 monkeypatch。"""
        raise AssertionError("FakeModelAccessAdapter.stream 未配置 — 请 monkeypatch")
        yield  # type: ignore[misc]  # make it a generator
