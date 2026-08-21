"""请求/响应日志中间件。

基于纯 ASGI 协议实现，在每次 HTTP 请求的入口和出口分别记录日志，
包含请求方法、路径、查询参数、请求头、请求体、响应状态码、响应头、
响应体和耗时信息。

相比 ``BaseHTTPMiddleware``，纯 ASGI 实现不会破坏流式响应，
也没有额外的性能开销。

对于请求体和响应体，会先按字段递归脱敏，再进行截断保护，超过阈值的报文
只记录前 N 个字符，避免大报文导致日志膨胀。
"""

import json
import logging
import time
from typing import Any, cast

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .logging_config import request_logging_config, response_logging_config

logger = logging.getLogger(__name__)
SENSITIVE_REPLACEMENT = "***"

def _truncate_body_text(text: str) -> str:
    """按配置阈值截断日志报文文本。

    Args:
        text: 已解码并完成必要脱敏的报文文本。

    Returns:
        截断后的日志文本；未超过阈值时返回原文。
    """
    max_size = request_logging_config.max_body_log_size
    if len(text) > max_size:
        return text[:max_size] + f"...(truncated, total {len(text)} chars)"
    return text


def _redact_json_value(value: Any, sensitive_fields: frozenset[str]) -> Any:
    """递归脱敏 JSON 对象中的敏感字段值。

    Args:
        value: 通过 ``json.loads`` 解析得到的任意 JSON 值。
        sensitive_fields: 小写敏感字段名集合。

    Returns:
        已脱敏的 JSON 值；命中敏感字段的 dict value 会被替换为固定占位符。
    """
    if isinstance(value, dict):
        value = cast(dict[Any, Any], value)
        return {
            key: (
                SENSITIVE_REPLACEMENT
                if str(key).lower() in sensitive_fields
                else _redact_json_value(item, sensitive_fields)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        value = cast(list[Any], value)
        return [_redact_json_value(item, sensitive_fields) for item in value]
    return value


def _safe_decode_body(raw: bytes) -> str:
    """将原始字节安全解码为字符串，用于日志输出。

    JSON 对象和数组会先递归脱敏敏感字段，再按
    ``request_logging_config.max_body_log_size`` 截断。非 JSON 文本仅做截断，
    避免正则误伤业务内容。

    Args:
        raw: 原始字节数据。

    Returns:
        解码后的字符串，可能带截断标记。
    """
    text = raw.decode("utf-8", errors="replace")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return _truncate_body_text(text)

    if not isinstance(parsed, (dict, list)):
        return _truncate_body_text(text)

    redacted = _redact_json_value(
        parsed,
        request_logging_config.get_sensitive_body_fields_set(),
    )
    return _truncate_body_text(json.dumps(redacted, ensure_ascii=False))


def safe_decode_body(raw: bytes) -> str:
    """安全解码并脱敏请求体。"""
    return _safe_decode_body(raw)


def _format_body_for_log(chunks: list[bytes], enabled: bool) -> str:
    """按开关格式化请求体或响应体日志内容。

    Args:
        chunks: ASGI 收集到的 body 字节块。
        enabled: 是否允许输出 body 内容。

    Returns:
        日志中的 body 字段值。
    """
    if not enabled:
        return "(disabled)"
    return _safe_decode_body(b"".join(chunks)) or "(empty)"


def _format_headers(raw_headers: list[tuple[bytes, bytes]]) -> str:
    """将 ASGI 原始头列表格式化为可读字符串。

    对敏感头的值进行脱敏，只保留前 8 个字符。
    敏感头列表从 ``request_logging_config.sensitive_headers`` 动态读取。

    Args:
        raw_headers: ASGI 格式的头列表，每个元素为 ``(name_bytes, value_bytes)``。

    Returns:
        格式化后的头字符串，形如 ``{host: localhost, content-type: application/json}``。
    """
    if not raw_headers:
        return "{}"
    sensitive = request_logging_config.get_sensitive_headers_set()
    parts: list[str] = []
    for name_bytes, value_bytes in raw_headers:
        name = name_bytes.decode("latin-1")
        value = value_bytes.decode("latin-1")
        if name.lower() in sensitive:
            value = value[:8] + "***" if len(value) > 8 else "***"
        parts.append(f"{name}: {value}")
    return "{" + ", ".join(parts) + "}"


class RequestLoggingMiddleware:
    """纯 ASGI 请求日志中间件。

    对每个 HTTP 请求记录：
    - 入站日志：方法、路径、查询参数、请求头、请求体
    - 出站日志：方法、路径、状态码、响应头、响应体、处理耗时（毫秒）

    非 HTTP 类型的 ASGI 连接（如 WebSocket、lifespan）会直接透传，不做日志记录。

    敏感头信息（Authorization、Cookie 等）会自动脱敏处理。
    """

    def __init__(self, app: ASGIApp) -> None:
        """初始化中间件。

        Args:
            app: 被包装的下游 ASGI 应用。
        """
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """ASGI 入口，拦截 HTTP 请求并记录完整的请求/响应日志。

        通过包装 ``receive`` 收集请求体，包装 ``send`` 收集响应状态码、
        响应头和响应体，在请求处理完成后统一输出完整日志。

        Args:
            scope: ASGI 连接作用域，包含请求元信息。
            receive: 用于接收客户端消息的可调用对象。
            send: 用于向客户端发送消息的可调用对象。
        """
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "UNKNOWN")
        path = scope.get("path", "/")
        query_string = scope.get("query_string", b"").decode("utf-8", errors="replace")
        query_part = f"?{query_string}" if query_string else ""
        request_headers = _format_headers(scope.get("headers", []))

        # 收集请求体
        request_body_chunks: list[bytes] = []

        async def receive_wrapper() -> Message:
            """包装 receive 回调，拦截请求体数据块。"""
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body", b"")
                if body and request_logging_config.body_enabled:
                    request_body_chunks.append(body)
            return message

        # 收集响应信息
        status_code: int | None = None
        response_headers_raw: list[tuple[bytes, bytes]] = []
        response_body_chunks: list[bytes] = []

        async def send_wrapper(message: Message) -> None:
            """包装 send 回调，拦截响应状态码、响应头和响应体数据块。"""
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message.get("status")
                response_headers_raw.extend(message.get("headers", []))
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body and response_logging_config.body_enabled:
                    response_body_chunks.append(body)
            await send(message)

        start_time = time.perf_counter()

        try:
            await self.app(scope, receive_wrapper, send_wrapper)
        finally:
            elapsed_ms = (time.perf_counter() - start_time) * 1000

            request_body = _format_body_for_log(
                request_body_chunks,
                request_logging_config.body_enabled,
            )
            response_body = _format_body_for_log(
                response_body_chunks,
                response_logging_config.body_enabled,
            )
            response_headers = _format_headers(response_headers_raw)

            logger.info(
                "→ %s %s%s headers=%s body=%s",
                method,
                path,
                query_part,
                request_headers,
                request_body,
            )
            logger.info(
                "← %s %s status=%s duration=%.1fms headers=%s body=%s",
                method,
                path,
                status_code,
                elapsed_ms,
                response_headers,
                response_body,
            )
