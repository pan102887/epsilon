"""Workspace 工作区配置模块。

基于 ``pydantic-settings``，从 ``config.properties`` 和环境变量加载以
``WORKSPACE_`` 为前缀的配置项。通过 ``PropertiesBaseSettings + create_config``
工厂模式与仓库内其他配置类（如 ``ShellExecConfig`` / ``PythonExecConfig`` /
``ChatConfig``）保持一致。

配置字段：

- ``backend``：后端种类，对应 ``WORKSPACE_BACKEND``，默认
  ``local_filesystem``。本期通过 ``@model_validator(mode="after")`` 强制
  拒绝非 ``local_filesystem`` 的取值（fail-fast），但枚举 ``WorkspaceBackendKind``
  保留扩展位置供未来新增后端。
- ``root``：工作区根路径，对应 ``WORKSPACE_ROOT``；为空时由基础设施层
  ``_create_local_filesystem_workspace`` 默认解析为进程当前工作目录。显式配置时
  必须为宿主绝对路径，并在启动期做存在性、类型、权限等二次校验。
- ``follow_symlinks``：是否允许解引用符号链接，对应
  ``WORKSPACE_FOLLOW_SYMLINKS``，默认 ``False``（更严格）。
- ``create_if_missing``：当 ``WORKSPACE_ROOT`` 不存在时是否自动创建（含父级），
  对应 ``WORKSPACE_CREATE_IF_MISSING``，默认 ``False``（缺失时直接 fail-fast）。

**关键设计约束**：

- ``WorkspaceConfig`` 不声明 ``hot_reload``（保持 ``PropertiesBaseSettings``
  的默认 ``False``）。``backend`` 与 ``root`` 在进程生命周期内不可变（需求 5.12）。
- ``@model_validator(mode="after")`` 中拒绝非本期支持的 ``backend`` 取值；
  错误消息为中文且明确列出当前不支持的取值。
"""

from pydantic import model_validator
from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config
from domain.workspace.value_objects import WorkspaceBackendKind


class WorkspaceConfig(PropertiesBaseSettings):
    """Workspace 工作区配置，对应环境变量前缀 ``WORKSPACE_``。

    Attributes:
        backend: 后端种类，对应 ``WORKSPACE_BACKEND``，默认
            ``local_filesystem``。本期仅允许 ``local_filesystem``；其他
            合法枚举值（如未来的 ``oss``）会在 ``_reject_unsupported_backend``
            校验器中被拒绝，并以 ``Startup_Failure`` 语义终止启动。
        root: 工作区根路径，对应 ``WORKSPACE_ROOT``。为空时基础设施层默认
            使用进程当前工作目录；显式配置时必须为宿主绝对路径，并在启动期
            做存在性、是否为目录、读写权限等校验
            （``_create_local_filesystem_workspace`` 工厂负责）。
        follow_symlinks: 是否允许解引用符号链接，对应
            ``WORKSPACE_FOLLOW_SYMLINKS``，默认 ``False``（更严格）。
        create_if_missing: 当 ``WORKSPACE_ROOT`` 不存在时是否自动创建
            （含父级），对应 ``WORKSPACE_CREATE_IF_MISSING``，默认
            ``False``（缺失时直接 fail-fast）。
    """

    model_config = SettingsConfigDict(env_prefix="WORKSPACE_")

    backend: WorkspaceBackendKind = WorkspaceBackendKind.LOCAL_FILESYSTEM
    root: str = ""
    follow_symlinks: bool = False
    create_if_missing: bool = False

    @model_validator(mode="after")
    def _reject_unsupported_backend(self) -> "WorkspaceConfig":
        """本期仅允许 ``LOCAL_FILESYSTEM``；其他合法枚举值本期拒绝启动。

        Raises:
            ValueError: ``backend`` 非 ``LOCAL_FILESYSTEM`` 时抛出，错误
                消息明确指出"本期仅支持 WORKSPACE_BACKEND=local_filesystem"，
                由 pydantic 在校验阶段转换为 ``ValidationError``，进而由
                上层 ``configure_container()`` 触发 ``Startup_Failure``。
        """
        if self.backend != WorkspaceBackendKind.LOCAL_FILESYSTEM:
            raise ValueError(
                f"本期仅支持 WORKSPACE_BACKEND=local_filesystem，实际值：{self.backend.value}"
            )
        return self


workspace_config = create_config(WorkspaceConfig)
"""全局 Workspace 配置实例，通过工厂函数创建。

``hot_reload`` 保持默认 ``False``，保证 ``backend`` 与 ``root`` 在进程
生命周期内不可变（需求 5.12）。
"""
