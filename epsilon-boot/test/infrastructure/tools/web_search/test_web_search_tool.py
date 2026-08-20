"""WebSearchTool 测试模块。

包含属性测试和单元测试，验证：
- 条件注册正确性（Property 2）
- 搜索结果格式化完整性（Property 3）
- 异常包装正确性（Property 4）
- 接口合规与边界情况（单元测试）
"""

from unittest.mock import MagicMock, patch

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from domain.agent.exceptions import ToolExecutionError
from domain.agent.tools import Tool, ToolRegistry
from infrastructure.tools.web_search.web_search_tool import WebSearchTool

# ── Hypothesis 策略 ──

# 搜索结果条目策略：生成包含 title/url/content 的字典
search_result_st = st.fixed_dictionaries(
    {
        "title": st.text(min_size=1, max_size=100),
        "url": st.text(min_size=1, max_size=200),
        "content": st.text(min_size=1, max_size=500),
    }
)

# 搜索结果列表策略：1~10 条结果
search_results_list_st = st.lists(search_result_st, min_size=1, max_size=10)

# 异常消息策略
exception_message_st = st.text(min_size=1, max_size=200)

# API 密钥策略：包含空字符串和非空字符串
api_key_st = st.text(min_size=0, max_size=100)


# ── Property 2: 条件注册正确性 ──
# Feature: web-search-tool, Property 2: 条件注册正确性
# **Validates: Requirements 1.3, 3.2, 3.3**


@settings(max_examples=100, deadline=5000)
@given(api_key=api_key_st)
def test_conditional_registration(api_key: str) -> None:
    """验证 WebSearchTool 条件注册逻辑的正确性。

    模拟 ``_create_tool_registry()`` 中的条件注册逻辑：
    - 当 api_key 为非空字符串时，ToolRegistry 应包含 ``"web_search"`` 工具
    - 当 api_key 为空字符串时，ToolRegistry 不应包含 ``"web_search"`` 工具
    - 无论 api_key 是否为空，其他已注册工具不受影响

    Args:
        api_key: 随机生成的 API 密钥字符串（含空字符串）。
    """
    with patch("infrastructure.tools.web_search.web_search_tool.TavilyClient"):
        registry = ToolRegistry()

        # 预先注册一个 mock 工具，模拟 filesystem 等其他工具
        other_tool = MagicMock()
        other_tool.name = "mock_tool"
        registry.register(other_tool)

        # 模拟 _create_tool_registry 中的条件注册逻辑
        if api_key:
            registry.register(
                WebSearchTool(
                    api_key=api_key,
                    default_max_results=5,
                )
            )

        # 验证：非空 key 时注册表包含 web_search，空 key 时不包含
        if api_key:
            assert registry.has("web_search"), (
                f"api_key={api_key!r} 非空时，ToolRegistry 应包含 'web_search'"
            )
        else:
            assert not registry.has("web_search"), (
                "api_key 为空时，ToolRegistry 不应包含 'web_search'"
            )

        # 验证：其他工具不受影响
        assert registry.has("mock_tool"), "条件注册不应影响其他已注册工具"


# ── Property 3: 搜索结果格式化完整性 ──
# Feature: web-search-tool, Property 3: 搜索结果格式化完整性
# **Validates: Requirements 2.4, 2.5**


@settings(max_examples=100, deadline=5000)
@given(results=search_results_list_st)
@pytest.mark.asyncio
async def test_format_completeness(results: list[dict]) -> None:
    """验证格式化输出包含每条结果的标题、URL 和内容摘要，且用 --- 分隔。

    对于任意包含 title、url、content 字段的搜索结果列表，
    WebSearchTool 的格式化输出应包含每条结果的所有字段，
    且各结果之间使用 ``---`` 分隔符区分。

    Args:
        results: 随机生成的搜索结果列表。
    """
    with patch("infrastructure.tools.web_search.web_search_tool.TavilyClient") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.search.return_value = {"results": results}

        tool = WebSearchTool(api_key="test-api-key")
        output = (await tool.execute(query="test")).content

    # 验证每条结果的字段都出现在输出中
    for result in results:
        assert result["title"] in output, f"标题 {result['title']!r} 未出现在输出中"
        assert result["url"] in output, f"URL {result['url']!r} 未出现在输出中"
        assert result["content"] in output, f"摘要 {result['content']!r} 未出现在输出中"

    # 验证多条结果之间使用 --- 分隔
    if len(results) > 1:
        assert "---" in output, "多条结果之间应使用 --- 分隔"
        # N 条结果之间至少有 N-1 个分隔符；字段内容本身可能含分隔符，故用 >=
        assert output.count("\n---\n") >= len(results) - 1, (
            f"{len(results)} 条结果之间应至少有 {len(results) - 1} 个分隔符"
        )


# ── Property 4: 异常包装正确性 ──
# Feature: web-search-tool, Property 4: 异常包装正确性
# **Validates: Requirements 2.6**


@settings(max_examples=100, deadline=5000)
@given(error_msg=exception_message_st)
@pytest.mark.asyncio
async def test_exception_wrapping(error_msg: str) -> None:
    """验证 TavilyClient 异常被包装为 ToolExecutionError 且保留原始描述。

    对于任意异常消息，当 TavilyClient.search 抛出异常时，
    execute 应将其包装为 ToolExecutionError，且错误信息包含原始异常描述。

    Args:
        error_msg: 随机生成的异常消息。
    """
    with patch("infrastructure.tools.web_search.web_search_tool.TavilyClient") as mock_cls:
        mock_client = MagicMock()
        mock_cls.return_value = mock_client
        mock_client.search.side_effect = RuntimeError(error_msg)

        tool = WebSearchTool(api_key="test-api-key")

        with pytest.raises(ToolExecutionError) as exc_info:
            await tool.execute(query="test")

        assert error_msg in exc_info.value.message, (
            f"错误信息应包含原始异常描述 {error_msg!r}，实际: {exc_info.value.message!r}"
        )
        assert exc_info.value.tool_name == "web_search"


# ── 单元测试：WebSearchTool 接口合规与边界情况 ──


class TestWebSearchToolInterface:
    """WebSearchTool 接口合规性和边界情况测试。"""

    def _create_tool(self) -> WebSearchTool:
        """创建 Mock 过 TavilyClient 的 WebSearchTool 实例。"""
        with patch("infrastructure.tools.web_search.web_search_tool.TavilyClient"):
            return WebSearchTool(api_key="test-api-key")

    def test_is_instance_of_tool(self) -> None:
        """验证 WebSearchTool 是 Tool 的实例。"""
        tool = self._create_tool()
        assert isinstance(tool, Tool)

    def test_name_returns_web_search(self) -> None:
        """验证 name 属性返回 'web_search'。"""
        tool = self._create_tool()
        assert tool.name == "web_search"

    def test_parameters_schema_structure(self) -> None:
        """验证 parameters 返回正确的 JSON Schema 结构。"""
        tool = self._create_tool()
        params = tool.parameters

        assert params["type"] == "object"
        assert "query" in params["properties"]
        assert params["properties"]["query"]["type"] == "string"
        assert "max_results" in params["properties"]
        assert params["properties"]["max_results"]["type"] == "integer"
        assert "query" in params["required"]

    @pytest.mark.asyncio
    async def test_empty_results_returns_message(self) -> None:
        """验证空结果时返回英文提示。"""
        with patch("infrastructure.tools.web_search.web_search_tool.TavilyClient") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.search.return_value = {"results": []}

            tool = WebSearchTool(api_key="test-api-key")
            result = await tool.execute(query="nonexistent")

        assert result.content == "No relevant search results found."
        assert result.metadata["result_count"] == 0
        # query 截断至 128 字符（design §3.8），此处原样保留短查询。
        assert result.metadata["query"] == "nonexistent"
        assert set(result.metadata.keys()) == {"query", "result_count"}

    @pytest.mark.asyncio
    async def test_metadata_result_count_reflects_returned_results(self) -> None:
        """非空结果时 metadata.result_count 等于返回条目数，query 保留查询文本。"""
        with patch("infrastructure.tools.web_search.web_search_tool.TavilyClient") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.search.return_value = {
                "results": [
                    {"title": "T1", "url": "http://a", "content": "c1"},
                    {"title": "T2", "url": "http://b", "content": "c2"},
                ]
            }

            tool = WebSearchTool(api_key="test-api-key")
            result = await tool.execute(query="python asyncio")

        assert result.metadata["result_count"] == 2
        assert isinstance(result.metadata["result_count"], int)
        assert result.metadata["query"] == "python asyncio"

    @pytest.mark.asyncio
    async def test_metadata_query_truncated_to_128_chars(self) -> None:
        """超长查询在 metadata.query 中截断至 128 字符。"""
        long_query = "q" * 300
        with patch("infrastructure.tools.web_search.web_search_tool.TavilyClient") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.search.return_value = {"results": []}

            tool = WebSearchTool(api_key="test-api-key")
            result = await tool.execute(query=long_query)

        assert result.metadata["query"] == long_query[:128]
        assert len(result.metadata["query"]) == 128

    @pytest.mark.asyncio
    async def test_default_max_results_used(self) -> None:
        """验证不传 max_results 时使用构造函数的 default_max_results。"""
        with patch("infrastructure.tools.web_search.web_search_tool.TavilyClient") as mock_cls:
            mock_client = MagicMock()
            mock_cls.return_value = mock_client
            mock_client.search.return_value = {"results": []}

            tool = WebSearchTool(api_key="test-api-key", default_max_results=8)
            await tool.execute(query="test")

            mock_client.search.assert_called_once_with(query="test", max_results=8)
