"""基于 pydantic-settings 的配置属性注入工具模块。

提供 ``PropertiesBaseSettings`` 基类，所有配置类继承该基类即可自动从
环境变量、``config.local.properties`` 本地覆盖文件、``config.properties`` 文件、
``.env`` 文件和 secrets 文件源加载配置。

配置源优先级（从高到低）：
1. 构造参数（init_settings）
2. 环境变量（env_settings）
3. ``config.local.properties`` 本地覆盖文件（local properties_settings）
4. ``config.properties`` 文件（properties_settings）
5. ``.env`` 文件（dotenv_settings）
6. secrets 文件源（file_secret_settings）
7. 字段默认值

``config.local.properties`` 为本地覆盖配置（不入库），仅用于本地调试：其优先级
低于环境变量、高于 ``config.properties``，缺失时不报错、行为与不引入该文件完全一致
（ADR-0004）。``config.properties`` 仍为「新增/修改配置项优先写入」的主配置源。

``config.properties`` 使用 Java Properties 格式（``prefix.field_name=value``），
键名中的 ``.`` 会被转换为 ``_`` 并大写后与 ``env_prefix`` 匹配。
例如 ``redis.host=localhost`` 在 ``env_prefix="REDIS_"`` 时匹配字段 ``host``。

Usage::

    class RedisConfig(PropertiesBaseSettings):
        model_config = SettingsConfigDict(env_prefix="REDIS_")

        host: str = "localhost"
        port: int = 6379

    redis_config = RedisConfig()
    print(redis_config.host)  # 从环境变量、config.properties 或 .env 读取
"""

import os
from pathlib import Path
from typing import Any, ClassVar

from pydantic.fields import FieldInfo
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


def _find_file(filename: str) -> Path:
    """从当前模块位置向上查找指定文件。

    最多查找 5 层目录。如果找不到，返回项目根目录（src 的上一级）下的路径。

    Args:
        filename: 要查找的文件名，如 ".env" 或 "config.properties"。

    Returns:
        文件的 Path 对象。
    """
    current = Path(__file__).resolve().parent
    for _ in range(5):
        candidate = current / filename
        if candidate.exists():
            return candidate
        current = current.parent
    # 兜底：假设 src/ 的上一级是项目根目录
    return Path(__file__).resolve().parent.parent.parent.parent / filename


def _find_local_properties_file() -> Path:
    """定位 ``config.local.properties`` 本地覆盖配置文件。

    优先在 ``<WORKSPACE_ROOT 或 CWD>/.epsilon/config.local.properties`` 定位
    （``WORKSPACE_ROOT`` 环境变量为空时退回进程 CWD）；该处不存在时退回
    ``_find_file`` 从当前模块位置向上查找的兜底路径。

    与 ``_find_file`` 风格保持一致：只返回路径，缺失文件由
    ``_parse_properties_file`` 返回空 dict、不报错（需求 5.5、ADR-0004）。

    Returns:
        ``config.local.properties`` 的 Path 对象（文件可能不存在）。
    """
    workspace_root = os.environ.get("WORKSPACE_ROOT", "").strip()
    base = Path(workspace_root) if workspace_root else Path.cwd()
    candidate = base / ".epsilon" / "config.local.properties"
    if candidate.exists():
        return candidate
    return _find_file("config.local.properties")


_ENV_FILE = _find_file(".env")
ENV_FILE = _ENV_FILE
_PROPERTIES_FILE = _find_file("config.properties")
_LOCAL_PROPERTIES_FILE = _find_local_properties_file()

# Public read-only paths and resolver for callers that inspect configuration sources.
PROPERTIES_FILE = _PROPERTIES_FILE
LOCAL_PROPERTIES_FILE = _LOCAL_PROPERTIES_FILE
find_file = _find_file


def _parse_properties_file(path: Path) -> dict[str, str]:
    """解析 Java Properties 格式的配置文件。

    支持 ``key=value`` 和 ``key:value`` 两种分隔符，
    忽略注释行（``#`` 或 ``!`` 开头）和空行，自动去除键值前后空格。

    Args:
        path: 配置文件路径。

    Returns:
        键值对字典，键和值均为字符串。文件不存在时返回空字典。
    """
    if not path.exists():
        return {}

    properties: dict[str, str] = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("!"):
                    continue

                separator_idx = -1
                for i, char in enumerate(line):
                    if char in ("=", ":"):
                        separator_idx = i
                        break

                if separator_idx == -1:
                    continue

                key = line[:separator_idx].strip()
                value = line[separator_idx + 1 :].strip()
                if key:
                    properties[key] = value
    except OSError:
        pass

    return properties


def parse_properties_file(path: Path) -> dict[str, str]:
    """Parse a Java properties file for application-level configuration checks."""

    return _parse_properties_file(path)


class PropertiesFileSettingsSource(PydanticBaseSettingsSource):
    """从 config.properties 文件加载配置的自定义设置源。

    将 properties 文件中的键名（如 ``redis.host``）转换为环境变量风格
    （如 ``REDIS_HOST``），然后根据配置类的 ``env_prefix`` 匹配字段。

    转换规则：
    - 键名中的 ``.`` 替换为 ``_``
    - 整体转为大写
    - 去除 ``env_prefix`` 前缀后与字段名匹配

    示例映射：
    - ``redis.host`` → ``REDIS_HOST``
      → 匹配 ``env_prefix="REDIS_"`` 的 ``host`` 字段
    - ``model.claude.enabled`` → ``MODEL_CLAUDE_ENABLED``
      → 匹配 ``env_prefix="MODEL_"`` 的 ``claude_enabled`` 字段
    - ``logging.request.enabled`` → ``LOGGING_REQUEST_ENABLED``
      → 匹配 ``env_prefix="LOGGING_REQUEST_"`` 的 ``enabled`` 字段
    """

    def __init__(
        self,
        settings_cls: type[BaseSettings],
        properties_path: Path | None = None,
    ):
        """初始化 properties 文件设置源。

        Args:
            settings_cls: 目标配置类。
            properties_path: properties 文件路径，为 None 时使用自动查找的路径。
        """
        super().__init__(settings_cls)
        self._path = properties_path or _PROPERTIES_FILE
        self._raw = _parse_properties_file(self._path)

        # 预处理：将 properties 键名转换为环境变量风格，便于快速查找
        self._env_map: dict[str, str] = {}
        for key, value in self._raw.items():
            env_key = key.upper().replace(".", "_").replace("-", "_")
            self._env_map[env_key] = value

    def get_field_value(self, field: FieldInfo, field_name: str) -> tuple[Any, str, bool]:
        """获取指定字段在 properties 文件中的值。

        根据配置类的 ``env_prefix`` 和字段名构造环境变量风格的键名，
        在预处理后的映射表中查找对应值。

        Args:
            field: pydantic 字段信息。
            field_name: 字段名称。

        Returns:
            三元组 ``(value, field_name, value_is_complex)``：
            - value: 找到的字符串值，未找到时为 None。
            - field_name: 原始字段名。
            - value_is_complex: 始终为 False，properties 文件中的值均为简单字符串。
        """
        env_prefix = self.config.get("env_prefix", "")
        env_key = f"{env_prefix}{field_name}".upper()
        value = self._env_map.get(env_key)
        return value, field_name, False

    def __call__(self) -> dict[str, Any]:
        """执行配置加载，返回从 properties 文件中匹配到的字段值字典。

        Returns:
            字段名到值的映射字典，仅包含在 properties 文件中找到的字段。
        """
        d: dict[str, Any] = {}
        for field_name, field_info in self.settings_cls.model_fields.items():
            value, _, _ = self.get_field_value(field_info, field_name)
            if value is not None:
                d[field_name] = value
        return d


class PropertiesBaseSettings(BaseSettings):
    """项目配置基类，所有配置类应继承此类。

    统一配置 ``.env`` 文件路径和编码，子类只需通过
    ``model_config = SettingsConfigDict(env_prefix="XXX_")`` 指定环境变量前缀。

    特性：
    - 自动从环境变量、``config.local.properties`` 本地覆盖文件、``config.properties``
      文件、``.env`` 文件和 secrets 文件源加载配置
    - 优先级：构造参数 > 环境变量 > config.local.properties > config.properties
      > .env 文件 > secrets 文件源 > 默认值
    - 支持 pydantic 的全部类型校验能力（包括 dict、list 等复杂类型）
    - 实例创建后字段值不可变（frozen），保证配置一致性
    - 子类可通过 ``hot_reload: ClassVar[bool] = True`` 启用配置热更新，
      配合 ``create_config`` 工厂函数使用时，框架会返回 ConfigProxy 代理对象，
      在属性访问时自动检测配置文件变更并重新加载
    """

    hot_reload: ClassVar[bool] = False

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """自定义配置源加载顺序。

        在默认的 init → env → dotenv → file_secret 链路中，将
        ``config.local.properties`` 本地覆盖源与 ``config.properties`` 主配置源
        依次插入到 env 与 dotenv 之间；本地覆盖源优先级高于主配置源、低于环境变量
        （ADR-0004）。

        优先级从高到低：
        1. init_settings（构造参数）
        2. env_settings（环境变量）
        3. PropertiesFileSettingsSource(config.local.properties 本地覆盖文件)
        4. PropertiesFileSettingsSource(config.properties 主配置文件)
        5. dotenv_settings（.env 文件）
        6. file_secret_settings（secrets 目录）

        ``config.local.properties`` 缺失时由 ``_parse_properties_file`` 返回空
        dict，该源不贡献任何字段值，行为与不引入本地覆盖完全一致（需求 5.5）。

        Args:
            settings_cls: 当前配置类。
            init_settings: 构造参数设置源。
            env_settings: 环境变量设置源。
            dotenv_settings: .env 文件设置源。
            file_secret_settings: secrets 目录设置源。

        Returns:
            配置源元组，按优先级从高到低排列。
        """
        return (
            init_settings,
            env_settings,
            PropertiesFileSettingsSource(
                settings_cls, properties_path=_LOCAL_PROPERTIES_FILE
            ),
            PropertiesFileSettingsSource(settings_cls),
            dotenv_settings,
            file_secret_settings,
        )


class ConfigurationError(Exception):
    """配置错误异常。

    用于在配置校验失败或必需配置缺失时抛出，
    保持与原有代码的异常类型兼容。
    """

    pass
