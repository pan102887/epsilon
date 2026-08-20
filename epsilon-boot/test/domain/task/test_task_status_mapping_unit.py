"""TaskStatusMapping 领域服务单元测试（脱离运行时）。

追溯 需求 4（AC4.1 / AC4.2）与 design Property 3：验证封闭枚举
``TaskStatus`` 的全部 4 个取值映射为 ``TaskOutcomeKind``——
SUCCESS→SUCCEEDED、PAUSED→PAUSED、HUMAN_INTERVENTION_REQUIRED→
AWAITING_APPROVAL、FAILED→FAILED。本测试仅 import ``domain.*``，不触碰运行时。
"""

import pytest

from domain.task.enums import TaskOutcomeKind
from domain.task.policy import TaskStatusMapping
from domain.task.value_objects import TaskStatus


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (TaskStatus.SUCCESS, TaskOutcomeKind.SUCCEEDED),
        (TaskStatus.PAUSED, TaskOutcomeKind.PAUSED),
        (TaskStatus.HUMAN_INTERVENTION_REQUIRED, TaskOutcomeKind.AWAITING_APPROVAL),
        (TaskStatus.FAILED, TaskOutcomeKind.FAILED),
    ],
)
def test_outcome_of(status: TaskStatus, expected: TaskOutcomeKind) -> None:
    """验证 4 个 TaskStatus 取值到 TaskOutcomeKind 的映射逐一等价。"""
    assert TaskStatusMapping.outcome_of(status) is expected


def test_covers_all_task_status_members() -> None:
    """确保断言覆盖 TaskStatus 的全部封闭取值（防新增成员遗漏）。"""
    covered = {
        TaskStatus.SUCCESS,
        TaskStatus.PAUSED,
        TaskStatus.HUMAN_INTERVENTION_REQUIRED,
        TaskStatus.FAILED,
    }
    assert covered == set(TaskStatus)
