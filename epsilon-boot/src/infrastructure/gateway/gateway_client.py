"""集群网关/Sidecar 统一 HTTP 客户端。

封装对集群内部服务的 HTTP 调用，统一管理 base_url、超时、重试、
请求头透传等横切关注点。所有通过网关或本地 Sidecar 访问的外部服务
均应通过此客户端发起请求。

典型用法::

    # 在 container_config.py 中注册为异步资源
    gateway = GatewayClient()
    await gateway.start()

    # 在业务 Adapter 中使用
    resp = await gateway.post("/material-service/api/v1/upload", files=...)

    # 应用关闭时清理
    await gateway.stop()

设计要点：

- 基于 httpx.AsyncClient，天然支持异步和连接池复用
- base_url 由 GatewayConfig 统一管理，业务 Adapter 只需关心相对路径
- 作为容器异步资源管理生命周期，确保连接池正确初始化和关闭
- 预留 default_headers 扩展点，便于后续添加 trace-id 透传、认证 token 等
"""

import logging
from typing import Any

import httpx

from infrastructure.gateway.gateway_config import GatewayConfig, gateway_config

logger = logging.getLogger(__name__)


class GatewayClient:
    """集群网关统一 HTTP 客户端。

    提供 GET / POST / PUT / DELETE 等常用方法，自动附加网关 base_url、
    公共请求头和超时配置。底层使用 httpx.AsyncClient 管理连接池。

    Attributes:
        _config: 网关配置实例，包含 base_url、timeout、max_retries。
        _client: httpx 异步客户端实例，在 start() 中初始化。
        _default_headers: 每次请求自动附加的公共请求头。
    """

    def __init__(
        self,
        config: GatewayConfig | None = None,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        """初始化网关客户端。

        Args:
            config: 网关配置，为 None 时使用模块级全局单例 gateway_config。
            default_headers: 每次请求自动附加的公共请求头，
                如 trace-id、认证 token 等。为 None 时使用空字典。
        """
        self._config = config or gateway_config
        self._default_headers = default_headers or {}
        self._client: httpx.AsyncClient | None = None

    @property
    def is_started(self) -> bool:
        """客户端是否已启动（连接池已初始化）。"""
        return self._client is not None

    async def start(self) -> None:
        """初始化底层 HTTP 连接池。

        创建 httpx.AsyncClient 实例，配置 base_url、超时和重试策略。
        此方法应在容器异步资源初始化阶段调用。

        Raises:
            RuntimeError: 如果客户端已经启动。
        """
        if self._client is not None:
            raise RuntimeError("GatewayClient 已启动，请勿重复调用 start()")

        transport = httpx.AsyncHTTPTransport(retries=self._config.max_retries)
        self._client = httpx.AsyncClient(
            base_url=self._config.base_url,
            timeout=httpx.Timeout(self._config.timeout),
            headers=self._default_headers,
            transport=transport,
        )
        logger.info(
            "GatewayClient 已启动，base_url=%s, timeout=%ss, max_retries=%s",
            self._config.base_url,
            self._config.timeout,
            self._config.max_retries,
        )

    async def stop(self) -> None:
        """关闭底层 HTTP 连接池。

        释放所有连接资源。此方法应在容器异步资源清理阶段调用。
        重复调用是安全的（幂等）。
        """
        if self._client is not None:
            await self._client.aclose()
            self._client = None
            logger.info("GatewayClient 已关闭")

    async def replace_client(self, client: httpx.AsyncClient) -> None:
        """Replace the active HTTP client, closing the previous instance.

        This supports alternate transports (for example an in-memory transport in
        tests) while preserving the client's lifecycle ownership rules.
        """
        if self._client is None:
            raise RuntimeError("GatewayClient 尚未启动，请先调用 start()")
        await self._client.aclose()
        self._client = client

    def _ensure_started(self) -> httpx.AsyncClient:
        """确保客户端已启动，返回底层 httpx 客户端。

        Returns:
            已初始化的 httpx.AsyncClient 实例。

        Raises:
            RuntimeError: 如果客户端尚未启动。
        """
        if self._client is None:
            raise RuntimeError("GatewayClient 尚未启动，请先调用 start() 或通过容器管理生命周期")
        return self._client

    async def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        """发起 HTTP 请求。

        这是所有便捷方法（get/post/put/delete）的底层实现。
        自动合并 default_headers 和本次请求的 headers。

        Args:
            method: HTTP 方法，如 "GET"、"POST"。
            path: 请求路径（相对于 base_url），如 "/api/v1/materials"。
            headers: 本次请求额外附加的请求头，会与 default_headers 合并。
            **kwargs: 传递给 httpx.AsyncClient.request 的其他参数，
                如 json、data、files、params 等。

        Returns:
            httpx.Response 响应对象。

        Raises:
            RuntimeError: 如果客户端尚未启动。
            httpx.HTTPStatusError: 如果响应状态码表示错误（需调用方自行处理）。
        """
        client = self._ensure_started()
        merged_headers = {**self._default_headers, **(headers or {})}
        response = await client.request(method, path, headers=merged_headers, **kwargs)
        return response

    async def get(self, path: str, **kwargs: Any) -> httpx.Response:
        """发起 GET 请求。

        Args:
            path: 请求路径（相对于 base_url）。
            **kwargs: 传递给 request() 的其他参数。

        Returns:
            httpx.Response 响应对象。
        """
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> httpx.Response:
        """发起 POST 请求。

        Args:
            path: 请求路径（相对于 base_url）。
            **kwargs: 传递给 request() 的其他参数，常用 json、data、files。

        Returns:
            httpx.Response 响应对象。
        """
        return await self.request("POST", path, **kwargs)

    async def put(self, path: str, **kwargs: Any) -> httpx.Response:
        """发起 PUT 请求。

        Args:
            path: 请求路径（相对于 base_url）。
            **kwargs: 传递给 request() 的其他参数。

        Returns:
            httpx.Response 响应对象。
        """
        return await self.request("PUT", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> httpx.Response:
        """发起 DELETE 请求。

        Args:
            path: 请求路径（相对于 base_url）。
            **kwargs: 传递给 request() 的其他参数。

        Returns:
            httpx.Response 响应对象。
        """
        return await self.request("DELETE", path, **kwargs)
