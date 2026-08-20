"""基础设施层本地文件存储子包。

聚合本地文件存储相关的基础设施实现（如 StorageTier → 目录解析器）。
本子包是仓库中唯一知晓 `.epsilon`、`~`、`WORKSPACE_ROOT` 等物理路径字面量的地方，
domain 与 application 层不得直接感知这些实现细节。
"""

from __future__ import annotations

from infrastructure.storage.local_file_tier_resolver import (
    LocalFileTierResolver,
    ResolvedTierLayout,
)

__all__ = ["LocalFileTierResolver", "ResolvedTierLayout"]
