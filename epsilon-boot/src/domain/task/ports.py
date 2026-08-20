"""任务 Agent 端口定义。

定义面向任务的 Agent 端口接口（Port），遵循六边形架构原则。
TaskAgentPort 描述"接收 Task、自主执行、返回 TaskResult"的统一接口，
支持有 session_id（关联已有对话上下文）和无 session_id（一次性任务）两种场景，
由基础设施层提供具体的适配器实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from domain.task.value_objects import (
        Task,
        TaskApprovalResumeRequest,
        TaskContinueRequest,
        TaskResult,
    )


class TaskAgentPort(Protocol):
    """面向任务的 Agent 端口协议。

    定义"接收 Task、自主执行、返回 TaskResult"的统一接口。
    支持有 session_id（关联已有对话上下文）和无 session_id（一次性任务）两种场景。

    实现者负责将 Task 转换为 Agent 可执行的格式，委托 AgentPort 执行 Agent Loop，
    并将执行结果转换为结构化的 TaskResult 返回。
    """

    async def execute(self, task: Task) -> TaskResult:
        """执行任务。

        将 Task 转换为 Agent 可执行的格式，委托 AgentPort 执行 Agent Loop，
        将 AgentResult 转换为 TaskResult 返回。

        当 task.session_id 不为 None 时，加载已有对话上下文并在执行后保存；
        当 task.session_id 为 None 时，创建空上下文，执行后不保存。

        Args:
            task: 任务值对象，包含目标描述、输入数据、约束条件和期望输出格式

        Returns:
            TaskResult，包含执行结果、状态和执行轨迹
        """
        ...

    async def continue_task(self, request: TaskContinueRequest) -> TaskResult:
        """基于已有任务会话上下文继续执行。

        继续请求复用已保存的 ConversationContext，不追加原始任务目标，
        并由实现者确保工具访问边界不会被扩大。

        Args:
            request: 任务继续请求值对象，包含会话标识和可选模型。

        Returns:
            TaskResult，包含继续执行后的任务状态、结果和终止原因。
        """
        ...

    async def resume_approval(
        self,
        request: TaskApprovalResumeRequest,
    ) -> TaskResult:
        """提交审批决策并恢复任务 Agent 执行。

        Args:
            request: 任务审批恢复请求值对象，包含审批批次和决策列表。

        Returns:
            TaskResult，包含审批恢复后的任务状态、结果和终止原因。
        """
        ...
