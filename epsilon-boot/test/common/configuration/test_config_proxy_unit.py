"""ConfigProxy 代理类的单元测试：边界情况和并发安全。

覆盖以下场景：
- 配置文件不存在时 ConfigProxy 正常工作（需求 3.4）
- mtime 读取 OSError 时记录警告日志（需求 3.5）
- 多线程并发刷新仅执行一次实例化（需求 4.2）
- 刷新失败时记录错误日志（需求 8.2）
- isinstance(proxy, ConfigClass) 返回 True（需求 2.3）
"""

import logging
import os
import threading
from pathlib import Path

from pydantic_settings import SettingsConfigDict

from common.configuration import ConfigProxy, PropertiesBaseSettings
from common.configuration.configuration_utils import PropertiesFileSettingsSource

# ---------------------------------------------------------------------------
# 辅助函数：创建基于临时目录的配置类和 mock_find_file
# ---------------------------------------------------------------------------


def _setup_temp_config(
    tmp_path: Path,
    monkeypatch,
    env_prefix: str,
    props_content: str = "",
    env_content: str = "",
):
    """在临时目录创建配置文件并 monkeypatch _find_file。

    返回 (env_file, props_file, mock_find_file) 三元组。

    Args:
        tmp_path: pytest 提供的临时目录。
        monkeypatch: pytest monkeypatch fixture。
        env_prefix: 配置类的环境变量前缀。
        props_content: config.properties 文件内容。
        env_content: .env 文件内容。

    Returns:
        (env_file, props_file) 路径元组。
    """
    env_file = tmp_path / ".env"
    props_file = tmp_path / "config.properties"
    env_file.write_text(env_content, encoding="utf-8")
    props_file.write_text(props_content, encoding="utf-8")

    def mock_find_file(filename: str) -> Path:
        """返回临时目录中的配置文件路径。"""
        if filename == ".env":
            return env_file
        if filename == "config.properties":
            return props_file
        return tmp_path / filename

    monkeypatch.setattr("common.configuration.config_proxy._find_file", mock_find_file)
    return env_file, props_file


def _make_config_cls(
    env_file: Path,
    props_file: Path,
    env_prefix: str,
    fields: dict,
    annotations: dict,
    hot_reload: bool = True,
):
    """动态创建配置子类，使用临时文件路径和自定义 settings_customise_sources。

    Args:
        env_file: .env 文件路径。
        props_file: config.properties 文件路径。
        env_prefix: 环境变量前缀。
        fields: 字段名到默认值的映射。
        annotations: 字段名到类型注解的映射。
        hot_reload: 是否启用热更新。

    Returns:
        动态创建的配置子类。
    """
    cls_dict = {
        "model_config": SettingsConfigDict(
            env_prefix=env_prefix,
            env_file=str(env_file),
            env_file_encoding="utf-8",
            extra="ignore",
            frozen=True,
        ),
        "hot_reload": hot_reload,
        "__annotations__": annotations,
    }
    cls_dict.update(fields)

    config_cls = type(
        "_DynamicUnitTestConfig",
        (PropertiesBaseSettings,),
        cls_dict,
    )

    @classmethod  # type: ignore[misc]
    def _custom_sources(
        cls,
        settings_cls,
        init_settings,
        env_settings,
        dotenv_settings,
        file_secret_settings,
    ):
        """使用临时 properties 文件路径的自定义配置源。"""
        return (
            init_settings,
            env_settings,
            dotenv_settings,
            PropertiesFileSettingsSource(settings_cls, properties_path=props_file),
            file_secret_settings,
        )

    config_cls.settings_customise_sources = _custom_sources
    return config_cls


# ===========================================================================
# 测试 1：配置文件不存在时 ConfigProxy 正常工作（需求 3.4）
# ===========================================================================


class TestConfigFileNotExist:
    """验证配置源文件不存在时 ConfigProxy 仍能正常工作。

    当 .env 和 config.properties 文件不存在时，ConfigProxy 应将 mtime 视为 0.0，
    不抛出异常，并使用配置类的字段默认值正常提供服务。

    **Validates: Requirement 3.4**
    """

    def test_proxy_works_when_config_files_missing(self, tmp_path: Path, monkeypatch) -> None:
        """配置文件不存在时，代理应使用默认值正常工作，不抛出异常。"""
        # 指向不存在的文件路径
        env_file = tmp_path / "nonexistent" / ".env"
        props_file = tmp_path / "nonexistent" / "config.properties"

        def mock_find_file(filename: str) -> Path:
            """返回不存在的文件路径。"""
            if filename == ".env":
                return env_file
            if filename == "config.properties":
                return props_file
            return tmp_path / "nonexistent" / filename

        monkeypatch.setattr("common.configuration.config_proxy._find_file", mock_find_file)

        # 创建配置类，使用默认值
        config_cls = type(
            "_MissingFileConfig",
            (PropertiesBaseSettings,),
            {
                "model_config": SettingsConfigDict(
                    env_prefix="UNIT_MISSING_",
                    env_file=str(env_file),
                    env_file_encoding="utf-8",
                    extra="ignore",
                    frozen=True,
                ),
                "hot_reload": True,
                "__annotations__": {"host": str, "port": int},
                "host": "default-host",
                "port": 3000,
            },
        )

        # ConfigProxy 应正常创建，不抛出异常
        proxy = ConfigProxy(config_cls)

        # 应返回字段默认值
        assert proxy.host == "default-host"
        assert proxy.port == 3000

        # 内部 mtime 应全部为 0.0（文件不存在）
        mtimes = object.__getattribute__(proxy, "_mtimes")
        for filepath, mtime_val in mtimes.items():
            assert mtime_val == 0.0, (
                f"文件不存在时 mtime 应为 0.0，实际为 {mtime_val}（路径: {filepath}）"
            )


# ===========================================================================
# 测试 2：mtime 读取 OSError 时记录警告日志（需求 3.5）
# ===========================================================================


class TestMtimeOSErrorWarning:
    """验证 os.path.getmtime 抛出 OSError 时，ConfigProxy 记录警告日志并将 mtime 视为 0.0。

    **Validates: Requirement 3.5**
    """

    def test_oserror_on_getmtime_logs_warning(self, tmp_path: Path, monkeypatch, caplog) -> None:
        """getmtime 抛出 OSError 时，应记录警告日志且 mtime 为 0.0。"""
        env_file, props_file = _setup_temp_config(
            tmp_path,
            monkeypatch,
            env_prefix="UNIT_OSERR_",
            props_content="unit.oserr.port=8080\n",
        )

        config_cls = _make_config_cls(
            env_file,
            props_file,
            env_prefix="UNIT_OSERR_",
            fields={"port": 0},
            annotations={"port": int},
        )

        # 先正常创建代理
        proxy = ConfigProxy(config_cls)
        assert proxy.port == 8080

        # 让 os.path.getmtime 对 props_file 抛出 OSError
        original_getmtime = os.path.getmtime

        def raising_getmtime(path):
            """对 props_file 路径抛出 OSError。"""
            if str(path) == str(props_file):
                raise OSError("模拟磁盘错误")
            return original_getmtime(path)

        monkeypatch.setattr("os.path.getmtime", raising_getmtime)

        # 捕获日志
        with caplog.at_level(logging.WARNING, logger="common.configuration.config_proxy"):
            # 调用 _get_current_mtimes 触发 OSError
            mtimes = proxy._get_current_mtimes()

        # 验证 props_file 的 mtime 为 0.0
        assert mtimes[str(props_file)] == 0.0

        # 验证警告日志已记录
        warning_records = [
            r
            for r in caplog.records
            if r.levelno == logging.WARNING and "无法读取配置源文件" in r.message
        ]
        assert len(warning_records) >= 1, (
            "应记录包含 '无法读取配置源文件' 的警告日志，"
            f"实际日志: {[r.message for r in caplog.records]}"
        )


# ===========================================================================
# 测试 3：多线程并发刷新仅执行一次实例化（需求 4.2）
# ===========================================================================


class TestConcurrentRefreshSingleInstantiation:
    """验证多线程同时触发配置刷新时，仅执行一次配置重新实例化。

    通过 threading.Barrier 同步多个线程，使它们同时检测到 mtime 变更并尝试刷新。
    使用计数器追踪配置类构造器的实际调用次数，断言仅被调用一次。

    **Validates: Requirement 4.2**
    """

    def test_concurrent_refresh_instantiates_only_once(self, tmp_path: Path, monkeypatch) -> None:
        """多线程同时触发刷新时，配置类构造器应仅被调用一次。"""
        env_file, props_file = _setup_temp_config(
            tmp_path,
            monkeypatch,
            env_prefix="UNIT_CONC_",
            props_content="unit.conc.port=1000\n",
        )

        config_cls = _make_config_cls(
            env_file,
            props_file,
            env_prefix="UNIT_CONC_",
            fields={"port": 0},
            annotations={"port": int},
        )

        # 创建代理并验证初始值
        proxy = ConfigProxy(config_cls)
        assert proxy.port == 1000

        # 修改配置文件触发 mtime 变更
        props_file.write_text("unit.conc.port=2000\n", encoding="utf-8")
        new_mtime = os.path.getmtime(str(props_file)) + 2.0
        os.utime(str(props_file), (new_mtime, new_mtime))

        # 追踪 _refresh 内部实际执行实例化的次数
        # 包装配置类构造器来计数
        instantiation_count = 0
        count_lock = threading.Lock()
        original_init = config_cls.__init__

        def counting_init(self_inner, *args, **kwargs):
            """追踪构造器调用次数的包装函数。"""
            nonlocal instantiation_count
            with count_lock:
                instantiation_count += 1
            return original_init(self_inner, *args, **kwargs)

        monkeypatch.setattr(config_cls, "__init__", counting_init)

        # 使用 Barrier 同步多个线程，确保它们同时开始访问代理
        num_threads = 10
        barrier = threading.Barrier(num_threads)
        errors: list[Exception] = []

        def worker():
            """工作线程：等待 barrier 后访问代理属性触发刷新。"""
            try:
                barrier.wait(timeout=5)
                _ = proxy.port
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # 不应有线程异常
        assert not errors, f"线程执行中出现异常: {errors}"

        # 需求 4.2: 多线程并发刷新时，构造器应仅被调用一次
        assert instantiation_count == 1, (
            f"并发刷新时配置类构造器应仅被调用 1 次，实际被调用 {instantiation_count} 次"
        )

        # 验证刷新后的值正确
        assert proxy.port == 2000


# ===========================================================================
# 测试 4：刷新失败时记录错误日志（需求 8.2）
# ===========================================================================


class TestRefreshFailureErrorLog:
    """验证配置刷新失败时，ConfigProxy 记录包含异常详情的错误日志。

    将配置文件修改为非法内容（导致 pydantic 校验失败），触发刷新后检查
    错误日志是否包含预期的错误消息和异常堆栈信息。

    **Validates: Requirement 8.2**
    """

    def test_refresh_failure_logs_error_with_exc_info(
        self, tmp_path: Path, monkeypatch, caplog
    ) -> None:
        """刷新失败时应记录包含配置类名称和异常详情的错误日志。"""
        env_file, props_file = _setup_temp_config(
            tmp_path,
            monkeypatch,
            env_prefix="UNIT_ERRLOG_",
            props_content="unit.errlog.port=5000\n",
        )

        config_cls = _make_config_cls(
            env_file,
            props_file,
            env_prefix="UNIT_ERRLOG_",
            fields={"port": 0},
            annotations={"port": int},
        )

        proxy = ConfigProxy(config_cls)
        assert proxy.port == 5000

        # 将配置文件修改为非法内容（port 为非数字，导致 int 校验失败）
        props_file.write_text("unit.errlog.port=not_a_number\n", encoding="utf-8")
        new_mtime = os.path.getmtime(str(props_file)) + 2.0
        os.utime(str(props_file), (new_mtime, new_mtime))

        # 捕获错误日志
        with caplog.at_level(logging.ERROR, logger="common.configuration.config_proxy"):
            # 访问属性触发刷新（刷新会失败）
            result = proxy.port

        # 代理应保留旧值
        assert result == 5000

        # 验证错误日志已记录
        error_records = [
            r
            for r in caplog.records
            if r.levelno == logging.ERROR and "刷新配置类" in r.message and "失败" in r.message
        ]
        assert len(error_records) >= 1, (
            f"应记录包含 '刷新配置类...失败' 的错误日志，"
            f"实际日志: {[r.message for r in caplog.records]}"
        )

        # 验证日志包含配置类名称
        err_record = error_records[0]
        assert "_DynamicUnitTestConfig" in err_record.message, (
            f"错误日志应包含配置类名称 '_DynamicUnitTestConfig'，实际消息: {err_record.message}"
        )

        # 验证 exc_info 被设置（即日志包含异常堆栈）
        assert err_record.exc_info is not None, "错误日志应包含 exc_info（异常堆栈信息）"


# ===========================================================================
# 测试 5：isinstance(proxy, ConfigClass) 返回 True（需求 2.3）
# ===========================================================================


class TestIsinstanceCheck:
    """验证 isinstance(proxy, ConfigClass) 返回 True。

    ConfigProxy 通过 __class__ 属性伪装实现 isinstance 检查支持，
    使代理对象对调用方完全透明。

    **Validates: Requirement 2.3**
    """

    def test_isinstance_returns_true(self) -> None:
        """isinstance(proxy, ConfigClass) 应返回 True。"""
        config_cls = type(
            "_IsinstanceConfig",
            (PropertiesBaseSettings,),
            {
                "model_config": SettingsConfigDict(env_prefix="UNIT_INST_"),
                "hot_reload": True,
                "__annotations__": {"name": str},
                "name": "test",
            },
        )

        proxy = ConfigProxy(config_cls)

        # 需求 2.3: isinstance 检查应返回 True
        assert isinstance(proxy, config_cls), (
            f"isinstance(proxy, {config_cls.__name__}) 应返回 True，实际返回 False"
        )

    def test_isinstance_with_base_class(self) -> None:
        """isinstance(proxy, PropertiesBaseSettings) 也应返回 True。"""
        config_cls = type(
            "_IsinstanceBaseConfig",
            (PropertiesBaseSettings,),
            {
                "model_config": SettingsConfigDict(env_prefix="UNIT_INSTB_"),
                "hot_reload": True,
                "__annotations__": {"value": str},
                "value": "base-test",
            },
        )

        proxy = ConfigProxy(config_cls)

        # 代理对象也应通过基类的 isinstance 检查
        assert isinstance(proxy, PropertiesBaseSettings), (
            "isinstance(proxy, PropertiesBaseSettings) 应返回 True"
        )
