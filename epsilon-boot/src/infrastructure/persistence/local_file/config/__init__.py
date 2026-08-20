"""本地文件持久化配置包。

对外导出 ``SessionStoreConfig`` / ``LocalPersistenceConfig`` 以及模块级
单例 ``session_store_config`` / ``local_persistence_config``；具体字段定义
请见各子模块。

本期明确**不**提供 ``EventStoreConfig``；领域事件基础设施已随
``Domain_Event_Decommission`` 清理。
"""

from .backend_config import (
    SessionStoreBackendKind,
    SessionStoreConfig,
    session_store_config,
)
from .local_persistence_config import (
    LocalPersistenceConfig,
    local_persistence_config,
)

__all__ = [
    "LocalPersistenceConfig",
    "SessionStoreBackendKind",
    "SessionStoreConfig",
    "local_persistence_config",
    "session_store_config",
]
