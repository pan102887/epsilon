"""PromptVersionConfig 字段校验属性测试。

使用 Hypothesis 验证 Property 4（设计 §正确性属性 / 需求 2.6、9.7）：
对于任意字段 ``<name>_version``，当且仅当值匹配 ``^v[1-9]\\d*$`` 时构造成功；
``v0`` / ``v01`` / ``v1.0.0`` / 空字符串 / 大写 ``V1`` / ``"v"`` / ``"v-1"``
均触发 :class:`InvalidPromptVersionTagError`。

注：本测试通过 ``monkeypatch.setenv`` 注入候选值并构造新实例，
不污染模块级单例 ``prompt_version_config``。
"""

# Validates: Property 4 / Requirements 2.6, 9.7

from __future__ import annotations

import hypothesis.strategies as st
import pytest
from _pytest.monkeypatch import MonkeyPatch
from hypothesis import HealthCheck, given, settings

from infrastructure.prompt.prompt_version_config import (
    InvalidPromptVersionTagError,
    PromptVersionConfig,
)

# 合法版本号：v + 1-4 位无前导零正整数。
_valid_version_st = st.from_regex(r"\Av[1-9]\d{0,3}\Z")


@settings(
    max_examples=100,
    deadline=5000,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(version=_valid_version_st)
def test_valid_version_tag_constructs_successfully(version: str) -> None:
    """任意合法 ``v<正整数>`` 注入后 PromptVersionConfig 构造成功。

    Hypothesis 与 pytest 的 ``monkeypatch`` fixture 不兼容（function-scoped
    fixture 不会在每次样本间重置）；此处改用 ``MonkeyPatch.context`` 在样本
    内自持 setenv/cleanup，兼容 Hypothesis 的样本隔离语义。

    Validates: Property 4 / Requirements 2.6, 9.7
    """
    with MonkeyPatch.context() as mp:
        mp.setenv("PROMPT_CHAT_DEFAULT_VERSION", version)
        mp.setenv("PROMPT_TASK_TEMPLATE_VERSION", version)

        config = PromptVersionConfig()

        assert config.chat_default_version == version
        assert config.task_template_version == version


@pytest.mark.parametrize(
    "invalid_value",
    [
        "v0",
        "v01",
        "v1.0.0",
        "V1",
        "",
        "v",
        "v-1",
    ],
)
def test_invalid_version_tag_raises_invalid_prompt_version_tag_error(
    invalid_value: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """非法版本号样本全部触发 InvalidPromptVersionTagError。

    覆盖以下失败模式：
    - ``v0`` / ``v01``：前导零或零值（正整数要求排除 ``0`` 与前导零）；
    - ``v1.0.0``：SemVer 风格（需求术语表 Prompt_Version_Tag 明确排除）；
    - ``V1``：大写字母（需求 1.3 大小写敏感）；
    - 空串：空字段值；
    - ``"v"``：缺少数字部分；
    - ``"v-1"``：带负号。

    Validates: Property 4 / Requirements 2.6, 9.7
    """
    monkeypatch.setenv("PROMPT_CHAT_DEFAULT_VERSION", invalid_value)
    monkeypatch.delenv("PROMPT_TASK_TEMPLATE_VERSION", raising=False)

    with pytest.raises(InvalidPromptVersionTagError):
        PromptVersionConfig()
