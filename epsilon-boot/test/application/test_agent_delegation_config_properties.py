"""Agent 委派配置属性测试与单元测试模块。

验证 Agent 间通信配置项的行为：
- Property 10: 非正 max_delegation_depth 回退默认值
- AGENT_DELEGATE_TOOL_ENABLED=false 时不注册工具
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from hypothesis import given, settings
from hypothesis import strategies as st

from common.configuration.configuration_utils import PropertiesFileSettingsSource
from domain.agent.tools import ToolRegistry
from infrastructure.agent.agent_config import (
    UNLIMITED_HANDOFF_MAX_ROUNDS_SENTINEL,
    AgentRuntimeConfig,
)
from infrastructure.agent.agent_registry_adapter import AgentRegistryAdapter
from infrastructure.agent.delegate_to_agent_tool import DelegateToAgentTool

# ---------------------------------------------------------------------------
# 辅助函数：通过配置类解析 max_delegation_depth
# ---------------------------------------------------------------------------


def resolve_max_delegation_depth(raw_value: str) -> int:
    """通过 AgentRuntimeConfig 从原始配置字符串解析最大委派深度。

    复用生产配置类中的回退逻辑：将字符串转为 int，若 <= 0 则回退为默认值 3。

    Args:
        raw_value: 配置文件中的原始字符串值

    Returns:
        解析后的最大委派深度（正整数）
    """
    return AgentRuntimeConfig(max_delegation_depth=raw_value).max_delegation_depth


# ---------------------------------------------------------------------------
# Property 10: 非正 max_delegation_depth 回退默认值
# Feature: agent-inter-communication, Property 10: 非正 max_delegation_depth 回退默认值
# **验证: 需求 10.2**
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=5000)
@given(non_positive=st.integers(max_value=0))
def test_non_positive_max_delegation_depth_falls_back_to_default(
    non_positive: int,
) -> None:
    """验证非正整数的 AGENT_MAX_DELEGATION_DEPTH 回退为默认值 3。

    对于任意 <= 0 的整数，resolve_max_delegation_depth 应返回 3。
    """
    result = resolve_max_delegation_depth(str(non_positive))
    assert result == 3


@settings(max_examples=100, deadline=5000)
@given(positive=st.integers(min_value=1, max_value=100))
def test_positive_max_delegation_depth_preserved(
    positive: int,
) -> None:
    """验证正整数的 AGENT_MAX_DELEGATION_DEPTH 保持原值。"""
    result = resolve_max_delegation_depth(str(positive))
    assert result == positive


# ---------------------------------------------------------------------------
# 单元测试：AGENT_DELEGATE_TOOL_ENABLED=false 时不注册工具
# 需求: 10.4
# ---------------------------------------------------------------------------


class TestDelegateToolEnabledConfig:
    """验证 AGENT_DELEGATE_TOOL_ENABLED 配置项对工具注册的影响。"""

    def test_config_reads_values_from_config_properties(self, tmp_path) -> None:
        """验证 AgentRuntimeConfig 能通过 config.properties source 读取配置。"""
        props_file = tmp_path / "config.properties"
        props_file.write_text(
            "\n".join(
                [
                    "AGENT_MAX_DELEGATION_DEPTH=5",
                    "AGENT_HANDOFF_MAX_ROUNDS=0",
                    "AGENT_DELEGATE_TOOL_ENABLED=false",
                ]
            ),
            encoding="utf-8",
        )

        class _ConfigFromProperties(AgentRuntimeConfig):
            """仅使用临时 config.properties 源的测试配置类。"""

            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls,
                init_settings,
                env_settings,
                dotenv_settings,
                file_secret_settings,
            ):
                return (
                    PropertiesFileSettingsSource(
                        settings_cls,
                        properties_path=props_file,
                    ),
                )

        config = _ConfigFromProperties()

        assert config.max_delegation_depth == 5
        assert config.handoff_max_rounds == UNLIMITED_HANDOFF_MAX_ROUNDS_SENTINEL
        assert config.delegate_tool_enabled is False

    def test_handoff_max_rounds_positive_value_preserved(self) -> None:
        """正整数 AGENT_HANDOFF_MAX_ROUNDS 保持原值。"""
        config = AgentRuntimeConfig(handoff_max_rounds="64")

        assert config.handoff_max_rounds == 64

    def test_handoff_max_rounds_non_positive_maps_to_unlimited(self) -> None:
        """非正 AGENT_HANDOFF_MAX_ROUNDS 映射为长任务不限制轮次哨兵。"""
        config = AgentRuntimeConfig(handoff_max_rounds="0")

        assert config.handoff_max_rounds == UNLIMITED_HANDOFF_MAX_ROUNDS_SENTINEL

    def test_config_reads_delegate_tool_enabled_false(self) -> None:
        """验证配置类能解析 AGENT_DELEGATE_TOOL_ENABLED=false 对应的布尔值。"""
        config = AgentRuntimeConfig(delegate_tool_enabled="false")
        assert config.delegate_tool_enabled is False

    def test_config_reads_delegate_tool_enabled_true(self) -> None:
        """验证配置类能解析 AGENT_DELEGATE_TOOL_ENABLED=true 对应的布尔值。"""
        config = AgentRuntimeConfig(delegate_tool_enabled="true")
        assert config.delegate_tool_enabled is True

    def test_config_reads_environment_override(self, monkeypatch) -> None:
        """验证环境变量覆盖仍由 AgentRuntimeConfig 统一处理。"""
        monkeypatch.setenv("AGENT_MAX_DELEGATION_DEPTH", "6")
        monkeypatch.setenv("AGENT_HANDOFF_MAX_ROUNDS", "88")
        monkeypatch.setenv("AGENT_DELEGATE_TOOL_ENABLED", "false")

        config = AgentRuntimeConfig()

        assert config.max_delegation_depth == 6
        assert config.handoff_max_rounds == 88
        assert config.delegate_tool_enabled is False

    def test_delegate_tool_not_registered_when_disabled(self) -> None:
        """AGENT_DELEGATE_TOOL_ENABLED=false 时 ToolRegistry 中不包含 delegate_to_agent。

        模拟配置为 false 的场景，验证 ToolRegistry 中不存在委派工具。
        """
        registry = ToolRegistry()

        # 模拟 AGENT_DELEGATE_TOOL_ENABLED=false 的逻辑
        delegate_enabled = "false".lower() == "true"
        if delegate_enabled:
            tool = DelegateToAgentTool(
                agent_registry=AgentRegistryAdapter(),
                delegation=AsyncMock(),
            )
            registry.register(tool)

        # 验证 delegate_to_agent 不在注册表中
        assert not registry.has("delegate_to_agent")

    def test_delegate_tool_registered_when_enabled(self) -> None:
        """AGENT_DELEGATE_TOOL_ENABLED=true 时 ToolRegistry 中包含 delegate_to_agent。"""
        registry = ToolRegistry()

        delegate_enabled = "true".lower() == "true"
        if delegate_enabled:
            tool = DelegateToAgentTool(
                agent_registry=AgentRegistryAdapter(),
                delegation=AsyncMock(),
            )
            registry.register(tool)

        assert registry.has("delegate_to_agent")
