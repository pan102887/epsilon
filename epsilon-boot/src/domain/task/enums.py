"""任务子域中立结局枚举模块。

本模块定义 ``TaskOutcomeKind`` —— 刻画「任务状态 → 领域中立结局」判定
输出的枚举，供 ``domain/task`` 领域服务（见 ``policy.py::TaskStatusMapping``）
返回，再由应用层装配为 ``domain/run`` 的 ``RunStatus`` 或
``ApprovalResumeStoreResult`` 状态字符串。

**关键约束**：本模块刻意**不引用** ``domain/run`` 的 ``RunStatus``，从而避免
``domain/task → domain/run`` 的反向依赖（分层依赖方向见
``docs/steering/ddd-architecture.md``）；跨上下文的状态语义装配是应用层职责，
不下沉领域层。

允许的 import：仅 ``enum.Enum``；禁止引入 ``application`` / ``infrastructure`` /
FastAPI / Pydantic / ``domain.run``。
"""

from __future__ import annotations

from enum import Enum


class TaskOutcomeKind(Enum):
    """任务结局中立类别枚举（领域内判定结果）。

    刻画「任务状态 → 领域中立结局」的判定输出，供应用层再装配为
    domain/run 的 RunStatus 或 ApprovalResumeStoreResult 状态字符串。
    刻意不引用 domain/run 的 RunStatus，避免 domain/task 反向依赖 domain/run。

    Members:
        SUCCEEDED: 任务成功结局（对应 TaskStatus.SUCCESS）。
        PAUSED: 任务暂停结局（对应 TaskStatus.PAUSED）。
        AWAITING_APPROVAL: 等待人工审批结局（对应 HUMAN_INTERVENTION_REQUIRED）。
        FAILED: 任务失败结局（对应 TaskStatus.FAILED 及其余未知状态）。
    """

    SUCCEEDED = "succeeded"
    PAUSED = "paused"
    AWAITING_APPROVAL = "awaiting_approval"
    FAILED = "failed"
