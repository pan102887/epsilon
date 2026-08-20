"""Agent 注册表适配器模块。

本模块提供 AgentRegistryPort 协议的具体实现，使用内部字典管理
命名 Agent 配置的注册、查找和列举。
"""

from __future__ import annotations

from domain.agent.value_objects import NamedAgentConfig


class AgentRegistryAdapter:
    """Agent 注册表适配器，实现 AgentRegistryPort 协议。

    使用内部字典管理命名 Agent 配置，支持注册、查找和列举。
    同名 Agent 重复注册时覆盖先前的配置。
    """

    def __init__(self) -> None:
        """初始化空的 Agent 注册表。"""
        self._agents: dict[str, NamedAgentConfig] = {}

    def register(self, config: NamedAgentConfig) -> None:
        """注册一个命名 Agent 配置。

        按 config.name 存入内部字典，同名 Agent 重复注册时覆盖。

        Args:
            config: 命名 Agent 配置值对象
        """
        self._agents[config.name] = config

    def get(self, name: str) -> NamedAgentConfig | None:
        """按名称查找已注册的命名 Agent 配置。

        Args:
            name: Agent 唯一标识名称

        Returns:
            对应的 NamedAgentConfig 实例，未找到时返回 None
        """
        return self._agents.get(name)

    def has(self, name: str) -> bool:
        """判断指定名称的 Agent 是否已注册。

        Args:
            name: Agent 唯一标识名称

        Returns:
            已注册返回 True，否则返回 False
        """
        return name in self._agents

    def list_names(self) -> list[str]:
        """返回所有已注册 Agent 的名称列表。

        Returns:
            已注册 Agent 名称的列表
        """
        return list(self._agents.keys())
