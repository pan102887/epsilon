"""``IdValidationConfig`` 加载行为单元测试（Task 7.4）。

覆盖 4 个 case：

- 默认值：清空相关 env 与 properties 覆盖后构造 ``IdValidationConfig()``，
  断言 ``history_restore_strategy == "filter"``
- 环境变量覆盖：``monkeypatch.setenv("ID_VALIDATION_HISTORY_RESTORE_STRATEGY",
  "raise")`` 后实例化新 IdValidationConfig，断言生效
- properties 覆盖：通过临时 ``config.properties`` 写入键值，验证生效
- 非法值回退：调用
  ``domain.chat.context.normalize_history_restore_strategy()``，断言返回
  ``"filter"``（D8 / design §`BaseMessage.from_dict` 改造的非法值兜底契约）

注：env 覆盖用例使用单下划线 ``ID_VALIDATION_HISTORY_RESTORE_STRATEGY``
（**不是**双下划线），因为 ``pydantic_settings`` 默认 ``env_nested_delimiter``
未启用，``env_prefix="ID_VALIDATION_"`` 直接拼接字段名
``history_restore_strategy`` 即可命中。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

from common.configuration.configuration_utils import PropertiesFileSettingsSource
from common.configuration.id_validation_config import IdValidationConfig


def _build_isolated_config_cls(props_file: Path) -> type[IdValidationConfig]:
    """构造一个仅以 ``props_file`` 作为 properties 源的 IdValidationConfig 子类。

    避免实际项目 ``config.properties`` 干扰本测试。
    """

    class _IsolatedConfig(IdValidationConfig):
        model_config = SettingsConfigDict(
            env_prefix="ID_VALIDATION_",
            env_file=None,
            extra="ignore",
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
            del cls, dotenv_settings
            return (
                init_settings,
                env_settings,
                PropertiesFileSettingsSource(settings_cls, properties_path=props_file),
                file_secret_settings,
            )

    return _IsolatedConfig


def test_default_value_when_no_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """无 env / properties 覆盖时默认值为 'filter'。"""
    monkeypatch.delenv("ID_VALIDATION_HISTORY_RESTORE_STRATEGY", raising=False)
    props = tmp_path / "config.properties"
    props.write_text("", encoding="utf-8")

    cfg_cls = _build_isolated_config_cls(props)
    cfg = cfg_cls()
    assert cfg.history_restore_strategy == "filter"


def test_env_var_overrides_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """ID_VALIDATION_HISTORY_RESTORE_STRATEGY=raise 时生效。"""
    monkeypatch.setenv("ID_VALIDATION_HISTORY_RESTORE_STRATEGY", "raise")
    props = tmp_path / "config.properties"
    props.write_text("", encoding="utf-8")

    cfg_cls = _build_isolated_config_cls(props)
    cfg = cfg_cls()
    assert cfg.history_restore_strategy == "raise"


def test_properties_file_overrides_default(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """临时 config.properties 写入键值后生效（env 未设置）。"""
    monkeypatch.delenv("ID_VALIDATION_HISTORY_RESTORE_STRATEGY", raising=False)
    props = tmp_path / "config.properties"
    props.write_text(
        "ID_VALIDATION_HISTORY_RESTORE_STRATEGY=raise\n",
        encoding="utf-8",
    )

    cfg_cls = _build_isolated_config_cls(props)
    cfg = cfg_cls()
    assert cfg.history_restore_strategy == "raise"


def test_invalid_value_falls_back_to_filter() -> None:
    """非法配置值由 normalize_history_restore_strategy() 兜底回退到 'filter'。"""
    from domain.chat import context as ctx_module

    assert ctx_module.normalize_history_restore_strategy("invalid_strategy") == "filter"


def test_normalize_history_restore_strategy_passes_through_filter() -> None:
    """合法值 'filter' 透传。"""
    from domain.chat import context as ctx_module

    assert ctx_module.normalize_history_restore_strategy("filter") == "filter"


def test_configure_history_restore_strategy_passes_through_raise() -> None:
    """合法值 'raise' 透传。"""
    from domain.chat import context as ctx_module

    original = ctx_module.history_restore_strategy
    try:
        ctx_module.configure_history_restore_strategy("raise")
        assert ctx_module.history_restore_strategy == "raise"
    finally:
        ctx_module.configure_history_restore_strategy(original)


def test_id_validation_config_env_prefix() -> None:
    """生产 IdValidationConfig 类正确暴露 env_prefix。"""
    assert IdValidationConfig.model_config.get("env_prefix") == "ID_VALIDATION_"
