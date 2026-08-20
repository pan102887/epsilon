"""任务基础设施层模块。

本模块提供面向任务的 Agent 适配器实现，包括：

- TaskAgentAdapter：实现 TaskAgentPort 协议，将 Task 转换为 ConversationContext + AgentConfig
  后委托现有 AgentPort 执行，复用已有的 Agent Loop 基础设施。
"""

from .task_agent_adapter import TaskAgentAdapter

__all__ = [
    "TaskAgentAdapter",
]
