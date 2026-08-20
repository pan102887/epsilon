"""桩 ``SessionContextStorePort`` 实现。

本模块提供 :class:`InMemorySessionContextStore`：使用进程级 ``dict``
模拟会话上下文存储，供 Delegation_Correctness 指标在同一进程内读写
父子 Agent 会话上下文，规避 Redis / MySQL 等基础设施依赖。

结构类型匹配：
    以鸭子类型匹配 ``domain/chat/ports.py`` 中的
    ``SessionContextStorePort``：提供同名 ``async`` 方法
    ``save`` / ``load`` / ``delete`` / ``exists``，签名与返回类型一致。
    不继承 Protocol，不导入 ``infrastructure/``。

负载行为：
    :meth:`load` 在 ``session_id`` 不存在时返回一个空的
    :class:`ConversationContext`（与协议注释一致），避免评测样本因
    首次读写会话而需要显式判空。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.chat.context import ConversationContext


@dataclass
class InMemorySessionContextStore:
    """基于 ``dict`` 的内存会话上下文存储。

    Attributes:
        sessions: ``session_id`` 到 :class:`ConversationContext` 的映射；
            构造时默认为空字典，评测样本按需覆盖。
    """

    sessions: dict[str, ConversationContext] = field(default_factory=dict)

    async def save(self, session_id: str, context: ConversationContext) -> None:
        """保存会话上下文。

        Args:
            session_id: 会话唯一标识符。
            context: 对话上下文对象；按引用存入字典，调用方若在保存后
                继续变更该对象，存储内容亦会随之改变（与真实 Redis
                适配器的语义保持一致，评测样本应避免在保存后再原地
                修改上下文）。
        """

        self.sessions[session_id] = context

    async def load(self, session_id: str) -> ConversationContext:
        """加载会话上下文。

        Args:
            session_id: 会话唯一标识符。

        Returns:
            对应的 :class:`ConversationContext`；若会话不存在，返回一个
            全新的空 :class:`ConversationContext`，**不**自动写回字典。
        """

        if session_id in self.sessions:
            return self.sessions[session_id]
        return ConversationContext()

    async def delete(self, session_id: str) -> None:
        """删除会话上下文。

        Args:
            session_id: 会话唯一标识符。若不存在则静默返回，与真实
                存储一致。
        """

        self.sessions.pop(session_id, None)

    async def exists(self, session_id: str) -> bool:
        """判断会话上下文是否已保存。"""

        return session_id in self.sessions
