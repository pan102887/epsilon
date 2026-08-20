"""HITL 配置属性测试模块。"""

import hypothesis.strategies as st
from hypothesis import given, settings

from infrastructure.agent.hitl_config import DEFAULT_HITL_STATE_TTL_SECONDS, HitlConfig


@settings(max_examples=100, deadline=5000)
@given(ttl=st.integers(max_value=0))
def test_non_positive_ttl_falls_back(ttl: int) -> None:
    """验证任意非正 TTL 都回退默认值。"""
    config = HitlConfig(state_ttl_seconds=ttl)

    assert config.state_ttl_seconds == DEFAULT_HITL_STATE_TTL_SECONDS


@settings(max_examples=100, deadline=5000)
@given(ttl=st.integers(min_value=1, max_value=86400))
def test_positive_ttl_is_preserved(ttl: int) -> None:
    """验证任意正 TTL 都保留。"""
    config = HitlConfig(state_ttl_seconds=ttl)

    assert config.state_ttl_seconds == ttl
