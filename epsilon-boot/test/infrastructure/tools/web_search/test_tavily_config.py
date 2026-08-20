"""TavilyConfig 属性测试模块。

使用 Hypothesis 对 TavilyConfig 的配置读取行为进行属性测试，验证：
- 配置读取正确性：通过环境变量设置的 api_key 和 search_max_results 能被正确读取
- 默认值正确性：未设置 TAVILY_SEARCH_MAX_RESULTS 时默认值为 5
"""

import os

import hypothesis.strategies as st
from hypothesis import given, settings

from infrastructure.tools.web_search.tavily_config import TavilyConfig

# ── Hypothesis 策略 ──

# 环境变量安全的文本策略：排除 null 字符和代理字符（surrogate），
# 因为 os.environ 不接受包含 null 字节的值。
env_safe_text_st = st.text(
    alphabet=st.characters(blacklist_categories=("Cs",), blacklist_characters="\x00"),
    min_size=0,
    max_size=100,
)

# Feature: web-search-tool, Property 1: 配置读取正确性
# **Validates: Requirements 1.1, 1.2**


@settings(max_examples=100, deadline=5000)
@given(
    api_key=env_safe_text_st,
    max_results=st.integers(min_value=1, max_value=100),
)
def test_config_reads_env_vars_correctly(
    api_key: str,
    max_results: int,
) -> None:
    """验证 TavilyConfig 能正确读取环境变量中的配置值。

    对于任意有效的 api_key 字符串和 max_results 整数值，
    通过设置对应环境变量后，直接实例化 TavilyConfig
    应读取到与环境变量一致的字段值。

    Args:
        api_key: 随机生成的 API 密钥字符串。
        max_results: 随机生成的最大结果数整数。
    """
    old_key = os.environ.get("TAVILY_API_KEY")
    old_max = os.environ.get("TAVILY_SEARCH_MAX_RESULTS")
    try:
        os.environ["TAVILY_API_KEY"] = api_key
        os.environ["TAVILY_SEARCH_MAX_RESULTS"] = str(max_results)

        config = TavilyConfig()

        assert config.api_key == api_key, (
            f"api_key 不一致: 期望 {api_key!r}, 实际 {config.api_key!r}"
        )
        assert config.search_max_results == max_results, (
            f"search_max_results 不一致: 期望 {max_results}, 实际 {config.search_max_results}"
        )
    finally:
        if old_key is None:
            os.environ.pop("TAVILY_API_KEY", None)
        else:
            os.environ["TAVILY_API_KEY"] = old_key
        if old_max is None:
            os.environ.pop("TAVILY_SEARCH_MAX_RESULTS", None)
        else:
            os.environ["TAVILY_SEARCH_MAX_RESULTS"] = old_max


@settings(max_examples=100, deadline=5000)
@given(
    api_key=env_safe_text_st,
)
def test_config_default_max_results_is_five(
    api_key: str,
) -> None:
    """验证未设置 TAVILY_SEARCH_MAX_RESULTS 时默认值为 5。

    对于任意 api_key 字符串，仅设置 TAVILY_API_KEY 环境变量，
    不设置 TAVILY_SEARCH_MAX_RESULTS，验证 search_max_results 默认值为 5。

    Args:
        api_key: 随机生成的 API 密钥字符串。
    """
    old_key = os.environ.get("TAVILY_API_KEY")
    old_max = os.environ.get("TAVILY_SEARCH_MAX_RESULTS")
    try:
        os.environ["TAVILY_API_KEY"] = api_key
        os.environ.pop("TAVILY_SEARCH_MAX_RESULTS", None)

        config = TavilyConfig()

        assert config.api_key == api_key, (
            f"api_key 不一致: 期望 {api_key!r}, 实际 {config.api_key!r}"
        )
        assert config.search_max_results == 5, (
            f"search_max_results 默认值应为 5, 实际 {config.search_max_results}"
        )
    finally:
        if old_key is None:
            os.environ.pop("TAVILY_API_KEY", None)
        else:
            os.environ["TAVILY_API_KEY"] = old_key
        if old_max is None:
            os.environ.pop("TAVILY_SEARCH_MAX_RESULTS", None)
        else:
            os.environ["TAVILY_SEARCH_MAX_RESULTS"] = old_max
