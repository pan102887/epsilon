"""Web 搜索工具模块。

基于 Tavily Python SDK 实现的 Web 搜索工具，继承 Tool 抽象基类，
为 LLM Agent 提供实时联网搜索能力。

搜索结果以格式化文本返回，包含标题、URL 和内容摘要，
各结果之间使用分隔符区分，便于 LLM 解析和引用。
"""

from typing import Any

from tavily import TavilyClient

from domain.agent.exceptions import ToolExecutionError
from domain.agent.guardrails import ToolRiskLevel
from domain.agent.tools import Tool, ToolExecutionResult


class WebSearchTool(Tool):
    """Web 搜索工具，封装 Tavily API 提供联网搜索能力。

    继承 Tool 抽象基类，实现 name、description、parameters、execute 四个抽象成员。
    在构造时创建 TavilyClient 实例并复用，避免重复初始化开销。

    Attributes:
        _client: Tavily 搜索客户端实例。
        _default_max_results: 默认最大返回结果数，当 execute 未传 max_results 时使用。
    """

    def __init__(self, api_key: str, default_max_results: int = 5) -> None:
        """初始化 Web 搜索工具。

        Args:
            api_key: Tavily API 密钥，传递给 TavilyClient。
            default_max_results: 默认最大返回结果数，默认为 5。
        """
        self._client = TavilyClient(api_key=api_key)
        self._default_max_results = default_max_results

    @property
    def name(self) -> str:
        """返回工具唯一名称。"""
        return "web_search"

    @property
    def risk_level(self) -> ToolRiskLevel:
        """搜索工具为低风险。"""
        return ToolRiskLevel.LOW

    @property
    def description(self) -> str:
        """返回工具功能描述。"""
        return (
            "Search the web for current or external information. Returns result "
            "titles, URLs, and content snippets for follow-up reading or citation."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        """返回符合 JSON Schema 规范的参数描述字典。

        定义两个参数：
        - query: 必填，搜索关键词
        - max_results: 可选，最大返回结果数
        """
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query.",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of results to return.",
                },
            },
            "required": ["query"],
        }

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """执行 Web 搜索并返回格式化结果。

        从 kwargs 提取搜索参数，调用 TavilyClient 执行搜索，
        将结果格式化为包含标题、URL 和摘要的可读文本。

        Args:
            **kwargs: 工具参数，包含 query（必填）和 max_results（可选）。

        Returns:
            :class:`ToolExecutionResult`，``content`` 为格式化的搜索结果
            字符串（各结果之间用 ``---`` 分隔；无结果时为
            "No relevant search results found."）；``metadata`` 含以下键：

            - ``query`` (str): 搜索关键词（截断至 128 字符）。
            - ``result_count`` (int): 返回的结果条数。

        Raises:
            ToolExecutionError: 当 Tavily API 调用失败时抛出，包含原始异常描述。
        """
        query: str = kwargs["query"]
        max_results: int = kwargs.get("max_results", self._default_max_results)

        try:
            response = self._client.search(query=query, max_results=max_results)
            results = response.get("results", [])

            if not results:
                return ToolExecutionResult(
                    content="No relevant search results found.",
                    metadata={"query": query[:128], "result_count": 0},
                )

            formatted = []
            for i, result in enumerate(results, start=1):
                title = result.get("title", "")
                url = result.get("url", "")
                content = result.get("content", "")
                formatted.append(f"[{i}] {title}\nURL: {url}\nSummary: {content}")

            return ToolExecutionResult(
                content="\n---\n".join(formatted),
                metadata={"query": query[:128], "result_count": len(results)},
            )
        except Exception as e:
            raise ToolExecutionError(
                message=f"Web 搜索失败: {e}",
                tool_name=self.name,
            ) from e
