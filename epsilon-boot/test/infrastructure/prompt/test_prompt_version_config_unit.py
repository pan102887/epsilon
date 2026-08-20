"""PromptVersionConfig 配置单元测试。

覆盖以下需求（与 design.md §4 / Property 4 对齐）：

- 默认构造时 ``chat_default_version`` 与 ``task_template_version`` 均为 ``"v1"``；
- 通过 ``PROMPT_CHAT_DEFAULT_VERSION`` / ``PROMPT_TASK_TEMPLATE_VERSION``
  环境变量覆盖生效；
- 非法格式（``v0`` / ``v01`` / ``V1`` / 空串 / ``v1.0.0``）触发
  :class:`InvalidPromptVersionTagError`；
- :meth:`PromptVersionConfig.as_mapping` 返回 ``{"chat-default": ...,
  "task-template": ...}``；
- ``PromptVersionConfig.hot_reload`` 始终为 ``False``（设计决策 #5）。

注：所有用例均通过 ``monkeypatch.setenv`` 注入环境变量并构造 **新实例**
（避免污染 ``prompt_version_config`` 模块级单例）。
"""

# Validates: Requirements 2.1-2.6, 11.2

from __future__ import annotations

import pytest

from infrastructure.prompt.prompt_version_config import (
    InvalidPromptVersionTagError,
    PromptVersionConfig,
)


class TestPromptVersionConfigDefaults:
    """默认值与 ``hot_reload`` 行为相关用例。"""

    def test_default_chat_default_version_is_v1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未设置环境变量时 ``chat_default_version`` 默认 ``v1``。"""
        monkeypatch.delenv("PROMPT_CHAT_DEFAULT_VERSION", raising=False)
        monkeypatch.delenv("PROMPT_TASK_TEMPLATE_VERSION", raising=False)
        monkeypatch.delenv("PROMPT_CONTEXT_SUMMARY_VERSION", raising=False)

        config = PromptVersionConfig()

        assert config.chat_default_version == "v1"

    def test_default_task_template_version_is_v1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未设置环境变量时 ``task_template_version`` 默认 ``v1``。"""
        monkeypatch.delenv("PROMPT_CHAT_DEFAULT_VERSION", raising=False)
        monkeypatch.delenv("PROMPT_TASK_TEMPLATE_VERSION", raising=False)
        monkeypatch.delenv("PROMPT_CONTEXT_SUMMARY_VERSION", raising=False)

        config = PromptVersionConfig()

        assert config.task_template_version == "v1"

    def test_default_context_summary_version_is_v1(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """未设置环境变量时 ``context_summary_version`` 默认 ``v1``。"""
        monkeypatch.delenv("PROMPT_CHAT_DEFAULT_VERSION", raising=False)
        monkeypatch.delenv("PROMPT_TASK_TEMPLATE_VERSION", raising=False)
        monkeypatch.delenv("PROMPT_CONTEXT_SUMMARY_VERSION", raising=False)

        config = PromptVersionConfig()

        assert config.context_summary_version == "v1"

    def test_hot_reload_is_false_by_class_default(self) -> None:
        """``PromptVersionConfig.hot_reload`` 恒为 ``False``（设计决策 #5）。"""
        assert PromptVersionConfig.hot_reload is False


class TestPromptVersionConfigEnvOverride:
    """环境变量覆盖行为用例。"""

    def test_env_override_chat_default_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``PROMPT_CHAT_DEFAULT_VERSION=v3`` 覆盖默认值生效。"""
        monkeypatch.setenv("PROMPT_CHAT_DEFAULT_VERSION", "v3")
        monkeypatch.delenv("PROMPT_TASK_TEMPLATE_VERSION", raising=False)

        config = PromptVersionConfig()

        assert config.chat_default_version == "v3"
        # 未覆盖的字段保持默认值。
        assert config.task_template_version == "v1"

    def test_env_override_task_template_version(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """``PROMPT_TASK_TEMPLATE_VERSION=v10`` 覆盖默认值生效。"""
        monkeypatch.delenv("PROMPT_CHAT_DEFAULT_VERSION", raising=False)
        monkeypatch.setenv("PROMPT_TASK_TEMPLATE_VERSION", "v10")

        config = PromptVersionConfig()

        assert config.task_template_version == "v10"
        assert config.chat_default_version == "v1"

    def test_env_override_both_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """两字段同时被环境变量覆盖时均生效。"""
        monkeypatch.setenv("PROMPT_CHAT_DEFAULT_VERSION", "v2")
        monkeypatch.setenv("PROMPT_TASK_TEMPLATE_VERSION", "v5")

        config = PromptVersionConfig()

        assert config.chat_default_version == "v2"
        assert config.task_template_version == "v5"


class TestPromptVersionConfigValidation:
    """非法格式触发 :class:`InvalidPromptVersionTagError` 用例。"""

    @pytest.mark.parametrize(
        "invalid_value",
        ["v0", "v01", "V1", "", "v1.0.0"],
    )
    def test_invalid_chat_default_version_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        invalid_value: str,
    ) -> None:
        """非法 ``PROMPT_CHAT_DEFAULT_VERSION`` 触发 InvalidPromptVersionTagError。"""
        monkeypatch.setenv("PROMPT_CHAT_DEFAULT_VERSION", invalid_value)
        monkeypatch.delenv("PROMPT_TASK_TEMPLATE_VERSION", raising=False)

        with pytest.raises(InvalidPromptVersionTagError) as exc_info:
            PromptVersionConfig()

        message = str(exc_info.value)
        assert "chat_default_version" in message
        assert repr(invalid_value) in message
        assert "v<正整数>" in message

    @pytest.mark.parametrize(
        "invalid_value",
        ["v0", "v01", "V1", "", "v1.0.0"],
    )
    def test_invalid_task_template_version_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
        invalid_value: str,
    ) -> None:
        """非法 ``PROMPT_TASK_TEMPLATE_VERSION`` 触发 InvalidPromptVersionTagError。"""
        monkeypatch.delenv("PROMPT_CHAT_DEFAULT_VERSION", raising=False)
        monkeypatch.setenv("PROMPT_TASK_TEMPLATE_VERSION", invalid_value)

        with pytest.raises(InvalidPromptVersionTagError) as exc_info:
            PromptVersionConfig()

        message = str(exc_info.value)
        assert "task_template_version" in message
        assert repr(invalid_value) in message


class TestPromptVersionConfigAsMapping:
    """:meth:`PromptVersionConfig.as_mapping` 行为用例。"""

    def test_as_mapping_default_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """默认字段值下 as_mapping 返回两个 Prompt 名的映射。"""
        monkeypatch.delenv("PROMPT_CHAT_DEFAULT_VERSION", raising=False)
        monkeypatch.delenv("PROMPT_TASK_TEMPLATE_VERSION", raising=False)

        config = PromptVersionConfig()

        assert config.as_mapping() == {
            "chat-default": "v1",
            "task-template": "v1",
            "context-summary": "v1",
        }

    def test_as_mapping_with_env_overrides(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """环境变量覆盖后 as_mapping 反映新值，字段名按 ``_`` → ``-`` 还原。"""
        monkeypatch.setenv("PROMPT_CHAT_DEFAULT_VERSION", "v7")
        monkeypatch.setenv("PROMPT_TASK_TEMPLATE_VERSION", "v2")

        config = PromptVersionConfig()

        assert config.as_mapping() == {
            "chat-default": "v7",
            "task-template": "v2",
            "context-summary": "v1",
        }

    def test_invalid_context_summary_version_raises(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """非法 ``PROMPT_CONTEXT_SUMMARY_VERSION`` 触发 InvalidPromptVersionTagError。"""
        monkeypatch.delenv("PROMPT_CHAT_DEFAULT_VERSION", raising=False)
        monkeypatch.delenv("PROMPT_TASK_TEMPLATE_VERSION", raising=False)
        monkeypatch.setenv("PROMPT_CONTEXT_SUMMARY_VERSION", "v0")

        with pytest.raises(InvalidPromptVersionTagError) as exc_info:
            PromptVersionConfig()

        assert "context_summary_version" in str(exc_info.value)
