"""OpenAI 兼容协议模型接入适配器。

基于 OpenAI Python SDK 实现 ``ModelAccessPort``，支持所有兼容 OpenAI Chat Completions API
的模型提供商（如阿里云百炼/Qwen、智谱 GLM、DeepSeek 等）。

适配器通过 ``ProviderConfig`` 注入 API 端点和认证信息，使用 ``AsyncOpenAI`` 客户端
发起异步 HTTP 请求，并将 OpenAI SDK 的响应对象转换为领域层值对象。

典型用法::

    config = create_provider_config("MODEL_QWEN_")
    adapter = OpenAICompatibleAdapter(config)
    response = await adapter.chat(ChatRequest(messages=[...]))
"""

import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, cast

import httpx
import tiktoken
from openai import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AsyncStream,
    RateLimitError,
)
from openai.types.chat import ChatCompletion, ChatCompletionChunk

from common.configuration import ConfigurationError
from domain.chat.context import (
    AssistantMessage,
    BaseMessage,
    ToolMessage,
)
from domain.model_access.exceptions import (
    InvalidToolCallIdError,
    ModelAccessError,
    ModelConnectionError,
    ModelRateLimitError,
    ModelTimeoutError,
)
from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
    StreamingToolCallDelta,
    ToolCallRequest,
)
from infrastructure.model_access._retry import build_retry
from infrastructure.model_access.provider_config import ProviderConfig

logger = logging.getLogger(__name__)

StreamToolCallIdRecoveryMode = Literal["recover", "raise"]
"""流式工具调用 id 缺失时的处理策略。"""


@dataclass(frozen=True)
class _StreamToolCallIdRecovery:
    """流式工具调用 id 兼容恢复结果。"""

    recovered_count: int = 0
    synthetic_ids: tuple[str, ...] = ()

    @property
    def occurred(self) -> bool:
        """是否发生过 id 恢复。"""
        return self.recovered_count > 0


class OpenAICompatibleAdapter:
    """OpenAI 兼容协议模型接入适配器。

    通过 OpenAI Python SDK 的 ``AsyncOpenAI`` 客户端，调用任何兼容
    OpenAI Chat Completions API 的模型服务。适配器负责：

    - 将领域层 ``ChatRequest`` 转换为 OpenAI SDK 调用参数
    - 将 SDK 响应转换为领域层 ``LLMResponse`` / ``StreamingChunk``
    - 统一异常处理，将 SDK 异常映射为领域层异常
    - 通过 :mod:`infrastructure.model_access._retry`（tenacity）对瞬时
      网络/服务错误做"指数退避 + 随机 jitter"重试，覆盖 ``chat`` 与
      ``stream`` 首次握手；``stream`` yield 后中途断流不重试。

    Attributes:
        _client: AsyncOpenAI 客户端实例
        _config: 提供商配置，包含 API 端点、密钥、默认参数等
        _retry_attempts: tenacity 重试上限（含首次）；``<=1`` 时禁用包装
    """

    _MESSAGE_OVERHEAD = 4

    def __init__(
        self,
        config: ProviderConfig,
        retry_attempts: int = 1,
        *,
        tokenizer_encoding: str | None = None,
    ) -> None:
        """初始化适配器。

        根据 ``ProviderConfig`` 创建 ``AsyncOpenAI`` 客户端，配置 API 端点、
        密钥、超时和连接池参数。

        Args:
            config: 提供商配置实例，包含 api_base、api_key、timeout 等参数。
            retry_attempts: tenacity 重试上限（含首次），默认 1（禁用）。
                设置为 ``> 1`` 时启用跨 SDK 层的瞬时错误退避重试，与 OpenAI SDK
                的内置 ``max_retries`` 互不冲突（前者覆盖网络层，后者覆盖
                请求 body 层）。
            tokenizer_encoding: tiktoken encoding 名称，用于 ``count_tokens``
                估算。默认 ``"cl100k_base"``。

        Raises:
            ConfigurationError: ``tokenizer_encoding`` 非法或无法加载时抛出。
        """
        self._config = config
        self._retry_attempts = retry_attempts
        encoding_name = tokenizer_encoding or "cl100k_base"
        try:
            self._tokenizer = tiktoken.get_encoding(encoding_name)
        except Exception as exc:
            raise ConfigurationError(
                f"CHAT_COMPACTION_ENCODING 非法或不可用: {encoding_name}"
            ) from exc
        self._client = AsyncOpenAI(
            api_key=config.api_key,
            base_url=config.api_base,
            timeout=httpx.Timeout(config.timeout, connect=10.0),
            max_retries=config.max_retries,
            http_client=httpx.AsyncClient(
                limits=httpx.Limits(
                    max_connections=config.max_connections,
                    max_keepalive_connections=config.max_keepalive_connections,
                ),
            ),
        )
        # 装饰内部 once 函数：tenacity 在 attempts<=1 时返回恒等装饰器，无开销
        retry = build_retry(retry_attempts)
        self._chat_completion = retry(self._chat_completion_once)
        self._stream_open = retry(self._stream_open_once)
        logger.info(
            "OpenAI 兼容适配器已创建: provider=%s, base_url=%s, retry_attempts=%d",
            config.provider_name,
            config.api_base,
            retry_attempts,
        )

    def set_chat_completion_handler(
        self,
        handler: Callable[[dict[str, Any]], Awaitable[ChatCompletion]],
    ) -> None:
        """Replace the completion handler used by :meth:`chat`.

        This is primarily useful for deterministic transports and tests that
        exercise response conversion without making a network request.
        """
        self._chat_completion = handler

    async def chat(self, request: ChatRequest) -> LLMResponse:
        """同步对话接口。

        将 ``ChatRequest`` 转换为 OpenAI Chat Completions API 调用，
        等待完整响应后转换为 ``LLMResponse`` 返回。

        Args:
            request: 对话请求，包含消息列表和可选参数。

        Returns:
            完整的对话响应，包含回复内容、token 用量和耗时信息。

        Raises:
            ModelTimeoutError: 请求超时。
            ModelRateLimitError: 触发速率限制（HTTP 429）。
            ModelConnectionError: 模型服务不可达（连接被拒绝、DNS 解析失败等）。
            ModelAccessError: 其他模型调用错误。
        """
        params = self._build_params(request, stream=False)
        start = time.monotonic()
        completion = await self._chat_completion(params)

        latency_ms = (time.monotonic() - start) * 1000
        choice = completion.choices[0]
        message = choice.message

        # 解析 tool_calls：在构造 ToolCallRequest 之前对 id 做前置校验，
        # 把 Provider 返回的 None / 空串统一暴露为 InvalidToolCallIdError，
        # 避免裸 ValueError 让上层无法定位 Provider / 模型 / tool_name。
        tool_calls: list[ToolCallRequest] = []
        if message.tool_calls:
            for tc in message.tool_calls:
                tc_id = getattr(tc, "id", None)
                tc_function = getattr(tc, "function", None)
                tc_name = getattr(tc_function, "name", None) if tc_function else None
                tc_arguments = (
                    getattr(tc_function, "arguments", None) if tc_function else None
                )
                tc_index = getattr(tc, "index", None)
                if not isinstance(tc_id, str) or not tc_id:
                    details = {
                        "source": "chat_sync",
                        "provider": self._config.provider_name,
                        "model": completion.model,
                        "tool_name": tc_name,
                        "tool_call_index": tc_index,
                        "raw_id_value": tc_id,
                    }
                    logger.warning(
                        "OpenAI 兼容 Provider 返回的 tool_call.id 不合法，"
                        "将抛出 InvalidToolCallIdError",
                        extra=details,
                    )
                    raise InvalidToolCallIdError(
                        source="chat_sync",
                        raw_id_value=tc_id,
                        provider=self._config.provider_name,
                        model=completion.model,
                        tool_name=tc_name,
                        tool_call_index=tc_index,
                    )
                if not isinstance(tc_name, str) or not isinstance(tc_arguments, str):
                    raise ModelAccessError(
                        message="模型返回了不支持的非函数工具调用",
                        details={
                            "model": completion.model,
                            "tool_call_id": tc_id,
                        },
                    )
                tool_calls.append(
                    ToolCallRequest(
                        id=tc_id,
                        name=tc_name,
                        arguments=tc_arguments,
                    )
                )

        usage: dict[str, int] = {}
        if completion.usage:
            usage = {
                "prompt_tokens": completion.usage.prompt_tokens,
                "completion_tokens": completion.usage.completion_tokens,
                "total_tokens": completion.usage.total_tokens,
            }

        return LLMResponse(
            content=message.content or "",
            model=completion.model,
            usage=usage,
            latency_ms=latency_ms,
            tool_calls=tool_calls,
        )

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        """流式对话接口。

        将 ``ChatRequest`` 转换为 OpenAI Chat Completions API 流式调用，
        逐个返回 ``StreamingChunk``。

        Args:
            request: 对话请求，包含消息列表和可选参数。

        Yields:
            流式响应分片，最后一个分片的 finished 标志为 True。

        Raises:
            ModelTimeoutError: 请求超时。
            ModelRateLimitError: 触发速率限制（HTTP 429）。
            ModelConnectionError: 模型服务不可达（连接被拒绝、DNS 解析失败等）。
            ModelAccessError: 其他模型调用错误。
        """
        params = self._build_params(request, stream=True)
        response = await self._stream_open(params)
        request_nonce = uuid.uuid4().hex[:12]

        # 工具调用累积态：键为 SDK ``tool_calls[i].index``，值记录该工具调用
        # 跨分片观察到的 ``id`` / ``name`` / ``arguments`` 累积值。仅在 ``finished=True``
        # / 最末仅含 usage 的分片中重组为 ``StreamingToolCallDelta`` 完整列表。
        acc: dict[int, dict[str, Any]] = {}

        # 迭代阶段异常映射：
        # ``async for chunk in response`` 期间，OpenAI SDK 的 ``AsyncStream``
        # 通过 ``response.aiter_bytes()`` 持续读取 SSE 数据流，可能触发
        # ``APITimeoutError`` / ``APIError`` / ``httpx.ReadTimeout`` /
        # ``httpx.RemoteProtocolError`` / ``httpx.ReadError`` 等多种异常。
        # 这些异常需统一映射为领域异常，避免基础设施层异常泄漏到应用层。
        # 与 ``_stream_open_once`` 握手阶段的异常区分：``request_info.phase``
        # 字段标记为 ``"stream_iteration"``，便于日志排障。
        # 不重试：迭代阶段已 yield 部分 token，重试会导致重复回放。
        try:
            async for chunk in response:
                if not chunk.choices:
                    # 最后一个 chunk 可能只包含 usage 信息
                    usage_info = None
                    if chunk.usage:
                        usage_info = {
                            "prompt_tokens": chunk.usage.prompt_tokens,
                            "completion_tokens": chunk.usage.completion_tokens,
                            "total_tokens": chunk.usage.total_tokens,
                        }
                    if usage_info:
                        usage_tool_calls, recovery = self._materialize_full_tool_calls(
                            acc,
                            params,
                            request_nonce=request_nonce,
                        )
                        yield StreamingChunk(
                            finished=True,
                            usage=usage_info,
                            tool_calls=usage_tool_calls,
                            metadata=self._stream_tool_call_recovery_metadata(recovery),
                        )
                    continue

                delta = chunk.choices[0].delta
                finish_reason = chunk.choices[0].finish_reason
                finished = finish_reason is not None

                # 中间分片：仅产出"本分片观察到的增量切片"，不携带累积值。
                chunk_deltas: list[StreamingToolCallDelta] | None = None
                sdk_tool_calls = getattr(delta, "tool_calls", None) if delta else None
                if sdk_tool_calls:
                    chunk_deltas = []
                    for tc in sdk_tool_calls:
                        index = tc.index
                        tc_id = getattr(tc, "id", None)
                        tc_name = None
                        tc_args_delta = None
                        if getattr(tc, "function", None) is not None:
                            tc_name = getattr(tc.function, "name", None)
                            tc_args_delta = getattr(tc.function, "arguments", None)
                        chunk_deltas.append(
                            StreamingToolCallDelta(
                                index=index,
                                id=tc_id,
                                name=tc_name,
                                arguments_delta=tc_args_delta,
                            )
                        )
                        # 同步更新累积态
                        slot = acc.setdefault(index, {"id": None, "name": None, "arguments": ""})
                        if tc_id is not None:
                            slot["id"] = tc_id
                        if tc_name is not None:
                            slot["name"] = tc_name
                        if tc_args_delta is not None:
                            slot["arguments"] = (slot["arguments"] or "") + tc_args_delta

                tool_calls_field: list[StreamingToolCallDelta] | None
                if finished:
                    # 末尾分片：把累积态展开为完整列表，并按配置恢复缺失 id。
                    tool_calls_field, recovery = self._materialize_full_tool_calls(
                        acc,
                        params,
                        request_nonce=request_nonce,
                    )
                    metadata = self._stream_tool_call_recovery_metadata(recovery)
                else:
                    tool_calls_field = chunk_deltas
                    metadata = {}

                yield StreamingChunk(
                    delta_content=delta.content or "" if delta else "",
                    finished=finished,
                    usage=None,
                    tool_calls=tool_calls_field,
                    metadata=metadata,
                )
        except APITimeoutError as exc:
            raise ModelTimeoutError(
                timeout_seconds=self._config.timeout,
                request_info={
                    "model": params.get("model"),
                    "phase": "stream_iteration",
                },
            ) from exc
        except RateLimitError as exc:
            retry_after = exc.response.headers.get("retry-after") if exc.response else None
            raise ModelRateLimitError(
                retry_after_seconds=float(retry_after) if retry_after else None,
                request_info={
                    "model": params.get("model"),
                    "phase": "stream_iteration",
                },
            ) from exc
        except APIConnectionError as exc:
            raise ModelConnectionError(
                reason=str(exc),
                request_info={
                    "model": params.get("model"),
                    "phase": "stream_iteration",
                },
            ) from exc
        except APIError as exc:
            raise ModelAccessError(
                message=f"流式迭代中模型服务错误: {exc.message}",
                details={
                    "model": params.get("model"),
                    "status_code": getattr(exc, "status_code", None),
                    "phase": "stream_iteration",
                },
            ) from exc
        except httpx.ReadTimeout as exc:
            raise ModelTimeoutError(
                timeout_seconds=self._config.timeout,
                request_info={
                    "model": params.get("model"),
                    "phase": "stream_iteration",
                },
            ) from exc
        except (httpx.RemoteProtocolError, httpx.ReadError) as exc:
            raise ModelConnectionError(
                reason=str(exc),
                request_info={
                    "model": params.get("model"),
                    "phase": "stream_iteration",
                },
            ) from exc

    async def _chat_completion_once(self, params: dict[str, Any]) -> ChatCompletion:
        """单次同步对话的 SDK 调用，含 SDK→领域异常映射。

        被 :func:`build_retry` 装饰后，瞬时网络/服务错误（``ModelTimeoutError``
        / ``ModelRateLimitError`` / ``ModelConnectionError``）触发指数退避；
        语义错误（``ModelAccessError``）不重试。

        Args:
            params: OpenAI SDK ``chat.completions.create`` 调用参数。

        Returns:
            SDK 原始 ``ChatCompletion`` 对象，由调用方继续转换为
            ``LLMResponse``。

        Raises:
            ModelTimeoutError / ModelRateLimitError / ModelConnectionError /
                ModelAccessError: 与原 ``chat`` 实现一致的领域异常映射。
        """
        try:
            return cast(
                ChatCompletion,
                await self._client.chat.completions.create(**params),
            )
        except APITimeoutError as exc:
            raise ModelTimeoutError(
                timeout_seconds=self._config.timeout,
                request_info={"model": params.get("model")},
            ) from exc
        except RateLimitError as exc:
            retry_after = exc.response.headers.get("retry-after") if exc.response else None
            raise ModelRateLimitError(
                retry_after_seconds=float(retry_after) if retry_after else None,
                request_info={"model": params.get("model")},
            ) from exc
        except APIConnectionError as exc:
            raise ModelConnectionError(
                reason=str(exc),
                request_info={"model": params.get("model")},
            ) from exc
        except APIError as exc:
            status_code = exc.status_code if isinstance(exc, APIStatusError) else None
            raise ModelAccessError(
                message=f"模型调用失败: {exc.message}",
                details={"model": params.get("model"), "status_code": status_code},
            ) from exc

    async def _stream_open_once(
        self,
        params: dict[str, Any],
    ) -> AsyncStream[ChatCompletionChunk]:
        """单次流式对话的"首次握手"——发起 SDK 调用并等待返回 stream 对象。

        与 :meth:`_chat_completion_once` 共享异常翻译。返回的对象是
        ``AsyncStream``，调用方负责异步迭代；**迭代过程中**的异常不再被
        重试装饰器覆盖，避免向上游回放重复 token。

        Raises:
            同 :meth:`_chat_completion_once`。
        """
        try:
            return cast(
                AsyncStream[ChatCompletionChunk],
                await self._client.chat.completions.create(**params),
            )
        except APITimeoutError as exc:
            raise ModelTimeoutError(
                timeout_seconds=self._config.timeout,
                request_info={"model": params.get("model")},
            ) from exc
        except RateLimitError as exc:
            retry_after = exc.response.headers.get("retry-after") if exc.response else None
            raise ModelRateLimitError(
                retry_after_seconds=float(retry_after) if retry_after else None,
                request_info={"model": params.get("model")},
            ) from exc
        except APIConnectionError as exc:
            raise ModelConnectionError(
                reason=str(exc),
                request_info={"model": params.get("model")},
            ) from exc
        except APIError as exc:
            status_code = exc.status_code if isinstance(exc, APIStatusError) else None
            raise ModelAccessError(
                message=f"模型调用失败: {exc.message}",
                details={"model": params.get("model"), "status_code": status_code},
            ) from exc

    def _stream_tool_call_id_strategy(self) -> StreamToolCallIdRecoveryMode:
        """返回流式工具调用 id 缺失处理策略。

        兼容旧测试和手写 ``MagicMock`` 配置：当配置对象没有提供字符串字段时
        使用默认 ``recover``。当配置显式给出非法字符串时 fail-fast。
        """
        raw_strategy = getattr(self._config, "stream_tool_call_id_strategy", "recover")
        if not isinstance(raw_strategy, str):
            raw_strategy = "recover"
        strategy = raw_strategy.strip().lower()
        if strategy == "recover":
            return "recover"
        if strategy == "raise":
            return "raise"
        raise ConfigurationError(
            "MODEL_*_STREAM_TOOL_CALL_ID_STRATEGY 非法: "
            f"{raw_strategy!r}，允许值为 recover 或 raise"
        )

    @staticmethod
    def _synthetic_tool_call_id(request_nonce: str, index: int) -> str:
        """生成本地合成工具调用 id。"""
        return f"call_synthetic_{request_nonce}_{index}"

    def _log_stream_tool_call_id_recovery(
        self,
        *,
        params: dict[str, Any],
        delta: StreamingToolCallDelta,
        synthetic_id: str,
    ) -> None:
        """输出流式工具调用 id 恢复的结构化 WARN 日志。"""
        logger.warning(
            "流式工具调用缺失 id，已生成本地合成 id",
            extra={
                "source": "stream_finished",
                "provider": self._config.provider_name,
                "model": params.get("model"),
                "tool_name": delta.name,
                "tool_call_index": delta.index,
                "raw_id_value": None,
                "synthetic_id": synthetic_id,
                "recovery_strategy": "recover",
            },
        )

    @staticmethod
    def _stream_tool_call_recovery_metadata(
        recovery: _StreamToolCallIdRecovery,
    ) -> dict[str, Any]:
        """把恢复结果转换为 finished chunk 的轻量 metadata。"""
        if not recovery.occurred:
            return {}
        return {
            "tool_call_id_recovered": True,
            "synthetic_tool_call_count": recovery.recovered_count,
        }

    def _materialize_full_tool_calls(
        self,
        acc: dict[int, dict[str, Any]],
        params: dict[str, Any],
        *,
        request_nonce: str,
    ) -> tuple[list[StreamingToolCallDelta] | None, _StreamToolCallIdRecovery]:
        """把累积态展开为按 ``index`` 升序的完整工具调用列表。

        ``finished=True`` 分片用此方法回填 ``StreamingChunk.tool_calls``。
        每个 :class:`StreamingToolCallDelta` 的 ``arguments_delta`` 在此处被
        约定为"完整 arguments JSON"；完整槽位会保证 ``id`` 非空，名称或
        参数不完整的槽位保留 ``None`` 供下游违约回退。

        当 Provider 未返回 id 但工具名称和参数已完整时，按配置选择：
        ``recover`` 生成本地合成 id 并写回累积槽位；``raise`` 保持严格
        协议校验并抛 ``InvalidToolCallIdError``。工具名称或参数不完整时
        不生成可执行工具调用，保留 ``None`` 供下游累积器违约回退。

        当累积态为空（纯文本流）时返回 ``None``，确保
        ``StreamingChunk.tool_calls`` 与"无工具调用"语义对齐（**不**写空列表）。
        """
        if not acc:
            return None, _StreamToolCallIdRecovery()
        strategy = self._stream_tool_call_id_strategy()
        result: list[StreamingToolCallDelta] = []
        synthetic_ids: list[str] = []
        for index in sorted(acc):
            slot = acc[index]
            slot_id = slot.get("id") or None
            slot_name = slot.get("name") or None
            slot_args = slot.get("arguments") or None
            delta = StreamingToolCallDelta(
                index=index,
                id=slot_id,
                name=slot_name,
                arguments_delta=slot_args,
            )
            if not slot_id and slot_name and slot_args:
                if strategy == "raise":
                    raise InvalidToolCallIdError(
                        source="stream_finished",
                        raw_id_value=slot_id,
                        provider=self._config.provider_name,
                        model=params.get("model"),
                        tool_name=slot_name,
                        tool_call_index=index,
                    )
                synthetic_id = self._synthetic_tool_call_id(request_nonce, index)
                slot["id"] = synthetic_id
                delta = StreamingToolCallDelta(
                    index=index,
                    id=synthetic_id,
                    name=slot_name,
                    arguments_delta=slot_args,
                )
                synthetic_ids.append(synthetic_id)
                self._log_stream_tool_call_id_recovery(
                    params=params,
                    delta=delta,
                    synthetic_id=synthetic_id,
                )
            result.append(delta)
        recovery = _StreamToolCallIdRecovery(
            recovered_count=len(synthetic_ids),
            synthetic_ids=tuple(synthetic_ids),
        )
        return result, recovery

    def materialize_full_tool_calls(
        self,
        acc: dict[int, dict[str, Any]],
        params: dict[str, Any],
        *,
        request_nonce: str,
    ) -> tuple[list[StreamingToolCallDelta] | None, _StreamToolCallIdRecovery]:
        """Materialize accumulated streaming tool calls into complete deltas."""
        return self._materialize_full_tool_calls(
            acc, params, request_nonce=request_nonce
        )

    def count_tokens(self, messages: list[BaseMessage]) -> int:
        """估算给定领域消息列表的 token 数量。

        使用 tiktoken 对每条消息的 OpenAI 协议 JSON 表示进行编码，加上固定
        overhead（模拟 OpenAI API 的 token 计费公式），累加所有消息作为估算总量。

        Args:
            messages: 领域消息列表。空列表返回 0。

        Returns:
            非负整数，估算的 token 总数。
        """
        if not messages:
            return 0
        total = 0
        for message in messages:
            openai_dict = self._to_openai_messages(
                [message],
                system_role=self._system_role_for_model(self._config.default_model),
            )[0]
            message_text = json.dumps(
                openai_dict,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            total += len(self._tokenizer.encode(message_text)) + self._MESSAGE_OVERHEAD
        return total

    @staticmethod
    def to_openai_messages(
        messages: Sequence[BaseMessage],
        *,
        system_role: str = "system",
    ) -> list[dict[str, Any]]:
        """把领域消息转换为 OpenAI Chat Completions 消息。"""
        return OpenAICompatibleAdapter._to_openai_messages(
            messages, system_role=system_role
        )

    @staticmethod
    def _to_openai_messages(
        messages: Sequence[BaseMessage],
        *,
        system_role: str = "system",
    ) -> list[dict[str, Any]]:
        """把领域消息列表转换为 OpenAI Chat Completions API 所需的字典列表。

        本方法是 ``OpenAICompatibleAdapter`` 内部承担"领域消息 → OpenAI 协议
        字典"协议转换的私有 helper，**不**对外暴露。其语义与 commit ``040695a``
        加固后既有协议转换函数完全等价（dict-equal）：

        - ``AssistantMessage`` 携带非空 ``tool_calls`` 时输出 OpenAI ``tool_calls``
          嵌套结构 ``{"id", "type": "function", "function": {"name", "arguments"}}``，
          同时保留 ``role`` 与 ``content`` 字段；
        - ``ToolMessage`` 输出 ``role`` / ``content`` / ``tool_call_id``；
        - 其他消息（``SystemMessage`` / ``UserMessage`` / 不携带 ``tool_calls``
          的 ``AssistantMessage``）仅输出 ``role`` 与 ``content``。

        本方法**不**对 ``AssistantMessage.tool_calls`` 中每个 ``ToolCallRequest``
        的 ``id`` 做额外校验——``ToolCallRequest.__post_init__`` 已强制 ``id``
        非空，且 commit ``040695a`` 在所有上游入站链路（同步/流式响应解析、
        历史会话恢复、审批 resume）已统一加固 id 校验，本方法仅信任已通过
        校验的领域消息。

        Args:
            messages: 领域消息列表，元素为 ``BaseMessage`` 子类实例。空列表
                合法，输出为空列表。本方法不会修改输入列表或任何元素。
            system_role: ``SystemMessage`` 输出到 OpenAI 协议时使用的 role。
                默认保留 ``"system"``，官方 OpenAI GPT-5 系列由 ``_build_params``
                传入 ``"developer"`` 以贴合当前 OpenAI 指令优先级规范。

        Returns:
            OpenAI Chat Completions API 兼容的字典列表，可直接作为
            ``chat.completions.create(..., messages=...)`` 的入参。
        """
        result: list[dict[str, Any]] = []
        for message in messages:
            role = system_role if message.role == "system" else message.role
            if isinstance(message, AssistantMessage) and message.tool_calls:
                result.append(
                    {
                        "role": role,
                        "content": message.content,
                        "tool_calls": [
                            {
                                "id": tool_call.id,
                                "type": "function",
                                "function": {
                                    "name": tool_call.name,
                                    "arguments": tool_call.arguments,
                                },
                            }
                            for tool_call in message.tool_calls
                        ],
                    }
                )
            elif isinstance(message, ToolMessage):
                result.append(
                    {
                        "role": role,
                        "content": message.content,
                        "tool_call_id": message.tool_call_id,
                    }
                )
            else:
                result.append({"role": role, "content": message.content})
        return result

    def _is_official_openai_provider(self) -> bool:
        """判断当前配置是否指向 OpenAI 官方 API。

        兼容协议 Provider（Qwen / 智谱 / DeepSeek 等）虽然复用本 adapter，
        但不一定支持 OpenAI 官方最新参数别名或 ``developer`` role，因此
        最新官方规范只在 provider_name 或 api_base 明确指向 OpenAI 时启用。
        """
        provider_name = (self._config.provider_name or "").strip().lower()
        api_base = (self._config.api_base or "").strip().lower()
        return provider_name == "openai" or "api.openai.com" in api_base

    def _token_limit_param_name(self) -> str:
        """返回当前 Provider 的输出 token 上限参数名。

        OpenAI Chat Completions 已将 ``max_tokens`` 标记为 deprecated，官方
        OpenAI Provider 使用 ``max_completion_tokens``；兼容 Provider 保持
        ``max_tokens``，避免破坏第三方 OpenAI-compatible 端点。
        """
        if self._is_official_openai_provider():
            return "max_completion_tokens"
        return "max_tokens"

    def _system_role_for_model(self, model: str) -> str:
        """返回 ``SystemMessage`` 在 OpenAI 协议中的 role。

        OpenAI 当前文档把应用开发者指令表达为 ``developer`` 消息，并说明
        其优先级高于用户消息。为降低对兼容 Provider 和旧模型的影响，仅在
        官方 OpenAI GPT-5 系列模型上将领域 ``SystemMessage`` 映射为
        ``developer``；其他场景保持 ``system``。
        """
        if self._is_official_openai_provider() and model.startswith("gpt-5"):
            return "developer"
        return "system"

    def _normalize_extra_params(self, extra_params: dict[str, Any] | None) -> dict[str, Any]:
        """按当前 Provider 规范归一化透传参数。

        ``extra_params`` 是扩展逃生口。对于官方 OpenAI Provider，若调用方仍
        传入 deprecated 的 ``max_tokens``，这里迁移为
        ``max_completion_tokens``；若两者同时存在，则保留新字段并丢弃旧字段。
        """
        if not extra_params:
            return {}
        normalized = dict(extra_params)
        if self._is_official_openai_provider() and "max_tokens" in normalized:
            max_tokens = normalized.pop("max_tokens")
            normalized.setdefault("max_completion_tokens", max_tokens)
        return normalized

    def build_params(self, request: ChatRequest, *, stream: bool) -> dict[str, Any]:
        """构建 SDK 请求参数，供集成诊断与契约测试使用。"""
        return self._build_params(request, stream=stream)

    def _build_params(self, request: ChatRequest, *, stream: bool) -> dict[str, Any]:
        """构建 OpenAI SDK 调用参数。

        将领域层 ``ChatRequest`` 转换为 OpenAI Chat Completions API 所需的参数字典。
        未在请求中指定的可选参数使用配置中的默认值。

        ``request.messages`` 承载领域消息（``BaseMessage`` 子类实例），本方法
        在传入 SDK 之前通过 ``_to_openai_messages`` 完成"领域消息 → OpenAI 协议
        字典"的协议转换；端口契约不再隐含特定 LLM 协议假设，协议细节封闭在
        本 adapter 内部。

        当 ``request.tools`` 为非空列表时，将其作为 ``"tools"`` 参数传递给 SDK；
        ``None`` 和空列表 ``[]`` 均不传递，以避免部分模型对空 tools 数组报错。

        Args:
            request: 对话请求。
            stream: 是否启用流式输出。

        Returns:
            OpenAI SDK ``chat.completions.create()`` 所需的参数字典。
        """
        model = request.model or self._config.default_model
        token_limit_param = self._token_limit_param_name()
        params: dict[str, Any] = {
            "model": model,
            "messages": self._to_openai_messages(
                request.messages,
                system_role=self._system_role_for_model(model),
            ),
            "temperature": request.temperature
            if request.temperature is not None
            else self._config.temperature,
            token_limit_param: request.max_tokens
            if request.max_tokens is not None
            else self._config.max_tokens,
            "stream": stream,
        }

        if stream:
            params["stream_options"] = {"include_usage": True}

        if request.tools:
            params["tools"] = request.tools

        if self._config.safety_identifier:
            params["user"] = self._config.safety_identifier

        extra_params = self._normalize_extra_params(request.extra_params)
        if extra_params:
            params.update(extra_params)

        return params

    @property
    def client(self) -> AsyncOpenAI:
        """返回底层异步客户端，供生命周期管理与受控测试替换调用端点。"""
        return self._client
