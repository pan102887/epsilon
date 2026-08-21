"""配置代理模块。

提供 ``ConfigProxy`` 代理类，用于实现配置热更新。代理对象通过 ``__getattr__``
将属性访问透明转发到内部缓存的真实配置实例，并在每次属性访问时检查配置源文件的
mtime（最后修改时间），若文件已变更则重新实例化底层配置对象。

使用双重检查锁定（Double-Checked Locking）模式保证线程安全，同时减少锁竞争：
- 锁外快速路径：检查 mtime，未变更时直接返回缓存值（无锁开销）
- 锁内慢速路径：仅在检测到 mtime 变更时获取锁，再次确认后执行刷新

刷新失败时保留旧配置实例，保证服务稳定性。
"""

import logging
import os
import threading
from typing import Any, Generic, TypeVar

from .configuration_utils import LOCAL_PROPERTIES_FILE, PropertiesBaseSettings, find_file

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=PropertiesBaseSettings)

# Module-local aliases intentionally remain patchable for configuration tests.
_find_file = find_file
_LOCAL_PROPERTIES_FILE = LOCAL_PROPERTIES_FILE

# 需要通过 object.__setattr__ 设置的内部属性名集合
_INTERNAL_ATTRS = frozenset({"_config_class", "_instance", "_lock", "_mtimes", "_source_files"})


class ConfigProxy(Generic[T]):
    """配置代理对象，透明转发属性访问并支持基于 mtime 的热更新。

    通过 ``__getattr__`` 将属性访问转发到内部缓存的真实配置实例。
    每次属性访问时检查配置源文件（``.env``、``config.properties`` 以及存在时的
    ``config.local.properties``）的 mtime，若文件变更则重新实例化配置对象。
    使用双重检查锁定保证线程安全。

    泛型参数 ``T`` 表示被代理的配置类类型，使调用方获得正确的类型提示。

    Args:
        config_class: 要代理的配置类（``PropertiesBaseSettings`` 的子类）。

    示例::

        class RedisConfig(PropertiesBaseSettings):
            hot_reload: ClassVar[bool] = True
            model_config = SettingsConfigDict(env_prefix="REDIS_")
            host: str = "localhost"

        proxy = ConfigProxy(RedisConfig)
        print(proxy.host)  # 透明转发到真实配置实例
    """

    def __init__(self, config_class: type[T]) -> None:
        """初始化配置代理。

        使用 ``object.__setattr__`` 设置内部属性，避免触发自定义的 ``__setattr__``。
        初始化时立即实例化一次配置对象作为缓存实例，并记录配置源文件的初始 mtime。

        Args:
            config_class: 要代理的配置类，必须是可实例化的配置类。
        """
        # 运行时调用 _find_file 确定配置源文件路径（支持测试 monkeypatch）
        env_file = _find_file(".env")
        props_file = _find_file("config.properties")
        # config.local.properties 本地覆盖文件存在时纳入 mtime 热更新监听（ADR-0004）
        candidate_files = [env_file, props_file, _LOCAL_PROPERTIES_FILE]
        source_files = [str(s) for s in candidate_files if s.exists()]

        # 使用 object.__setattr__ 绕过自定义 __setattr__
        object.__setattr__(self, "_config_class", config_class)
        object.__setattr__(self, "_instance", config_class())
        object.__setattr__(self, "_lock", threading.Lock())
        object.__setattr__(self, "_source_files", source_files)
        object.__setattr__(self, "_mtimes", self._get_current_mtimes())

    def _get_current_mtimes(self) -> dict[str, float]:
        """获取所有配置源文件的当前 mtime。

        遍历 ``_source_files`` 中的每个文件路径，使用 ``os.path.getmtime``
        获取最后修改时间。文件不存在或发生 ``OSError`` 时返回 ``0.0`` 并记录警告日志。

        Returns:
            文件路径到 mtime 时间戳的映射字典。
        """
        mtimes: dict[str, float] = {}
        for filepath in self._source_files:
            try:
                mtimes[filepath] = os.path.getmtime(filepath)
            except OSError as e:
                logger.warning(
                    "无法读取配置源文件 '%s' 的 mtime，将视为 0.0: %s",
                    filepath,
                    e,
                )
                mtimes[filepath] = 0.0
        return mtimes

    def current_mtimes(self) -> dict[str, float]:
        """Return the current source-file modification times for diagnostics."""
        return self._get_current_mtimes()

    def _mtimes_changed(self) -> bool:
        """比较当前 mtime 与缓存的 mtime，判断配置源文件是否发生变更。

        Returns:
            若任一配置源文件的 mtime 与缓存值不同，返回 ``True``；否则返回 ``False``。
        """
        current = self._get_current_mtimes()
        return current != self._mtimes

    def _refresh(self) -> None:
        """执行配置刷新（双重检查锁定模式）。

        在锁内再次检查 mtime 是否确实发生变更（防止多线程重复刷新），
        确认变更后重新实例化配置对象。

        刷新失败时：
        - 保留当前缓存实例不变，继续使用旧配置
        - 更新 mtime 为当前值，避免后续每次访问都重复尝试失败的刷新
        - 记录包含异常详情的错误日志
        """
        with self._lock:
            # 双重检查：锁内再次确认 mtime 确实变更
            current_mtimes = self._get_current_mtimes()
            if current_mtimes == self._mtimes:
                return

            try:
                new_instance = self._config_class()
                object.__setattr__(self, "_instance", new_instance)
            except Exception:
                logger.error(
                    "刷新配置类 '%s' 失败，保留旧配置实例",
                    self._config_class.__name__,
                    exc_info=True,
                )
            finally:
                # 无论成功或失败，都更新 mtime，避免重复尝试
                object.__setattr__(self, "_mtimes", current_mtimes)

    def __getattr__(self, name: str) -> Any:
        """拦截属性访问，检查配置文件变更并转发到缓存实例。

        锁外快速路径：检查 mtime 是否变更，未变更时直接转发（无锁开销）。
        若检测到 mtime 变更，调用 ``_refresh`` 执行双重检查锁定刷新。

        Args:
            name: 要访问的属性名。

        Returns:
            缓存配置实例上对应属性的值。

        Raises:
            AttributeError: 当缓存实例不存在该属性时。
        """
        if self._mtimes_changed():
            self._refresh()
        return getattr(self._instance, name)

    def __setattr__(self, name: str, value: Any) -> None:
        """禁止对非内部属性进行赋值，保持不可变语义。

        内部属性（``_config_class``、``_instance``、``_lock``、``_mtimes``、
        ``_source_files``）通过 ``object.__setattr__`` 在 ``__init__`` 中设置，
        不经过此方法。

        Args:
            name: 属性名。
            value: 要设置的值。

        Raises:
            AttributeError: 对任何非内部属性的赋值操作。
        """
        if name in _INTERNAL_ATTRS:
            object.__setattr__(self, name, value)
        else:
            raise AttributeError(f"配置对象不可变，无法设置属性 '{name}'")

    def __repr__(self) -> str:
        """返回缓存配置实例的 repr 表示。

        Returns:
            与直接访问真实配置实例的 ``repr()`` 输出一致的字符串。
        """
        return repr(self._instance)

    def __str__(self) -> str:
        """返回缓存配置实例的字符串表示。

        Returns:
            与直接访问真实配置实例的 ``str()`` 输出一致的字符串。
        """
        return str(self._instance)

    @property
    def __class__(self) -> type[T]:  # type: ignore[override]
        """伪装代理对象的类型，使 ``isinstance`` 检查通过。

        返回被代理的配置类而非 ``ConfigProxy`` 本身，
        使 ``isinstance(proxy, ConfigClass)`` 返回 ``True``。

        Returns:
            被代理的配置类类型。
        """
        return self._config_class


def create_config(config_class: type[T]) -> T:
    """配置工厂函数，根据 ``hot_reload`` 标志创建配置实例。

    统一的配置实例创建入口。根据配置类上声明的 ``hot_reload`` 类变量，
    自动决定返回 ``ConfigProxy`` 代理对象（支持热更新）还是普通配置实例
    （零额外开销）。

    对于 ``hot_reload=False`` 的配置类，返回的实例与直接调用 ``ConfigClass()``
    完全一致，不引入任何代理层开销。

    Args:
        config_class: ``PropertiesBaseSettings`` 的子类，用于创建配置实例。

    Returns:
        ``hot_reload=True`` 时返回 ``ConfigProxy`` 代理对象，
        ``hot_reload=False`` 时返回配置类的直接实例。
        类型标注为 ``T``，保证调用方获得正确的类型提示和 IDE 自动补全。

    示例::

        class RedisConfig(PropertiesBaseSettings):
            hot_reload: ClassVar[bool] = True
            host: str = "localhost"

        redis_config = create_config(RedisConfig)
        print(redis_config.host)  # 类型提示为 str，IDE 自动补全正常
    """
    if config_class.hot_reload:
        return ConfigProxy(config_class)  # type: ignore[return-value]
    return config_class()
