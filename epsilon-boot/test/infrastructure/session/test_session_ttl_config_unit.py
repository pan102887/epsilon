"""SessionRedisTtlConfig 配置加载单元测试。"""

import pytest

from common.configuration import ConfigurationError


def test_default_ttl_seconds_is_3600() -> None:
    """默认 Redis 会话 TTL 为 3600 秒。"""
    from infrastructure.session.session_ttl_config import SessionRedisTtlConfig

    config = SessionRedisTtlConfig()
    assert config.ttl_seconds == 3600


def test_custom_ttl_seconds_loaded_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """可通过环境变量覆盖 Redis 会话 TTL。"""
    monkeypatch.setenv("SESSION_REDIS_TTL_SECONDS", "7200")
    from infrastructure.session.session_ttl_config import SessionRedisTtlConfig

    config = SessionRedisTtlConfig()
    assert config.ttl_seconds == 7200


@pytest.mark.parametrize("value", ["0", "-1"])
def test_non_positive_ttl_seconds_raises_configuration_error(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    """TTL 小于等于 0 时拒绝配置。"""
    monkeypatch.setenv("SESSION_REDIS_TTL_SECONDS", value)
    from infrastructure.session.session_ttl_config import SessionRedisTtlConfig

    with pytest.raises(ConfigurationError):
        SessionRedisTtlConfig()
