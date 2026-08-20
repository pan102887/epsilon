"""MySQL 健康检查适配器。

实现 HealthCheckPort，通过 SELECT 1 命令检测数据库连通性。
超时时长从 DatabaseConfig.health_check_timeout 配置项动态读取，支持配置热刷新。
所有异常均被捕获并转化为 DOWN 状态，健康检查本身不会抛出异常。
"""

import asyncio
import logging

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from domain.health.ports import HealthCheckPort
from domain.health.value_objects import HealthCheckResult, HealthStatus
from infrastructure.database.database_config import database_config

logger = logging.getLogger(__name__)


class MysqlHealthCheckAdapter(HealthCheckPort):
    """MySQL 健康检查适配器。

    通过执行 SELECT 1 命令检测 MySQL 数据库的连通性。
    使用 asyncio.wait_for 包裹 SELECT 1 调用，
    超时时长从 database_config.health_check_timeout 动态读取。
    捕获 TimeoutError、SQLAlchemyError 和通用 Exception，均返回 DOWN 状态并携带 reason。
    """

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        """初始化适配器。

        Args:
            session_factory: 已初始化的异步会话工厂
        """
        self._session_factory = session_factory

    async def check(self) -> HealthCheckResult:
        """执行 MySQL SELECT 1 检查。

        通过 asyncio.wait_for 包裹 SELECT 1 命令，超时时长从
        database_config.health_check_timeout 动态读取，支持配置热刷新。
        捕获所有可能的异常并转化为 DOWN 状态，确保健康检查不会抛出异常。

        Returns:
            MySQL 连通性检查结果，name 固定为 "mysql"
        """
        timeout = database_config.health_check_timeout
        try:
            async with self._session_factory() as session:
                await asyncio.wait_for(
                    session.execute(text("SELECT 1")),
                    timeout=timeout,
                )
            return HealthCheckResult(name="mysql", status=HealthStatus.UP)
        except TimeoutError:
            reason = f"MySQL SELECT 1 超时（>{timeout}s）"
            logger.warning(reason)
            return HealthCheckResult(name="mysql", status=HealthStatus.DOWN, reason=reason)
        except SQLAlchemyError as e:
            reason = f"MySQL 连接异常: {e}"
            logger.warning(reason)
            return HealthCheckResult(name="mysql", status=HealthStatus.DOWN, reason=str(e))
        except Exception as e:
            reason = f"MySQL 健康检查未知异常: {e}"
            logger.error(reason)
            return HealthCheckResult(name="mysql", status=HealthStatus.DOWN, reason=str(e))
