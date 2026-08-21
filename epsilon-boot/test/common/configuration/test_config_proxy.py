"""ConfigProxy 代理类与 create_config 工厂函数的属性测试。

使用 Hypothesis 进行基于属性的测试，验证配置代理系统的核心正确性属性。
包含以下属性测试：
- Property 1：工厂函数路由正确性
- Property 2：属性访问透明转发
- Property 3：代理身份等价性
- Property 4：不可变语义
- Property 5：基于 mtime 的配置刷新
- Property 6：刷新失败保留旧配置
- Property 7：失败后 mtime 更新防止重复刷新
"""

import os
from pathlib import Path
from typing import Any, cast

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from common.configuration import ConfigProxy, PropertiesBaseSettings, create_config
from common.configuration.configuration_utils import PropertiesFileSettingsSource


def _make_config_class(enable_hot_reload: bool) -> type[PropertiesBaseSettings]:
    """根据给定的 hot_reload 值动态创建配置子类。

    通过 type() 动态构造 PropertiesBaseSettings 子类，
    将 hot_reload 设置为指定的布尔值，避免测试间的类级状态污染。

    注意：不能在 class 语句体内直接引用外层函数参数作为 ClassVar 的默认值，
    因为 Python 的类体作用域无法访问闭包变量。因此使用 type() 动态创建，
    并在创建后设置 hot_reload 类变量。

    Args:
        enable_hot_reload: 是否启用热更新。

    Returns:
        动态创建的配置子类。
    """
    cls = type(
        "_DynamicConfig",
        (PropertiesBaseSettings,),
        {
            "model_config": SettingsConfigDict(env_prefix="PBT_DYN_TEST_"),
            "hot_reload": enable_hot_reload,
            "__annotations__": {"value": str},
            "value": "test",
        },
    )
    return cls


class TestFactoryRoutingProperty:
    """Property 1: 工厂函数路由正确性。

    验证对于任意 PropertiesBaseSettings 子类，create_config 根据其 hot_reload
    标志返回正确的实例类型：
    - hot_reload=True → ConfigProxy 代理对象
    - hot_reload=False → 配置类的直接实例（非 ConfigProxy）

    **Validates: Requirements 1.2, 1.3, 5.2, 5.3, 6.1**
    """

    @given(hot_reload=st.booleans())
    @settings(
        max_examples=100,
        deadline=5000,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_create_config_returns_correct_type_based_on_hot_reload(self, hot_reload: bool) -> None:
        """验证 create_config 根据 hot_reload 布尔值返回正确的实例类型。

        Feature: config-hot-reload, Property 1: 工厂函数路由正确性

        **Validates: Requirements 1.2, 1.3, 5.2, 5.3, 6.1**

        对于随机生成的 hot_reload 布尔值，动态创建配置子类并调用 create_config，
        断言返回类型与 hot_reload 标志一致：
        - hot_reload=True 时，返回值应为 ConfigProxy 实例
        - hot_reload=False 时，返回值应为配置类的直接实例，且不是 ConfigProxy
        """
        config_class = _make_config_class(hot_reload)
        result = create_config(config_class)

        if hot_reload:
            # 需求 1.2, 5.2: hot_reload=True 时返回 ConfigProxy 代理对象
            assert type(result) is ConfigProxy, (
                f"hot_reload=True 时，create_config 应返回 ConfigProxy 实例，"
                f"实际返回 {type(result).__name__}"
            )
        else:
            # 需求 1.3, 5.3, 6.1: hot_reload=False 时返回直接实例，无代理层
            assert type(result) is not ConfigProxy, (
                f"hot_reload=False 时，create_config 不应返回 ConfigProxy，"
                f"实际返回 {type(result).__name__}"
            )
            assert isinstance(result, config_class), (
                f"hot_reload=False 时，create_config 应返回 {config_class.__name__} "
                f"的直接实例，实际返回 {type(result).__name__}"
            )


class TestAttributeForwardingProperty:
    """Property 2: 属性访问透明转发。

    验证对于任意通过 create_config 创建的 ConfigProxy 代理对象，
    通过代理访问配置字段的返回值应与直接实例化配置类后访问同一字段的返回值完全相等。

    使用 Hypothesis 生成随机的字符串、整数和布尔值作为配置字段默认值，
    动态构造配置子类（hot_reload=True），分别通过代理和直接实例访问字段，
    断言两者返回值一致。

    **Validates: Requirements 2.1, 2.2, 6.2, 7.1**
    """

    @given(
        name_val=st.text(min_size=0, max_size=50),
        port_val=st.integers(min_value=0, max_value=65535),
        enabled_val=st.booleans(),
    )
    @settings(
        max_examples=100,
        deadline=5000,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_proxy_attribute_access_equals_direct_instance(
        self, name_val: str, port_val: int, enabled_val: bool
    ) -> None:
        """验证代理对象的属性访问与直接实例化配置类的属性访问返回相同的值。

        Feature: config-hot-reload, Property 2: 属性访问透明转发

        **Validates: Requirements 2.1, 2.2, 6.2, 7.1**

        对于 Hypothesis 生成的随机 name（字符串）、port（整数）和 enabled（布尔值），
        动态创建一个 hot_reload=True 的配置子类，将随机值作为字段默认值。
        然后分别通过 create_config 创建代理对象和直接实例化配置类，
        断言通过代理访问的每个字段值与直接实例访问的字段值完全相等。
        """
        # 动态创建 hot_reload=True 的配置子类，使用随机值作为默认值
        config_cls = type(
            "_DynamicForwardConfig",
            (PropertiesBaseSettings,),
            {
                "model_config": SettingsConfigDict(env_prefix="PBT_FWD_TEST_"),
                "hot_reload": True,
                "__annotations__": {"name": str, "port": int, "enabled": bool},
                "name": name_val,
                "port": port_val,
                "enabled": enabled_val,
            },
        )

        # 通过工厂函数创建代理对象（hot_reload=True → ConfigProxy）
        proxy = cast(Any, create_config(config_cls))

        # 直接实例化配置类
        direct = cast(Any, config_cls())

        # 需求 2.1, 7.1: 代理通过 __getattr__ 透明转发属性访问
        # 需求 2.2, 6.2: 代理返回值与直接实例访问相同
        assert proxy.name == direct.name, (
            f"代理的 name 字段值 ({proxy.name!r}) 与直接实例 ({direct.name!r}) 不一致"
        )
        assert proxy.port == direct.port, (
            f"代理的 port 字段值 ({proxy.port!r}) 与直接实例 ({direct.port!r}) 不一致"
        )
        assert proxy.enabled == direct.enabled, (
            f"代理的 enabled 字段值 ({proxy.enabled!r}) 与直接实例 ({direct.enabled!r}) 不一致"
        )


class TestProxyIdentityEquivalenceProperty:
    """Property 3: 代理身份等价性。

    验证对于任意通过 create_config 创建的 ConfigProxy 代理对象：
    - ``isinstance(proxy, ConfigClass)`` 应返回 ``True``
    - ``repr(proxy)`` 应等于 ``repr(direct_instance)``
    - ``str(proxy)`` 应等于 ``str(direct_instance)``

    使用 Hypothesis 生成随机的字符串、整数和布尔值作为配置字段默认值，
    动态构造配置子类（hot_reload=True），分别通过代理和直接实例验证身份等价性。

    **Validates: Requirements 2.3, 2.4**
    """

    @given(
        name_val=st.text(min_size=0, max_size=50),
        port_val=st.integers(min_value=0, max_value=65535),
        enabled_val=st.booleans(),
    )
    @settings(
        max_examples=100,
        deadline=5000,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_proxy_identity_equivalence(
        self, name_val: str, port_val: int, enabled_val: bool
    ) -> None:
        """验证代理对象的 isinstance、repr、str 与直接实例等价。

        Feature: config-hot-reload, Property 3: 代理身份等价性

        **Validates: Requirements 2.3, 2.4**

        对于 Hypothesis 生成的随机 name（字符串）、port（整数）和 enabled（布尔值），
        动态创建一个 hot_reload=True 的配置子类，将随机值作为字段默认值。
        然后分别通过 create_config 创建代理对象和直接实例化配置类，
        断言：
        - isinstance(proxy, ConfigClass) 返回 True
        - repr(proxy) 与 repr(direct_instance) 完全相等
        - str(proxy) 与 str(direct_instance) 完全相等
        """
        # 动态创建 hot_reload=True 的配置子类，使用随机值作为默认值
        config_cls = type(
            "_DynamicIdentityConfig",
            (PropertiesBaseSettings,),
            {
                "model_config": SettingsConfigDict(env_prefix="PBT_ID_TEST_"),
                "hot_reload": True,
                "__annotations__": {"name": str, "port": int, "enabled": bool},
                "name": name_val,
                "port": port_val,
                "enabled": enabled_val,
            },
        )

        # 通过工厂函数创建代理对象（hot_reload=True → ConfigProxy）
        proxy = cast(Any, create_config(config_cls))

        # 直接实例化配置类
        direct = config_cls()

        # 需求 2.3: isinstance(proxy, ConfigClass) 应返回 True
        assert isinstance(proxy, config_cls), (
            f"isinstance(proxy, {config_cls.__name__}) 应返回 True，"
            f"实际返回 False（proxy 类型为 {type(proxy).__name__}）"
        )

        # 需求 2.4: repr(proxy) 应等于 repr(direct_instance)
        assert repr(proxy) == repr(direct), (
            f"repr(proxy) 与 repr(direct) 不一致：\n"
            f"  proxy:  {repr(proxy)!r}\n"
            f"  direct: {repr(direct)!r}"
        )

        # 需求 2.4: str(proxy) 应等于 str(direct_instance)
        assert str(proxy) == str(direct), (
            f"str(proxy) 与 str(direct) 不一致：\n"
            f"  proxy:  {str(proxy)!r}\n"
            f"  direct: {str(direct)!r}"
        )


class TestImmutableSemanticsProperty:
    """Property 4: 不可变语义。

    验证对于任意 ConfigProxy 代理对象和任意属性名，对代理执行属性赋值操作
    应抛出 ``AttributeError``，且代理内部状态不发生变化。

    使用 Hypothesis 生成随机的属性名和值，确保不可变语义在各种输入下均成立。
    内部属性（以 ``_`` 开头的管理属性）不在此测试范围内，因为它们由
    ``ConfigProxy.__init__`` 通过 ``object.__setattr__`` 设置。

    **Validates: Requirements 2.5, 2.6**
    """

    @given(
        attr_name=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "Pc")),
            min_size=1,
            max_size=30,
        ).filter(lambda s: s.isidentifier() and not s.startswith("_")),
        attr_value=st.one_of(
            st.text(min_size=0, max_size=50),
            st.integers(min_value=-10000, max_value=10000),
            st.booleans(),
            st.none(),
        ),
    )
    @settings(
        max_examples=100,
        deadline=5000,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_setattr_raises_attribute_error_and_state_unchanged(
        self, attr_name: str, attr_value: object
    ) -> None:
        """验证对代理对象的属性赋值操作抛出 AttributeError 且内部状态不变。

        Feature: config-hot-reload, Property 4: 不可变语义

        **Validates: Requirements 2.5, 2.6**

        对于 Hypothesis 生成的随机属性名（合法 Python 标识符，非下划线开头）和
        随机值（字符串、整数、布尔值或 None），创建一个 hot_reload=True 的
        ConfigProxy 代理对象，然后：
        1. 记录赋值前代理的 repr 快照
        2. 尝试对代理执行属性赋值，断言抛出 AttributeError
        3. 断言赋值后代理的 repr 快照与赋值前完全一致（内部状态未变化）
        """
        # 创建 hot_reload=True 的配置子类和代理对象
        config_cls = type(
            "_DynamicImmutableConfig",
            (PropertiesBaseSettings,),
            {
                "model_config": SettingsConfigDict(env_prefix="PBT_IMM_TEST_"),
                "hot_reload": True,
                "__annotations__": {"name": str, "port": int},
                "name": "immutable_test",
                "port": 9090,
            },
        )
        proxy = cast(Any, create_config(config_cls))

        # 记录赋值前的状态快照
        repr_before = repr(proxy)
        name_before = proxy.name
        port_before = proxy.port

        # 需求 2.5, 2.6: 对代理执行属性赋值应抛出 AttributeError
        with pytest.raises(AttributeError):
            setattr(proxy, attr_name, attr_value)

        # 验证代理内部状态未发生变化
        assert repr(proxy) == repr_before, (
            f"赋值 '{attr_name}' 后代理的 repr 发生了变化：\n"
            f"  赋值前: {repr_before!r}\n"
            f"  赋值后: {repr(proxy)!r}"
        )
        assert proxy.name == name_before, f"赋值 '{attr_name}' 后代理的 name 字段发生了变化"
        assert proxy.port == port_before, f"赋值 '{attr_name}' 后代理的 port 字段发生了变化"


class TestMtimeBasedRefreshProperty:
    """Property 5: 基于 mtime 的配置刷新。

    验证对于任意 ConfigProxy 代理对象：
    - 若配置源文件的 mtime 未发生变化，多次属性访问应返回同一缓存实例的值
      （对象身份不变）
    - 若配置源文件被修改（mtime 变化）且文件内容合法，下一次属性访问应返回
      基于新文件内容实例化的配置值

    使用 ``tmp_path`` 创建临时配置文件，通过 ``monkeypatch`` 替换
    ``_find_file`` 函数使 ConfigProxy 读取临时文件。

    **Validates: Requirements 3.2, 3.3**
    """

    @given(
        initial_port=st.integers(min_value=1, max_value=30000),
        updated_port=st.integers(min_value=30001, max_value=65535),
    )
    @settings(
        max_examples=100,
        deadline=10000,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_mtime_based_config_refresh(
        self,
        initial_port: int,
        updated_port: int,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """验证 mtime 未变更时返回缓存实例，mtime 变更后返回新配置值。

        Feature: config-hot-reload, Property 5: 基于 mtime 的配置刷新

        **Validates: Requirements 3.2, 3.3**

        对于 Hypothesis 生成的随机初始端口和更新端口值：
        1. 在临时目录创建 .env 和 config.properties 文件
        2. 通过 monkeypatch 替换 _find_file 使 ConfigProxy 读取临时文件
        3. 创建代理对象，验证初始值正确
        4. 多次访问属性，验证缓存实例身份不变（需求 3.3）
        5. 修改 config.properties 文件内容并更新 mtime
        6. 再次访问属性，验证返回新值（需求 3.2）
        """
        # 在临时目录创建配置源文件
        env_file = tmp_path / ".env"
        props_file = tmp_path / "config.properties"
        env_file.write_text("", encoding="utf-8")
        props_file.write_text(f"pbt.refresh.port={initial_port}\n", encoding="utf-8")

        # monkeypatch _find_file 使 ConfigProxy 读取临时文件
        def mock_find_file(filename: str) -> Path:
            """返回临时目录中的配置文件路径。"""
            if filename == ".env":
                return env_file
            if filename == "config.properties":
                return props_file
            return tmp_path / filename

        monkeypatch.setattr("common.configuration.config_proxy._find_file", mock_find_file)

        # 动态创建 hot_reload=True 的配置子类
        config_cls = type(
            "_DynamicRefreshConfig",
            (PropertiesBaseSettings,),
            {
                "model_config": SettingsConfigDict(
                    env_prefix="PBT_REFRESH_",
                    env_file=str(env_file),
                    env_file_encoding="utf-8",
                    extra="ignore",
                    frozen=True,
                ),
                "hot_reload": True,
                "__annotations__": {"port": int},
                "port": 0,
            },
        )

        # 覆盖 settings_customise_sources 以使用临时 properties 文件
        @classmethod
        def _custom_sources(
            cls: type[PropertiesBaseSettings],
            settings_cls: type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            dotenv_settings: PydanticBaseSettingsSource,
            file_secret_settings: PydanticBaseSettingsSource,
        ) -> tuple[PydanticBaseSettingsSource, ...]:
            """使用临时 properties 文件路径的自定义配置源。"""
            return (
                init_settings,
                env_settings,
                dotenv_settings,
                PropertiesFileSettingsSource(settings_cls, properties_path=props_file),
                file_secret_settings,
            )

        cast(Any, config_cls).settings_customise_sources = _custom_sources

        # 创建代理对象
        proxy = cast(Any, ConfigProxy(config_cls))

        # 验证初始值
        assert proxy.port == initial_port, f"初始 port 应为 {initial_port}，实际为 {proxy.port}"

        # 需求 3.3: mtime 未变更时，多次访问应返回同一缓存实例
        instance_before = object.__getattribute__(proxy, "_instance")
        _ = proxy.port
        _ = proxy.port
        instance_after = object.__getattribute__(proxy, "_instance")
        assert instance_before is instance_after, "mtime 未变更时，缓存实例的对象身份应保持不变"

        # 修改 config.properties 文件内容
        props_file.write_text(f"pbt.refresh.port={updated_port}\n", encoding="utf-8")
        # 确保 mtime 发生变化（某些文件系统精度为 1 秒）
        new_mtime = os.path.getmtime(str(props_file)) + 2.0
        os.utime(str(props_file), (new_mtime, new_mtime))

        # 需求 3.2: mtime 变更后，下一次属性访问应返回新值
        assert proxy.port == updated_port, (
            f"文件修改后 port 应为 {updated_port}，实际为 {proxy.port}"
        )

        # 验证缓存实例已被替换
        instance_refreshed = object.__getattribute__(proxy, "_instance")
        assert instance_before is not instance_refreshed, "mtime 变更后，缓存实例应被替换为新实例"


class TestRefreshFailureRetainsOldConfigProperty:
    """Property 6: 刷新失败保留旧配置。

    验证对于任意 ConfigProxy 代理对象，若配置源文件被修改为非法内容
    （导致实例化失败），则代理应继续返回刷新前的旧配置值，不抛出异常。

    使用 ``tmp_path`` 创建临时配置文件，通过 ``monkeypatch`` 替换
    ``_find_file`` 函数使 ConfigProxy 读取临时文件。先写入合法配置，
    创建代理并验证初始值，然后将配置文件修改为非法内容（如 port=not_a_number），
    触发 mtime 变更后访问代理属性，验证仍返回旧的合法配置值。

    **Validates: Requirements 8.1**
    """

    @given(
        initial_port=st.integers(min_value=1, max_value=65535),
    )
    @settings(
        max_examples=100,
        deadline=10000,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_refresh_failure_retains_old_config(
        self,
        initial_port: int,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """验证配置刷新失败时代理继续返回旧配置值，不抛出异常。

        Feature: config-hot-reload, Property 6: 刷新失败保留旧配置

        **Validates: Requirements 8.1**

        对于 Hypothesis 生成的随机初始端口值：
        1. 在临时目录创建 .env 和 config.properties 文件，写入合法配置
        2. 通过 monkeypatch 替换 _find_file 使 ConfigProxy 读取临时文件
        3. 创建代理对象，验证初始值正确
        4. 将 config.properties 修改为非法内容（port=not_a_number），使实例化失败
        5. 更新 mtime 确保变更被检测到
        6. 访问代理属性，验证仍返回旧的合法配置值，不抛出异常
        """
        # 在临时目录创建配置源文件
        env_file = tmp_path / ".env"
        props_file = tmp_path / "config.properties"
        env_file.write_text("", encoding="utf-8")
        props_file.write_text(f"pbt.failretain.port={initial_port}\n", encoding="utf-8")

        # monkeypatch _find_file 使 ConfigProxy 读取临时文件
        def mock_find_file(filename: str) -> Path:
            """返回临时目录中的配置文件路径。"""
            if filename == ".env":
                return env_file
            if filename == "config.properties":
                return props_file
            return tmp_path / filename

        monkeypatch.setattr("common.configuration.config_proxy._find_file", mock_find_file)

        # 动态创建 hot_reload=True 的配置子类，port 为 int 类型（严格校验）
        config_cls = type(
            "_DynamicFailRetainConfig",
            (PropertiesBaseSettings,),
            {
                "model_config": SettingsConfigDict(
                    env_prefix="PBT_FAILRETAIN_",
                    env_file=str(env_file),
                    env_file_encoding="utf-8",
                    extra="ignore",
                    frozen=True,
                ),
                "hot_reload": True,
                "__annotations__": {"port": int},
                "port": 0,
            },
        )

        # 覆盖 settings_customise_sources 以使用临时 properties 文件
        @classmethod
        def _custom_sources(
            cls: type[PropertiesBaseSettings],
            settings_cls: type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            dotenv_settings: PydanticBaseSettingsSource,
            file_secret_settings: PydanticBaseSettingsSource,
        ) -> tuple[PydanticBaseSettingsSource, ...]:
            """使用临时 properties 文件路径的自定义配置源。"""
            return (
                init_settings,
                env_settings,
                dotenv_settings,
                PropertiesFileSettingsSource(settings_cls, properties_path=props_file),
                file_secret_settings,
            )

        cast(Any, config_cls).settings_customise_sources = _custom_sources

        # 创建代理对象并验证初始值
        proxy = cast(Any, ConfigProxy(config_cls))
        assert proxy.port == initial_port, f"初始 port 应为 {initial_port}，实际为 {proxy.port}"

        # 将 config.properties 修改为非法内容（port 为非数字字符串，导致 int 校验失败）
        props_file.write_text("pbt.failretain.port=not_a_number\n", encoding="utf-8")
        # 确保 mtime 发生变化（某些文件系统精度为 1 秒）
        new_mtime = os.path.getmtime(str(props_file)) + 2.0
        os.utime(str(props_file), (new_mtime, new_mtime))

        # 需求 8.1: 刷新失败时，代理应继续返回旧配置值，不抛出异常
        assert proxy.port == initial_port, (
            f"刷新失败后 port 应保留旧值 {initial_port}，实际为 {proxy.port}"
        )

        # 验证缓存实例未被替换（仍为旧实例）
        instance_after_fail = object.__getattribute__(proxy, "_instance")
        assert instance_after_fail.port == initial_port, (
            f"刷新失败后缓存实例的 port 应为 {initial_port}，实际为 {instance_after_fail.port}"
        )


class TestFailedRefreshPreventsRetryProperty:
    """Property 7: 失败后 mtime 更新防止重复刷新。

    验证对于任意 ConfigProxy 代理对象，若一次刷新因配置文件格式错误而失败，
    则在文件未再次修改的情况下，后续属性访问不应再次尝试重新实例化配置对象。

    核心机制：刷新失败后，ConfigProxy 在 ``_refresh()`` 的 ``finally`` 块中
    将 ``_mtimes`` 更新为当前文件的 mtime 值，因此后续属性访问检测到 mtime
    未变化，跳过刷新流程，不再尝试重新实例化。

    使用 ``tmp_path`` 创建临时配置文件，通过 ``monkeypatch`` 替换
    ``_find_file`` 函数使 ConfigProxy 读取临时文件。先写入合法配置创建代理，
    然后写入非法内容触发失败刷新，最后通过 ``unittest.mock.patch`` 包装
    配置类构造器，验证后续多次属性访问不再调用构造器。

    **Validates: Requirements 8.3**
    """

    @given(
        initial_port=st.integers(min_value=1, max_value=65535),
    )
    @settings(
        max_examples=100,
        deadline=10000,
        suppress_health_check=[HealthCheck.function_scoped_fixture],
    )
    def test_failed_refresh_prevents_subsequent_retry(
        self,
        initial_port: int,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """验证失败刷新后，文件未再次修改时后续访问不再尝试重新实例化。

        Feature: config-hot-reload, Property 7: 失败后 mtime 更新防止重复刷新

        **Validates: Requirements 8.3**

        对于 Hypothesis 生成的随机初始端口值：
        1. 在临时目录创建 .env 和 config.properties 文件，写入合法配置
        2. 通过 monkeypatch 替换 _find_file 使 ConfigProxy 读取临时文件
        3. 创建代理对象，验证初始值正确
        4. 将 config.properties 修改为非法内容（port=not_a_number），更新 mtime
        5. 访问代理属性，触发失败刷新（代理保留旧值，mtime 被更新为当前值）
        6. 不再修改文件，使用 unittest.mock.patch 包装配置类构造器
        7. 多次访问代理属性，断言构造器 mock 未被调用
        8. 这证明失败刷新后 mtime 已更新，后续访问不再重试实例化
        """
        # 在临时目录创建配置源文件
        env_file = tmp_path / ".env"
        props_file = tmp_path / "config.properties"
        env_file.write_text("", encoding="utf-8")
        props_file.write_text(f"pbt.noretry.port={initial_port}\n", encoding="utf-8")

        # monkeypatch _find_file 使 ConfigProxy 读取临时文件
        def mock_find_file(filename: str) -> Path:
            """返回临时目录中的配置文件路径。"""
            if filename == ".env":
                return env_file
            if filename == "config.properties":
                return props_file
            return tmp_path / filename

        monkeypatch.setattr("common.configuration.config_proxy._find_file", mock_find_file)

        # 动态创建 hot_reload=True 的配置子类
        config_cls = type(
            "_DynamicNoRetryConfig",
            (PropertiesBaseSettings,),
            {
                "model_config": SettingsConfigDict(
                    env_prefix="PBT_NORETRY_",
                    env_file=str(env_file),
                    env_file_encoding="utf-8",
                    extra="ignore",
                    frozen=True,
                ),
                "hot_reload": True,
                "__annotations__": {"port": int},
                "port": 0,
            },
        )

        # 覆盖 settings_customise_sources 以使用临时 properties 文件
        @classmethod
        def _custom_sources(
            cls: type[PropertiesBaseSettings],
            settings_cls: type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            dotenv_settings: PydanticBaseSettingsSource,
            file_secret_settings: PydanticBaseSettingsSource,
        ) -> tuple[PydanticBaseSettingsSource, ...]:
            """使用临时 properties 文件路径的自定义配置源。"""
            return (
                init_settings,
                env_settings,
                dotenv_settings,
                PropertiesFileSettingsSource(settings_cls, properties_path=props_file),
                file_secret_settings,
            )

        cast(Any, config_cls).settings_customise_sources = _custom_sources

        # 创建代理对象并验证初始值
        proxy = cast(Any, ConfigProxy(config_cls))
        assert proxy.port == initial_port, f"初始 port 应为 {initial_port}，实际为 {proxy.port}"

        # 将 config.properties 修改为非法内容（port 为非数字字符串，导致 int 校验失败）
        props_file.write_text("pbt.noretry.port=not_a_number\n", encoding="utf-8")
        # 确保 mtime 发生变化（某些文件系统精度为 1 秒）
        new_mtime = os.path.getmtime(str(props_file)) + 2.0
        os.utime(str(props_file), (new_mtime, new_mtime))

        # 触发失败刷新：访问属性，ConfigProxy 检测到 mtime 变更，
        # 尝试重新实例化但失败，保留旧值，同时在 finally 块中更新 _mtimes
        assert proxy.port == initial_port, (
            f"刷新失败后 port 应保留旧值 {initial_port}，实际为 {proxy.port}"
        )

        # 此时 _mtimes 已被更新为当前文件的 mtime 值
        # 不再修改文件，后续访问应检测到 mtime 未变化，跳过刷新

        # 使用 mock 包装配置类构造器，验证后续访问不再调用它
        original_init = config_cls.__init__

        call_count = 0

        def tracking_init(self_inner: Any, *args: Any, **kwargs: Any) -> None:
            """追踪构造器调用次数的包装函数。"""
            nonlocal call_count
            call_count += 1
            return original_init(self_inner, *args, **kwargs)

        monkeypatch.setattr(config_cls, "__init__", tracking_init)

        # 多次访问代理属性（不修改文件）
        for _ in range(5):
            _ = proxy.port

        # 需求 8.3: 失败刷新后，文件未再次修改时，不应再次尝试实例化
        assert call_count == 0, (
            f"失败刷新后，文件未修改时不应再次尝试实例化配置对象，但构造器被调用了 {call_count} 次"
        )
