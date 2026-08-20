"""数据库连接配置模块。

基于 pydantic-settings，从 .env 文件和环境变量加载以 ``DB_`` 为前缀的配置项。

仅包含 MySQL 连接池参数和健康检查超时配置。
模块级实例 ``database_config`` 通过 ``create_config`` 工厂函数创建，
启用热更新后会自动感知配置文件变更并重新加载。
"""

from typing import ClassVar

from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config


class DatabaseConfig(PropertiesBaseSettings):
    """MySQL 数据库连接配置，对应环境变量前缀 ``DB_``。

    通过 ``hot_reload = True`` 启用配置热更新，配置文件变更后自动重新加载。

    Attributes:
        host: MySQL 服务地址，对应 ``DB_HOST``，默认 ``localhost``。
        port: MySQL 服务端口，对应 ``DB_PORT``，默认 ``3306``。
        username: 数据库用户名，对应 ``DB_USERNAME``，默认 ``root``。
        password: 数据库密码，对应 ``DB_PASSWORD``，默认空字符串。
        database: 数据库名称，对应 ``DB_DATABASE``，默认空字符串。
        pool_size: 连接池大小，对应 ``DB_POOL_SIZE``，默认 ``5``。
        max_overflow: 连接池最大溢出数，对应 ``DB_MAX_OVERFLOW``，默认 ``10``。
        pool_recycle: 连接回收时间（秒），对应 ``DB_POOL_RECYCLE``，默认 ``3600``。
        echo: 是否输出 SQL 日志，对应 ``DB_ECHO``，默认 ``False``。
        health_check_timeout: 健康检查超时时间（秒），对应 ``DB_HEALTH_CHECK_TIMEOUT``，默认 ``3``。
    """

    hot_reload: ClassVar[bool] = True

    model_config = SettingsConfigDict(env_prefix="DB_")

    host: str = "localhost"
    port: int = 3306
    username: str = "root"
    password: str = "root123"
    database: str = "mydb"
    pool_size: int = 5
    max_overflow: int = 10
    pool_recycle: int = 3600
    echo: bool = False
    health_check_timeout: int = 3


database_config = create_config(DatabaseConfig)
"""全局数据库配置实例，通过工厂函数创建，支持热更新。"""
