"""Shell 命令执行工具配置模块。

基于 pydantic-settings，从 config.properties 和环境变量加载以 ``SHELL_EXEC_`` 为前缀的配置项。

包含命令执行超时时间、输出大小上限、工具启用开关和工作目录四项配置。
模块级实例 ``shell_exec_config`` 通过 ``create_config`` 工厂函数创建。
"""

from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config


class ShellExecConfig(PropertiesBaseSettings):
    """Shell 命令执行工具配置，对应环境变量前缀 ``SHELL_EXEC_``。

    Attributes:
        timeout: 默认命令执行超时秒数，对应 ``SHELL_EXEC_TIMEOUT``，默认 ``30``。
        max_output_size:
            stdout/stderr 合并输出大小上限（字节），
            对应 ``SHELL_EXEC_MAX_OUTPUT_SIZE``，默认 ``51200``（50KB）。
        enabled: 工具启用开关，对应 ``SHELL_EXEC_ENABLED``，默认 ``False``（安全优先）。
        working_dir: 工作目录路径，对应 ``SHELL_EXEC_WORKING_DIR``，默认空字符串（运行时回退为
            ``os.path.join(tempfile.gettempdir(), "agent_exec")``）。
    """

    model_config = SettingsConfigDict(env_prefix="SHELL_EXEC_")

    timeout: int = 30
    max_output_size: int = 51200
    enabled: bool = False
    working_dir: str = ""


shell_exec_config = create_config(ShellExecConfig)
"""全局 Shell 命令执行工具配置实例，通过工厂函数创建。"""
