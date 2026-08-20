"""聊天会话上下文 workflow。

该模块承载聊天入口共享的会话上下文加载、会话标识写入、系统提示词幂等
注入，以及上下文保存后刷新会话索引的应用层编排。Prompt 文件加载、Workspace
guidance 追加、模型解析和流式协议包装仍由基础设施适配器负责。
"""

from __future__ import annotations

import time

from domain.chat.context import ConversationContext
from domain.chat.ports import SessionContextStorePort, SessionIndexPort
from domain.chat.value_objects import (
    ChatContinueRequestVO,
    ChatRequestVO,
    SessionMetadata,
)


class ChatSessionContextWorkflow:
    """聊天会话上下文应用层 workflow。

    该 workflow 只处理会话状态本身：加载上下文、写入 ``session_id``、
    幂等注入已加载好的系统提示词、追加首轮用户消息、保存上下文并刷新
    ``SessionIndexPort``。它不读取 prompt 文件，不解析模型，也不处理流式事件。
    """

    def __init__(
        self,
        session_store: SessionContextStorePort,
        session_index: SessionIndexPort | None,
        system_prompt: str,
        prompt_id: str,
    ) -> None:
        """初始化聊天会话 workflow。

        Args:
            session_store: 会话上下文存储端口。
            session_index: 可选会话索引端口；为空时只保存上下文。
            system_prompt: 已由基础设施加载并补齐 workspace guidance 的系统提示词。
            prompt_id: 当前系统提示词版本标识，用于响应和索引侧追踪。
        """

        self._session_store = session_store
        self._session_index = session_index
        self._system_prompt = system_prompt
        self._prompt_id = prompt_id

    @property
    def prompt_id(self) -> str:
        """返回当前 workflow 绑定的 Prompt 标识符。"""

        return self._prompt_id

    async def load_for_chat(self, request: ChatRequestVO) -> ConversationContext:
        """加载聊天上下文并追加本次用户消息。

        Args:
            request: 聊天请求值对象。

        Returns:
            已设置 ``session_id``、已确保 system prompt、并追加用户消息的上下文。
        """

        context = await self._session_store.load(request.session_id)
        context.session_id = request.session_id
        self.ensure_system_prompt(context)
        context.add_user_message(request.message)
        return context

    async def load_for_continue(self, request: ChatContinueRequestVO) -> ConversationContext:
        """加载继续执行所需的既有上下文。

        继续入口不追加用户消息，也不重新注入系统提示词；是否可继续由应用服务
        或调用方基于上下文尾部消息判断。

        Args:
            request: 聊天继续请求值对象。

        Returns:
            已设置 ``session_id`` 的既有上下文。
        """

        context = await self._session_store.load(request.session_id)
        context.session_id = request.session_id
        return context

    def ensure_system_prompt(self, context: ConversationContext) -> None:
        """在上下文缺少 system 消息时幂等注入系统提示词。

        Args:
            context: 需要检查和可能被原地修改的会话上下文。
        """

        if not any(message.role == "system" for message in context.get_messages()):
            context.add_system_message(self._system_prompt)

    async def save_context_and_index(
        self,
        session_id: str,
        context: ConversationContext,
        *,
        model: str | None = None,
    ) -> None:
        """保存完整上下文，并在配置索引时刷新轻量会话元数据。

        Args:
            session_id: 会话唯一标识。
            context: 待保存的完整会话上下文。
            model: 本次保存对应的实际模型名称；用于保持既有索引字段行为。
        """

        await self._session_store.save(session_id, context)
        if self._session_index is None:
            return

        updated_at_epoch_ms = int(time.time() * 1000)
        existing = await self._session_index.get(session_id)
        created_at_epoch_ms = (
            existing.created_at_epoch_ms
            if existing is not None and existing.created_at_epoch_ms is not None
            else updated_at_epoch_ms
        )
        await self._session_index.upsert(
            SessionMetadata(
                session_id=session_id,
                updated_at_epoch_ms=updated_at_epoch_ms,
                message_count=context.message_count,
                preview=self._build_session_preview(context),
                created_at_epoch_ms=created_at_epoch_ms,
                model=model,
            )
        )

    @staticmethod
    def _build_session_preview(context: ConversationContext) -> str:
        """从最后一条非 system 消息生成会话列表预览。"""

        for message in reversed(context.get_messages()):
            if message.role == "system":
                continue
            preview = " ".join(message.content.split())
            if preview:
                return preview[:120]
        return "(空会话)"
