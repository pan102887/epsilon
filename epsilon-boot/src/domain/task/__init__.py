"""任务领域模块。

本模块提供面向任务的 Agent 入口所需的领域对象，包括：

- TaskStatus：任务执行状态枚举
- Task：任务值对象，封装一次 Agent 执行的完整任务定义
- TraceEntry：执行轨迹条目值对象
- TaskApprovalResumeRequest：任务审批恢复请求值对象
- TaskResult：任务执行结果值对象
- TaskAgentPort：面向任务的 Agent 端口协议
"""

from .ports import TaskAgentPort
from .value_objects import Task, TaskApprovalResumeRequest, TaskResult, TaskStatus, TraceEntry

__all__ = [
    "Task",
    "TaskAgentPort",
    "TaskApprovalResumeRequest",
    "TaskResult",
    "TaskStatus",
    "TraceEntry",
]
