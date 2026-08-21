"""``ProviderConfig.safety_identifier`` 单元测试（Task 3.3）。

验证 ``_build_params()`` 中 ``safety_identifier`` 的传递逻辑：
- 非空时设置 ``params["user"]``
- 空时不传递 ``user``
- ``extra_params`` 中的 ``user`` 可覆盖 config 默认值
"""

from __future__ import annotations

from unittest.mock import MagicMock

from domain.chat.context import UserMessage
from domain.model_access.value_objects import ChatRequest
from infrastructure.model_access.openai_compatible_adapter import OpenAICompatibleAdapter


def _make_adapter(safety_identifier: str = "") -> OpenAICompatibleAdapter:
    """构造测试用 adapter。"""
    cfg = MagicMock()
    cfg.api_key = "k"
    cfg.api_base = "https://fake/v1"
    cfg.timeout = 30
    cfg.max_retries = 0
    cfg.max_connections = 10
    cfg.max_keepalive_connections = 5
    cfg.provider_name = "test"
    cfg.default_model = "test-model"
    cfg.temperature = 0.7
    cfg.max_tokens = 4096
    cfg.safety_identifier = safety_identifier
    return OpenAICompatibleAdapter(cfg)


def test_safety_identifier_nonempty_sets_user() -> None:
    """safety_identifier 非空时 params 包含 user 字段。"""
    adapter = _make_adapter(safety_identifier="app-session-123")
    request = ChatRequest(messages=[UserMessage(content="hi")])
    params = adapter.build_params(request, stream=False)
    assert params["user"] == "app-session-123"


def test_safety_identifier_empty_no_user() -> None:
    """safety_identifier 为空时 params 不包含 user 字段。"""
    adapter = _make_adapter(safety_identifier="")
    request = ChatRequest(messages=[UserMessage(content="hi")])
    params = adapter.build_params(request, stream=False)
    assert "user" not in params


def test_extra_params_overrides_safety_identifier() -> None:
    """extra_params 中的 user 字段覆盖 config 的 safety_identifier。"""
    adapter = _make_adapter(safety_identifier="config-value")
    request = ChatRequest(
        messages=[UserMessage(content="hi")],
        extra_params={"user": "override-value"},
    )
    params = adapter.build_params(request, stream=False)
    assert params["user"] == "override-value"
