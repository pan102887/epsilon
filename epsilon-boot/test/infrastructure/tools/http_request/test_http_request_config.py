"""HttpRequestConfig 属性测试模块。

使用 Hypothesis 对 HttpRequestConfig 的配置读取行为进行属性测试，验证：
- 配置读取正确性：通过环境变量设置的 timeout、max_response_size 和 enabled 能被正确读取
- 默认值正确性：未设置环境变量时默认值分别为 30、51200 和 True
"""

import hypothesis.strategies as st
import pytest
from hypothesis import HealthCheck, given, settings

from infrastructure.tools.http_request.http_request_config import HttpRequestConfig

# ── Hypothesis 策略 ──

# timeout 策略：1~300 的整数
timeout_st = st.integers(min_value=1, max_value=300)

# max_response_size 策略：1024~1048576 的整数
max_response_size_st = st.integers(min_value=1024, max_value=1048576)

# enabled 策略：布尔值
enabled_st = st.booleans()

# Feature: http-request-tool, Property 1: 配置读取正确性
# **Validates: Requirements 1.1, 1.2, 1.3**


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
    """验证 HttpRequestConfig 能正确读取环境变量中的配置值。

    对于任意有效的 timeout 整数值、max_response_size 整数值和 enabled 布尔值，
    通过 monkeypatch 设置对应环境变量后，直接实例化 HttpRequestConfig
    应读取到与环境变量一致的字段值。

    Args:
        monkeypatch: pytest 的 monkeypatch fixture，用于安全地设置环境变量。
        timeout: 随机生成的超时秒数。
        max_response_size: 随机生成的响应体大小上限。
        enabled: 随机生成的启用开关布尔值。
    """
    monkeypatch.setenv("HTTP_REQUEST_TIMEOUT", str(timeout))
    monkeypatch.setenv("HTTP_REQUEST_MAX_RESPONSE_SIZE", str(max_response_size))
    monkeypatch.setenv("HTTP_REQUEST_ENABLED", str(enabled))

    config = HttpRequestConfig()

    assert config.timeout == timeout, f"timeout 不一致: 期望 {timeout}, 实际 {config.timeout}"
    assert config.max_response_size == max_response_size, (
        f"max_response_size 不一致: 期望 {max_response_size}, 实际 {config.max_response_size}"
    )
    assert config.enabled == enabled, f"enabled 不一致: 期望 {enabled}, 实际 {config.enabled}"


@settings(
    max_examples=100, deadline=5000, suppress_health_check=[HealthCheck.function_scoped_fixture]
)
@given(data=st.data())
def test_config_defaults_when_env_vars_not_set(
    monkeypatch: pytest.MonkeyPatch,
    data: st.DataObject,
) -> None:
    """验证未设置环境变量时 HttpRequestConfig 使用正确的默认值。

    清除所有 HTTP_REQUEST_ 前缀的环境变量后，直接实例化 HttpRequestConfig，
    验证 timeout 默认值为 30、max_response_size 默认值为 51200、enabled 默认值为 True。

    使用 Hypothesis 的 data 策略驱动多次执行，确保默认值在各种运行条件下保持稳定。

    Args:
        monkeypatch: pytest 的 monkeypatch fixture，用于安全地清除环境变量。
        data: Hypothesis data 策略，驱动多次执行。
    """
    monkeypatch.delenv("HTTP_REQUEST_TIMEOUT", raising=False)
    monkeypatch.delenv("HTTP_REQUEST_MAX_RESPONSE_SIZE", raising=False)
    monkeypatch.delenv("HTTP_REQUEST_ENABLED", raising=False)

    config = HttpRequestConfig()

    assert config.timeout == 30, f"timeout 默认值应为 30, 实际 {config.timeout}"
    assert config.max_response_size == 51200, (
        f"max_response_size 默认值应为 51200, 实际 {config.max_response_size}"
    )
    assert config.enabled is True, f"enabled 默认值应为 True, 实际 {config.enabled}"
