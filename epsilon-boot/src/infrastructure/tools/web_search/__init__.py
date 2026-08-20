"""Web 搜索工具包。

提供基于 Tavily SDK 的 Web 搜索工具实现，供 ToolRegistry 注册使用。
"""

from .web_search_tool import WebSearchTool

__all__ = ["WebSearchTool"]
