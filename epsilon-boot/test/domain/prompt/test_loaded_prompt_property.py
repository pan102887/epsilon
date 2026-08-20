"""LoadedPrompt 值对象属性测试。

使用 Hypothesis 对 :class:`domain.prompt.value_objects.LoadedPrompt` 的
构造校验做覆盖：

- 合法 ``name`` / ``version`` / ``prompt_id`` 组合构造成功；
- 非法样本（``v0`` / ``v01`` / ``v1.0.0`` / 空白 ``content`` /
  ``prompt_id`` 与 ``name@version`` 不一致）均触发 ``ValueError``。

与 ``test_prompt_exceptions_unit.py`` 共同承担领域层 Prompt 模块的
自动化覆盖；不依赖基础设施层或磁盘 I/O。
"""

# Validates: Requirements 3.2, 4.1

from __future__ import annotations

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from domain.prompt.value_objects import LoadedPrompt

# ── Hypothesis 策略 ──

# 合法 name：小写字母开头，后接小写字母/数字/连字符，最长 21 字符；
# 与 domain/prompt/value_objects.py 的 _PROMPT_ID_PATTERN 一致。
_name_st = st.from_regex(r"\A[a-z][a-z0-9\-]{0,20}\Z")

# 合法 version：v + 1-4 位无前导零正整数（1-9999）。
_version_st = st.from_regex(r"\Av[1-9]\d{0,3}\Z")


@settings(max_examples=100, deadline=5000)
@given(name=_name_st, version=_version_st)
def test_valid_name_and_version_construct_successfully(
    name: str,
    version: str,
) -> None:
    """任意合法 ``name`` / ``version`` 组合均能构造 LoadedPrompt 并保留字段。

    Validates: Requirements 3.2, 4.1
    """
    prompt_id = f"{name}@{version}"
    prompt = LoadedPrompt(
        prompt_id=prompt_id,
        name=name,
        version=version,
        content="非空文本",
    )

    assert prompt.prompt_id == prompt_id
    assert prompt.name == name
    assert prompt.version == version
    assert prompt.content == "非空文本"


@pytest.mark.parametrize("invalid_version", ["v0", "v01", "v1.0.0", "V1", "", "v"])
def test_invalid_version_raises_value_error(invalid_version: str) -> None:
    """非法 ``version`` 样本（v0 / v01 / v1.0.0 / 大写 / 空 / 纯 v）触发 ValueError。

    Validates: Requirements 3.2, 4.1
    """
    with pytest.raises(ValueError):
        LoadedPrompt(
            prompt_id=f"chat-default@{invalid_version}",
            name="chat-default",
            version=invalid_version,
            content="非空文本",
        )


@pytest.mark.parametrize("blank_content", ["", " ", "\n", "\t\t  \n"])
def test_blank_content_raises_value_error(blank_content: str) -> None:
    """空串或纯空白 ``content`` 触发 ValueError（需求 3.2）。

    Validates: Requirements 3.2
    """
    with pytest.raises(ValueError, match="content"):
        LoadedPrompt(
            prompt_id="chat-default@v1",
            name="chat-default",
            version="v1",
            content=blank_content,
        )


def test_prompt_id_not_matching_name_and_version_raises_value_error() -> None:
    """``prompt_id`` 与 ``name@version`` 不一致时触发 ValueError（需求 3.2）。

    Validates: Requirements 3.2
    """
    with pytest.raises(ValueError, match="prompt_id 与 name@version 不一致"):
        LoadedPrompt(
            prompt_id="chat-default@v2",
            name="chat-default",
            version="v1",
            content="非空文本",
        )


def test_prompt_id_format_invalid_raises_value_error() -> None:
    """``prompt_id`` 整体格式不符合 ``name@v<N>`` 时触发 ValueError。

    该用例构造一个 ``name`` 以大写字母开头的值对象；
    一致性校验会先通过（``prompt_id`` 等于 ``name@version``），
    随后 ``_PROMPT_ID_PATTERN`` 整体校验失败。

    Validates: Requirements 4.1
    """
    with pytest.raises(ValueError, match="非法 prompt_id 格式"):
        LoadedPrompt(
            prompt_id="Chat-Default@v1",
            name="Chat-Default",
            version="v1",
            content="非空文本",
        )
