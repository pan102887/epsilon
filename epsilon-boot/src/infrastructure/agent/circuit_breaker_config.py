"""工具级 circuit breaker 配置模块。

基于 pydantic-settings 从 ``config.properties``、环境变量和 ``.env`` 文件
加载以 ``TOOL_CB_`` 为前缀的配置项。
"""

from __future__ import annotations

from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config


class CircuitBreakerConfig(PropertiesBaseSettings):
    """工具熔断器配置，对应环境变量前缀 ``TOOL_CB_``。

    Attributes:
        enabled: 是否启用工具熔断器。
        failure_threshold: 连续失败次数达到此阈值后进入 OPEN 状态。
        recovery_timeout_seconds: OPEN 状态持续此时长后进入 HALF_OPEN 探测。
        half_open_max_calls: HALF_OPEN 状态最多允许同时通过的探测调用数。
    """

    model_config = SettingsConfigDict(env_prefix="TOOL_CB_")

    enabled: bool = False
    failure_threshold: int = 5
    recovery_timeout_seconds: float = 30.0
    half_open_max_calls: int = 1


circuit_breaker_config = create_config(CircuitBreakerConfig)
"""全局工具熔断器配置实例。"""
