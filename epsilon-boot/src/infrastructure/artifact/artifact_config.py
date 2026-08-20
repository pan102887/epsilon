"""任务产物存储配置模块。

基于 pydantic-settings 从 ``config.properties``、环境变量和 ``.env`` 文件
加载以 ``ARTIFACT_`` 为前缀的配置项，与 ``TraceConfig`` 对称。
"""

from __future__ import annotations

from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config


class ArtifactConfig(PropertiesBaseSettings):
    """任务产物存储配置，对应环境变量前缀 ``ARTIFACT_``。

    Attributes:
        enabled: 是否启用任务产物持久化；禁用时工厂返回 None，写入方静默跳过。
    """

    model_config = SettingsConfigDict(env_prefix="ARTIFACT_")

    enabled: bool = True


artifact_config = create_config(ArtifactConfig)
"""模块级全局配置实例。"""
