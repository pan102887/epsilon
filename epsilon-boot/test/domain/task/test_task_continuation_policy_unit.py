"""TaskContinuationPolicy 领域服务单元测试（脱离运行时）。

追溯 需求 3（AC3.2）与 design Property 2：验证 ``should_pause`` 对
``AgentTerminationReason`` 各取值的判定——``max_rounds`` /
``token_budget_exceeded`` → True（PAUSED），``completed`` → False（SUCCESS 分支）。
本测试仅 import ``domain.*``，不触碰运行时。
"""

import pytest

from domain.agent.value_objects import AgentTerminationReason
from domain.task.policy import TaskContinuationPolicy


@pytest.mark.parametrize(
    ("terminated_reason", "expected"),
    [
        ("max_rounds", True),
        ("token_budget_exceeded", True),
        ("completed", False),
    ],
)
def test_should_pause(
    terminated_reason: AgentTerminationReason, expected: bool
) -> None:
    """验证 ``should_pause`` 等价于终止原因属于 {max_rounds, token_budget_exceeded}。"""
    assert TaskContinuationPolicy.should_pause(terminated_reason) is expected
