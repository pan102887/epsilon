"""结构化 Agent 追踪配置模块。

基于 pydantic-settings 从 ``config.properties``、环境变量和 ``.env`` 文件
加载以 ``TRACE_`` 为前缀的配置项。
"""

from __future__ import annotations

from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config


class TraceConfig(PropertiesBaseSettings):
    """追踪配置，对应环境变量前缀 ``TRACE_``。

    Attributes:
        enabled: 是否启用结构化 Agent 追踪。
        store_dir: trace 文件存储目录，相对于进程 CWD。
    """

    model_config = SettingsConfigDict(env_prefix="TRACE_")

    enabled: bool = True
    store_dir: str = ".epsilon/traces"


trace_config = create_config(TraceConfig)
"""模块级全局配置实例。"""
