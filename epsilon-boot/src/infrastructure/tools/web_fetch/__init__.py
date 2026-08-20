"""Web 抓取工具包。

提供面向网页内容抓取的专用工具实现，供 ToolRegistry 注册使用。
"""

from .web_fetch_tool import WebFetchTool

__all__ = ["WebFetchTool"]
