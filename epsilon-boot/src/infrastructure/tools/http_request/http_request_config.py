"""HTTP 请求工具配置模块。

基于 pydantic-settings，从 config.properties 和环境变量加载以 ``HTTP_REQUEST_`` 为前缀的配置项。

包含请求超时时间、响应体大小上限和工具启用开关三项配置。
模块级实例 ``http_request_config`` 通过 ``create_config`` 工厂函数创建。
"""

from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config


class HttpRequestConfig(PropertiesBaseSettings):
    """HTTP 请求工具配置，对应环境变量前缀 ``HTTP_REQUEST_``。

    Attributes:
        timeout: 默认请求超时秒数，对应 ``HTTP_REQUEST_TIMEOUT``，默认 ``30``。
        max_response_size:
            响应体大小上限（字节），
            对应 ``HTTP_REQUEST_MAX_RESPONSE_SIZE``，默认 ``51200``（50KB）。
        enabled: 工具启用开关，对应 ``HTTP_REQUEST_ENABLED``，默认 ``True``。
    """

    model_config = SettingsConfigDict(env_prefix="HTTP_REQUEST_")

    timeout: int = 30
    max_response_size: int = 51200
    enabled: bool = True


http_request_config = create_config(HttpRequestConfig)
"""全局 HTTP 请求工具配置实例，通过工厂函数创建。"""
