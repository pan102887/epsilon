"""容器数据模型。

定义依赖注入容器使用的核心数据类型，包括 Scope 枚举、Registration 数据类、
RegistryKey 类型别名以及辅助函数。
"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any


class Scope(Enum):
    """依赖的生命周期范围。"""

    SINGLETON = "singleton"
    TRANSIENT = "transient"


@dataclass
class Registration:
    """单个依赖的注册信息。"""

    provider: Callable[..., Any]
    scope: Scope
    is_async: bool


@dataclass
class AsyncResourceEntry:
    """异步资源的生命周期回调。"""

    name: str
    initializer: Callable[[], Any]
    cleanup: Callable[[], Any] | None


RegistryKey = type | tuple[type, str]
"""注册表键类型。

容器公开 API 统一生成 ``(type, str)`` 元组键；保留纯 ``type`` 联合项仅用于
兼容可能由旧代码直接构造的注册表键。同一个 Port 接口可以通过不同名称注册
多个 Adapter 实现。
"""


def default_dependency_name(abstract_type: type) -> str:
    """按 Spring JavaBeans 规则生成类型的默认依赖名称。

    普通 PascalCase 类型名将首字母小写，例如 ``ModelRegistryPort`` 转换为
    ``modelRegistryPort``。与 Spring 使用的 ``Introspector.decapitalize``
    一致，前两个字符均为大写的缩略词保持不变，例如 ``URLService``。
    """
    type_name = abstract_type.__name__
    if len(type_name) > 1 and type_name[0].isupper() and type_name[1].isupper():
        return type_name
    return type_name[:1].lower() + type_name[1:]


def make_registry_key(abstract_type: type, name: str | None = None) -> RegistryKey:
    """根据类型和可选名称构造注册表键。

    当 ``name`` 为 None 时，使用类型名称对应的 Spring 风格默认名称；
    当 ``name`` 不为 None 时保留显式名称。两种情况均返回 ``(type, name)``。

    Args:
        abstract_type: 抽象类型（Port 接口）。
        name: 可选的依赖名称。为 None 时使用类型的默认 lowerCamel 名称。

    Returns:
        ``(type, name)`` 元组。
    """
    if name is None:
        name = default_dependency_name(abstract_type)
    return abstract_type, name
