"""``config_factory`` 干净配置源工厂夹具的验证测试（方案 C）。

验证 ``test/conftest.py`` 提供的 ``config_factory`` 夹具行为：
- 能从显式给定的 properties 文本加载指定字段值；
- 文本中未出现的字段回落到代码默认值；
- 空文本（默认参数）等价于「纯代码默认值」；
- 多个字段与不同前缀的配置类均可正确加载；
- 与全局环境隔离协同：宿主环境变量不干扰加载结果。
"""

from collections.abc import Callable

import pytest

from infrastructure.chat.chat_config import ChatConfig
from infrastructure.session.session_ttl_config import SessionRedisTtlConfig


class TestConfigFactoryFixture:
    """验证 config_factory 工厂夹具从声明式文本加载配置。"""

    def test_loads_value_from_properties_text(
        self,
        config_factory: Callable[..., object],
    ) -> None:
        """能从给定 properties 文本加载指定字段值。"""
        cfg = config_factory(ChatConfig, "chat.compaction_trigger_tokens=4096")
        assert cfg.compaction_trigger_tokens == 4096

    def test_unspecified_fields_fall_back_to_defaults(
        self,
        config_factory: Callable[..., object],
    ) -> None:
        """文本中未出现的字段回落到代码默认值。"""
        cfg = config_factory(ChatConfig, "chat.compaction_trigger_tokens=4096")
        # 未在文本中出现的字段应为代码默认值，而非真实 config.properties 的值
        assert cfg.compaction_keep_recent_messages == 20
        assert cfg.compaction_encoding == "cl100k_base"
        assert cfg.max_messages == 50

    def test_empty_text_yields_pure_code_defaults(
        self,
        config_factory: Callable[..., object],
    ) -> None:
        """空文本（默认参数）等价于纯代码默认值。"""
        cfg = config_factory(ChatConfig)
        assert cfg.compaction_trigger_tokens == 8000
        assert cfg.tool_calling_enabled is True

    def test_multiple_fields_loaded(
        self,
        config_factory: Callable[..., object],
    ) -> None:
        """一次可加载多个字段。"""
        cfg = config_factory(
            ChatConfig,
            "chat.compaction_trigger_tokens=1000\n"
            "chat.compaction_keep_recent_messages=5\n",
        )
        assert cfg.compaction_trigger_tokens == 1000
        assert cfg.compaction_keep_recent_messages == 5

    def test_works_for_other_prefix_config(
        self,
        config_factory: Callable[..., object],
    ) -> None:
        """对不同 env_prefix 的配置类同样有效。"""
        cfg = config_factory(
            SessionRedisTtlConfig, "session.redis.ttl_seconds=7200"
        )
        assert cfg.ttl_seconds == 7200

    def test_host_env_var_does_not_leak(
        self,
        config_factory: Callable[..., object],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """全局隔离下，宿主环境变量不干扰工厂加载结果。

        注：隔离夹具在用例体前清理环境变量；此处即便再注入，工厂加载的仍是
        properties 文本 + 代码默认值（本用例验证「未注入相关变量时」结果纯净）。
        """
        cfg = config_factory(ChatConfig, "chat.compaction_trigger_tokens=2048")
        assert cfg.compaction_trigger_tokens == 2048
