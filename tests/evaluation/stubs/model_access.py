"""桩 ``ModelAccessPort`` 实现。

本模块提供 :class:`ScriptedModelAccess`：按预设的 ``LLMResponse`` 脚本
逐次返回响应，用于为评测指标（Tool_Call_Success_Rate /
Delegation_Correctness / Context_Compaction_Effectiveness）提供确定性
模型输出，规避真实 LLM 波动与外部网络依赖。

结构类型匹配：
    本桩实现以鸭子类型（structural typing）形式匹配
    ``epsilon-boot/src/domain/model_access/ports.py`` 中的
    ``ModelAccessPort``：提供同名 ``chat(request)`` 与
    ``stream(request)`` 方法，参数与返回类型一致。**不**继承 Protocol，
    **不**导入 ``infrastructure/`` 模块。

可用性约束：
    评测三项核心指标均走非流式路径（``chat``），故 ``stream`` 方法作为
    防御性实现抛 :class:`NotImplementedError`，阻止评测样本不慎依赖
    流式通道。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
)


@dataclass
class ScriptedModelAccess:
    """按预设脚本逐次返回响应的桩 ``ModelAccessPort``。

    Attributes:
        scripted_responses: 预先准备的 :class:`LLMResponse` 序列，每次
            :meth:`chat` 调用按顺序弹出一条；脚本耗尽后，后续调用返回
            一个空 ``content`` 的 :class:`LLMResponse`（``model`` 记为
            ``"scripted-exhausted"``），用于避免样本在异常路径下死锁。
        default_model: 脚本耗尽时默认响应携带的模型名称，便于在聚合
            结果中识别"脚本耗尽"情况。
        calls: 已消费的 ``chat`` 调用次数，供自测使用。
    """

    scripted_responses: list[LLMResponse] = field(default_factory=list)
    default_model: str = "scripted-exhausted"
    calls: int = 0

    async def chat(self, request: ChatRequest) -> LLMResponse:
        """按顺序返回脚本中的下一条响应。

        Args:
            request: 评测样本构造的 :class:`ChatRequest`，本桩不依赖
                请求内容，仅按 FIFO 返回预设响应。

        Returns:
            预设的 :class:`LLMResponse`；若脚本耗尽，返回一个空
            ``content``、``tool_calls`` 为空的 :class:`LLMResponse`
            作为兜底。
        """

        self.calls += 1
        if self.scripted_responses:
            return self.scripted_responses.pop(0)
        return LLMResponse(
            content="",
            model=self.default_model,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            latency_ms=0.0,
            tool_calls=[],
        )

    def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        """流式通道：将脚本响应转化为等价的 StreamingChunk 序列。

        v3 ReAct 适配器内部全程使用 ``stream()``，故本方法需要产出与
        ``chat()`` 语义等价的分片序列：一个 ``finished=True`` 的终止分片
        携带完整内容、tool_calls 与 usage。

        Args:
            request: 评测样本构造的 :class:`ChatRequest`。

        Returns:
            产出单个 ``finished=True`` :class:`StreamingChunk` 的异步迭代器，
            内容等价于 :meth:`chat` 返回的 :class:`LLMResponse`。
        """

        from domain.model_access.value_objects import StreamingToolCallDelta

        self.calls += 1
        if self.scripted_responses:
            response = self.scripted_responses.pop(0)
        else:
            response = LLMResponse(
                content="",
                model=self.default_model,
                usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
                latency_ms=0.0,
                tool_calls=[],
            )

        tool_call_deltas: list[StreamingToolCallDelta] | None = None
        if response.tool_calls:
            tool_call_deltas = [
                StreamingToolCallDelta(
                    index=i,
                    id=tc.id,
                    name=tc.name,
                    arguments_delta=tc.arguments,
                )
                for i, tc in enumerate(response.tool_calls)
            ]

        chunk = StreamingChunk(
            delta_content=response.content or "",
            finished=True,
            usage=response.usage,
            tool_calls=tool_call_deltas,
        )

        async def _gen() -> AsyncIterator[StreamingChunk]:
            yield chunk

        return _gen()
