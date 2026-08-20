"""存储等级领域包。

本包定义产物存储的逻辑定位维度抽象，供 TraceStorePort / ArtifactStorePort
及其读写方使用：

- ``StorageTier``：产物存储等级枚举（USER / PROJECT / 预留 TENANT），
  见 ``storage_tier.py``。本包仅依赖标准库，不含任何物理路径或后端实现细节。
"""

from domain.storage.storage_tier import StorageTier

__all__ = [
    "StorageTier",
]
