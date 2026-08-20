"""会话领域端口定义。

定义会话上下文的持久化操作、聊天服务和上下文压缩等端口接口（Port），
遵循六边形架构原则，由 Infrastructure 层提供具体的适配器实现。
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Protocol, TypeVar

T = TypeVar("T")

if TYPE_CHECKING:
    from domain.agent.value_objects import AgentStreamEvent
    from domain.chat.context import BaseMessage, ConversationContext
    from domain.chat.value_objects import (
        ApprovalResumeRequestVO,
        ChatContinueRequestVO,
        ChatRequestVO,
        ChatResponseVO,
        ContextBuilderResult,
        ContextCompactionResult,
        SessionMetadata,
    )
    from domain.model_access.ports import ModelAccessPort
    from domain.model_access.value_objects import StreamingChunk


class SessionContextStorePort(Protocol):
    """会话上下文存储端口。

    定义会话上下文的持久化操作接口，由 Infrastructure 层提供具体实现。
    支持保存、加载和删除三种操作。
    """

    async def save(self, session_id: str, context: ConversationContext) -> None:
        """保存会话上下文。

        Args:
            session_id: 会话唯一标识符
            context: 对话上下文对象
        """
        ...

    async def load(self, session_id: str) -> ConversationContext:
        """加载会话上下文。

        Args:
            session_id: 会话唯一标识符

        Returns:
            对应的对话上下文，若不存在则返回空的 ConversationContext
        """
        ...

    async def delete(self, session_id: str) -> None:
        """删除会话上下文。

        Args:
            session_id: 会话唯一标识符
        """
        ...

    async def exists(self, session_id: str) -> bool:
        """判断指定会话上下文是否真实存在。

        Args:
            session_id: 会话唯一标识符。

        Returns:
            当底层后端存在该会话上下文时返回 ``True``；否则返回 ``False``。
            本方法不得加载完整上下文正文，也不得刷新 Redis TTL。
        """
        ...

    async def compare_and_swap(
        self,
        session_id: str,
        mutator: Callable[[ConversationContext], Awaitable[T]],
    ) -> T:
        """在乐观锁周期内原子地"读取-修改-提交"会话上下文。

        语义：加载当前会话上下文，在底层后端的原子保护期内调用 mutator，
        若提交时检测到写入冲突则由 adapter 内部按配置上限自动重试。

        Args:
            session_id: 会话唯一标识符。
            mutator: 异步修改回调，接收当前 ConversationContext 并就地修改；
                返回值原样透传给调用方。mutator 可能因冲突重试而被多次调用，
                必须保证幂等。

        Returns:
            mutator 的返回值。

        Raises:
            SessionConflictError: 重试上限耗尽仍发生写入冲突时抛出。
        """
        ...


class SessionIndexPort(Protocol):
    """会话索引端口。

    该端口提供会话发现、恢复校验和显式删除所需的轻量元数据索引能力。
    索引不是聊天主数据；调用方恢复会话时仍需通过
    ``SessionContextStorePort.exists`` 校验上下文真实存在。
    """

    async def upsert(self, metadata: SessionMetadata) -> None:
        """新增或更新会话元数据。

        Args:
            metadata: 会话列表和恢复提示使用的轻量元数据。
        """
        ...

    async def get(self, session_id: str) -> SessionMetadata | None:
        """按会话 ID 读取元数据。

        Args:
            session_id: 会话唯一标识符。

        Returns:
            命中的会话元数据；不存在时返回 ``None``。
        """
        ...

    async def list_recent(self, limit: int = 20) -> list[SessionMetadata]:
        """按更新时间倒序列出最近会话。

        Args:
            limit: 返回数量上限。

        Returns:
            最近会话元数据列表。
        """
        ...

    async def delete(self, session_id: str) -> None:
        """幂等删除指定会话索引项。

        Args:
            session_id: 会话唯一标识符。
        """
        ...


class ChatServicePort(Protocol):
    """聊天服务端口。

    定义聊天对话的标准操作接口，遵循六边形架构原则，
    由 Infrastructure 层提供具体的适配器实现（ChatServiceAdapter）。
    支持同步对话、流式对话和会话清除三种操作。
    """

    async def chat(self, request: ChatRequestVO) -> ChatResponseVO:
        """同步对话。

        接收用户的聊天请求，调用底层 LLM 模型获取完整回复后一次性返回。
        适用于不需要实时展示生成过程的场景。

        Args:
            request: 聊天请求值对象，包含会话标识、用户消息和响应模式。

        Returns:
            聊天响应值对象，包含模型回复内容、模型名称和 token 用量信息。

        Raises:
            ModelAccessError: 当模型调用失败时向上传播。
        """
        ...

    async def resume_approval(
        self,
        request: ApprovalResumeRequestVO,
    ) -> ChatResponseVO:
        """提交审批决策并恢复聊天执行。

        Args:
            request: 审批恢复请求值对象，包含会话、审批批次和有序决策。

        Returns:
            聊天响应值对象，可能为 completed 或新的 approval_required。
        """
        ...

    def stream_resume_approval(
        self,
        request: ApprovalResumeRequestVO,
    ) -> AsyncIterator[AgentStreamEvent]:
        """提交审批决策并以结构化事件流恢复聊天执行。

        与 ``stream_chat_events`` 对称：恢复自然完成时依次产出
        ``assistant_delta`` / ``assistant_done``；恢复后再次触发工具审批
        中断时产出新的 ``kind="approval_required"`` 事件（metadata 携带新的
        session_id / approval_id / 动作摘要）。决策应用与校验复用既有
        ``resume_approval`` 内核，不在本方法重复实现 approve/edit/reject。

        Args:
            request: 审批恢复请求值对象，包含会话、审批批次和有序决策。

        Returns:
            异步迭代器，逐个产出 AgentStreamEvent。
        """
        ...

    async def continue_chat(
        self,
        request: ChatContinueRequestVO,
    ) -> ChatResponseVO:
        """基于已有会话上下文继续聊天 Agent 执行。

        继续请求不追加新的用户消息，仅复用已保存的 ConversationContext
        进入下一段 Agent_Run。

        Args:
            request: 聊天继续请求值对象，包含会话标识和可选模型。

        Returns:
            聊天响应值对象，可能为 completed、paused 或 approval_required。
        """
        ...

    def stream_chat(self, request: ChatRequestVO) -> AsyncIterator[StreamingChunk]:
        """流式对话。

        接收用户的聊天请求，通过异步迭代器逐个产出流式响应分片，
        适用于需要实时展示 AI 生成过程的场景。

        Args:
            request: 聊天请求值对象，包含会话标识、用户消息和响应模式。

        Returns:
            异步迭代器，逐个产出 StreamingChunk 分片，最后一个分片的 finished 为 True。

        Raises:
            ModelAccessError: 当模型调用失败时停止产出并向上传播。
        """
        ...

    def stream_chat_events(self, request: ChatRequestVO) -> AsyncIterator[AgentStreamEvent]:
        """Stream structured chat events for interactive clients.

        Text-only API and SSE callers remain on ``stream_chat``. CLI/TUI clients
        can use this stream to render tool calls, status updates, and assistant
        deltas as separate UI elements.
        """
        ...

    def stream_continue_chat_events(
        self,
        request: ChatContinueRequestVO,
    ) -> AsyncIterator[AgentStreamEvent]:
        """基于已有会话上下文继续执行并产出结构化事件流。

        HTTP SSE 继续入口使用该方法，入口处应先校验继续前置条件；
        校验失败时抛出业务异常而不是产出错误事件。
        """
        ...

    def stream_segmented_chat_events(
        self,
        request: ChatRequestVO,
    ) -> AsyncIterator[AgentStreamEvent]:
        """执行分段聊天并产出结构化事件流。"""
        ...

    def stream_segmented_continue_chat_events(
        self,
        request: ChatContinueRequestVO,
    ) -> AsyncIterator[AgentStreamEvent]:
        """基于已有会话继续分段聊天并产出结构化事件流。"""
        ...

    async def clear_session(self, session_id: str) -> None:
        """清除会话上下文。

        删除指定会话的全部对话历史，使用户可以开始新的对话。

        Args:
            session_id: 会话唯一标识符。
        """
        ...

    @property
    def prompt_id(self) -> str:
        """当前加载的 Prompt 标识符（形如 ``chat-default@v1``）。

        供路由层在 SSE 流结束时发送 ``prompt_id`` 事件使用。
        """
        ...


class ContextCompactionPort(Protocol):
    """上下文压缩端口。

    定义将完整消息列表压缩为适合发送给模型的结构化结果的标准操作。
    由基础设施层提供具体的压缩策略实现（如滑动窗口、Token 触发、LLM 摘要等）。
    """

    async def compact(
        self,
        messages: list[BaseMessage],
        *,
        model_access: ModelAccessPort | None = None,
        model: str | None = None,
    ) -> ContextCompactionResult:
        """压缩消息列表并返回结构化结果。

        对完整的对话消息列表执行压缩操作，返回适合发送给模型的消息、
        摘要调用 usage 和摘要创建标记。``model_access`` / ``model`` 用于
        LLM 摘要策略，滑动窗口策略可以忽略。保存完整历史不属于端口职责。

        Args:
            messages: 完整的消息列表，包含所有角色（system/user/assistant/tool）的消息
            model_access: 当前请求已解析出的模型访问端口，供摘要策略复用
            model: 当前请求已解析出的模型名称，供摘要策略复用

        Returns:
            上下文压缩结果，包含压缩后的消息列表和摘要 usage。
        """
        ...


class ContextBuilderPort(Protocol):
    """上下文构建端口。

    定义从完整会话历史构建单次模型调用序列化消息的业务能力边界。
    端口不负责保存历史，也不接收或变更工具 schema；``model_access`` /
    ``model`` 仅用于透传给底层上下文压缩策略。
    """

    async def build(
        self,
        messages: list[BaseMessage],
        *,
        model_access: ModelAccessPort | None = None,
        model: str | None = None,
    ) -> ContextBuilderResult:
        """构建单次模型调用使用的序列化消息列表。

        Args:
            messages: 完整的会话消息快照，不应在构建过程中被写入或修改。
            model_access: 当前请求已解析出的模型访问端口，仅供压缩策略复用。
            model: 当前请求已解析出的模型名称，仅供压缩策略复用。

        Returns:
            上下文构建结果，包含可直接发送给模型的序列化消息和构建阶段 usage。
        """
        ...
