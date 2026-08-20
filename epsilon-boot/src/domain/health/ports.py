"""健康检查领域端口定义。

定义健康检查的抽象接口，由 Infrastructure 层实现。
领域层不引用任何基础设施层模块，通过 Protocol 实现依赖反转。
"""

from typing import Protocol

from domain.health.value_objects import HealthCheckResult


class HealthCheckPort(Protocol):
    """健康检查端口接口。

    每个实现对应一个外部依赖的连通性检测（如 Redis、数据库等）。
    实现类需提供异步 check 方法，返回包含依赖名称、状态和
    可选失败原因的 HealthCheckResult 值对象。
    """

    async def check(self) -> HealthCheckResult:
        """执行健康检查并返回结果。

        Returns:
            包含依赖名称、状态和可选失败原因的检查结果
        """
        ...
