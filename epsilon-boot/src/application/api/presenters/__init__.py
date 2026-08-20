"""HTTP/API 响应 presenter 包。

本包承载应用层面向 HTTP 边界的 response body 映射函数，不属于领域层，
也不向领域对象引入 FastAPI、Pydantic 或基础设施序列化依赖。
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import health_presenter as health_presenter
    from . import task_presenter as task_presenter

__all__ = [
    "health_presenter",
    "task_presenter",
]
