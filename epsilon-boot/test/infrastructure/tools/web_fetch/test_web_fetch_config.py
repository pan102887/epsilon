"""WebFetchConfig 属性测试模块。

使用 Hypothesis 对 WebFetchConfig 的配置读取行为进行属性测试，验证：
- 配置读取正确性：通过环境变量设置的 timeout、max_response_size 和 enabled 能被正确读取
- 默认值正确性：未设置环境变量时默认值分别为 30、51200 和 True
"""

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings

from infrastructure.tools.web_fetch.web_fetch_config import WebFetchConfig

timeout_st = st.integers(min_value=1, max_value=300)
max_response_size_st = st.integers(min_value=1024, max_value=1048576)
enabled_st = st.booleans()


@settings(
    max_examples=100, deadline=5000, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(
    timeout=timeout_st,
    max_response_size=max_response_size_st,
    enabled=enabled_st,
)
def test_config_reads_env_vars_correctly(
    monkeypatch: pytest.MonkeyPatch,
    timeout: int,
    max_response_size: int,
    enabled: bool,
) -> None:
    """验证 WebFetchConfig 能正确读取环境变量中的配置值。"""
    monkeypatch.setenv("WEB_FETCH_TIMEOUT", str(timeout))
    monkeypatch.setenv("WEB_FETCH_MAX_RESPONSE_SIZE", str(max_response_size))
    monkeypatch.setenv("WEB_FETCH_ENABLED", str(enabled))

    config = WebFetchConfig()

    assert config.timeout == timeout
    assert config.max_response_size == max_response_size
    assert config.enabled == enabled


@settings(
    max_examples=100, deadline=5000, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(data=st.data())
def test_config_defaults_when_env_vars_not_set(
    monkeypatch: pytest.MonkeyPatch,
    data: st.DataObject,
) -> None:
    """验证未设置环境变量时 WebFetchConfig 使用正确的默认值。"""
    monkeypatch.delenv("WEB_FETCH_TIMEOUT", raising=False)
    monkeypatch.delenv("WEB_FETCH_MAX_RESPONSE_SIZE", raising=False)
    monkeypatch.delenv("WEB_FETCH_ENABLED", raising=False)

    config = WebFetchConfig()

    assert config.timeout == 30
    assert config.max_response_size == 51200
    assert config.enabled is True
