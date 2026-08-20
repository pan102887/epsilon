"""HttpRequestTool 测试模块。

包含属性测试，验证：
- SSRF 私有 IP 拒绝（Property 3）
- 响应内容 Content-Type 分派（Property 4）
- 异常包装正确性（Property 6）
"""

import ipaddress
import json
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from domain.agent.exceptions import ToolExecutionError
from domain.agent.tools import Tool
from infrastructure.tools.http_request.http_request_tool import (
    _PRIVATE_NETWORKS,
    HttpRequestTool,
    _host_block_reason,
    _normalise_header_name,
    _reject_sensitive_headers,
    _sensitive_header_reason,
    process_response,
    validate_url_safety,
)

# ── Hypothesis 策略 ──


@st.composite
def private_ip_strategy(
    draw: st.DrawFn,
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, str]:
    """从私有网段中生成随机 IP 地址。

    随机选取一个私有网段，然后在该网段内生成一个随机主机地址。

    Returns:
        (network, ip_str) 元组：所属网段和生成的 IP 字符串。
    """
    network = draw(st.sampled_from(_PRIVATE_NETWORKS))
    # 网段内的主机地址数量
    num_addresses = network.num_addresses
    # 生成网段内的随机偏移量
    offset = draw(st.integers(min_value=0, max_value=num_addresses - 1))
    ip_addr = network.network_address + offset
    return network, str(ip_addr)


# ── Property 3: SSRF 私有 IP 拒绝 ──
# Feature: http-request-tool, Property 3: SSRF 私有 IP 拒绝
# **Validates: Requirements 3.1, 3.2**


@settings(max_examples=100, deadline=5000)
@given(data=private_ip_strategy())
def test_ssrf_private_ip_rejection(data: tuple) -> None:
    """验证 SSRF 防护对私有 IP 地址的拒绝行为。

    对于任意解析后 IP 地址属于私有网段的 URL，validate_url_safety()
    应抛出 ToolExecutionError，且错误信息中包含 "SSRF" 关键词和被拒绝的 IP 地址。

    策略：从各私有网段生成随机 IP 地址，Mock socket.getaddrinfo 返回该 IP，
    验证 validate_url_safety 拒绝并包含 "SSRF" 和 IP。

    Args:
        data: (network, ip_str) 元组，包含所属网段和生成的 IP 字符串。
    """
    _network, ip_str = data
    ip_obj = ipaddress.ip_address(ip_str)
    is_ipv6 = isinstance(ip_obj, ipaddress.IPv6Address)

    # 根据 IP 版本构造 getaddrinfo 返回值
    if is_ipv6:
        mock_return = [(socket.AF_INET6, socket.SOCK_STREAM, 0, "", (ip_str, 0, 0, 0))]
    else:
        mock_return = [(socket.AF_INET, socket.SOCK_STREAM, 0, "", (ip_str, 0))]

    test_url = "http://example.com/test"

    with patch(
        "infrastructure.tools.http_request.http_request_tool.socket.getaddrinfo",
        return_value=mock_return,
    ):
        with pytest.raises(ToolExecutionError) as exc_info:
            validate_url_safety(test_url)

        # 验证错误信息包含 "SSRF" 关键词
        assert "SSRF" in exc_info.value.message, (
            f"错误信息应包含 'SSRF'，实际: {exc_info.value.message!r}"
        )
        # 验证错误信息包含被拒绝的 IP 地址
        assert ip_str in exc_info.value.message, (
            f"错误信息应包含被拒绝的 IP '{ip_str}'，实际: {exc_info.value.message!r}"
        )


# ── Property 5: 模型可控敏感 Header 前置阻断 ──
# Feature: agent-tool-guardrails, Property 5: 模型可控敏感 Header 在请求前被拒绝
# **Validates: Requirements 4.4, 4.8, 4.9**


@pytest.mark.parametrize(
    ("header_name", "normalised_name"),
    [
        ("Authorization", "authorization"),
        (" authorization ", "authorization"),
        ("COOKIE", "cookie"),
        ("X-API-Key", "x-api-key"),
        ("API-Key", "api-key"),
        ("Proxy-Authorization", "proxy-authorization"),
    ],
)
def test_sensitive_header_helpers_reject_model_controlled_headers(
    header_name: str,
    normalised_name: str,
) -> None:
    """验证敏感 Header 名大小写和空白变体均会被拒绝。"""
    headers = {header_name: "secret-token"}

    assert _normalise_header_name(header_name) == normalised_name
    assert _sensitive_header_reason(headers) == f"sensitive-header: {normalised_name}"

    with pytest.raises(ToolExecutionError) as exc_info:
        _reject_sensitive_headers(headers, tool_name="http_request")

    assert normalised_name in exc_info.value.message
    assert "secret-token" not in exc_info.value.message


@pytest.mark.parametrize(
    "header_name",
    [
        "Authorization",
        " authorization ",
        "COOKIE",
        "X-API-Key",
        "API-Key",
        "Proxy-Authorization",
    ],
)
@pytest.mark.asyncio
async def test_sensitive_header_execute_blocks_before_url_dns_and_request(header_name: str) -> None:
    """验证敏感 Header 阻断早于 URL 校验、DNS 解析和网络请求。"""
    tool = HttpRequestTool()

    with (
        patch(
            "infrastructure.tools.http_request.http_request_tool.validate_url_safety"
        ) as mock_validate,
        patch(
            "infrastructure.tools.http_request.http_request_tool.socket.getaddrinfo"
        ) as mock_getaddrinfo,
        patch.object(tool._client, "request", new_callable=AsyncMock) as mock_request,
        pytest.raises(ToolExecutionError) as exc_info,
    ):
        await tool.execute(
            url="http://example.com",
            headers={header_name: "secret-token"},
        )

    mock_validate.assert_not_called()
    mock_getaddrinfo.assert_not_called()
    mock_request.assert_not_called()
    assert _normalise_header_name(header_name) in exc_info.value.message
    assert "secret-token" not in exc_info.value.message


@pytest.mark.asyncio
async def test_non_sensitive_headers_are_passed_to_request() -> None:
    """验证非敏感 Header 不被拦截，并继续随请求传递。"""
    tool = HttpRequestTool()
    headers = {
        "User-Agent": "agent-tool-test",
        "Accept": "application/json",
    }
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = 200
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.json.return_value = {"ok": True}
    mock_resp.text = '{"ok": true}'
    mock_resp.content = b'{"ok": true}'

    with (
        patch(
            "infrastructure.tools.http_request.http_request_tool.validate_url_safety"
        ) as mock_validate,
        patch.object(tool._client, "request", new_callable=AsyncMock) as mock_request,
    ):
        mock_request.return_value = mock_resp

        result = await tool.execute(url="http://example.com", headers=headers)

    mock_validate.assert_called_once_with("http://example.com", tool_name="http_request")
    mock_request.assert_awaited_once()
    assert mock_request.call_args.kwargs["headers"] == headers
    assert "HTTP 200" in result.content
    assert result.metadata["method"] == "GET"
    assert result.metadata["status_code"] == 200
    assert result.metadata["url"] == "http://example.com"
    assert result.metadata["response_bytes"] == len(b'{"ok": true}')


# ── Hypothesis 策略：Content-Type 分派 ──


@st.composite
def json_response_strategy(draw: st.DrawFn) -> tuple[str, dict, MagicMock]:
    """生成 application/json 类型的 Mock 响应。

    随机生成一个 JSON 字典，构造对应的 Mock httpx.Response。

    Returns:
        (content_type, json_data, mock_response) 元组。
    """
    json_data = draw(
        st.dictionaries(
            st.text(min_size=1, max_size=20),
            st.text(min_size=0, max_size=50),
            min_size=0,
            max_size=10,
        )
    )
    status_code = draw(st.sampled_from([200, 201, 204, 301, 400, 404, 500]))

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.headers = {"content-type": "application/json"}
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_data
    mock_resp.text = json.dumps(json_data, ensure_ascii=False)

    return "application/json", json_data, mock_resp


@st.composite
def html_response_strategy(draw: st.DrawFn) -> tuple[str, str, MagicMock]:
    """生成 text/html 类型的 Mock 响应。

    随机生成文本内容，包裹在 HTML 结构中，构造对应的 Mock httpx.Response。

    Returns:
        (content_type, text_content, mock_response) 元组。
    """
    # 使用字母和数字避免 readability 解析问题
    text_content = draw(
        st.text(
            alphabet=st.characters(whitelist_categories=("L", "N", "Z")),
            min_size=5,
            max_size=200,
        )
    )
    status_code = draw(st.sampled_from([200, 301, 404, 500]))

    html = f"<html><head><title>Test</title></head><body><p>{text_content}</p></body></html>"

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.headers = {"content-type": "text/html"}
    mock_resp.status_code = status_code
    mock_resp.text = html

    return "text/html", text_content, mock_resp


@st.composite
def plain_text_response_strategy(draw: st.DrawFn) -> tuple[str, str, MagicMock]:
    """生成 text/plain 类型的 Mock 响应。

    随机生成纯文本内容，构造对应的 Mock httpx.Response。

    Returns:
        (content_type, text_content, mock_response) 元组。
    """
    text_content = draw(st.text(min_size=1, max_size=200))
    status_code = draw(st.sampled_from([200, 201, 400, 500]))

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.headers = {"content-type": "text/plain"}
    mock_resp.status_code = status_code
    mock_resp.text = text_content

    return "text/plain", text_content, mock_resp


@st.composite
def binary_response_strategy(draw: st.DrawFn) -> tuple[str, int, MagicMock]:
    """生成二进制类型（如 image/png）的 Mock 响应。

    随机生成 Content-Length，构造对应的 Mock httpx.Response。

    Returns:
        (content_type, content_length, mock_response) 元组。
    """
    content_type = draw(
        st.sampled_from(["image/png", "image/jpeg", "application/pdf", "application/octet-stream"])
    )
    content_length = draw(st.integers(min_value=0, max_value=10485760))
    status_code = draw(st.sampled_from([200, 206, 404, 500]))

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.headers = {"content-type": content_type, "content-length": str(content_length)}
    mock_resp.status_code = status_code

    return content_type, content_length, mock_resp


# ── Property 4: 响应内容 Content-Type 分派 ──
# Feature: http-request-tool, Property 4: 响应内容 Content-Type 分派
# **Validates: Requirements 4.1, 4.2, 4.3, 4.4, 4.6**


@settings(max_examples=100, deadline=5000)
@given(data=json_response_strategy())
def test_content_type_dispatch_json(data: tuple) -> None:
    """验证 application/json 响应的 Content-Type 分派行为。

    对于 Content-Type 为 application/json 的响应，process_response 应返回
    格式化的 JSON 字符串（json.dumps 缩进输出），且结果可被 json.loads 解析回原始数据。

    Args:
        data: (content_type, json_data, mock_response) 元组。
    """
    _content_type, json_data, mock_resp = data
    max_size = 1048576  # 1MB，避免截断干扰

    result = process_response(mock_resp, max_size)

    # 验证输出包含格式化的 JSON 文本
    expected_json = json.dumps(json_data, ensure_ascii=False, indent=2)
    assert expected_json in result, (
        f"JSON 响应输出应包含格式化的 JSON 文本，\n期望包含: {expected_json!r}\n实际: {result!r}"
    )

    # 验证输出可被解析回原始数据
    parsed = json.loads(result)
    assert parsed == json_data, (
        f"JSON 响应输出解析后应与原始数据一致，\n期望: {json_data!r}\n实际: {parsed!r}"
    )


@settings(max_examples=100, deadline=5000)
@given(data=html_response_strategy())
def test_content_type_dispatch_html(data: tuple) -> None:
    """验证 text/html 响应的 Content-Type 分派行为。

    对于 Content-Type 为 text/html 的响应，process_response 应使用 readability
    提取正文内容，输出中不应包含 HTML 标签噪音（如 <html>、<body>、<head> 等）。

    Args:
        data: (content_type, text_content, mock_response) 元组。
    """
    _content_type, _text_content, mock_resp = data
    max_size = 1048576

    result = process_response(mock_resp, max_size)

    # 验证输出不包含常见 HTML 结构标签（readability 提取后应去除）
    assert "<html>" not in result.lower(), f"HTML 响应输出不应包含 <html> 标签，实际: {result!r}"
    assert "<head>" not in result.lower(), f"HTML 响应输出不应包含 <head> 标签，实际: {result!r}"
    assert "<body>" not in result.lower(), f"HTML 响应输出不应包含 <body> 标签，实际: {result!r}"


@settings(max_examples=100, deadline=5000)
@given(data=plain_text_response_strategy())
def test_content_type_dispatch_plain_text(data: tuple) -> None:
    """验证 text/plain 响应的 Content-Type 分派行为。

    对于 Content-Type 为 text/plain 的响应，process_response 应直接返回
    原始文本内容，不做任何转换。

    Args:
        data: (content_type, text_content, mock_response) 元组。
    """
    _content_type, text_content, mock_resp = data
    max_size = 1048576

    result = process_response(mock_resp, max_size)

    # 验证输出等于原始文本内容
    assert result == text_content, (
        f"纯文本响应输出应等于原始文本，\n期望: {text_content!r}\n实际: {result!r}"
    )


@settings(max_examples=100, deadline=5000)
@given(data=binary_response_strategy())
def test_content_type_dispatch_binary(data: tuple) -> None:
    """验证二进制类型响应的 Content-Type 分派行为。

    对于 Content-Type 为二进制类型（image/png、application/pdf 等）的响应，
    process_response 应返回元数据字符串，包含 Content-Type 和 Content-Length 信息。

    Args:
        data: (content_type, content_length, mock_response) 元组。
    """
    content_type, content_length, mock_resp = data
    max_size = 1048576

    result = process_response(mock_resp, max_size)

    # 验证输出包含 Content-Type 元数据
    assert "Content-Type" in result, f"二进制响应输出应包含 'Content-Type'，实际: {result!r}"
    assert content_type in result, (
        f"二进制响应输出应包含实际的 Content-Type '{content_type}'，实际: {result!r}"
    )

    # 验证输出包含 Content-Length 元数据
    assert "Content-Length" in result, f"二进制响应输出应包含 'Content-Length'，实际: {result!r}"
    assert str(content_length) in result, (
        f"二进制响应输出应包含实际的 Content-Length '{content_length}'，实际: {result!r}"
    )

    # 验证输出标识为二进制内容
    assert "二进制内容" in result, f"二进制响应输出应包含 '二进制内容' 标识，实际: {result!r}"


# ── Property 5: 响应体截断 ──
# Feature: http-request-tool, Property 5: 响应体截断
# **Validates: Requirements 4.5**


@settings(max_examples=100, deadline=5000)
@given(
    max_size=st.integers(min_value=100, max_value=10000),
    seed_text=st.text(
        alphabet=st.characters(whitelist_categories=("L",)),
        min_size=50,
        max_size=200,
    ),
)
def test_response_truncation(max_size: int, seed_text: str) -> None:
    """验证响应体超过 max_response_size 时的截断行为。

    对于任意 max_response_size 和超过该大小的响应体，process_response 应截断内容，
    且输出末尾包含格式为 "[响应已截断，原始大小: XXX bytes]" 的提示信息，
    其中 XXX 为原始响应体的实际 UTF-8 字节数。

    策略：生成随机 max_response_size，然后通过重复短文本构造超过该大小的 ASCII 文本内容，
    构造 text/plain 类型的 Mock 响应，验证截断行为和提示信息格式。

    Args:
        max_size: 随机生成的响应体大小上限（字节）。
        seed_text: 随机生成的种子文本，通过重复拼接确保超过 max_size。
    """
    # 通过重复种子文本构造超过 max_size 的内容（避免 Hypothesis min_size 上限问题）
    if not seed_text:
        seed_text = "abcdefghij"
    repeat_count = (max_size // len(seed_text.encode("utf-8"))) + 2
    text_content = seed_text * repeat_count

    # 构造 text/plain 类型的 Mock httpx.Response
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.headers = {"content-type": "text/plain"}
    mock_resp.text = text_content

    result = process_response(mock_resp, max_size)

    # 计算原始文本的 UTF-8 字节数
    original_byte_size = len(text_content.encode("utf-8"))

    # 验证输出末尾包含截断提示信息
    expected_suffix = f"[响应已截断，原始大小: {original_byte_size} bytes]"
    assert result.endswith(expected_suffix), (
        f"截断后输出应以 '{expected_suffix}' 结尾，\n实际结尾: {result[-100:]!r}"
    )

    # 验证提示信息中的原始大小与实际 UTF-8 字节数一致
    import re as _re

    match = _re.search(r"\[响应已截断，原始大小: (\d+) bytes\]", result)
    assert match is not None, (
        "输出中应包含截断提示信息格式 "
        f"'[响应已截断，原始大小: XXX bytes]'，\n实际: {result[-100:]!r}"
    )
    reported_size = int(match.group(1))
    assert reported_size == original_byte_size, (
        f"截断提示中的原始大小应为 {original_byte_size}，实际报告: {reported_size}"
    )


# ── Property 6: 异常包装正确性 ──
# Feature: http-request-tool, Property 6: 异常包装正确性
# **Validates: Requirements 2.6**


@settings(max_examples=100, deadline=5000)
@given(
    exception_cls=st.sampled_from(
        [httpx.TimeoutException, httpx.ConnectError, httpx.ReadError, httpx.WriteError]
    ),
    error_message=st.text(min_size=1, max_size=200),
)
@pytest.mark.asyncio
async def test_exception_wrapping(exception_cls: type, error_message: str) -> None:
    """验证 httpx 异常被正确包装为 ToolExecutionError。

    对于任意 httpx 请求过程中抛出的异常（网络超时、连接失败等），
    HttpRequestTool 应将其包装为 ToolExecutionError，且错误信息中包含
    原始异常的描述文本，tool_name 字段为 "http_request"。

    策略：生成随机异常类型和错误消息，Mock validate_url_safety 通过校验，
    Mock httpx.AsyncClient.request 抛出生成的异常，验证包装后的
    ToolExecutionError 保留原始信息且 tool_name 正确。

    Args:
        exception_cls: 随机选取的 httpx 异常类型。
        error_message: 随机生成的错误消息文本。
    """
    tool = HttpRequestTool()

    with (
        patch("infrastructure.tools.http_request.http_request_tool.validate_url_safety"),
        patch.object(tool._client, "request", new_callable=AsyncMock) as mock_request,
    ):
        mock_request.side_effect = exception_cls(error_message)

        with pytest.raises(ToolExecutionError) as exc_info:
            await tool.execute(url="http://example.com")

        # 验证 tool_name 为 "http_request"
        assert exc_info.value.tool_name == "http_request", (
            f"tool_name 应为 'http_request'，实际: {exc_info.value.tool_name!r}"
        )

        # 验证错误信息包含原始异常的描述文本
        assert error_message in exc_info.value.message, (
            f"错误信息应包含原始异常描述 {error_message!r}，实际: {exc_info.value.message!r}"
        )


# ── 单元测试：HttpRequestTool 接口合规与边界情况 ──


class TestHttpRequestToolInterface:
    """HttpRequestTool 接口合规性和边界情况测试。"""

    def _create_tool(self) -> HttpRequestTool:
        """创建 HttpRequestTool 实例。"""
        return HttpRequestTool()

    def test_is_instance_of_tool(self) -> None:
        """验证 HttpRequestTool 是 Tool 的实例。"""
        tool = self._create_tool()
        assert isinstance(tool, Tool)

    def test_name_returns_http_request(self) -> None:
        """验证 name 属性返回 'http_request'。"""
        tool = self._create_tool()
        assert tool.name == "http_request"

    def test_parameters_schema_structure(self) -> None:
        """验证 parameters 返回正确的 JSON Schema 结构。"""
        tool = self._create_tool()
        params = tool.parameters

        assert params["type"] == "object"
        # url: string, required
        assert "url" in params["properties"]
        assert params["properties"]["url"]["type"] == "string"
        # method: string with enum
        assert "method" in params["properties"]
        assert params["properties"]["method"]["type"] == "string"
        assert "enum" in params["properties"]["method"]
        # headers: object
        assert "headers" in params["properties"]
        assert params["properties"]["headers"]["type"] == "object"
        # body: string
        assert "body" in params["properties"]
        assert params["properties"]["body"]["type"] == "string"
        # timeout: integer
        assert "timeout" in params["properties"]
        assert params["properties"]["timeout"]["type"] == "integer"
        # url is required
        assert "url" in params["required"]

    def test_client_is_not_gateway_client(self) -> None:
        """验证构造时创建的 AsyncClient 不是 GatewayClient 实例。"""
        from infrastructure.gateway.gateway_client import GatewayClient

        tool = self._create_tool()
        assert isinstance(tool._client, httpx.AsyncClient)
        assert not isinstance(tool._client, GatewayClient)
        assert tool._client.follow_redirects is False

    @pytest.mark.asyncio
    async def test_default_method_is_get(self) -> None:
        """验证不传 method 时默认使用 GET。"""
        tool = self._create_tool()

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.status_code = 200
        mock_resp.headers = {"content-type": "text/plain"}
        mock_resp.text = "ok"

        with (
            patch("infrastructure.tools.http_request.http_request_tool.validate_url_safety"),
            patch.object(tool._client, "request", new_callable=AsyncMock) as mock_request,
        ):
            mock_request.return_value = mock_resp
            await tool.execute(url="http://example.com")

            mock_request.assert_called_once()
            call_kwargs = mock_request.call_args
            assert call_kwargs.kwargs["method"] == "GET"

    @pytest.mark.asyncio
    async def test_body_json_parse_failure(self) -> None:
        """验证 body 参数 JSON 解析失败时抛出 ToolExecutionError。"""
        tool = self._create_tool()

        with patch("infrastructure.tools.http_request.http_request_tool.validate_url_safety"):
            with pytest.raises(ToolExecutionError) as exc_info:
                await tool.execute(url="http://example.com", method="POST", body="not-valid-json")

            assert "JSON" in exc_info.value.message

    def test_dns_resolution_failure(self) -> None:
        """验证 DNS 解析失败时错误信息包含主机名。"""
        with patch(
            "infrastructure.tools.http_request.http_request_tool.socket.getaddrinfo",
            side_effect=socket.gaierror("Name or service not known"),
        ):
            with pytest.raises(ToolExecutionError) as exc_info:
                validate_url_safety("http://nonexistent.invalid/test")

            assert "nonexistent.invalid" in exc_info.value.message

    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "ftp://example.com/file",
            "gopher://example.com/",
        ],
    )
    def test_rejects_non_http_scheme_without_dns_lookup(self, url: str) -> None:
        """验证只允许 http/https URL，且不为非法 scheme 做 DNS 查询。"""
        with patch(
            "infrastructure.tools.http_request.http_request_tool.socket.getaddrinfo"
        ) as mock_getaddrinfo, pytest.raises(ToolExecutionError) as exc_info:
            validate_url_safety(url)

        mock_getaddrinfo.assert_not_called()
        assert "scheme" in exc_info.value.message

    @pytest.mark.parametrize(
        ("url", "hostname", "expected_reason"),
        [
            ("http://169.254.169.254/latest/meta-data", "169.254.169.254", "metadata"),
            ("http://localhost/", "localhost", "localhost"),
            ("http://LOCALHOST./", "LOCALHOST.", "localhost"),
            ("http://localhost.localdomain/", "localhost.localdomain", "localhost"),
            ("http://127.0.0.1/", "127.0.0.1", "loopback"),
            ("http://10.0.0.1/", "10.0.0.1", "private"),
            ("http://192.168.1.1/", "192.168.1.1", "private"),
            ("http://172.16.0.1/", "172.16.0.1", "private"),
            ("http://169.254.1.1/", "169.254.1.1", "link-local"),
            ("http://0.0.0.0/", "0.0.0.0", "unspecified"),
            ("http://224.0.0.1/", "224.0.0.1", "multicast"),
            ("http://240.0.0.1/", "240.0.0.1", None),
            ("http://100.64.0.1/", "100.64.0.1", "non-global"),
            ("http://[::1]/", "::1", "loopback"),
        ],
    )
    def test_rejects_ssrf_host_targets_without_dns_lookup(
        self,
        url: str,
        hostname: str,
        expected_reason: str | None,
    ) -> None:
        """验证 metadata、localhost 与 IP literal 在 DNS 查询前被拒绝。"""
        host_reason = _host_block_reason(hostname)
        assert host_reason is not None
        if expected_reason is not None:
            assert host_reason == expected_reason

        with patch(
            "infrastructure.tools.http_request.http_request_tool.socket.getaddrinfo"
        ) as mock_getaddrinfo, pytest.raises(ToolExecutionError) as exc_info:
            validate_url_safety(url)

        mock_getaddrinfo.assert_not_called()
        assert "SSRF" in exc_info.value.message
        assert hostname.rstrip(".").casefold() in exc_info.value.message.casefold()

    @pytest.mark.asyncio
    async def test_execute_blocks_ssrf_before_request(self) -> None:
        """验证 execute() 层 SSRF 阻断时不会发起 HTTP 请求。"""
        tool = self._create_tool()

        with (
            patch(
                "infrastructure.tools.http_request.http_request_tool.socket.getaddrinfo"
            ) as mock_getaddrinfo,
            patch.object(tool._client, "request", new_callable=AsyncMock) as mock_request,
            pytest.raises(ToolExecutionError) as exc_info,
        ):
            await tool.execute(url="http://169.254.169.254/latest/meta-data")

        mock_getaddrinfo.assert_not_called()
        mock_request.assert_not_called()
        assert "SSRF" in exc_info.value.message

    def test_checks_all_dns_results_and_rejects_any_unsafe_ip(self) -> None:
        """验证 DNS 多结果中任一不安全 IP 都会导致拒绝。"""
        mock_return = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0)),
        ]

        with patch(
            "infrastructure.tools.http_request.http_request_tool.socket.getaddrinfo",
            return_value=mock_return,
        ), pytest.raises(ToolExecutionError) as exc_info:
            validate_url_safety("https://example.com/page")

        assert "127.0.0.1" in exc_info.value.message

    @pytest.mark.parametrize("url", ["http://example.com/page", "https://example.com/page"])
    def test_accepts_http_and_https_when_all_dns_results_are_public(self, url: str) -> None:
        """验证 http/https URL 在所有 DNS 结果均为公网地址时允许通过。"""
        mock_return = [
            (socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0)),
            (
                socket.AF_INET6,
                socket.SOCK_STREAM,
                0,
                "",
                ("2606:2800:220:1:248:1893:25c8:1946", 0, 0, 0),
            ),
        ]

        with patch(
            "infrastructure.tools.http_request.http_request_tool.socket.getaddrinfo",
            return_value=mock_return,
        ):
            validate_url_safety(url)

    def test_empty_json_response(self) -> None:
        """验证空 JSON 响应 {} 正常格式化。"""
        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.headers = {"content-type": "application/json"}
        mock_resp.json.return_value = {}

        result = process_response(mock_resp, 51200)
        assert result == "{}"

    def test_readability_failure_fallback(self) -> None:
        """验证 readability 提取失败时回退为原始 HTML。"""
        raw_html = "<html><body>raw html</body></html>"

        mock_resp = MagicMock(spec=httpx.Response)
        mock_resp.headers = {"content-type": "text/html"}
        mock_resp.text = raw_html

        with patch(
            "infrastructure.tools.http_request.http_request_tool.ReadabilityDocument",
            side_effect=Exception("readability failed"),
        ):
            result = process_response(mock_resp, 51200)

        assert "raw html" in result


# ── Property 2: 条件注册正确性 ──
# Feature: http-request-tool, Property 2: 条件注册正确性
# **Validates: Requirements 1.4, 5.2, 5.3**


@settings(max_examples=100, deadline=5000)
@given(enabled=st.booleans())
def test_conditional_registration(enabled: bool) -> None:
    """验证 HttpRequestTool 条件注册逻辑的正确性。

    模拟 ``_create_tool_registry()`` 中的条件注册逻辑：
    - 当 enabled 为 True 时，ToolRegistry 应包含 ``"http_request"`` 工具
    - 当 enabled 为 False 时，ToolRegistry 不应包含 ``"http_request"`` 工具
    - 无论 enabled 值如何，其他已注册工具不受影响

    Args:
        enabled: 随机生成的布尔值，模拟 HTTP_REQUEST_ENABLED 配置项。
    """
    from domain.agent.tools import ToolRegistry

    registry = ToolRegistry()

    # 预先注册一个 mock 工具，模拟 filesystem 等其他工具
    other_tool = MagicMock()
    other_tool.name = "mock_tool"
    registry.register(other_tool)

    # 模拟 _create_tool_registry 中的条件注册逻辑
    if enabled:
        registry.register(HttpRequestTool(timeout=30, max_response_size=51200))

    # 验证：enabled=True 时注册表包含 http_request，enabled=False 时不包含
    if enabled:
        assert registry.has("http_request"), "enabled=True 时，ToolRegistry 应包含 'http_request'"
    else:
        assert not registry.has("http_request"), (
            "enabled=False 时，ToolRegistry 不应包含 'http_request'"
        )

    # 验证：其他工具不受影响
    assert registry.has("mock_tool"), "条件注册不应影响其他已注册工具"
