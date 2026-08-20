"""本地持久化目录健康检查适配器。

实现 ``HealthCheckPort``，用于在 ``/health/ready`` 探针中验证
``LOCAL_PERSISTENCE_ROOT`` 目录的"可访问、可读写、可创建临时文件"三项
基本能力。

触发注册条件：仅当 ``SESSION_STORE_BACKEND=file`` 被实际装配时，组合根
才会把本适配器追加到 ``ReadinessAggregator`` 的检查列表中。Redis 后端
被选用时本适配器不会被构造（需求 6.3.5、7.4.5）。

需求：6.3.5、9.1。
"""

import logging
import os
import tempfile
from pathlib import Path

from domain.health.ports import HealthCheckPort
from domain.health.value_objects import HealthCheckResult, HealthStatus

logger = logging.getLogger(__name__)


class LocalPersistenceHealthCheckAdapter(HealthCheckPort):
    """本地持久化根目录的健康检查。

    检查链：``is_dir`` → ``os.access(R_OK | W_OK)`` →
    ``tempfile.NamedTemporaryFile(dir=root, prefix=".health-", delete=True)``
    一次 touch 写。任一环节失败均返回 ``HealthStatus.DOWN`` 并在
    ``reason`` 字段中给出中文原因；成功返回 ``HealthStatus.UP``。

    本适配器**不向上抛出**任何 ``OSError``；所有异常都翻译为 DOWN
    结果，以避免健康检查本身抛异常拖垮 ``/health/ready`` 响应链。
    """

    # 就绪探针里的固定名称；``ReadinessResult.to_dict`` 会用它做键。
    NAME: str = "local_persistence"

    def __init__(self, root: Path) -> None:
        """初始化适配器。

        Args:
            root: 本地持久化根目录的绝对路径（规范化后的结果）。
        """
        self._root = root

    async def check(self) -> HealthCheckResult:
        """执行本地持久化根目录健康检查。

        Returns:
            ``HealthCheckResult``：``status=UP`` 表示目录可读写且 touch
            测试通过；否则 ``status=DOWN`` 且 ``reason`` 为中文原因。
        """
        try:
            if not self._root.is_dir():
                return HealthCheckResult(
                    name=self.NAME,
                    status=HealthStatus.DOWN,
                    reason=f"LOCAL_PERSISTENCE_ROOT 不是目录：{self._root}",
                )
            if not (os.access(self._root, os.R_OK) and os.access(self._root, os.W_OK)):
                missing: list[str] = []
                if not os.access(self._root, os.R_OK):
                    missing.append("R")
                if not os.access(self._root, os.W_OK):
                    missing.append("W")
                return HealthCheckResult(
                    name=self.NAME,
                    status=HealthStatus.DOWN,
                    reason=(f"LOCAL_PERSISTENCE_ROOT 缺少 {'/'.join(missing)} 权限：{self._root}"),
                )
            # 轻量 touch 写验证
            with tempfile.NamedTemporaryFile(dir=str(self._root), prefix=".health-", delete=True):
                pass
            return HealthCheckResult(name=self.NAME, status=HealthStatus.UP)
        except OSError as exc:
            logger.warning("local_persistence 健康检查失败: %s", exc)
            return HealthCheckResult(
                name=self.NAME,
                status=HealthStatus.DOWN,
                reason=str(exc),
            )
