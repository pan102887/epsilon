"""ReActAgentAdapter 流式心跳与工具进度分片单元测试模块。

验证 ``run_streaming`` 在中间轮次（有工具调用时）产出：
- 至少 1 个 Heartbeat 分片（``finished=False``, ``delta_content=""``,
  ``metadata.type=="heartbeat"``）
- 每个工具调用产出 ``phase="start"`` / ``phase="end"`` 各 1 个
  Tool_Progress_Chunk（``finished=False``, ``delta_content=""``,
  ``metadata.type=="tool_progress"``）
- 心跳与工具进度分片的 ``metadata`` 含 ``round`` / ``tool_name`` /
  ``tool_call_id`` 但不含 ``arguments``

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.8**
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import AgentConfig
from domain.chat.context import BaseMessage, ConversationContext
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
    ToolCallRequest,
)
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter


class _FakeContextBuilder:
    """按轮次顺序返回上下文构建结果的测试 fake。"""

    async def build(
        self,
        messages: list[BaseMessage],
        *,
        model_access: ModelAccessPort | None = None,
        model: str | None = None,
    ) -> ContextBuilderResult:
        del model_access, model
        return ContextBuilderResult(
            messages=messages,
            usage={},
        )


def _response_to_chunks(response: LLMResponse) -> list[StreamingChunk]:
    """v3 ``stream`` 等价分片序列。"""
    from domain.model_access.value_objects import StreamingToolCallDelta

    chunks: list[StreamingChunk] = []
    if response.content:
        chunks.append(StreamingChunk(delta_content=response.content, finished=False))
    if response.tool_calls:
        full = [
            StreamingToolCallDelta(
                index=i,
                id=tc.id,
                name=tc.name,
                arguments_delta=tc.arguments,
            )
            for i, tc in enumerate(response.tool_calls)
        ]
        chunks.append(
            StreamingChunk(
                delta_content="",
                finished=True,
                usage=response.usage,
                tool_calls=full,
            )
        )
    else:
        chunks.append(StreamingChunk(delta_content="", finished=True, usage=response.usage))
    return chunks


class _FakeModel:
    """v3：ReAct 内部 + 最后一轮均通过 ``stream`` 推进。``stream`` 按
    ``chat_responses`` 队列顺序产出分片；队列空时回退到默认 "最终回答" 分片
    （即原 v2 ``stream`` 默认行为，用于最后一轮 ``_stream_final_round``）。"""

    def __init__(self, chat_responses: list[LLMResponse]) -> None:
        self._chat_responses = list(chat_responses)
        self.chat_call_count = 0
        self.stream_call_count = 0

    async def chat(self, request: ChatRequest) -> LLMResponse:
        self.chat_call_count += 1
        return self._chat_responses.pop(0)

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        self.stream_call_count += 1
        if self._chat_responses:
            response = self._chat_responses.pop(0)
            for chunk in _response_to_chunks(response):
                yield chunk
            return
        # 默认最后一轮分片，与 v2 ``stream`` 默认行为一致。
        yield StreamingChunk(delta_content="最终回答", finished=True, usage={"total_tokens": 5})


def _config(max_rounds: int = 3) -> AgentConfig:
    """构造测试用 AgentConfig。"""
    return AgentConfig(
        system_prompt="你是助手",
        tool_schemas=[
            {"type": "function", "function": {"name": "search"}},
            {"type": "function", "function": {"name": "read_file"}},
        ],
        model="test-model",
        max_rounds=max_rounds,
        prompt_id="chat-default@v1",
    )


def _adapter() -> ReActAgentAdapter:
    """构造使用 mock 工具注册表的 ReActAgentAdapter。"""
    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="tool result"))
    return ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_FakeContextBuilder(),  # type: ignore[arg-type]
    )


async def test_intermediate_round_emits_at_least_one_heartbeat() -> None:
    """中间轮次产出至少 1 个 Heartbeat 分片。

    构造 2 轮工具调用 + 最后 1 轮流式回复（max_rounds=3），验证
    run_streaming 在中间轮次（第 1、2 轮）中至少产出了 heartbeat chunk。

    **Validates: Requirement 3.1, 3.4, 3.5**
    """
    chat_responses = [
        # 第 1 轮：返回工具调用
        LLMResponse(
            content="",
            model="test-model",
            usage={"prompt_tokens": 10},
            tool_calls=[
                ToolCallRequest(id="call-1", name="search", arguments='{"q": "hello"}'),
            ],
        ),
        # 第 2 轮：再次返回工具调用
        LLMResponse(
            content="",
            model="test-model",
            usage={"prompt_tokens": 20},
            tool_calls=[
                ToolCallRequest(id="call-2", name="read_file", arguments='{"path": "a.txt"}'),
            ],
        ),
    ]

    model = _FakeModel(chat_responses)
    adapter = _adapter()
    context = ConversationContext()
    context.add_user_message("搜索")

    chunks: list[StreamingChunk] = []
    async for chunk in adapter.run_streaming(context, _config(max_rounds=3), model):  # type: ignore[arg-type]
        chunks.append(chunk)

    heartbeats = [c for c in chunks if c.metadata.get("type") == "heartbeat"]
    assert len(heartbeats) >= 1, (
        f"期望中间轮次至少产出 1 个 heartbeat，实际产出 {len(heartbeats)} 个"
    )

    # 验证 heartbeat 属性
    for hb in heartbeats:
        assert hb.finished is False, "heartbeat 分片 finished 必须为 False"
        assert hb.delta_content == "", "heartbeat 分片 delta_content 必须为空字符串"
        assert "round" in hb.metadata, "heartbeat metadata 必须包含 round"


async def test_each_tool_emits_start_and_end_progress_chunks() -> None:
    """每个工具产出 phase="start" / phase="end" 各 1 个 Tool_Progress_Chunk。

    构造 1 轮含 2 个工具调用 + 最后 1 轮流式回复（max_rounds=2），验证
    每个工具都产出了 start 和 end 进度分片。

    **Validates: Requirement 3.2, 3.3, 3.4, 3.5**
    """
    chat_responses = [
        # 第 1 轮：返回 2 个工具调用
        LLMResponse(
            content="",
            model="test-model",
            usage={"prompt_tokens": 10},
            tool_calls=[
                ToolCallRequest(id="call-a", name="search", arguments='{"q": "x"}'),
                ToolCallRequest(id="call-b", name="read_file", arguments='{"path": "y"}'),
            ],
        ),
    ]

    model = _FakeModel(chat_responses)
    adapter = _adapter()
    context = ConversationContext()
    context.add_user_message("搜索并读取")

    chunks: list[StreamingChunk] = []
    async for chunk in adapter.run_streaming(context, _config(max_rounds=2), model):  # type: ignore[arg-type]
        chunks.append(chunk)

    # 提取工具进度分片
    progress_chunks = [c for c in chunks if c.metadata.get("type") == "tool_progress"]

    # 每个工具各有 start + end = 2，共 2 个工具 = 4 个进度分片
    assert len(progress_chunks) == 4, (
        f"期望 4 个 tool_progress 分片（2 工具 × 2 阶段），实际 {len(progress_chunks)} 个"
    )

    # 按 tool_call_id 分组检验
    by_tool: dict[str, list[str]] = {}
    for pc in progress_chunks:
        tid = pc.metadata["tool_call_id"]
        phase = pc.metadata["phase"]
        by_tool.setdefault(tid, []).append(phase)

    assert "call-a" in by_tool
    assert "call-b" in by_tool
    assert by_tool["call-a"] == ["start", "end"], (
        f"工具 call-a 期望 [start, end]，实际 {by_tool['call-a']}"
    )
    assert by_tool["call-b"] == ["start", "end"], (
        f"工具 call-b 期望 [start, end]，实际 {by_tool['call-b']}"
    )

    # 验证所有进度分片属性
    for pc in progress_chunks:
        assert pc.finished is False, "tool_progress 分片 finished 必须为 False"
        assert pc.delta_content == "", "tool_progress 分片 delta_content 必须为空字符串"


async def test_progress_chunk_metadata_contains_required_fields_without_arguments() -> None:
    """工具进度分片 metadata 含 round/tool_name/tool_call_id 但不含 arguments。

    **Validates: Requirement 3.2, 3.3, 3.8**
    """
    chat_responses = [
        LLMResponse(
            content="",
            model="test-model",
            usage={"prompt_tokens": 10},
            tool_calls=[
                ToolCallRequest(
                    id="call-secret",
                    name="search",
                    arguments='{"api_key": "sk-secret-DO-NOT-LEAK"}',
                ),
            ],
        ),
    ]

    model = _FakeModel(chat_responses)
    adapter = _adapter()
    context = ConversationContext()
    context.add_user_message("搜索")

    chunks: list[StreamingChunk] = []
    async for chunk in adapter.run_streaming(context, _config(max_rounds=2), model):  # type: ignore[arg-type]
        chunks.append(chunk)

    progress_chunks = [c for c in chunks if c.metadata.get("type") == "tool_progress"]
    assert len(progress_chunks) >= 2, "至少应有 start + end 两个 tool_progress 分片"

    for pc in progress_chunks:
        # 必须包含的字段
        assert "round" in pc.metadata, "metadata 必须包含 round"
        assert "tool_name" in pc.metadata, "metadata 必须包含 tool_name"
        assert "tool_call_id" in pc.metadata, "metadata 必须包含 tool_call_id"
        assert "phase" in pc.metadata, "metadata 必须包含 phase"

        # 验证字段值正确
        assert pc.metadata["tool_name"] == "search"
        assert pc.metadata["tool_call_id"] == "call-secret"
        assert pc.metadata["phase"] in ("start", "end")

        # 不得包含 arguments（避免泄露密钥）
        assert "arguments" not in pc.metadata, (
            f"metadata 不应包含 arguments 字段，但存在: {pc.metadata}"
        )
        # 双重检查：metadata 中不得出现 arguments 的内容
        metadata_str = str(pc.metadata)
        assert "sk-secret-DO-NOT-LEAK" not in metadata_str, "metadata 中不得出现工具入参完整文本"


async def test_heartbeat_metadata_contains_round_number() -> None:
    """heartbeat 分片的 metadata 包含正确的轮次号。

    **Validates: Requirement 3.1**
    """
    chat_responses = [
        # 第 1 轮：工具调用
        LLMResponse(
            content="",
            model="test-model",
            usage={"prompt_tokens": 5},
            tool_calls=[
                ToolCallRequest(id="call-1", name="search", arguments='{"q": "x"}'),
            ],
        ),
        # 第 2 轮：工具调用
        LLMResponse(
            content="",
            model="test-model",
            usage={"prompt_tokens": 10},
            tool_calls=[
                ToolCallRequest(id="call-2", name="search", arguments='{"q": "y"}'),
            ],
        ),
    ]

    model = _FakeModel(chat_responses)
    adapter = _adapter()
    context = ConversationContext()
    context.add_user_message("连续搜索")

    chunks: list[StreamingChunk] = []
    async for chunk in adapter.run_streaming(context, _config(max_rounds=3), model):  # type: ignore[arg-type]
        chunks.append(chunk)

    heartbeats = [c for c in chunks if c.metadata.get("type") == "heartbeat"]
    # 2 个中间轮次应各产出 1 个 heartbeat
    assert len(heartbeats) == 2, f"期望 2 个 heartbeat（2 个中间轮次），实际 {len(heartbeats)} 个"
    assert heartbeats[0].metadata["round"] == 1
    assert heartbeats[1].metadata["round"] == 2


async def test_final_chunk_remains_finished_true() -> None:
    """最终轮次仍产出 finished=True 的终止分片，保持既有语义。

    PR-4 更新：当 max_rounds 命中（中间轮次全部 tool_calls 命中循环耗尽）时，
    跳过 ``_stream_final_round``，产出 ``delta_content=""`` +
    ``metadata.terminated_reason="max_rounds"``。

    **Validates: Requirement 8.10**
    """
    chat_responses = [
        LLMResponse(
            content="",
            model="test-model",
            usage={"prompt_tokens": 5},
            tool_calls=[
                ToolCallRequest(id="call-1", name="search", arguments='{"q": "x"}'),
            ],
        ),
    ]

    model = _FakeModel(chat_responses)
    adapter = _adapter()
    context = ConversationContext()
    context.add_user_message("搜索")

    chunks: list[StreamingChunk] = []
    async for chunk in adapter.run_streaming(context, _config(max_rounds=2), model):  # type: ignore[arg-type]
        chunks.append(chunk)

    # 最终 chunk 必须 finished=True
    assert chunks[-1].finished is True, "最终分片必须 finished=True"
    # PR-4: max_rounds 命中时 delta_content 为空，携带 terminated_reason
    assert chunks[-1].delta_content == ""
    assert chunks[-1].metadata.get("terminated_reason") == "max_rounds"
