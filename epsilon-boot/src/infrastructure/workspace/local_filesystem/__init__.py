"""本地文件系统 Workspace 后端包。

对外仅导出 ``LocalFilesystemWorkspace``，是本期唯一的 Workspace 实现。
私有实现细节（``_guards`` / ``_common_impl``）以 ``_`` 前缀约束，禁止
被领域层或工具层直接导入。
"""

from __future__ import annotations

from infrastructure.workspace.local_filesystem.local_workspace import (
    LocalFilesystemWorkspace,
)

__all__ = ["LocalFilesystemWorkspace"]
