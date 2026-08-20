"""Workspace 基础设施层包。

对外可选再导出：

- ``LocalFilesystemWorkspace``：本期唯一实现，基于本地文件系统。
- ``WorkspaceConfig``：``config.properties`` 驱动的配置类。

未来新增 OSS 后端时仅需在此追加导出；现有调用方仍可通过具体子包路径
（``infrastructure.workspace.local_filesystem``）显式 import。
"""

from __future__ import annotations

from infrastructure.workspace.local_filesystem import LocalFilesystemWorkspace
from infrastructure.workspace.workspace_config import WorkspaceConfig

__all__ = [
    "LocalFilesystemWorkspace",
    "WorkspaceConfig",
]
