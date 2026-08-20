"""TUI/CLI 本地文件日志配置模块。

基于 pydantic-settings 从 ``config.properties``、环境变量和 ``.env`` 文件
加载以 ``EPSILON_LOG_`` 为前缀的配置项，供本地文件日志装配
（``configure_local_file_logging``）读取。默认开启文件日志并落 USER tier
（ADR-0005 决策 2b）。
"""

from __future__ import annotations

from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings


class LogSinkConfig(PropertiesBaseSettings):
    """TUI/CLI 本地文件日志配置，对应环境变量前缀 ``EPSILON_LOG_``。

    Attributes:
        to_file: 是否把 TUI/CLI 日志写入本地文件；关闭时不装配文件日志 handler。
        level: 文件日志 handler 的日志级别（如 ``INFO``/``DEBUG``）。
        rotation_max_bytes: 单个日志文件轮转阈值（字节），默认 10 MiB。
        rotation_backup_count: 轮转保留的历史日志文件数量。
    """

    model_config = SettingsConfigDict(env_prefix="EPSILON_LOG_")

    to_file: bool = True
    level: str = "INFO"
    rotation_max_bytes: int = 10_485_760
    rotation_backup_count: int = 5
