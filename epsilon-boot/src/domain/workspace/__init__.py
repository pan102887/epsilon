"""Workspace 抽象领域包。

本包定义 Workspace 抽象的领域层公共 API，供工具层与基础设施适配器共同使用：

- ``Workspace`` / ``LocallyMaterializable``：Port 协议（``typing.Protocol``），
  见 ``ports.py``。
- ``WorkspacePolicy``：逻辑路径规范化策略（纯函数对象），见 ``policy.py``。
- ``WorkspacePath`` / ``WorkspaceStatEntry`` / ``WorkspaceCapabilities`` /
  ``WorkspaceBackendKind``：值对象与枚举，见 ``value_objects.py``。
- ``ConfinementViolationReason`` / ``WorkspaceConfinementViolation`` /
  ``WorkspaceNotFoundError`` / ``WorkspaceIoError`` /
  ``WorkspaceUnsupportedOperationError``：领域错误模型，见 ``exceptions.py``。
"""

from domain.workspace.exceptions import (
    ConfinementViolationReason,
    WorkspaceConfinementViolation,
    WorkspaceIoError,
    WorkspaceNotFoundError,
    WorkspaceUnsupportedOperationError,
)
from domain.workspace.policy import WorkspacePolicy
from domain.workspace.ports import LocallyMaterializable, Workspace
from domain.workspace.value_objects import (
    WorkspaceBackendKind,
    WorkspaceCapabilities,
    WorkspacePath,
    WorkspaceStatEntry,
)

__all__ = [
    "ConfinementViolationReason",
    "LocallyMaterializable",
    "Workspace",
    "WorkspaceBackendKind",
    "WorkspaceCapabilities",
    "WorkspaceConfinementViolation",
    "WorkspaceIoError",
    "WorkspaceNotFoundError",
    "WorkspacePath",
    "WorkspacePolicy",
    "WorkspaceStatEntry",
    "WorkspaceUnsupportedOperationError",
]
