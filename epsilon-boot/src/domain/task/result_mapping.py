"""任务结果映射领域服务。

本模块承载 ``AgentResult`` 到任务子域结果的纯映射逻辑。它只依赖
``domain.agent`` 与 ``domain.task`` 的领域对象，不感知 application、
infrastructure 或 run 子域装配。
"""

from __future__ import annotations

from domain.agent.value_objects import AgentResult, AgentTerminationReason
from domain.task.policy import TaskContinuationPolicy
from domain.task.value_objects import TaskResult, TaskStatus, TraceEntry


class TaskResultMapper:
    """Agent 执行结果到 Task 结果的纯映射服务。"""

    @staticmethod
    def status_for_agent_result(agent_result: AgentResult) -> TaskStatus:
        """根据 AgentResult 的状态与终止原因映射 TaskStatus。"""
        if agent_result.status == "approval_required":
            return TaskStatus.HUMAN_INTERVENTION_REQUIRED

        terminated_reason: AgentTerminationReason = agent_result.terminated_reason
        if TaskContinuationPolicy.should_pause(terminated_reason):
            return TaskStatus.PAUSED
        return TaskStatus.SUCCESS

    @staticmethod
    def to_task_result(
        *,
        agent_result: AgentResult,
        trace: list[TraceEntry],
        context_can_continue: bool,
        prompt_id: str,
    ) -> TaskResult:
        """把 AgentResult 转换为 TaskResult。

        ``context_can_continue`` 由调用方基于上下文与工具边界判定后传入，
        避免本领域服务依赖聊天上下文、工具注册表等基础设施细节。
        """
        status = TaskResultMapper.status_for_agent_result(agent_result)

        if status is TaskStatus.HUMAN_INTERVENTION_REQUIRED:
            approval = agent_result.approval
            assert approval is not None
            return TaskResult(
                content="",
                status=status,
                model=agent_result.model,
                prompt_id=prompt_id,
                usage=agent_result.usage,
                trace=trace,
                latency_ms=agent_result.latency_ms,
                terminated_reason="completed",
                can_continue=False,
                approval_id=approval.approval_id,
            )

        if status is TaskStatus.SUCCESS:
            return TaskResult(
                content=agent_result.content,
                status=status,
                model=agent_result.model,
                prompt_id=prompt_id,
                usage=agent_result.usage,
                trace=trace,
                latency_ms=agent_result.latency_ms,
                terminated_reason="completed",
                can_continue=False,
            )

        terminated_reason: AgentTerminationReason = agent_result.terminated_reason
        return TaskResult(
            content="",
            status=status,
            model=agent_result.model,
            prompt_id=prompt_id,
            usage=agent_result.usage,
            trace=trace,
            latency_ms=agent_result.latency_ms,
            terminated_reason=terminated_reason,
            can_continue=context_can_continue,
        )
