"""``AgentConfig`` 校验单元测试模块。

覆盖 PR-3 任务 3.10 / PR-4 任务 4.13：

* ``tool_timeout_seconds = 0`` / ``< 0`` 触发 ``ValueError("tool_timeout_seconds 必须大于 0")``；
* ``max_total_tokens = 0`` / ``< 0`` 触发 ``ValueError("max_total_tokens 必须大于 0")``；
* ``None`` / ``> 0`` 通过校验；
* 既有 ``max_rounds`` / ``prompt_id`` 校验行为不变。
"""

import pytest

from domain.agent.value_objects import AgentConfig


def _base_kwargs(**overrides) -> dict:
    """构造一份合法的 ``AgentConfig`` 关键字参数。"""
    base = {
        "system_prompt": "你是助手",
        "tool_schemas": [],
        "model": "m",
        "max_rounds": 3,
        "prompt_id": "chat-default@v1",
    }
    base.update(overrides)
    return base


# ── tool_timeout_seconds ──


def test_tool_timeout_seconds_default_none() -> None:
    cfg = AgentConfig(**_base_kwargs())
    assert cfg.tool_timeout_seconds is None


def test_tool_timeout_seconds_zero_raises() -> None:
    with pytest.raises(ValueError, match="tool_timeout_seconds 必须大于 0"):
        AgentConfig(**_base_kwargs(tool_timeout_seconds=0))


def test_tool_timeout_seconds_negative_raises() -> None:
    with pytest.raises(ValueError, match="tool_timeout_seconds 必须大于 0"):
        AgentConfig(**_base_kwargs(tool_timeout_seconds=-0.1))


def test_tool_timeout_seconds_positive_accepted() -> None:
    cfg = AgentConfig(**_base_kwargs(tool_timeout_seconds=0.5))
    assert cfg.tool_timeout_seconds == 0.5


# ── max_total_tokens (PR-4) ──


def test_max_total_tokens_default_none() -> None:
    cfg = AgentConfig(**_base_kwargs())
    assert cfg.max_total_tokens is None


def test_max_total_tokens_zero_raises() -> None:
    with pytest.raises(ValueError, match="max_total_tokens 必须大于 0"):
        AgentConfig(**_base_kwargs(max_total_tokens=0))


def test_max_total_tokens_negative_raises() -> None:
    with pytest.raises(ValueError, match="max_total_tokens 必须大于 0"):
        AgentConfig(**_base_kwargs(max_total_tokens=-5))


def test_max_total_tokens_positive_accepted() -> None:
    cfg = AgentConfig(**_base_kwargs(max_total_tokens=500))
    assert cfg.max_total_tokens == 500


# ── 既有校验保持不变 ──


def test_max_rounds_zero_still_raises() -> None:
    with pytest.raises(ValueError, match="max_rounds 必须大于 0"):
        AgentConfig(**_base_kwargs(max_rounds=0))


def test_invalid_prompt_id_still_raises() -> None:
    with pytest.raises(ValueError, match="prompt_id 非法"):
        AgentConfig(**_base_kwargs(prompt_id="invalid-format"))
