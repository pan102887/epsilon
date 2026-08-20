"""模型接入端口定义。

定义了统一的模型接入接口和模型注册中心接口，具体实现由基础设施层提供。
"""

from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Protocol

from domain.model_access.value_objects import ChatRequest, LLMResponse, ModelInfo, StreamingChunk

if TYPE_CHECKING:
    from domain.chat.context import BaseMessage


class ModelAccessPort(Protocol):
    """统一模型接入端口。

    该接口定义了与 LLM 交互的标准操作，支持同步对话、流式对话以及 token
    估算三种能力。具体实现由基础设施层的适配器提供，以支持不同的模型
    提供商；不同 Provider 的 tokenizer 差异由各自 adapter 内部消化。

    Usage::

        # 同步对话
        request = ChatRequest(messages=[UserMessage(content="你好")])
        response = await model_access.chat(request)
        print(response.content)

        # 流式对话
        async for chunk in model_access.stream(request):
            print(chunk.delta_content, end="")

        # 估算消息列表 token 数
        tokens = model_access.count_tokens([UserMessage(content="你好")])
    """

    async def chat(self, request: ChatRequest) -> LLMResponse:
        """同步对话接口。

        发送完整的对话请求，等待模型返回完整响应。适合不需要实时展示生成过程的场景。

        Args:
            request: 对话请求，包含消息列表和可选参数

        Returns:
            完整的对话响应，包含回复内容、token 用量和耗时信息

        Raises:
            ModelTimeoutError: 请求超时，超过配置的超时时间
            ModelRateLimitError: 触发速率限制（HTTP 429）
            ModelAccessError: 其他模型调用错误（HTTP 错误、网络错误等）
        """
        ...

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        """流式对话接口。

        发送对话请求，以流式方式逐个返回响应分片。适合需要实时展示生成过程的场景（如聊天界面）。

        Args:
            request: 对话请求，包含消息列表和可选参数

        Yields:
            流式响应分片，按顺序返回增量内容。最后一个分片的 finished 标志为 True，
            并可能包含 token 用量信息。

        Raises:
            ModelTimeoutError: 请求超时，超过配置的超时时间
            ModelRateLimitError: 触发速率限制（HTTP 429）
            ModelAccessError: 其他模型调用错误（HTTP 错误、网络错误等）
        """
        ...

    def count_tokens(self, messages: "list[BaseMessage]") -> int:
        """估算给定领域消息列表的 token 数量。

        本方法用于上游编排层（典型为 ``LLMSummaryCompactionAdapter``）判定
        是否触发上下文摘要压缩等阈值类决策。返回值仅供阈值比较，**不**作为
        硬性截断上限；上游不应依赖跨 Provider 的绝对一致性。

        实现要求：

        - 每个具体 adapter 应使用与对应 Provider tokenizer 一致或近似的算法
          （OpenAI 兼容 adapter 使用 ``tiktoken``，Anthropic adapter 应使用
          其自身 tokenizer，Bedrock / Gemini 等可使用通用 BPE 或字符长度近似，
          需在 docstring 中显式说明回退策略）；
        - 返回值为非负整数；
        - 实现应是同步方法（协议为 ``def`` 而非 ``async def``），与单次估算
          的纯计算属性一致；上游对该方法的调用不应被网络 / IO 阻塞。

        Args:
            messages: 领域消息列表，元素为 ``BaseMessage`` 子类实例。
                空列表合法，返回 ``0``。

        Returns:
            非负整数，估算的 token 总数。
        """
        ...


class ModelRegistryPort(Protocol):
    """模型注册中心端口。

    定义了统一的模型注册中心接口，负责管理提供商注册和模型列表查询。
    具体实现由基础设施层提供。

    核心职责：
    - 维护提供商实例的注册与生命周期管理
    - 接受由配置文件驱动的模型列表，直接完成注册（无需 HTTP 发现）
    - 维护 ``model_name → Set[provider]`` 的映射关系
    - 提供可用模型列表查询能力
    """

    def register_provider(
        self,
        provider_name: str,
        adapter: ModelAccessPort,
        models: list[str],
    ) -> bool:
        """注册一个模型提供商，使用配置驱动的模型列表完成注册。

        注册流程：
        1. 接收由调用方从配置文件中读取的模型列表
        2. 将提供商及其模型列表注册到注册中心
        3. 模型列表为空时注册失败

        Args:
            provider_name: 提供商唯一标识名称。
            adapter: 实现了 ``ModelAccessPort`` 的提供商适配器实例。
            models: 该提供商支持的模型名称列表，由配置文件提供。

        Returns:
            注册成功返回 ``True``，模型列表为空时返回 ``False``。
        """
        ...

    def list_models(self) -> list[ModelInfo]:
        """查询所有可用模型列表。

        返回注册中心中所有已注册提供商支持的模型信息，
        同一模型由多个提供商提供时合并为一条记录。

        Returns:
            可用模型信息列表，按模型 ID 排序。
        """
        ...

    def get_adapter_for_model(self, model: str) -> ModelAccessPort:
        """根据模型名称获取对应的提供商适配器实例。

        当多个提供商支持同一模型时，使用负载均衡算法选择提供商。

        Args:
            model: 模型名称。

        Returns:
            选中的提供商适配器实例。

        Raises:
            ModelAccessError: 模型未注册或无可用提供商。
        """
        ...

    def get_default_model(self) -> str:
        """获取默认模型名称。

        Returns:
            默认模型名称。

        Raises:
            ModelAccessError: 无可用模型。
        """
        ...
