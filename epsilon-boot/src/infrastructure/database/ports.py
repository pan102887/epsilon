"""数据库基础设施端口定义。

本模块定义数据库会话提供器的基础设施端口接口。
这是纯技术层面的抽象，不属于领域层。
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession


class SessionProviderPort(Protocol):
    """数据库会话提供器端口。

    定义获取数据库会话的抽象接口。这是基础设施层的技术抽象，
    用于在 infrastructure 层内部解耦会话管理和具体使用方。

    会话生命周期管理：
    - 进入上下文时创建会话
    - 正常退出时自动提交事务
    - 异常退出时自动回滚事务
    - 最终关闭会话释放资源
    """

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        """获取数据库会话的异步上下文管理器。

        使用示例:
            async with session_provider.session() as session:
                result = await session.execute(select(User))
                # 正常退出时自动 commit
                # 异常退出时自动 rollback

        Yields:
            AsyncSession: SQLAlchemy 异步会话实例

        Raises:
            Exception: 会话操作过程中的任何异常都会在 rollback 后重新抛出
        """
        ...
