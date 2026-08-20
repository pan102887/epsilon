"""Redis 连接配置模块。

基于 pydantic-settings，从 .env 文件和环境变量加载以 ``REDIS_`` 为前缀的配置项。

仅包含 Redis 连接参数，不包含业务级配置（如 session TTL）。
模块级实例 ``redis_config`` 通过 ``create_config`` 工厂函数创建，
启用热更新后会自动感知配置文件变更并重新加载。
"""

from typing import ClassVar

from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config


class RedisConfig(PropertiesBaseSettings):
    """Redis 连接配置，对应环境变量前缀 ``REDIS_``。

    通过 ``hot_reload = True`` 启用配置热更新，配置文件变更后自动重新加载。

    Attributes:
        host: Redis 服务地址，对应 ``REDIS_HOST``，默认 ``localhost``。
        port: Redis 服务端口，对应 ``REDIS_PORT``，默认 ``6379``。
        password: Redis 密码，对应 ``REDIS_PASSWORD``，默认空字符串。
        db: Redis 数据库编号，对应 ``REDIS_DB``，默认 ``0``。
        health_check_timeout:
            健康检查超时时间（秒），对应 ``REDIS_HEALTH_CHECK_TIMEOUT``，默认 ``3``。
    """

    hot_reload: ClassVar[bool] = True

    model_config = SettingsConfigDict(env_prefix="REDIS_")

    host: str = "localhost"
    port: int = 6379
    password: str = ""
    db: int = 0
    health_check_timeout: int = 3


redis_config = create_config(RedisConfig)
"""全局 Redis 配置实例，通过工厂函数创建，支持热更新。"""
