"""公共模块包。

提供跨层共享的配置基类、异常定义等工具。
"""

from .configuration import ConfigurationError, PropertiesBaseSettings
from .exceptions import BizException

__all__ = [
    "BizException",
    "ConfigurationError",
    "PropertiesBaseSettings",
]
