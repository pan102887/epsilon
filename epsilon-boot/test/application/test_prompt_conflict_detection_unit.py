"""Prompt 遗留配置冲突检测单元测试。

# Validates: Requirement 8.2 / 8.5 / 8.6, Property 6

注意：直接 ``import application.container_config`` 会触发
``application/__init__.py`` → ``server_app.py`` → ``configure_container()``，
从而向全局容器注册 ``local_persistence`` 等异步资源，污染后续测试。
本测试通过 ``importlib.util`` 加载独立模块对象，仅获取
``_check_legacy_prompt_conflict`` 函数，不触及 ``application`` 包初始化。
"""

import importlib.util
import pathlib

import pytest

from infrastructure.prompt.exceptions import ConflictingLegacyPromptConfigError


def _load_container_config_module():
    """直接加载 ``container_config``，绕过 ``application`` 包的 ``__init__``。"""
    config_path = (
        pathlib.Path(__file__).resolve().parents[2] / "src" / "application" / "container_config.py"
    )
    spec = importlib.util.spec_from_file_location("test_prompt_conflict_module", str(config_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_config_module = _load_container_config_module()
_check_legacy_prompt_conflict = _config_module._check_legacy_prompt_conflict


class TestCheckLegacyPromptConflict:
    """_check_legacy_prompt_conflict 冲突检测测试。"""

    def test_env_chat_system_prompt_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量 CHAT_SYSTEM_PROMPT 存在时抛出 ConflictingLegacyPromptConfigError。"""
        monkeypatch.setenv("CHAT_SYSTEM_PROMPT", "你好")

        with pytest.raises(ConflictingLegacyPromptConfigError) as exc_info:
            _check_legacy_prompt_conflict()

        assert "CHAT_SYSTEM_PROMPT(env)" in str(exc_info.value)
        assert "三步迁移" in str(exc_info.value)

    def test_properties_file_chat_system_prompt_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """config.properties 中存在 CHAT_SYSTEM_PROMPT 键时抛出。"""
        monkeypatch.delenv("CHAT_SYSTEM_PROMPT", raising=False)

        import common.configuration.configuration_utils as cu

        monkeypatch.setattr(cu, "_parse_properties_file", lambda _: {"CHAT_SYSTEM_PROMPT": "x"})

        with pytest.raises(ConflictingLegacyPromptConfigError):
            _check_legacy_prompt_conflict()

    def test_no_legacy_config_passes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量和文件均无遗留配置时函数正常返回。"""
        monkeypatch.delenv("CHAT_SYSTEM_PROMPT", raising=False)

        import common.configuration.configuration_utils as cu

        monkeypatch.setattr(cu, "_parse_properties_file", lambda _: {})

        result = _check_legacy_prompt_conflict()
        assert result is None
