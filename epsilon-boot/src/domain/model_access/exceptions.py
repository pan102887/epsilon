"""模型接入层异常定义。

定义了模型调用过程中可能发生的各类异常，用于标准化错误处理。
"""

from typing import Any

from common.exceptions import BizException


class ModelAccessError(BizException):
    """模型接入基础异常。

    所有模型调用相关的异常都应继承自此类。

    Attributes:
        code: 错误码（默认 50001）
        message: 错误描述信息
        details: 错误上下文详情，用于调试（不应包含敏感信息如 API 密钥）
    """

    def __init__(self, message: str, code: int = 50001, details: dict[str, Any] | None = None):
        super().__init__(code=code, message=message)
        self.details = details or {}


class ModelTimeoutError(ModelAccessError):
    """模型调用超时异常。

    当模型 API 调用超过配置的超时时间时抛出。

    Args:
        timeout_seconds: 超时时长（秒）
        request_info: 请求信息（模型名称、消息数量等）
    """

    def __init__(self, timeout_seconds: float, request_info: dict[str, Any]):
        message = f"模型调用超时（>{timeout_seconds}秒）"
        super().__init__(
            message=message,
            code=50002,
            details={"timeout_seconds": timeout_seconds, **request_info},
        )


class ModelRateLimitError(ModelAccessError):
    """模型速率限制异常。

    当触发模型提供商的速率限制（HTTP 429）时抛出。

    Args:
        retry_after_seconds: 建议的重试等待时间（秒），如果响应头提供了该信息
        request_info: 请求信息（模型名称、状态码等）
    """

    def __init__(
        self, retry_after_seconds: float | None = None, request_info: dict[str, Any] | None = None
    ):
        if retry_after_seconds:
            message = f"模型调用触发速率限制，建议 {retry_after_seconds} 秒后重试"
        else:
            message = "模型调用触发速率限制"

        super().__init__(
            message=message,
            code=50003,
            details={"retry_after_seconds": retry_after_seconds, **(request_info or {})},
        )


class ProviderRegistrationError(ModelAccessError):
    """提供商注册失败异常。

    当提供商注册过程中模型发现失败（重试耗尽）时抛出。

    Args:
        provider_name: 提供商名称
        reason: 失败原因描述
    """

    def __init__(self, provider_name: str, reason: str):
        message = f"提供商 {provider_name} 注册失败: {reason}"
        super().__init__(
            message=message,
            code=50004,
            details={"provider_name": provider_name, "reason": reason},
        )


class NoAvailableModelError(ModelAccessError):
    """无可用模型异常。

    当注册中心中没有任何可用模型时抛出。
    """

    def __init__(self):
        super().__init__(
            message="注册中心中没有任何可用模型",
            code=50005,
            details={},
        )


class ModelConnectionError(ModelAccessError):
    """模型服务连接失败异常。

    当模型服务不可达时抛出，包括但不限于以下场景：

    - 连接被拒绝（Connection refused）
    - DNS 解析失败（DNS resolution failed）
    - 网络不可达（Network unreachable）
    - 连接超时（TCP 层面的连接建立超时，区别于 API 请求超时）

    此异常对应 OpenAI SDK 的 ``APIConnectionError``，该异常不继承自 ``APIError``，
    因此需要单独捕获处理。

    Args:
        reason: 连接失败的原因描述，通常来自底层 SDK 异常的消息
        request_info: 可选的请求上下文信息（如模型名称），用于调试
    """

    def __init__(self, reason: str, request_info: dict[str, Any] | None = None):
        message = f"模型服务连接失败: {reason}"
        super().__init__(
            message=message,
            code=50006,
            details={"reason": reason, **(request_info or {})},
        )


class InvalidToolCallIdError(ModelAccessError):
    """工具调用 id 不合法异常。

    用于刻画"从 LLM Provider / 历史会话快照 / 流式重组结果中得到的
    ``tool_call.id`` 为 ``None`` 或空字符串"这一类违约。所有同步 chat、
    流式 finished 分片重组、历史会话恢复链路上发现 id 违约的位置都
    抛出本异常，**不再裸抛 ``ValueError("id 不能为空")``**。

    错误码 ``50007``，与 ``ModelAccessError`` 同段；application 层可基于
    ``isinstance(exc, InvalidToolCallIdError)`` 单独捕获并转换为面向
    用户的 4xx 友好错误响应,且与既有 ``ModelTimeoutError`` /
    ``ModelRateLimitError`` 不共享类型。

    ``details`` 遵循统一诊断字段集，抛出方填充各链路对应的
    ``source`` / ``provider`` / ``model`` / ``tool_name`` /
    ``tool_call_index`` / ``raw_id_value`` 字段；缺失字段统一填
    ``None``，**不省略键**，便于日志聚合按统一查询命中。``message``
    仅含 ``source`` 与 ``raw_id_value`` 摘要，**不**拼接 API 密钥、
    完整 system prompt、用户原文等敏感字段。

    Attributes:
        code: 错误码，固定为 ``50007``。
        message: 中文错误描述，含 ``source`` 与 ``raw_id_value`` 摘要。
        details: 统一诊断字段集 + 抛出方扩展字段。
    """

    def __init__(
        self,
        source: str,
        raw_id_value: object,
        *,
        provider: str | None = None,
        model: str | None = None,
        tool_name: str | None = None,
        tool_call_index: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        """构造工具调用 id 不合法异常。

        Args:
            source: 抛出方所在链路标识，如 ``"chat_sync"`` /
                ``"stream_finished"`` / ``"history_restore"``，用于
                ELK 聚合按链路分组。
            raw_id_value: 原始违约 ``id`` 字段值（保留 ``None`` 与空串
                的类型差异），便于排障复现。
            provider: 模型 Provider 名称，如 ``"deepseek"``；不适用
                链路填 ``None``。
            model: 目标模型名称；不适用链路填 ``None``。
            tool_name: 违约工具调用对应的工具名称；信息缺失时填 ``None``。
            tool_call_index: 违约工具调用在 SDK 返回 ``tool_calls``
                列表中的序号；信息缺失时填 ``None``。
            extra: 抛出方追加的扩展字段（例如 ``skipped_count`` /
                ``session_id``），与统一字段集 ``update`` 合并。
        """
        message = f"工具调用 id 不合法（source={source}, raw_id_value={raw_id_value!r}）"
        details: dict[str, Any] = {
            "source": source,
            "provider": provider,
            "model": model,
            "tool_name": tool_name,
            "tool_call_index": tool_call_index,
            "raw_id_value": raw_id_value,
        }
        if extra:
            details.update(extra)
        super().__init__(message=message, code=50007, details=details)
