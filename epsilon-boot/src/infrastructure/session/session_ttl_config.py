"""Redis 会话 TTL 配置。

本模块集中管理 Redis 会话上下文与会话索引共用的 TTL，避免 context 与
index 生命周期分裂。配置项对应 ``SESSION_REDIS_TTL_SECONDS``，来源优先为
``config.properties``，可由环境变量覆盖。
"""

from typing import ClassVar

from pydantic import model_validator
from pydantic_settings import SettingsConfigDict

from common.configuration import ConfigurationError, PropertiesBaseSettings, create_config


class SessionRedisTtlConfig(PropertiesBaseSettings):
    """Redis 会话 context 与 session index 的 TTL 配置。

    Attributes:
        ttl_seconds: Redis 会话上下文与会话索引 key 的过期秒数，必须大于 0。
    """

    hot_reload: ClassVar[bool] = False

    model_config = SettingsConfigDict(env_prefix="SESSION_REDIS_")

    ttl_seconds: int = 3600

    @model_validator(mode="after")
    def _validate(self) -> "SessionRedisTtlConfig":
        """校验 Redis 会话 TTL 配置。"""
        if self.ttl_seconds <= 0:
            raise ConfigurationError("SESSION_REDIS_TTL_SECONDS 必须 > 0")
        return self


session_redis_ttl_config = create_config(SessionRedisTtlConfig)
"""全局 Redis 会话 TTL 配置实例。"""
