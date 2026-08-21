"""就绪状态聚合器。

负责汇总所有 HealthCheckPort 的检查结果并生成最终就绪状态。
聚合逻辑为纯业务规则：所有检查均为 UP 时整体为 UP，任一为 DOWN 时整体为 DOWN。
该模块位于领域层，不依赖任何基础设施层或应用层模块。
"""

from domain.health.ports import HealthCheckPort
from domain.health.value_objects import HealthCheckResult, HealthStatus, ReadinessResult


class ReadinessAggregator:
    """就绪状态聚合器。

    接收一组 HealthCheckPort 实例，依次执行每个实例的 check 方法，
    并将所有检查结果聚合为一个 ReadinessResult。

    聚合规则：
    - 所有检查结果状态均为 UP → 整体状态为 UP
    - 任意一个检查结果状态为 DOWN → 整体状态为 DOWN
    - 无论整体状态如何，返回所有逐项检查结果
    """

    def __init__(self, checks: list[HealthCheckPort]) -> None:
        """初始化聚合器。

        Args:
            checks: 健康检查端口实例列表，每个实例对应一个外部依赖的检查
        """
        self._checks = checks

    @property
    def checks(self) -> tuple[HealthCheckPort, ...]:
        """返回按执行顺序配置的健康检查。"""
        return tuple(self._checks)

    async def check_readiness(self) -> ReadinessResult:
        """执行所有健康检查并聚合结果。

        依次调用每个 HealthCheckPort 的 check 方法，收集所有检查结果后
        根据聚合规则计算整体状态。即使某个检查为 DOWN，仍继续执行剩余检查，
        确保返回完整的逐项结果。

        Returns:
            包含整体状态和逐项检查结果的 ReadinessResult
        """
        results: list[HealthCheckResult] = []
        for check in self._checks:
            result = await check.check()
            results.append(result)

        overall = HealthStatus.UP
        if any(r.status == HealthStatus.DOWN for r in results):
            overall = HealthStatus.DOWN

        return ReadinessResult(status=overall, checks=tuple(results))
