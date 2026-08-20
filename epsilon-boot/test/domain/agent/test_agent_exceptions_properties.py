"""Agent 异常类型属性测试与单元测试模块。

验证 AgentNotFoundError 和 DelegationDepthExceededError 的消息内容、
错误码和继承关系。
"""

from hypothesis import given, settings
from hypothesis import strategies as st

from common.exceptions import BizException
from domain.agent.exceptions import AgentNotFoundError, DelegationDepthExceededError

# ---------------------------------------------------------------------------
# Property 5: 异常消息包含标识信息
# Feature: agent-inter-communication, Property 5: 异常消息包含标识信息
# **Validates: Requirements 5.3, 5.6**
# ---------------------------------------------------------------------------


@settings(max_examples=100, deadline=5000)
@given(
    agent_name=st.text(min_size=1, max_size=50),
    registered_names=st.lists(st.text(min_size=1, max_size=30), max_size=10),
)
def test_agent_not_found_error_message_contains_identifying_info(
    agent_name: str, registered_names: list[str]
) -> None:
    """AgentNotFoundError 的 message 应包含 agent_name 和所有 registered_names。"""
    error = AgentNotFoundError(agent_name, registered_names)

    assert agent_name in error.message
    for name in registered_names:
        assert name in error.message


@settings(max_examples=100, deadline=5000)
@given(
    current_depth=st.integers(min_value=0, max_value=1000),
    max_depth=st.integers(min_value=0, max_value=1000),
    target_agent=st.text(min_size=1, max_size=50),
)
def test_delegation_depth_exceeded_error_message_contains_identifying_info(
    current_depth: int, max_depth: int, target_agent: str
) -> None:
    """DelegationDepthExceededError 的 message 应包含 current_depth、max_depth 和 target_agent。"""
    error = DelegationDepthExceededError(current_depth, max_depth, target_agent)

    assert str(current_depth) in error.message
    assert str(max_depth) in error.message
    assert target_agent in error.message


# ---------------------------------------------------------------------------
# 单元测试：异常错误码和继承关系
# 需求: 5.1, 5.4
# ---------------------------------------------------------------------------


class TestAgentNotFoundErrorCodeAndInheritance:
    """验证 AgentNotFoundError 的错误码和继承关系。"""

    def test_error_code_is_60010(self) -> None:
        error = AgentNotFoundError("test_agent", ["a", "b"])
        assert error.code == 60010

    def test_is_instance_of_biz_exception(self) -> None:
        error = AgentNotFoundError("test_agent", ["a", "b"])
        assert isinstance(error, BizException)


class TestDelegationDepthExceededErrorCodeAndInheritance:
    """验证 DelegationDepthExceededError 的错误码和继承关系。"""

    def test_error_code_is_60011(self) -> None:
        error = DelegationDepthExceededError(3, 3, "target")
        assert error.code == 60011

    def test_is_instance_of_biz_exception(self) -> None:
        error = DelegationDepthExceededError(3, 3, "target")
        assert isinstance(error, BizException)
