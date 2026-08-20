"""桩 ``AgentRegistryPort`` 实现。

本模块提供 :class:`StaticAgentRegistry`：按静态映射持有命名 Agent 配置，
用于 Delegation_Correctness 指标样本注入，免去真实 ``AgentRegistry``
的初始化副作用。

结构类型匹配：
    以鸭子类型匹配 ``domain/agent/ports.py`` 中的
    ``AgentRegistryPort``：提供 ``register`` / ``get`` / ``has`` /
    ``list_names`` 四个方法，签名与类型与协议一致。

行为差异：
    协议注释中允许 :meth:`get` 在未注册时返回 ``None``；为方便评测
    样本在"委派目标不存在"场景下直接观察异常路径（对齐
    :class:`domain.agent.exceptions.AgentNotFoundError`），本桩在
    :meth:`get` 未命中时**显式抛出** :class:`AgentNotFoundError`，
    复用领域既有异常类；该行为仍与协议的 ``NamedAgentConfig | None``
    返回类型兼容（总是抛异常，不返回 ``None``）。
"""

from __future__ import annotations

from dataclasses import dataclass, field

from domain.agent.exceptions import AgentNotFoundError
from domain.agent.value_objects import NamedAgentConfig


@dataclass
class StaticAgentRegistry:
    """基于 ``dict`` 的静态命名 Agent 注册表。

    Attributes:
        configs: 名称到 :class:`NamedAgentConfig` 的映射，构造时即填充；
            :meth:`register` 允许样本在运行期动态追加。
    """

    configs: dict[str, NamedAgentConfig] = field(default_factory=dict)

    def register(self, config: NamedAgentConfig) -> None:
        """按 ``config.name`` 注册命名 Agent 配置；同名覆盖。

        Args:
            config: 命名 Agent 配置值对象，``name`` 非空在构造时已保证。
        """

        self.configs[config.name] = config

    def get(self, name: str) -> NamedAgentConfig:
        """按名称查找命名 Agent 配置。

        Args:
            name: Agent 唯一标识名称。

        Returns:
            已注册的 :class:`NamedAgentConfig` 实例。

        Raises:
            AgentNotFoundError: 当 ``name`` 未在注册表中时抛出；异常消息
                与已注册列表由领域既有异常类统一格式化。
        """

        if name in self.configs:
            return self.configs[name]
        raise AgentNotFoundError(agent_name=name, registered_names=list(self.configs.keys()))

    def has(self, name: str) -> bool:
        """判断指定名称的 Agent 是否已注册。

        Args:
            name: Agent 唯一标识名称。

        Returns:
            已注册返回 ``True``，否则返回 ``False``。
        """

        return name in self.configs

    def list_names(self) -> list[str]:
        """返回已注册 Agent 名称的有序列表。

        Returns:
            名称列表，按注册先后顺序返回（``dict`` 保序）。
        """

        return list(self.configs.keys())
