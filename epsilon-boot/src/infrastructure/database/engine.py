"""SQLAlchemy 2.0 异步引擎与会话工厂模块。

负责创建 AsyncEngine 和 async_sessionmaker，并提供 DI 容器所需的
异步初始化/清理回调。模块级变量 ``_engine`` 和 ``_session_factory``
在 ``_init_db()`` 中赋值，在 ``_cleanup_db()`` 中释放。

引擎创建时通过 ``SELECT 1`` 验证数据库连通性，失败则抛出异常以触发
DI 容器的 fail-fast 回滚机制。
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from infrastructure.database.database_config import DatabaseConfig, database_config

logger = logging.getLogger(__name__)

_engine: AsyncEngine | None = None
"""模块级异步引擎实例，由 ``_init_db()`` 创建，``_cleanup_db()`` 释放。"""

_session_factory: async_sessionmaker[AsyncSession] | None = None
"""模块级异步会话工厂实例，由 ``_init_db()`` 创建。"""


def create_db_engine(config: DatabaseConfig) -> AsyncEngine:
    """根据配置创建 SQLAlchemy 异步引擎。

    连接字符串格式为 ``mysql+aiomysql://{username}:{password}@{host}:{port}/{database}``，
    并将连接池参数传递给 ``create_async_engine``。

    Args:
        config: 数据库连接配置实例

    Returns:
        已配置连接池参数的 AsyncEngine 实例
    """
    url = (
        f"mysql+aiomysql://{config.username}:{config.password}"
        f"@{config.host}:{config.port}/{config.database}"
    )
    return create_async_engine(
        url,
        pool_size=config.pool_size,
        max_overflow=config.max_overflow,
        pool_recycle=config.pool_recycle,
        echo=config.echo,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """创建绑定到指定引擎的异步会话工厂。

    配置 ``expire_on_commit=False``，避免异步上下文中的延迟加载问题。

    Args:
        engine: 已创建的 AsyncEngine 实例

    Returns:
        绑定该引擎的 async_sessionmaker 实例
    """
    return async_sessionmaker(engine, expire_on_commit=False)


async def _init_db() -> None:
    """异步初始化回调：创建引擎和会话工厂，并验证数据库连通性。

    由 DI 容器通过 ``register_async_resource`` 在启动时调用。
    通过执行 ``SELECT 1`` 验证数据库连通性，若失败则抛出异常，
    触发容器的 fail-fast 回滚机制。

    Raises:
        Exception: 数据库连接失败时，将异常向上传播
    """
    global _engine, _session_factory
    _engine = create_db_engine(database_config)
    _session_factory = create_session_factory(_engine)

    # 连通性验证
    async with _engine.begin() as conn:
        await conn.execute(text("SELECT 1"))
    logger.info("Database engine initialized and connectivity verified")


async def _cleanup_db() -> None:
    """异步清理回调：释放引擎连接池资源。

    由 DI 容器通过 ``register_async_resource`` 在关闭时调用。
    调用 ``engine.dispose()`` 释放所有连接池资源。
    """
    global _engine, _session_factory
    if _engine:
        await _engine.dispose()
        logger.info("Database engine disposed")
    _engine = None
    _session_factory = None


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """获取模块级异步会话工厂实例。

    供 SessionProviderAdapter 和其他需要创建 AsyncSession 的组件使用。
    必须在 ``_init_db()`` 执行成功后调用。

    Returns:
        已初始化的 async_sessionmaker 实例

    Raises:
        RuntimeError: 若会话工厂尚未初始化
    """
    if _session_factory is None:
        raise RuntimeError("Session factory not initialized. Ensure _init_db() has been called.")
    return _session_factory
