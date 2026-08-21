"""上下文构建适配器模块。

本模块实现领域层 ``ContextBuilderPort``，集中编排上下文压缩与运行期
环境上下文注入。环境上下文仅作为本次模型输入的临时消息存在，不写入
``ConversationContext`` 或传入的消息列表。

输出 ``ContextBuilderResult.messages`` 为领域消息（``BaseMessage`` 子类）
列表，**不再**进行 OpenAI 协议字典化；具体协议转换由
``ModelAccessPort`` 的具体 adapter（如 ``OpenAICompatibleAdapter``）在
SDK 调用前自行完成。
"""

import logging

from domain.chat.context import BaseMessage, SystemMessage
from domain.chat.ports import ContextBuilderPort, ContextCompactionPort
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.ports import ModelAccessPort
from infrastructure.chat.environment_context_provider import (
    EnvironmentContextBuildError,
    EnvironmentContextProvider,
    UnsafeEnvironmentContextError,
)

logger = logging.getLogger(__name__)


class ContextBuilderAdapter(ContextBuilderPort):
    """上下文构建适配器。

    适配器负责复用既有上下文压缩端口生成压缩后历史，并在压缩结果上
    插入安全的临时环境上下文 system 消息，最终输出领域消息列表
    （``list[BaseMessage]``）。协议转换（领域消息 → OpenAI / Anthropic /
    Gemini 等具体 LLM 协议字典）由 ``ModelAccessPort`` 的具体 adapter
    在 SDK 调用前自行完成，本适配器**不**进行任何协议字典化。
    """

    def __init__(
        self,
        *,
        compaction: ContextCompactionPort,
        environment_provider: EnvironmentContextProvider,
    ) -> None:
        """初始化上下文构建适配器。

        Args:
            compaction: 上下文压缩端口，用于生成压缩后的历史输入。
            environment_provider: 环境上下文提供器，用于生成安全环境文本。
        """
        self._compaction = compaction
        self._environment_provider = environment_provider

    @property
    def compaction(self) -> ContextCompactionPort:
        return self._compaction

    @property
    def environment_provider(self) -> EnvironmentContextProvider:
        return self._environment_provider

    async def build(
        self,
        messages: list[BaseMessage],
        *,
        model_access: ModelAccessPort | None = None,
        model: str | None = None,
    ) -> ContextBuilderResult:
        """构建单次模型调用的领域消息列表。

        输出的 ``ContextBuilderResult.messages`` 为领域消息（``BaseMessage``
        子类）列表，**不再**进行 OpenAI 协议字典化；协议转换由
        ``ModelAccessPort`` 的具体 adapter 在 SDK 调用前自行完成。

        Args:
            messages: 完整会话历史快照。方法不会修改该列表。
            model_access: 当前请求模型访问端口，仅透传给压缩策略。
            model: 当前请求模型名称，仅透传给压缩策略。

        Returns:
            上下文构建结果，包含压缩并注入环境上下文后的领域消息列表、
            压缩 usage、摘要生成标记和环境上下文注入标记。

        Raises:
            UnsafeEnvironmentContextError: 环境上下文包含不安全内容时向上传播。
            EnvironmentContextBuildError: 环境上下文提供器普通失败时抛出。
        """
        compaction_result = await self._compaction.compact(
            messages,
            model_access=model_access,
            model=model,
        )
        compacted_messages = list(compaction_result.messages)

        try:
            environment_text = self._environment_provider.build()
        except UnsafeEnvironmentContextError:
            logger.warning(
                "环境上下文包含不安全内容",
                extra={
                    "reason_class": "UnsafeEnvironmentContextError",
                    "message_count": len(compacted_messages),
                    "environment_injected": False,
                },
            )
            raise
        except Exception as exc:
            logger.warning(
                "环境上下文生成失败",
                extra={
                    "reason_class": type(exc).__name__,
                    "message_count": len(compacted_messages),
                    "environment_injected": False,
                },
            )
            raise EnvironmentContextBuildError("环境上下文生成失败") from exc

        environment_injected = bool(environment_text)
        combined_messages = (
            self._insert_environment_context(compacted_messages, environment_text)
            if environment_injected
            else compacted_messages
        )
        return ContextBuilderResult(
            messages=combined_messages,
            usage=dict(compaction_result.usage),
            summary_created=compaction_result.summary_created,
            environment_injected=environment_injected,
        )

    def _insert_environment_context(
        self,
        messages: list[BaseMessage],
        environment_text: str,
    ) -> list[BaseMessage]:
        """把环境上下文插入最后一条 system 消息之后。

        Args:
            messages: 压缩后的消息列表。本方法会复制列表，不原地修改。
            environment_text: 环境上下文正文。

        Returns:
            插入临时环境上下文消息后的新消息列表。若没有 system 消息，
            环境上下文位于列表头部。
        """
        combined_messages = list(messages)
        environment_message = SystemMessage(
            content=environment_text,
            metadata={"context_kind": "environment"},
        )
        insert_at = 0
        for index, message in enumerate(combined_messages):
            if message.role == "system":
                insert_at = index + 1
        combined_messages.insert(insert_at, environment_message)
        return combined_messages
