"""LLM 语义摘要上下文压缩适配器。"""

import json
import logging

from domain.chat.context import BaseMessage, SystemMessage, UserMessage
from domain.chat.value_objects import ContextCompactionResult
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import ChatRequest
from domain.prompt.ports import PromptRegistryPort
from domain.prompt.value_objects import LoadedPrompt
from infrastructure.chat.sliding_window_compaction_adapter import (
    SlidingWindowCompactionAdapter,
)

logger = logging.getLogger(__name__)


class LLMSummaryCompactionAdapter:
    """基于 token 触发的 LLM 语义摘要上下文压缩适配器。

    token 计数职责由 ``ModelAccessPort.count_tokens`` 端口承担，由具体
    adapter 持有 tokenizer 实现，本类不再直接依赖 ``TokenCounter``。
    """

    def __init__(
        self,
        *,
        prompt_registry: PromptRegistryPort,
        trigger_tokens: int,
        keep_recent_messages: int,
        fallback: SlidingWindowCompactionAdapter,
    ) -> None:
        """初始化摘要压缩适配器并加载摘要 Prompt。"""
        if trigger_tokens <= 0:
            raise ValueError("trigger_tokens 必须为正整数")
        if keep_recent_messages <= 0:
            raise ValueError("keep_recent_messages 必须为正整数")
        self._prompt: LoadedPrompt = prompt_registry.get("context-summary")
        self._trigger_tokens = trigger_tokens
        self._keep_recent_messages = keep_recent_messages
        self._fallback = fallback

    async def compact(
        self,
        messages: list[BaseMessage],
        *,
        model_access: ModelAccessPort | None = None,
        model: str | None = None,
    ) -> ContextCompactionResult:
        """按 token 阈值压缩消息列表。"""
        if model_access is None:
            return await self._fallback_with_warning(
                messages,
                reason_class="ModelAccessMissing",
            )
        if model_access.count_tokens(messages) < self._trigger_tokens:
            return ContextCompactionResult(messages=list(messages))

        system_messages, earlier_messages, recent_messages = self._split_messages(messages)
        if not earlier_messages:
            return ContextCompactionResult(messages=list(messages))

        try:
            request = self._build_summary_request(earlier_messages, model=model)
            response = await model_access.chat(request)
        except Exception as exc:
            return await self._fallback_with_warning(
                messages,
                reason_class=type(exc).__name__,
            )

        if response.tool_calls:
            return await self._fallback_with_warning(
                messages,
                reason_class="SummaryToolCalls",
            )

        summary = response.content.strip()
        if not summary:
            return await self._fallback_with_warning(
                messages,
                reason_class="BlankSummary",
            )

        compacted = [
            *system_messages,
            SystemMessage(content=summary),
            *recent_messages,
        ]
        return ContextCompactionResult(
            messages=compacted,
            usage=dict(response.usage),
            summary_created=True,
        )

    def _split_messages(
        self,
        messages: list[BaseMessage],
    ) -> tuple[list[BaseMessage], list[BaseMessage], list[BaseMessage]]:
        """拆分 system 消息、待摘要旧消息和最近非 system 消息。"""
        system_messages = [message for message in messages if message.role == "system"]
        non_system_messages = [message for message in messages if message.role != "system"]
        recent_messages = non_system_messages[-self._keep_recent_messages :]
        earlier_messages = non_system_messages[: -self._keep_recent_messages]
        return system_messages, earlier_messages, recent_messages

    def _build_summary_request(
        self,
        messages: list[BaseMessage],
        *,
        model: str | None,
    ) -> ChatRequest:
        """构造摘要模型调用请求。

        摘要 prompt 中历史消息字符串化路径：使用 ``BaseMessage.to_dict()``
        作为领域自身序列化能力（不含 OpenAI ``tool_calls`` 嵌套等协议
        特化形态），再经 ``json.dumps`` 序列化为可读 JSON 写入摘要 user
        消息正文。``ChatRequest.messages`` 本身承载领域 ``SystemMessage`` /
        ``UserMessage`` 实例而非协议字典，与端口契约对齐——具体协议转换由
        ``ModelAccessPort`` 的具体 adapter 在 SDK 调用前自行完成。

        Args:
            messages: 待摘要的领域消息列表。
            model: 可选的摘要模型名称；``None`` 时由 adapter 使用默认模型。

        Returns:
            可直接交给 ``ModelAccessPort.chat`` 的 ``ChatRequest`` 实例。
        """
        serialized = [m.to_dict() for m in messages]
        content = json.dumps(serialized, ensure_ascii=False, indent=2)
        return ChatRequest(
            messages=[
                SystemMessage(content=self._prompt.content),
                UserMessage(content=content),
            ],
            model=model,
        )

    async def _fallback_with_warning(
        self,
        messages: list[BaseMessage],
        *,
        reason_class: str,
    ) -> ContextCompactionResult:
        """记录摘要失败 warning 并降级到滑动窗口策略。"""
        logger.warning(
            "上下文摘要压缩降级为滑动窗口",
            extra={
                "message_count": len(messages),
                "trigger_tokens": self._trigger_tokens,
                "reason_class": reason_class,
            },
        )
        return await self._fallback.compact(messages)
