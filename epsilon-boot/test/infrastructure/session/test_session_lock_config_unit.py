"""SessionLockConfig 配置加载单元测试。"""

import pytest

from common.configuration import ConfigurationError


def test_default_value_is_three():
    """默认 conflict_retry_max 为 3。"""
    from infrastructure.session.session_lock_config import SessionLockConfig

    config = SessionLockConfig()
    assert config.conflict_retry_max == 3


def test_negative_value_raises_configuration_error(monkeypatch):
    """conflict_retry_max < 0 时启动校验失败。"""
    monkeypatch.setenv("SESSION_REDIS_CONFLICT_RETRY_MAX", "-1")
    from infrastructure.session.session_lock_config import SessionLockConfig

    with pytest.raises(ConfigurationError):
        SessionLockConfig()


def test_zero_value_is_valid(monkeypatch):
    """conflict_retry_max == 0 合法（表示不重试）。"""
    monkeypatch.setenv("SESSION_REDIS_CONFLICT_RETRY_MAX", "0")
    from infrastructure.session.session_lock_config import SessionLockConfig

    config = SessionLockConfig()
    assert config.conflict_retry_max == 0


def test_custom_value_loaded(monkeypatch):
    """可通过环境变量自定义值。"""
    monkeypatch.setenv("SESSION_REDIS_CONFLICT_RETRY_MAX", "5")
    from infrastructure.session.session_lock_config import SessionLockConfig

    config = SessionLockConfig()
    assert config.conflict_retry_max == 5
