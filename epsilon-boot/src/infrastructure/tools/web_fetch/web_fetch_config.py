"""Web 抓取工具配置模块。

基于 pydantic-settings，从 config.properties 和环境变量加载以 ``WEB_FETCH_`` 为前缀的配置项。

包含请求超时时间、响应体大小上限和工具启用开关三项配置。
模块级实例 ``web_fetch_config`` 通过 ``create_config`` 工厂函数创建。
"""

from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config


class WebFetchConfig(PropertiesBaseSettings):
    """Web 抓取工具配置，对应环境变量前缀 ``WEB_FETCH_``。

    Attributes:
        timeout: 默认请求超时秒数，对应 ``WEB_FETCH_TIMEOUT``，默认 ``30``。
        max_response_size:
            响应体大小上限（字节），对应 ``WEB_FETCH_MAX_RESPONSE_SIZE``，默认 ``51200``（50KB）。
        enabled: 工具启用开关，对应 ``WEB_FETCH_ENABLED``，默认 ``True``。
    """

    model_config = SettingsConfigDict(env_prefix="WEB_FETCH_")

    timeout: int = 30
    max_response_size: int = 51200
    enabled: bool = True


web_fetch_config = create_config(WebFetchConfig)
"""全局 Web 抓取工具配置实例，通过工厂函数创建。"""
