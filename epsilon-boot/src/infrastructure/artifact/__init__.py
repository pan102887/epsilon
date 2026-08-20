"""基础设施层任务产物存储子包。

聚合任务产物（ArtifactTrace）的本地文件后端实现与配置：
- LocalFileArtifactStoreAdapter：ArtifactStorePort 的本地 JSONL 实现。
- ArtifactConfig / artifact_config：产物存储开关配置。

物理路径映射经 infrastructure.storage 的 LocalFileTierResolver 完成，本子包
不直接感知 `.epsilon`/`~` 等路径字面量。
"""

from __future__ import annotations

from infrastructure.artifact.artifact_config import ArtifactConfig, artifact_config
from infrastructure.artifact.local_file_artifact_store_adapter import (
    LocalFileArtifactStoreAdapter,
)

__all__ = ["ArtifactConfig", "LocalFileArtifactStoreAdapter", "artifact_config"]
