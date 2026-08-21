"""HTTP 请求工具模块。

基于 httpx 异步客户端实现的通用 HTTP 请求工具，继承 Tool 抽象基类，
为 LLM Agent 提供直接访问指定 URL 获取网页内容和调用外部 API 的能力。

支持 GET/POST/PUT/DELETE/PATCH 全部 HTTP 方法，根据响应 Content-Type
自动切换处理策略（JSON 格式化、HTML 正文提取、二进制元数据返回）。

内置 SSRF（Server-Side Request Forgery）防护，在发起请求前限制 URL scheme，
并校验目标 IP 是否为公网地址，防止 Agent 被诱导访问内部服务。
"""

import ipaddress
import json
import re
import socket
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from readability import Document as ReadabilityDocument

from domain.agent.exceptions import ToolExecutionError
from domain.agent.guardrails import ToolRiskLevel
from domain.agent.tools import Tool, ToolExecutionResult

# trace metadata 中 URL 的最大长度，与 design.md §3.1 通用约定一致。
_URL_SUMMARY_MAX_LEN = 256

# 查询参数名命中以下（casefold 后）关键词时，其值在 trace metadata 中被替换为
# "***"，避免 access token / api key / 签名等敏感值随 URL 落盘。
_SENSITIVE_QUERY_KEYWORDS: tuple[str, ...] = (
    "key",
    "secret",
    "password",
    "passwd",
    "token",
    "credential",
    "sig",
    "signature",
    "auth",
)


def _summarize_url(url: str, *, max_len: int = _URL_SUMMARY_MAX_LEN) -> str:
    """将 URL 归约为可安全写入 trace metadata 的摘要串。

    先剥离敏感查询参数值（键名命中 :data:`_SENSITIVE_QUERY_KEYWORDS` 时值替换为
    ``"***"``），再截断至 ``max_len`` 字符。解析失败时回退为原串截断，保证纯字符串
    处理不抛异常。

    Args:
        url: 原始 URL 字符串。
        max_len: 截断上限，默认 :data:`_URL_SUMMARY_MAX_LEN`。

    Returns:
        剥离敏感查询参数并截断后的 URL 摘要串。
    """
    try:
        parsed = urlparse(url)
    except ValueError:
        return url[:max_len]

    if parsed.query:
        redacted_pairs = [
            (key, "***" if any(kw in key.casefold() for kw in _SENSITIVE_QUERY_KEYWORDS) else value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        ]
        parsed = parsed._replace(query=urlencode(redacted_pairs))

    return urlunparse(parsed)[:max_len]


def summarize_url(url: str, *, max_len: int = _URL_SUMMARY_MAX_LEN) -> str:
    """返回可安全写入日志与 trace 的 URL 摘要。"""
    return _summarize_url(url, max_len=max_len)


# 兼容既有测试导入；实际阻断逻辑以 _ip_block_reason 为准。
PRIVATE_NETWORKS = [
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
]

_SENSITIVE_HEADER_NAMES: frozenset[str] = frozenset(
    {
        "authorization",
        "cookie",
        "x-api-key",
        "api-key",
        "proxy-authorization",
    }
)
_METADATA_HOSTS: frozenset[str] = frozenset({"169.254.169.254"})
_LOCALHOST_HOSTS: frozenset[str] = frozenset({"localhost", "localhost.localdomain"})


def normalise_header_name(name: object) -> str:
    """规范化模型传入的 Header 名称，用于敏感 Header 判断。"""
    return str(name).strip().casefold()


def sensitive_header_reason(headers: Mapping[str, Any] | None) -> str | None:
    """返回模型可控敏感 Header 阻断原因，未命中时返回 None。"""
    if headers is None:
        return None

    for header_name in headers:
        normalised_name = normalise_header_name(header_name)
        if normalised_name in _SENSITIVE_HEADER_NAMES:
            return f"sensitive-header: {normalised_name}"
    return None


def reject_sensitive_headers(
    headers: Mapping[str, Any] | None,
    *,
    tool_name: str,
) -> None:
    """拒绝模型参数中的 Authorization/Cookie/API key 等敏感 Header。"""
    reason = sensitive_header_reason(headers)
    if reason is not None:
        raise ToolExecutionError(
            message=f"HTTP 请求安全护栏: 模型参数不允许设置敏感 Header {reason}",
            tool_name=tool_name,
        )


def _ip_block_reason(ip_addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> str | None:
    """返回 IP 不允许被工具访问的原因；公网地址返回 None。"""
    if ip_addr.is_loopback:
        return "loopback"
    if ip_addr.is_link_local:
        return "link-local"
    if ip_addr.is_multicast:
        return "multicast"
    if ip_addr.is_reserved:
        return "reserved"
    if ip_addr.is_unspecified:
        return "unspecified"
    if ip_addr.is_private:
        return "private"
    if not ip_addr.is_global:
        return "non-global"
    return None


def host_block_reason(hostname: str) -> str | None:
    """返回 URL host 语义层面的 SSRF 阻断原因，公网候选返回 None。"""
    normalised_host = hostname.strip().rstrip(".").casefold()
    if normalised_host in _METADATA_HOSTS:
        return "metadata"
    if normalised_host in _LOCALHOST_HOSTS:
        return "localhost"

    try:
        ip_addr = ipaddress.ip_address(normalised_host)
    except ValueError:
        return None
    return _ip_block_reason(ip_addr)


def _reject_unsafe_ip(
    *,
    ip_str: str,
    hostname: str,
    tool_name: str,
) -> None:
    """校验单个 IP 地址，不安全时抛出 ToolExecutionError。"""
    try:
        ip_addr = ipaddress.ip_address(ip_str)
    except ValueError as exc:
        raise ToolExecutionError(
            message=f"URL 解析失败: 无法解析目标 IP {ip_str}",
            tool_name=tool_name,
        ) from exc

    reason = _ip_block_reason(ip_addr)
    if reason is not None:
        raise ToolExecutionError(
            message=(
                f"SSRF 防护: 目标主机 {hostname} 解析到不安全 IP {ip_str} ({reason})，拒绝访问"
            ),
            tool_name=tool_name,
        )


def validate_url_safety(url: str, *, tool_name: str = "http_request") -> None:
    """校验 URL 安全性，防止 SSRF 攻击。

    仅允许 http/https URL，并校验 URL 主机解析得到的所有 IP 均为公网地址。

    Args:
        url: 待校验的目标 URL。
        tool_name: 抛出工具错误时使用的工具名称。

    Raises:
        ToolExecutionError: URL 非 http(s)、DNS 解析失败或指向非公网 IP 时抛出。
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    hostname = parsed.hostname

    if scheme not in {"http", "https"}:
        raise ToolExecutionError(
            message=f"URL scheme 不允许: {parsed.scheme or '(empty)'}，仅支持 http/https",
            tool_name=tool_name,
        )

    if not hostname:
        raise ToolExecutionError(
            message=f"URL 解析失败: 无法提取主机名 ({url})",
            tool_name=tool_name,
        )

    host_reason = host_block_reason(hostname)
    if host_reason is not None:
        raise ToolExecutionError(
            message=f"SSRF 防护: 目标主机 {hostname} ({host_reason}) 不允许访问",
            tool_name=tool_name,
        )

    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        pass
    else:
        return

    # DNS 解析获取 IP 地址
    try:
        addr_infos = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        raise ToolExecutionError(
            message=f"DNS 解析失败: {hostname}",
            tool_name=tool_name,
        ) from exc

    resolved_ips = {
        sockaddr[0]
        for *_, sockaddr in addr_infos
        if isinstance(sockaddr[0], str)
    }
    if not resolved_ips:
        raise ToolExecutionError(
            message=f"DNS 解析失败: {hostname} 未返回可用 IP",
            tool_name=tool_name,
        )

    for ip_str in sorted(resolved_ips):
        _reject_unsafe_ip(ip_str=ip_str, hostname=hostname, tool_name=tool_name)


def process_response(response: httpx.Response, max_size: int) -> str:
    """根据 Content-Type 处理 HTTP 响应内容。

    根据响应头中的 Content-Type 自动选择处理策略：
    - application/json：格式化为缩进的 JSON 字符串
    - text/html：使用 readability-lxml 提取网页正文，去除导航栏、广告等噪音；
      提取失败时回退为原始 HTML
    - 其他 text/* 类型：直接返回原始文本
    - 二进制类型（image/*、application/pdf 等）：返回元数据字符串

    超过 max_size 字节限制时，截断内容并附加截断提示。

    Args:
        response: httpx 响应对象。
        max_size: 响应内容的最大字节数，超过时截断。

    Returns:
        处理后的响应内容字符串。
    """
    content_type = response.headers.get("content-type", "")

    # 二进制类型：返回元数据
    if not content_type.startswith("text/") and "application/json" not in content_type:
        content_length = response.headers.get("content-length", "未知")
        return f"[二进制内容] Content-Type: {content_type}, Content-Length: {content_length} bytes"

    # JSON 响应：格式化输出
    if "application/json" in content_type:
        content = json.dumps(response.json(), ensure_ascii=False, indent=2)
    # HTML 响应：readability 提取正文
    elif content_type.startswith("text/html"):
        html = response.text
        try:
            doc = ReadabilityDocument(html)
            summary = doc.summary()
            content = re.sub(r"<[^>]+>", "", summary)
        except Exception:
            content = html
    # 其他文本类型：直接返回
    else:
        content = response.text

    # 截断检查
    original_size = len(content.encode("utf-8"))
    if original_size > max_size:
        # 按字符逐步截断，确保 UTF-8 编码后不超过 max_size
        truncated = content
        while len(truncated.encode("utf-8")) > max_size:
            truncated = truncated[: len(truncated) - 1]
        content = truncated + f"\n\n[响应已截断，原始大小: {original_size} bytes]"

    return content


class HttpRequestTool(Tool):
    """通用 HTTP 请求工具，封装 httpx 异步客户端提供 HTTP 请求能力。

    继承 Tool 抽象基类，实现 name、description、parameters、execute 四个抽象成员。
    支持 GET/POST/PUT/DELETE/PATCH 全部 HTTP 方法，根据响应 Content-Type
    自动切换处理策略（JSON 格式化、HTML 正文提取、二进制元数据返回）。

    在发起请求前执行 SSRF 安全校验，防止 Agent 被诱导访问内部网络服务。

    Attributes:
        _timeout: 默认请求超时秒数，当 execute 未传 timeout 时使用。
        _max_response_size: 响应体大小上限（字节），超过时截断。
        _client: 独立的 httpx 异步客户端实例。
    """

    def __init__(self, timeout: int = 30, max_response_size: int = 51200) -> None:
        """初始化 HTTP 请求工具。

        创建独立的 httpx.AsyncClient 实例，不复用 GatewayClient 的连接池。

        Args:
            timeout: 默认请求超时秒数，默认为 30。
            max_response_size: 响应体大小上限（字节），默认为 51200（50KB）。
        """
        self._timeout = timeout
        self._max_response_size = max_response_size
        self._client = httpx.AsyncClient(follow_redirects=False)

    @property
    def client(self) -> httpx.AsyncClient:
        """返回工具持有的异步 HTTP 客户端。"""
        return self._client

    @property
    def name(self) -> str:
        """返回工具唯一名称。"""
        return "http_request"

    @property
    def risk_level(self) -> ToolRiskLevel:
        """HTTP 请求工具为高风险。"""
        return ToolRiskLevel.HIGH

    @property
    def description(self) -> str:
        """返回工具功能描述。"""
        return (
            "Send an HTTP request to a public URL to fetch web content or call an "
            "external API. Supports GET, POST, PUT, DELETE, and PATCH. Prefer "
            "web_fetch for reading ordinary webpages."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        """返回符合 JSON Schema 规范的参数描述字典。

        定义五个参数：
        - url: 必填，请求目标 URL
        - method: 可选，HTTP 方法（默认 GET）
        - headers: 可选，自定义请求头
        - body: 可选，请求体 JSON 字符串
        - timeout: 可选，单次请求超时秒数
        """
        return {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "Target URL. Must start with http:// or https://.",
                },
                "method": {
                    "type": "string",
                    "description": (
                        "HTTP method. One of GET, POST, PUT, DELETE, PATCH. Defaults to GET."
                    ),
                    "enum": ["GET", "POST", "PUT", "DELETE", "PATCH"],
                },
                "headers": {
                    "type": "object",
                    "description": "Optional request headers.",
                },
                "body": {
                    "type": "string",
                    "description": "JSON request body string for POST, PUT, or PATCH requests.",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Request timeout in seconds.",
                },
            },
            "required": ["url"],
        }

    async def execute(self, **kwargs: Any) -> ToolExecutionResult:
        """执行 HTTP 请求并返回处理后的响应内容。

        从 kwargs 提取请求参数，执行 SSRF 安全校验，发起异步 HTTP 请求，
        根据响应 Content-Type 自动处理内容并返回格式化结果。

        执行流程：
        1. 提取参数（url、method、headers、body、timeout）
        2. 调用 validate_url_safety() 进行 SSRF 校验
        3. 如有 body 参数，解析为 JSON dict
        4. 使用 httpx.AsyncClient 发起异步请求
        5. 调用 process_response() 处理响应内容
        6. 返回包含状态码和处理后内容的格式化字符串

        Args:
            **kwargs: 工具参数，包含 url（必填）、method、headers、body、timeout（可选）。

        Returns:
            :class:`ToolExecutionResult`，``content`` 为格式化的响应字符串，格式为
            "HTTP {status_code}\\n\\n{处理后内容}"；``metadata`` 含以下键：

            - ``method`` (str): HTTP 方法。
            - ``url`` (str): 请求 URL（剥离敏感查询参数后截断至 256 字符）。
            - ``status_code`` (int): HTTP 状态码。
            - ``response_bytes`` (int): 响应体原始字节数。

        Raises:
            ToolExecutionError: SSRF 校验失败、body JSON 解析失败或请求过程中发生异常时抛出。
        """
        url: str = kwargs["url"]
        method: str = kwargs.get("method", "GET")
        headers: Mapping[str, Any] | None = kwargs.get("headers")
        body: str | None = kwargs.get("body")
        timeout: int = kwargs.get("timeout", self._timeout)

        try:
            reject_sensitive_headers(headers, tool_name=self.name)

            # SSRF 安全校验
            validate_url_safety(url, tool_name=self.name)

            # 解析 body JSON
            json_body = None
            if body is not None:
                try:
                    json_body = json.loads(body)
                except json.JSONDecodeError as e:
                    raise ToolExecutionError(
                        message=f"请求体 JSON 解析失败: {e}",
                        tool_name="http_request",
                    ) from e

            # 发起异步 HTTP 请求
            response = await self._client.request(
                method=method,
                url=url,
                headers=headers,
                json=json_body,
                timeout=timeout,
            )

            # 处理响应内容
            content = process_response(response, self._max_response_size)

            return ToolExecutionResult(
                content=f"HTTP {response.status_code}\n\n{content}",
                metadata={
                    "method": method,
                    "url": _summarize_url(url),
                    "status_code": response.status_code,
                    "response_bytes": len(response.content),
                },
            )

        except ToolExecutionError:
            raise
        except Exception as e:
            raise ToolExecutionError(
                message=f"HTTP 请求失败: {e}",
                tool_name="http_request",
            ) from e
