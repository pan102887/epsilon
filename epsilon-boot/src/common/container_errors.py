"""容器异常类。

定义依赖注入容器在注册、解析和 Provider 执行过程中可能抛出的异常。
所有异常均继承自 ``ContainerError`` 基类，便于统一捕获。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from common.container_models import RegistryKey


class ContainerError(Exception):
    """容器基础异常。"""

    pass


class DependencyNotRegisteredError(ContainerError):
    """解析未注册的依赖时抛出。

    包含未注册类型名和已注册类型列表。当解析命名依赖失败时，
    还会包含请求的名称和该类型下所有已注册的名称列表，帮助开发者快速定位问题。
    """

    def __init__(
        self,
        abstract_type: type,
        registered_types: list[type],
        *,
        name: str | None = None,
        registered_names: list[str] | None = None,
    ):
        self.abstract_type = abstract_type
        self.registered_types = registered_types
        self.name = name
        self.registered_names = registered_names

        if name is not None:
            names_display = registered_names if registered_names else []
            msg = (
                f"Type '{abstract_type.__name__}' with name '{name}' is not registered. "
                f"Registered names for '{abstract_type.__name__}': {names_display}"
            )
        else:
            type_names = [t.__name__ for t in registered_types]
            msg = (
                f"Type '{abstract_type.__name__}' is not registered. Registered types: {type_names}"
            )
        super().__init__(msg)


def _format_registry_key(key: RegistryKey) -> str:
    """将 RegistryKey 格式化为可读字符串。

    纯类型显示为类型名称，命名依赖显示为 ``TypeName(name)`` 格式。

    Args:
        key: 注册表键，可以是纯类型或 ``(type, name)`` 元组。

    Returns:
        格式化后的字符串。
    """
    if isinstance(key, tuple):
        return f"{key[0].__name__}({key[1]})"
    return key.__name__


class CircularDependencyError(ContainerError):
    """检测到循环依赖时抛出。

    包含完整的依赖链路径。支持 ``RegistryKey``，命名依赖在链路中
    显示为 ``TypeName(name)`` 格式。
    """

    def __init__(self, chain: list[RegistryKey]):
        self.chain = chain
        path = " → ".join(_format_registry_key(k) for k in chain)
        super().__init__(f"Circular dependency detected: {path}")


class ProviderError(ContainerError):
    """Provider 执行失败时抛出。包装原始异常并附加上下文。"""

    def __init__(self, abstract_type: type, provider: Callable, cause: Exception):
        self.abstract_type = abstract_type
        self.provider = provider
        self.cause = cause
        super().__init__(
            f"Provider for '{abstract_type.__name__}' ({provider.__qualname__}) failed: {cause}"
        )
