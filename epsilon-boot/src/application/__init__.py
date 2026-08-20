"""应用层适配器包的兼容导出。

顶层 ``application`` 包不在导入时创建 FastAPI app，也不配置 DI 容器。
需要兼容入口时，通过模块级 ``__getattr__`` 按需加载 ``app`` 与
``service_config``。
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .api.server_app import app as app
    from .api.server_config import service_config as service_config

__all__ = ["app", "service_config"]


def __getattr__(name: str):  # type: ignore[no-untyped-def]
    """按需加载历史兼容属性，避免普通子模块导入初始化 HTTP adapter。"""
    if name == "app":
        from .api.server_app import app

        return app
    if name == "service_config":
        from .api.server_config import service_config

        return service_config
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
