"""服务器配置模块。

基于 pydantic-settings，从 .env 文件和环境变量加载以 ``SERVER_`` 为前缀的配置项。

模块级实例 ``service_config`` 作为全局单例使用。
"""

from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings


class ServerConfig(PropertiesBaseSettings):
    """服务器运行参数配置，对应环境变量前缀 ``SERVER_``。

    Attributes:
        host: 监听地址，对应 ``SERVER_HOST``，默认 ``0.0.0.0``。
        port: 监听端口，对应 ``SERVER_PORT``，默认 ``7777``。
        debug: 是否开启调试模式，对应 ``SERVER_DEBUG``，默认 ``False``。
        workers: 工作进程数，对应 ``SERVER_WORKERS``，默认 ``1``。
    """

    model_config = SettingsConfigDict(env_prefix="SERVER_")

    host: str = "0.0.0.0"
    port: int = 7777
    debug: bool = False
    workers: int = 1


service_config = ServerConfig()
"""全局服务器配置实例，模块级单例。"""
