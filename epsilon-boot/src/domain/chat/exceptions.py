"""会话领域异常定义。

定义会话上下文存储相关的领域级异常。``SessionConflictError`` 用于在
``Session_Optimistic_Lock_Cycle`` 重试上限耗尽时让调用方感知并发冲突。
"""

from common.exceptions import BizException


class SessionConflictError(BizException):
    """会话写入冲突无法在重试上限内解决。

    ``RedisSessionContextAdapter`` 在 ``compare_and_swap`` 路径下检测到
    写入冲突并按 ``SESSION_REDIS_CONFLICT_RETRY_MAX`` 重试后仍失败时抛出。

    Attributes:
        session_id: 触发冲突的会话标识。
        retry_count: 实际重试次数（与配置上限相等时表示完全耗尽）。
    """

    def __init__(self, session_id: str, retry_count: int) -> None:
        super().__init__(
            code=60040,
            message=f"会话写入冲突重试 {retry_count} 次后仍失败",
        )
        self.session_id = session_id
        self.retry_count = retry_count


class ContinuationUnavailableError(BizException):
    """当前会话不满足继续执行前置条件。

    当聊天或任务继续入口无法从既有会话上下文恢复安全的下一段执行状态时
    抛出该异常。应用层应将其映射为客户端可见的冲突响应。

    Attributes:
        session_id: 请求继续执行的会话标识。
        reason: 不可继续的具体业务原因。
    """

    def __init__(self, session_id: str, reason: str) -> None:
        super().__init__(
            code=60041,
            message=f"当前会话不可继续执行：{reason}",
        )
        self.session_id = session_id
        self.reason = reason
