"""Python 脚本执行工具配置模块。

基于 pydantic-settings，从 config.properties 和环境变量加载以 ``PYTHON_EXEC_`` 为前缀的配置项。

包含执行超时时间、输出大小上限、内存限制、工具启用开关、工作目录和允许模块六项配置。
模块级实例 ``python_exec_config`` 通过 ``create_config`` 工厂函数创建。
"""

from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config

DEFAULT_ALLOWED_MODULES: frozenset[str] = frozenset(
    {
        "math",
        "json",
        "re",
        "datetime",
        "collections",
        "itertools",
        "functools",
        "string",
        "textwrap",
        "decimal",
        "fractions",
        "statistics",
        "random",
        "hashlib",
        "base64",
        "csv",
        "io",
    }
)
"""默认允许在沙箱中导入的 Python 标准库模块集合。"""


class PythonExecConfig(PropertiesBaseSettings):
    """Python 脚本执行工具配置，对应环境变量前缀 ``PYTHON_EXEC_``。

    Attributes:
        enabled: 工具启用开关，对应 ``PYTHON_EXEC_ENABLED``，默认 ``False``（安全优先）。
        timeout: 默认脚本执行超时秒数，对应 ``PYTHON_EXEC_TIMEOUT``，默认 ``30``。
        max_output_size:
            stdout/stderr 合并输出大小上限（字节），
            对应 ``PYTHON_EXEC_MAX_OUTPUT_SIZE``，默认 ``51200``（50KB）。
        max_memory_mb: 子进程内存限制（MB），对应 ``PYTHON_EXEC_MAX_MEMORY_MB``，默认 ``256``。
        working_dir: 工作目录路径，对应 ``PYTHON_EXEC_WORKING_DIR``，默认空字符串（运行时回退为
            ``os.path.join(tempfile.gettempdir(), "python_exec")``）。
        allowed_modules:
            逗号分隔的额外允许模块名，对应
            ``PYTHON_EXEC_ALLOWED_MODULES``，默认空字符串。
    """

    model_config = SettingsConfigDict(env_prefix="PYTHON_EXEC_")

    enabled: bool = False
    timeout: int = 30
    max_output_size: int = 51200
    max_memory_mb: int = 256
    working_dir: str = ""
    allowed_modules: str = ""

    def get_allowed_modules(self) -> frozenset[str]:
        """将配置的额外允许模块与默认白名单合并，返回完整的允许模块集合。

        将 ``allowed_modules`` 字段按逗号分隔解析，去除空白后与
        ``DEFAULT_ALLOWED_MODULES`` 取并集。

        Returns:
            合并后的允许模块名 frozenset，包含默认白名单和用户配置的额外模块。
        """
        if not self.allowed_modules.strip():
            return DEFAULT_ALLOWED_MODULES
        extra = {m.strip() for m in self.allowed_modules.split(",") if m.strip()}
        return DEFAULT_ALLOWED_MODULES | frozenset(extra)


python_exec_config = create_config(PythonExecConfig)
"""全局 Python 脚本执行工具配置实例，通过工厂函数创建。"""
