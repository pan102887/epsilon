"""
数据库会话提供器适配器

本模块实现 SessionProviderPort 端口，提供基于 SQLAlchemy AsyncSession 的会话管理。
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from infrastructure.database.ports import SessionProviderPort


class SessionProviderAdapter(SessionProviderPort):
    """
    数据库会话提供器适配器

    实现 SessionProviderPort 端口，通过 SQLAlchemy async_sessionmaker 提供会话管理。

    职责：
    - 创建和管理 AsyncSession 生命周期
    - 自动处理事务提交和回滚
    - 确保会话资源正确释放

    事务管理策略：
    - 正常退出：自动 commit 事务
    - 异常退出：自动 rollback 事务并重新抛出异常
    - 最终：无论成功或失败都 close 会话
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]):
        """
        初始化会话提供器适配器

        Args:
            session_factory: SQLAlchemy async_sessionmaker 实例，用于创建 AsyncSession
        """
        self._session_factory = session_factory

    @asynccontextmanager
    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """
        获取数据库会话的异步上下文管理器

        实现 SessionProviderPort.session() 方法。

        生命周期管理：
        1. 进入上下文：通过 session_factory 创建新的 AsyncSession
        2. 正常退出：调用 session.commit() 提交事务
        3. 异常退出：调用 session.rollback() 回滚事务，然后重新抛出异常
        4. 最终清理：调用 session.close() 关闭会话

        使用示例:
            async with session_provider.session() as session:
                user = User(name="Alice")
                session.add(user)
                # 正常退出时自动 commit

        Yields:
            AsyncSession: SQLAlchemy 异步会话实例

        Raises:
            Exception: 会话操作过程中的任何异常都会在 rollback 后重新抛出
        """
        session = self._session_factory()
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
