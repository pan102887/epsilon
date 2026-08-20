"""Agent 运行时配置模块。

基于 pydantic-settings 从 ``config.properties``、环境变量和 ``.env`` 文件
加载以 ``AGENT_`` 为前缀的配置项，供应用组合根装配委派工具时使用。
"""

from __future__ import annotations

from pydantic import model_validator
from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config
from domain.agent.config_policy import (
    DEFAULT_MAX_DELEGATION_DEPTH as _DEFAULT_MAX_DELEGATION_DEPTH,
)
from domain.agent.config_policy import (
    DelegationDepthNormalizationPolicy,
)

UNLIMITED_HANDOFF_MAX_ROUNDS_SENTINEL = 1_000_000
"""handoff 子 Agent 轮次配置为 0 或负数时使用的“不限制”哨兵值。"""


class AgentRuntimeConfig(PropertiesBaseSettings):
    """Agent 运行时配置，对应环境变量前缀 ``AGENT_``。

    Attributes:
        max_delegation_depth: Agent 间委派最大递归深度，对应
            ``AGENT_MAX_DELEGATION_DEPTH``。当配置值小于等于 0 时回退为默认值 3。
        delegate_tool_enabled: 是否注册 Agent 委派工具，对应
            ``AGENT_DELEGATE_TOOL_ENABLED``。
        handoff_max_rounds: handoff 子 Agent 的最大轮次，对应
            ``AGENT_HANDOFF_MAX_ROUNDS``。配置值小于等于 0 时表示不限制轮次，
            归一化为不可达大数哨兵。
    """

    model_config = SettingsConfigDict(env_prefix="AGENT_")

    max_delegation_depth: int = _DEFAULT_MAX_DELEGATION_DEPTH
    delegate_tool_enabled: bool = True
    handoff_max_rounds: int = UNLIMITED_HANDOFF_MAX_ROUNDS_SENTINEL

    @model_validator(mode="before")
    @classmethod
    def _normalize_limits(cls, values: dict[str, object]) -> dict[str, object]:
        """归一化委派深度与 handoff 子 Agent 轮次上限。

        归一判定上提至 ``domain/agent/config_policy.py`` 的领域服务，本 validator
        仅剩「取值→归一→写回」薄适配。等价性：原实现 ``raw = values.get(...)``
        在键缺失时返回 ``None`` → 不进 if → 不改动；本实现「键缺失 → 不进 if →
        不改动」，键存在且值为 ``None`` 时 ``normalize(None)`` 返回 ``None`` 写回
        （与不改动等价，pydantic 后续用字段默认值）；两者逐一等价。
        """
        if "max_delegation_depth" in values:
            values["max_delegation_depth"] = DelegationDepthNormalizationPolicy.normalize(
                values["max_delegation_depth"]
            )
        if "handoff_max_rounds" in values:
            try:
                if int(values["handoff_max_rounds"]) <= 0:
                    values["handoff_max_rounds"] = UNLIMITED_HANDOFF_MAX_ROUNDS_SENTINEL
            except (TypeError, ValueError):
                pass
        return values


agent_config = create_config(AgentRuntimeConfig)
"""全局 Agent 运行时配置实例，通过项目配置工厂创建。"""
