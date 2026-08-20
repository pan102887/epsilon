"""会话存储乐观锁配置。

对应 ``SESSION_REDIS_*`` 前缀；本期仅 ``SESSION_REDIS_CONFLICT_RETRY_MAX``。
配置项写入 ``epsilon-boot/config.properties``。
"""

from typing import ClassVar

from pydantic import model_validator
from pydantic_settings import SettingsConfigDict

from common.configuration import ConfigurationError, PropertiesBaseSettings, create_config


class SessionLockConfig(PropertiesBaseSettings):
    """会话乐观锁配置，对应 ``SESSION_REDIS_*`` 前缀。

    Attributes:
        conflict_retry_max: ``Session_Optimistic_Lock_Cycle`` 在
            ``RedisSessionContextAdapter.compare_and_swap`` 路径下的重试
            上限；对应 ``SESSION_REDIS_CONFLICT_RETRY_MAX``，默认 ``3``。
    """

    hot_reload: ClassVar[bool] = False

    model_config = SettingsConfigDict(env_prefix="SESSION_REDIS_")

    conflict_retry_max: int = 3

    @model_validator(mode="after")
    def _validate(self) -> "SessionLockConfig":
        """校验配置参数；非法值拒绝启动。"""
        if self.conflict_retry_max < 0:
            raise ConfigurationError("SESSION_REDIS_CONFLICT_RETRY_MAX 必须 >= 0")
        return self


session_lock_config = create_config(SessionLockConfig)
"""全局会话乐观锁配置实例。"""
