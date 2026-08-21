"""``config.local.properties`` 本地覆盖配置源单元测试（Task 9.3）。

覆盖以下正确性属性：

- Property 4：配置优先级全序（env > config.local.properties > config.properties > .env）；
  用 ``pytest.mark.parametrize`` 枚举「哪些源存在」的组合，断言取值命中最高优先级源。
- Property 5：``config.local.properties`` 缺失时行为与基线一致、不报错（缺失文件由
  ``_parse_properties_file`` 返回空 dict，退化为不覆盖）。
- ``ConfigProxy`` 的 mtime 热更新源文件列表在 ``config.local.properties`` 存在时包含它。

测试隔离策略：构造仅以临时文件作为 properties / local-properties 源的
``PropertiesBaseSettings`` 子类（复用既有 ``test_id_validation_config_unit`` 范式），
避免真实项目 ``config.properties`` / ``.env`` 干扰；``ConfigProxy`` 用例则 monkeypatch
模块级 ``_find_file`` 与 ``_LOCAL_PROPERTIES_FILE`` 指向临时文件。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

import common.configuration.config_proxy as config_proxy_module
from common.configuration import ConfigProxy, PropertiesBaseSettings
from common.configuration.configuration_utils import PropertiesFileSettingsSource

_ENV_PREFIX = "LOCALPROP_TEST_"
_ENV_KEY = f"{_ENV_PREFIX}VALUE"


class _ValueSettings(PropertiesBaseSettings):
    """Typed base for dynamically configured value settings."""

    value: str = "default"


def _build_config_cls(
    props_file: Path,
    local_props_file: Path,
    env_file: Path | None,
) -> type[_ValueSettings]:
    """构造隔离的配置类：源顺序与生产一致（env > local > properties > .env）。

    源顺序刻意复刻 ``PropertiesBaseSettings.settings_customise_sources`` 的新契约：
    ``init > env > local-properties > properties > dotenv > secrets``，但把
    properties / local-properties 指向传入的临时文件，避免真实配置干扰。

    Args:
        props_file: 作为 ``config.properties`` 源的临时文件。
        local_props_file: 作为 ``config.local.properties`` 源的临时文件。
        env_file: 作为 ``.env`` 源的临时文件；None 表示不启用 dotenv 源。

    Returns:
        隔离后的 ``PropertiesBaseSettings`` 子类。
    """

    class _IsolatedConfig(_ValueSettings):
        model_config = SettingsConfigDict(
            env_prefix=_ENV_PREFIX,
            env_file=str(env_file) if env_file is not None else None,
            env_file_encoding="utf-8",
            extra="ignore",
            frozen=True,
        )
        @classmethod
        def settings_customise_sources(
            cls: type[BaseSettings],
            settings_cls: type[BaseSettings],
            init_settings: PydanticBaseSettingsSource,
            env_settings: PydanticBaseSettingsSource,
            dotenv_settings: PydanticBaseSettingsSource,
            file_secret_settings: PydanticBaseSettingsSource,
        ) -> tuple[PydanticBaseSettingsSource, ...]:
            del cls
            return (
                init_settings,
                env_settings,
                PropertiesFileSettingsSource(
                    settings_cls, properties_path=local_props_file
                ),
                PropertiesFileSettingsSource(
                    settings_cls, properties_path=props_file
                ),
                dotenv_settings,
                file_secret_settings,
            )

    return _IsolatedConfig


# ---------------------------------------------------------------------------
# Property 4：优先级全序 env > local > properties > .env
# ---------------------------------------------------------------------------

# 每个 case：(是否设 env, local 值 or None, properties 值 or None, dotenv 值 or None,
#            期望取值, 用例说明)
_PRIORITY_CASES = [
    ("env_val", "local_val", "props_val", "dotenv_val", "env_val", "全源存在→env 最高"),
    (None, "local_val", "props_val", "dotenv_val", "local_val", "无 env→local 覆盖 props/dotenv"),
    (None, None, "props_val", "dotenv_val", "props_val", "仅 props/dotenv→props 覆盖 dotenv"),
    (None, None, None, "dotenv_val", "dotenv_val", "仅 dotenv→取 dotenv"),
    (None, None, None, None, "default", "全缺→字段默认值"),
    ("env_val", None, "props_val", None, "env_val", "env 与 props 共存→env"),
    (None, "local_val", "props_val", None, "local_val", "local 与 props 共存→local"),
]


@pytest.mark.parametrize(
    ("env_value", "local_value", "props_value", "dotenv_value", "expected", "desc"),
    _PRIORITY_CASES,
)
def test_source_priority_order(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    env_value: str | None,
    local_value: str | None,
    props_value: str | None,
    dotenv_value: str | None,
    expected: str,
    desc: str,
) -> None:
    """多源同键覆盖：断言取值顺序 env > local > properties > .env（Property 4）。"""
    # 环境变量：显式设置或清除，避免宿主环境污染
    if env_value is not None:
        monkeypatch.setenv(_ENV_KEY, env_value)
    else:
        monkeypatch.delenv(_ENV_KEY, raising=False)

    props = tmp_path / "config.properties"
    props.write_text(
        f"{_ENV_KEY}={props_value}\n" if props_value is not None else "",
        encoding="utf-8",
    )
    local_props = tmp_path / "config.local.properties"
    local_props.write_text(
        f"{_ENV_KEY}={local_value}\n" if local_value is not None else "",
        encoding="utf-8",
    )

    env_file: Path | None = None
    if dotenv_value is not None:
        env_file = tmp_path / ".env"
        env_file.write_text(f"{_ENV_KEY}={dotenv_value}\n", encoding="utf-8")

    cfg_cls = _build_config_cls(props, local_props, env_file)
    cfg = cfg_cls()
    assert cfg.value == expected, desc


def test_local_overrides_properties(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """同键同时存在于 local 与 properties 时取 local 值（需求 5.4、Property 4）。"""
    monkeypatch.delenv(_ENV_KEY, raising=False)
    props = tmp_path / "config.properties"
    props.write_text(f"{_ENV_KEY}=from_properties\n", encoding="utf-8")
    local_props = tmp_path / "config.local.properties"
    local_props.write_text(f"{_ENV_KEY}=from_local\n", encoding="utf-8")

    cfg_cls = _build_config_cls(props, local_props, env_file=None)
    assert cfg_cls().value == "from_local"


def test_env_overrides_local(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """同键同时存在于 env 与 local 时取 env 值（需求 5.3、Property 4）。"""
    monkeypatch.setenv(_ENV_KEY, "from_env")
    props = tmp_path / "config.properties"
    props.write_text("", encoding="utf-8")
    local_props = tmp_path / "config.local.properties"
    local_props.write_text(f"{_ENV_KEY}=from_local\n", encoding="utf-8")

    cfg_cls = _build_config_cls(props, local_props, env_file=None)
    assert cfg_cls().value == "from_env"


# ---------------------------------------------------------------------------
# Property 5：config.local.properties 缺失时行为与基线一致、不报错
# ---------------------------------------------------------------------------


def test_missing_local_properties_no_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """local 文件缺失时不报错，且 properties 值仍生效（Property 5）。"""
    monkeypatch.delenv(_ENV_KEY, raising=False)
    props = tmp_path / "config.properties"
    props.write_text(f"{_ENV_KEY}=from_properties\n", encoding="utf-8")
    # 指向一个不存在的 local 文件
    missing_local = tmp_path / "does_not_exist" / "config.local.properties"
    assert not missing_local.exists()

    cfg_cls = _build_config_cls(props, missing_local, env_file=None)
    cfg = cfg_cls()
    assert cfg.value == "from_properties"


def test_missing_local_properties_falls_back_to_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """local 与 properties 均缺失/为空时退化为字段默认值、不报错（Property 5）。"""
    monkeypatch.delenv(_ENV_KEY, raising=False)
    props = tmp_path / "config.properties"
    props.write_text("", encoding="utf-8")
    missing_local = tmp_path / "config.local.properties"
    assert not missing_local.exists()

    cfg_cls = _build_config_cls(props, missing_local, env_file=None)
    assert cfg_cls().value == "default"


# ---------------------------------------------------------------------------
# ConfigProxy mtime 源文件列表包含 config.local.properties（存在时）
# ---------------------------------------------------------------------------


class _HotReloadConfig(PropertiesBaseSettings):
    """启用热更新的最小配置类，供 ConfigProxy mtime 监听用例使用。"""

    model_config = SettingsConfigDict(env_prefix=_ENV_PREFIX, extra="ignore", frozen=True)

    value: str = "default"


def test_config_proxy_monitors_local_properties_when_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """local 文件存在时，ConfigProxy 的 mtime 监听源文件列表包含它。"""
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    props_file = tmp_path / "config.properties"
    props_file.write_text("", encoding="utf-8")
    local_file = tmp_path / "config.local.properties"
    local_file.write_text("", encoding="utf-8")

    def mock_find_file(filename: str) -> Path:
        return tmp_path / filename

    monkeypatch.setattr(config_proxy_module, "_find_file", mock_find_file)
    monkeypatch.setattr(config_proxy_module, "_LOCAL_PROPERTIES_FILE", local_file)

    proxy = ConfigProxy(_HotReloadConfig)
    source_files = object.__getattribute__(proxy, "_source_files")
    assert str(local_file) in source_files
    assert str(env_file) in source_files
    assert str(props_file) in source_files


def test_config_proxy_omits_local_properties_when_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """local 文件缺失时，ConfigProxy 的 mtime 监听列表不含它（Property 5 一致性）。"""
    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    props_file = tmp_path / "config.properties"
    props_file.write_text("", encoding="utf-8")
    missing_local = tmp_path / "config.local.properties"
    assert not missing_local.exists()

    def mock_find_file(filename: str) -> Path:
        return tmp_path / filename

    monkeypatch.setattr(config_proxy_module, "_find_file", mock_find_file)
    monkeypatch.setattr(config_proxy_module, "_LOCAL_PROPERTIES_FILE", missing_local)

    proxy = ConfigProxy(_HotReloadConfig)
    source_files = object.__getattribute__(proxy, "_source_files")
    assert str(missing_local) not in source_files
