"""健康检查领域值对象。

定义健康检查结果的不可变数据结构，用于表示单个依赖的检查结果
以及就绪探针的聚合结果。
"""

from dataclasses import dataclass
from enum import Enum


class HealthStatus(Enum):
    """健康状态枚举。

    表示依赖或整体应用的健康状态，仅有 UP（正常）和 DOWN（异常）两种取值。
    """

    UP = "UP"
    DOWN = "DOWN"


@dataclass(frozen=True)
class HealthCheckResult:
    """单个依赖的健康检查结果。

    不可变数据类，记录某个外部依赖（如 Redis）的连通性检查结果。

    Attributes:
        name: 依赖名称，如 "redis"
        status: 健康状态，UP 或 DOWN
        reason: 失败原因，仅在 status 为 DOWN 时有值
    """

    name: str
    status: HealthStatus
    reason: str | None = None


@dataclass(frozen=True)
class ReadinessResult:
    """就绪探针聚合结果。

    不可变数据类，汇总所有依赖的健康检查结果，生成整体就绪状态。
    使用 tuple 而非 list 存储检查结果，强化不可变语义。

    Attributes:
        status: 整体健康状态，全部 UP 则 UP，任一 DOWN 则 DOWN
        checks: 各依赖的逐项检查结果
    """

    status: HealthStatus
    checks: tuple[HealthCheckResult, ...]
