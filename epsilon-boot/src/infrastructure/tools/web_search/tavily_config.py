"""Tavily Web 搜索配置模块。

基于 pydantic-settings，从 config.properties 和环境变量加载以 ``TAVILY_`` 为前缀的配置项。

仅包含 Tavily API 连接参数（API 密钥和搜索结果数量上限）。
模块级实例 ``tavily_config`` 通过 ``create_config`` 工厂函数创建。
由于 API 密钥极少变更，不启用热更新。
"""

from pydantic_settings import SettingsConfigDict

from common.configuration import PropertiesBaseSettings, create_config


class TavilyConfig(PropertiesBaseSettings):
    """Tavily API 配置，对应环境变量前缀 ``TAVILY_``。

    Attributes:
        api_key: Tavily API 密钥，对应 ``TAVILY_API_KEY``，默认空字符串。
        search_max_results: 默认最大返回结果数，对应 ``TAVILY_SEARCH_MAX_RESULTS``，默认 ``5``。
    """

    model_config = SettingsConfigDict(env_prefix="TAVILY_")

    api_key: str = ""
    search_max_results: int = 5


tavily_config = create_config(TavilyConfig)
"""全局 Tavily 配置实例，通过工厂函数创建。"""
