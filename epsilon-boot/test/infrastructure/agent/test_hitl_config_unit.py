"""HITL 配置单元测试模块。"""

from infrastructure.agent.hitl_config import DEFAULT_HITL_STATE_TTL_SECONDS, HitlConfig


def test_hitl_config_default_disabled() -> None:
    """验证 HITL 默认关闭。"""
    config = HitlConfig()

    assert config.enabled is False
    assert config.interrupt_on == ""
    assert config.state_ttl_seconds == DEFAULT_HITL_STATE_TTL_SECONDS


def test_hitl_config_ttl_zero_falls_back() -> None:
    """验证 TTL 为 0 时回退默认值。"""
    config = HitlConfig(state_ttl_seconds=0)

    assert config.state_ttl_seconds == DEFAULT_HITL_STATE_TTL_SECONDS


def test_hitl_config_accepts_prefixed_fields() -> None:
    """验证 HITL_ 前缀字段映射后的属性构造。"""
    config = HitlConfig(
        enabled=True,
        interrupt_on='{"write_file": true}',
        state_ttl_seconds=60,
    )

    assert config.enabled is True
    assert config.interrupt_on == '{"write_file": true}'
    assert config.state_ttl_seconds == 60
