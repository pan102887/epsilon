"""Web 抓取工具模块。

基于 httpx 异步客户端实现的网页抓取工具，继承 Tool 抽象基类，
为 LLM Agent 提供专门的网页正文获取能力。

与通用 ``http_request`` 工具不同，本工具聚焦 GET 抓取网页内容，
保留 SSRF 防护与响应大小限制，并返回更适合模型阅读的抓取结果格式。
"""

from typing import Any
from urllib.parse import urljoin

import httpx

from domain.agent.exceptions import ToolExecutionError
from domain.agent.guardrails import ToolRiskLevel
from domain.agent.tools import Tool, ToolExecutionResult
from infrastructure.tools.http_request.http_request_tool import (
    process_response,
    summarize_url,
    validate_url_safety,
)

_MAX_REDIRECTS = 5


class WebFetchTool(Tool):
    """网页抓取工具，封装 httpx 异步客户端提供网页获取能力。

    继承 Tool 抽象基类，实现 name、description、parameters、execute 四个抽象成员。
    本工具仅支持根据 URL 发起 GET 请求，适用于抓取网页正文、文章内容和公开文档页面。

    Attributes:
        _timeout: 默认请求超时秒数，当 execute 未传 timeout 时使用。
        _max_response_size: 响应体大小上限（字节），超过时截断。
        _client: 独立的 httpx 异步客户端实例。
    """

    def __init__(self, timeout: int = 30, max_response_size: int = 51200) -> None:
        """初始化 Web 抓取工具。

        Args:
            timeout: 默认请求超时秒数，默认为 30。
            max_response_size: 响应体大小上限（字节），默认为 51200（50KB）。
        """
        self._timeout = timeout
        self._max_response_size = max_response_size
        self._client = httpx.AsyncClient(follow_redirects=False)

    @property
    def client(self) -> httpx.AsyncClient:
        return self._client

    @property
    def name(self) -> str:
        """返回工具唯一名称。"""
        return "web_fetch"

    @property
    def risk_level(self) -> ToolRiskLevel:
        """网页读取工具为低风险。"""
        return ToolRiskLevel.LOW

    @property
    def description(self) -> str:
        """返回工具功能描述。"""
        return (
            "Fetch readable content from a public webpage. Use this for articles, "
            "documentation, and pages where the exact source content matters."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        """返回符合 JSON Schema 规范的参数描述字典。

        定义两个参数：
        - url: 必填，待抓取网页 URL
        - timeout: 可选，单次请求超时秒数
        """
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Webpage URL to fetch. Must start with http:// or https://.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Fetch timeout in seconds.",
                },
            },
            "required": ["url"],
        }

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """执行网页抓取并返回格式化结果。

        从 kwargs 提取抓取参数，执行 SSRF 安全校验，发起异步 GET 请求，
        并复用现有响应处理逻辑提取网页正文或格式化文本内容。

        Args:
            **kwargs: 工具参数，包含 url（必填）和 timeout（可选）。

        Returns:
            :class:`ToolExecutionResult`，``content`` 为格式化的抓取结果字符串
            （含最终 URL、状态码和处理后内容）；``metadata`` 含以下键：

            - ``url`` (str): 请求 URL（剥离敏感查询参数后截断至 256 字符）。
            - ``response_bytes`` (int): 响应体原始字节数。
            - ``content_type`` (str | None): 响应 Content-Type，缺失时为 None。

        Raises:
            ToolExecutionError: SSRF 校验失败或请求过程中发生异常时抛出。
        """
        url: str = kwargs["url"]
        timeout: int = kwargs.get("timeout", self._timeout)

        try:
            response = await self._get_with_checked_redirects(url=url, timeout=timeout)
            content = process_response(response, self._max_response_size)
            final_url = str(response.url)
            content_type = response.headers.get("content-type")

            body = (
                f"FETCH {response.status_code}\n"
                f"Requested URL: {url}\n"
                f"Final URL: {final_url}\n"
                f"Content-Type: {content_type or '未知'}\n\n"
                f"{content}"
            )
            return ToolExecutionResult(
                content=body,
                metadata={
                    "url": summarize_url(url),
                    "response_bytes": len(response.content),
                    "content_type": content_type,
                },
            )
        except ToolExecutionError:
            raise
        except Exception as e:
            raise ToolExecutionError(
                message=f"网页抓取失败: {e}",
                tool_name=self.name,
            ) from e

    async def _get_with_checked_redirects(self, *, url: str, timeout: int) -> httpx.Response:  # noqa: ASYNC109  # timeout 透传给 httpx 客户端的原生超时机制
        """逐跳校验并跟随 GET 重定向。"""
        current_url = url
        headers = {"User-Agent": ("epsilon-web-fetch/1.0 (compatible; agent webpage fetcher)")}

        for _ in range(_MAX_REDIRECTS + 1):
            validate_url_safety(current_url, tool_name=self.name)
            response = await self._client.get(
                url=current_url,
                headers=headers,
                timeout=timeout,
            )

            if response.status_code not in {301, 302, 303, 307, 308}:
                return response

            location = response.headers.get("location")
            if not location:
                return response

            current_url = urljoin(str(response.url), location)

        raise ToolExecutionError(
            message=f"网页抓取重定向超过最大跳数: {_MAX_REDIRECTS}",
            tool_name=self.name,
        )
