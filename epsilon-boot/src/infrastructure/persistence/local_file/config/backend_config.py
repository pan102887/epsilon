"""会话后端选择配置。

对应 ``SESSION_STORE_*`` 前缀；取值仅 ``redis`` / ``file``，默认 ``file``。
本期明确**不**引入 ``EVENT_STORE_BACKEND``（领域事件基础设施已移除）。

需求：5.1、5.2、6.1。
"""

from enum import StrEnum
from typing import ClassVar

from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config


class SessionStoreBackendKind(StrEnum):
    """会话存储后端枚举。

    本期允许取值：

    - ``REDIS``：沿用 ``RedisSessionContextAdapter``；
    - ``FILE``：新增 ``LocalFileSessionContextAdapter``（默认）。
    """

    REDIS = "redis"
    FILE = "file"


class SessionStoreConfig(PropertiesBaseSettings):
    """对应 ``SESSION_STORE_*`` 前缀。

    Attributes:
        backend: 会话后端种类，默认 ``FILE``（零配置启动时的默认行为）。
    """

    hot_reload: ClassVar[bool] = False

    model_config = SettingsConfigDict(env_prefix="SESSION_STORE_")

    backend: SessionStoreBackendKind = SessionStoreBackendKind.FILE


session_store_config = create_config(SessionStoreConfig)
"""全局会话后端配置实例；进程生命周期内不可变。"""
