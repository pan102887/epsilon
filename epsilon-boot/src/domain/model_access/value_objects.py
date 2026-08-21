"""模型接入层值对象。

定义了与 LLM 交互所需的值对象，包括请求、响应、流式响应分片和模型信息。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, cast

if TYPE_CHECKING:
    from domain.chat.context import BaseMessage


def _usage_dict() -> dict[str, int]:
    return {}


def _tool_call_list() -> list[ToolCallRequest]:
    return []


def _metadata_dict() -> dict[str, Any]:
    return {}


def _provider_set() -> frozenset[str]:
    return frozenset()


@dataclass(frozen=True)
class ThinkingConfig:
    """Claude Extended Thinking 配置。

    Claude 的 Extended Thinking 功能允许模型在生成响应前进行显式推理，
    特别适合复杂的语义分析和配置生成任务。

    Attributes:
        type: 启用或禁用 thinking（"enabled" 或 "disabled"）
        budget_tokens: 可选的 thinking token 预算限制
    """

    type: Literal["enabled", "disabled"] = "enabled"
    budget_tokens: int | None = None


@dataclass(frozen=True)
class ChatRequest:
    """对话请求值对象。

    封装一次 LLM 对话调用所需的全部参数。``messages`` 字段承载领域消息
    列表（``BaseMessage`` 及其子类），不感知任何具体 LLM 协议形态；
    协议字典化由 ``ModelAccessPort`` 的具体 adapter 在 SDK 调用前自行完成。

    Attributes:
        messages: 对话消息列表，元素为 ``BaseMessage`` 子类实例
            （``SystemMessage`` / ``UserMessage`` / ``AssistantMessage`` /
            ``ToolMessage``）。不可为空。
        model: 可选的模型名称，未指定时使用配置的默认模型。
        temperature: 可选的温度参数（0.0-2.0），控制输出随机性，值越大越随机。
        max_tokens: 可选的最大 token 数，限制响应长度。
        system: 可选的 system 消息（Claude 风格），优先于 messages 中的 system 角色。
        provider: 可选的显式提供商指定（"openai"、"claude" 等），用于强制路由。
        thinking: 可选的 Claude Extended Thinking 配置。
        tools: 可选的工具 schema 列表（opaque ``list[dict]``）。语义为
            "由具体 adapter 在 SDK 调用前翻译为目标 Provider 的工具 schema
            表示"，端口契约不再硬绑定任何特定协议形态。``None`` 表示不传递
            工具信息，空列表 ``[]`` 视为"无工具"，均不会传递给底层 SDK。
            后续若需引入领域 ``ToolSchema`` 值对象，预留独立 spec 治理。
        extra_params: 扩展参数字典，用于传递特定模型的自定义参数。
    """

    messages: "list[BaseMessage]"
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    system: str | None = None
    provider: str | None = None
    thinking: ThinkingConfig | None = None
    tools: list[dict[str, Any]] | None = None
    extra_params: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        """验证请求参数的合法性。

        校验规则：

        - ``messages`` 不能为空；
        - 所有元素必须为 ``BaseMessage`` 子类实例；
        - ``temperature`` 在 ``[0.0, 2.0]`` 范围内（如指定）；
        - ``max_tokens`` 为正整数（如指定）。
        """
        # import 本地化以规避 ``domain.chat.context`` 与本模块潜在的循环依赖。
        from domain.chat.context import BaseMessage

        if not self.messages:
            raise ValueError("messages 不能为空")

        for index, msg in enumerate(cast("list[object]", self.messages)):
            if not isinstance(msg, BaseMessage):
                raise ValueError(
                    f"messages[{index}] 必须为 BaseMessage 子类实例，当前类型: {type(msg).__name__}"
                )

        if self.temperature is not None and not 0.0 <= self.temperature <= 2.0:
            raise ValueError(f"temperature 必须在 0.0-2.0 之间，当前值: {self.temperature}")

        if self.max_tokens is not None and self.max_tokens <= 0:
            raise ValueError(f"max_tokens 必须大于 0，当前值: {self.max_tokens}")


@dataclass(frozen=True)
class ToolCallRequest:
    """工具调用请求值对象。

    表示从 LLM 响应中的 function_call / tool_calls 部分解析得到的工具调用请求。
    当模型决定调用外部工具时，会在响应中返回此结构，包含要调用的函数名称、
    调用参数以及唯一标识符。

    Attributes:
        id: 工具调用的唯一标识符，由 LLM 生成，用于将工具执行结果关联回对应的调用请求
        name: 要调用的函数/工具名称
        arguments: 函数调用参数的 JSON 字符串，由调用方负责解析为具体类型
    """

    id: str
    name: str
    arguments: str

    def __post_init__(self) -> None:
        """验证工具调用请求参数的合法性。"""
        if not self.id:
            raise ValueError("id 不能为空")
        if not self.name:
            raise ValueError("name 不能为空")
        if not self.arguments:
            raise ValueError("arguments 不能为空")


@dataclass(frozen=True)
class LLMResponse:
    """对话响应值对象。

    封装 LLM 返回的完整响应信息。

    Attributes:
        content: 模型回复的文本内容
        model: 实际使用的模型名称
        usage:
            token 用量信息，格式为
            {"prompt_tokens": X, "completion_tokens": Y, "total_tokens": Z}
        latency_ms: 请求耗时（毫秒），用于性能监控
        tool_calls: 模型请求的工具调用列表，当模型决定调用工具时非空
    """

    content: str
    model: str
    usage: dict[str, int] = field(default_factory=_usage_dict)
    latency_ms: float = 0.0
    tool_calls: list[ToolCallRequest] = field(default_factory=_tool_call_list)


@dataclass(frozen=True)
class StreamingToolCallDelta:
    """流式工具调用增量切片值对象。

    刻画一个工具调用在某个 :class:`StreamingChunk` 上的增量信息，与
    OpenAI Python SDK 流式分片中 ``chunk.choices[0].delta.tool_calls[i]``
    的字段语义严格对齐。

    OpenAI SDK 的工具调用流式语义：同一个工具调用会跨越多个 SDK 分片，
    每个分片的 ``tool_calls[i].index`` 标识"该 delta 属于哪个工具调用"，
    第一个分片携带 ``id`` 与 ``function.name``，后续分片只携带
    ``function.arguments`` 增量（每个分片的 arguments 是字符串切片，
    需要按到达顺序拼接，同一 ``index`` 下的拼接结果即完整 arguments JSON）。

    本值对象的字段约定：

    - ``index``：必填，对应 SDK ``tool_calls[i].index``，用于跨分片合并同一工具调用。
    - ``id``：可选，工具调用的唯一标识符，通常仅出现在该工具调用的首个 delta；
      若该 delta 不携带 ``id``，保留 ``None``。
    - ``name``：可选，函数名，通常仅出现在该工具调用的首个 delta；
      若该 delta 不携带 ``name``，保留 ``None``。
    - ``arguments_delta``：可选，该 delta 携带的 ``function.arguments`` 增量片段；
      ``None`` 表示该 delta 不携带 arguments（例如首个 delta 仅携带 id/name）。

    ``finished=True`` 分片的特殊语义：当所属 :class:`StreamingChunk` 的
    ``finished=True`` 时，适配器内部状态机已累积出完整工具调用，并把每个
    完整工具调用以 ``StreamingToolCallDelta(index=..., id=完整 id,
    name=完整 name, arguments_delta=完整 arguments JSON)`` 形式回传——
    此时 ``arguments_delta`` 不再是"增量"，而是"重组后的完整 arguments"，
    且 ``id`` / ``name`` / ``arguments_delta`` 三者均不为 ``None``。
    这一契约保证下游消费者即使丢弃所有中间增量分片，也能仅凭
    ``finished=True`` 分片重组出完整的工具调用列表。

    Attributes:
        index: SDK ``tool_calls[i].index``，跨分片标识同一工具调用。
        id: 工具调用唯一标识；通常仅首个 delta 携带，后续 delta 为 ``None``；
            ``finished=True`` 分片中保证非 ``None``。
        name: 函数名；通常仅首个 delta 携带，后续 delta 为 ``None``；
            ``finished=True`` 分片中保证非 ``None``。
        arguments_delta: 本 delta 携带的 arguments 增量字符串；``None`` 表示
            该 delta 不携带 arguments；``finished=True`` 分片中为完整 arguments
            JSON，保证非 ``None``。
    """

    index: int
    id: str | None = None
    name: str | None = None
    arguments_delta: str | None = None


@dataclass(frozen=True)
class StreamingChunk:
    """流式响应分片值对象。

    在流式调用中，LLM 的响应会被拆分为多个分片逐个返回。

    Attributes:
        delta_content: 增量文本内容（相对于上一个分片的新增内容）。
        finished: 是否为最后一个分片。
        usage: 可选的 token 用量信息，通常仅在最后一个分片中提供。
        metadata: 面向结构化事件或兼容提示的附加元数据。
        tool_calls: 工具调用增量切片列表，遵循下述四项契约——
            (a) ``None`` 表示该分片不携带工具调用相关数据（纯文本流应保持
                ``None``，**禁止**写空列表 ``[]``）；
            (b) 中间分片（``finished=False``）若非 ``None``，仅携带"本分片
                观察到的增量切片"，其中每个 :class:`StreamingToolCallDelta`
                的 ``id`` / ``name`` / ``arguments_delta`` 仅记录本分片的增量
                信息（首片可能仅含 ``id``+``name``，后续片可能仅含
                ``arguments_delta``）；
            (c) ``finished=True`` 分片若包含完整工具调用，本字段为按
                ``StreamingToolCallDelta.index`` 升序重组后的完整列表，每个
                元素的 ``arguments_delta`` 为完整 arguments JSON（而非增量），
                且 ``id`` / ``name`` / ``arguments_delta`` 三者均不为
                ``None``；
            (d) 本类 ``frozen=True`` 不变。
    """

    delta_content: str = ""
    finished: bool = False
    usage: dict[str, int] | None = None
    metadata: dict[str, Any] = field(default_factory=_metadata_dict)
    tool_calls: list[StreamingToolCallDelta] | None = None


@dataclass(frozen=True)
class ModelInfo:
    """模型信息值对象。

    描述一个可用模型的基本信息，包括模型标识和提供该模型的提供商列表。

    Attributes:
        id: 模型唯一标识（即模型名称），如 ``"glm-4"``、``"gpt-4"``。
        object: 对象类型，固定为 ``"model"``，兼容 OpenAI API 格式。
        owned_by: 模型所有者标识，通常为提供商名称。
            当多个提供商提供同一模型时，取首个注册的提供商名称。
        providers: 提供该模型的所有提供商名称集合。
    """

    id: str
    object: str = "model"
    owned_by: str = ""
    providers: frozenset[str] = field(default_factory=_provider_set)
