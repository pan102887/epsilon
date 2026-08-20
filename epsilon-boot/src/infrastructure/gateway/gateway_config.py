"""网关连接配置模块。

基于 pydantic-settings，从 .env 文件和环境变量加载以 ``GATEWAY_`` 为前缀的配置项。

模块级实例 ``gateway_config`` 通过 ``create_config`` 工厂函数创建，
启用热更新后会自动感知配置文件变更并重新加载。
"""

from typing import ClassVar

from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config


class GatewayConfig(PropertiesBaseSettings):
    """网关连接配置，对应环境变量前缀 ``GATEWAY_``。

    通过 ``hot_reload = True`` 启用配置热更新，配置文件变更后自动重新加载。

    Attributes:
        base_url: 网关/Sidecar 基础地址，对应 ``GATEWAY_BASE_URL``，默认 ``http://localhost:5678``。
        timeout: 请求超时时间（秒），对应 ``GATEWAY_TIMEOUT``，默认 ``30``。
        max_retries: 最大重试次数，对应 ``GATEWAY_MAX_RETRIES``，默认 ``3``。
    """

    hot_reload: ClassVar[bool] = True

    model_config = SettingsConfigDict(env_prefix="GATEWAY_")

    base_url: str = "http://localhost:5678"
    timeout: int = 30
    max_retries: int = 3


gateway_config = create_config(GatewayConfig)
"""全局网关配置实例，通过工厂函数创建，支持热更新。"""
