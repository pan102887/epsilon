"""配置管理模块。

基于 pydantic-settings 提供类型安全的配置加载能力，
支持从环境变量、config.properties 文件、.env 文件和 secrets 文件源自动注入配置值。

通过 ``ConfigProxy`` 代理类和 ``create_config`` 工厂函数，
支持基于文件 mtime 的配置热更新能力，对调用方完全透明。
"""

from .config_proxy import ConfigProxy, create_config
from .configuration_utils import (
    ConfigurationError,
    PropertiesBaseSettings,
    PropertiesFileSettingsSource,
)

__all__ = [
    "ConfigProxy",
    "ConfigurationError",
    "PropertiesBaseSettings",
    "PropertiesFileSettingsSource",
    "create_config",
]
