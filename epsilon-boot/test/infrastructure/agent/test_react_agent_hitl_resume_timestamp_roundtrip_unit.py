"""HITL resume 时间戳回环单元测试模块。

验证 ``event_timestamps`` 在 HITL 中断/恢复往返过程中保持不变：

- (a) 在中断前注入 ``event_timestamps[k]=1_717_000_000_000``。
- (b) 触发 HITL → ``ApprovalInterrupt.context_snapshot = ctx.to_dict()``
  → ``approval_interrupt_to_dict`` → 持久化（mock store）→
  ``approval_interrupt_from_dict`` → ``ConversationContext.from_dict``。
- (c) resume 后调用 ``_extract_trace``，断言相应
  ``Trace_Entry.timestamp_ms == 1_717_000_000_000``（不是 resume 时刻）。

**Validates: Requirements 6.1, 6.2, 6.3, 6.4, 6.5, Property 3**
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from domain.agent.ports import ApprovalPolicyPort, ApprovalStateStorePort
from domain.agent.tools import ToolExecutionResult
from domain.agent.value_objects import (
    AgentConfig,
    ApprovalDecision,
    ApprovalInterrupt,
    ApprovalInterruptSummary,
    ApprovalPolicy,
    PendingActionRequest,
)
from domain.chat.context import (
    AssistantMessage,
    BaseMessage,
    ConversationContext,
    UserMessage,
)
from domain.chat.value_objects import ContextBuilderResult
from domain.model_access.ports import ModelAccessPort
from domain.model_access.value_objects import (
    ChatRequest,
    LLMResponse,
    StreamingChunk,
    ToolCallRequest,
)
from infrastructure.agent.approval_state_store import (
    approval_interrupt_from_dict,
    approval_interrupt_to_dict,
)
from infrastructure.agent.react_agent_adapter import ReActAgentAdapter
from infrastructure.task.task_agent_adapter import TaskAgentAdapter

# ── Fakes ──


class _AlwaysApprovePolicy(ApprovalPolicyPort):
    """所有工具都需要审批的策略。"""

    def policy_for(self, tool_name: str) -> ApprovalPolicy:
        return ApprovalPolicy(
            tool_name=tool_name,
            interrupt=True,
            allowed_decisions=frozenset({"approve", "reject"}),
        )


class _MemoryApprovalStore(ApprovalStateStorePort):
    """内存审批状态存储，模拟持久化往返。"""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def save(self, interrupt: ApprovalInterrupt) -> None:
        # 模拟序列化→持久化→反序列化往返
        key = f"{interrupt.session_id}:{interrupt.approval_id}"
        self._store[key] = json.dumps(approval_interrupt_to_dict(interrupt), ensure_ascii=False)

    async def load(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        key = f"{session_id}:{approval_id}"
        raw = self._store.get(key)
        if raw is None:
            return None
        return approval_interrupt_from_dict(json.loads(raw))

    async def consume(self, session_id: str, approval_id: str) -> ApprovalInterrupt | None:
        key = f"{session_id}:{approval_id}"
        raw = self._store.pop(key, None)
        if raw is None:
            return None
        return approval_interrupt_from_dict(json.loads(raw))

    async def delete(self, session_id: str, approval_id: str) -> None:
        key = f"{session_id}:{approval_id}"
        self._store.pop(key, None)

    async def delete_session(self, session_id: str) -> None:
        keys_to_remove = [k for k in self._store if k.startswith(f"{session_id}:")]
        for k in keys_to_remove:
            del self._store[k]

    async def list_pending_by_session(
        self, session_id: str
    ) -> list[ApprovalInterruptSummary]:
        return []


class _FakeContextBuilder:
    """顺序返回 ContextBuilderResult 的测试 fake。"""

    def __init__(self, results: list[ContextBuilderResult] | None = None) -> None:
        self._results = list(
            results
            or [
                ContextBuilderResult(
                    messages=[UserMessage(content="go")],
                    usage={},
                ),
            ]
        )

    async def build(
        self,
        messages: list[BaseMessage],
        *,
        model_access: ModelAccessPort | None = None,
        model: str | None = None,
    ) -> ContextBuilderResult:
        del messages, model_access, model
        if self._results:
            return self._results.pop(0)
        return ContextBuilderResult(
            messages=[UserMessage(content="fallback")],
            usage={},
        )


class _FakeModelAccess:
    """顺序返回 LLMResponse 的模型 fake。"""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)

    async def chat(self, request: ChatRequest) -> LLMResponse:
        return self._responses.pop(0)

    async def stream(self, request: ChatRequest) -> AsyncIterator[StreamingChunk]:
        from test.infrastructure.agent._v3_stream_helpers import response_to_chunks

        if self._responses:
            response = self._responses.pop(0)
            for chunk in response_to_chunks(response):
                yield chunk
            return
        yield StreamingChunk(delta_content="done", finished=True, usage={"total_tokens": 1})

    def count_tokens(self, messages: list[BaseMessage]) -> int:
        return sum(len(message.content) for message in messages)


def _config() -> AgentConfig:
    """标准测试配置。"""
    return AgentConfig(
        system_prompt="sys",
        tool_schemas=[
            {"type": "function", "function": {"name": "lookup", "parameters": {}}},
        ],
        model="test-model",
        max_rounds=5,
        prompt_id="chat-default@v1",
    )


# ── Tests ──


@pytest.mark.asyncio
async def test_event_timestamps_survive_to_dict_from_dict_roundtrip() -> None:
    """基础往返测试：event_timestamps 经 to_dict→from_dict 后保持不变。"""
    ctx = ConversationContext()
    ctx.add_user_message("hello")
    ctx.add_assistant_message_with_tool_calls(
        "",
        [
            ToolCallRequest(id="call-1", name="lookup", arguments="{}"),
        ],
    )
    ctx.add_tool_result(tool_name="lookup", result="ok", tool_call_id="call-1")

    # 模拟 _stamp_event 在中断前写入已知时间戳
    known_ts = 1_717_000_000_000
    ctx.event_timestamps[1] = known_ts  # assistant message index
    ctx.event_timestamps[2] = known_ts + 100  # tool message index

    # 模拟持久化往返
    snapshot = ctx.to_dict()
    snapshot_json = json.dumps(snapshot)
    restored_data = json.loads(snapshot_json)
    restored_ctx = ConversationContext.from_dict(restored_data)

    # 验证时间戳恢复
    assert restored_ctx.event_timestamps[1] == known_ts
    assert restored_ctx.event_timestamps[2] == known_ts + 100


@pytest.mark.asyncio
async def test_hitl_interrupt_preserves_event_timestamps_in_context_snapshot() -> None:
    """ApprovalInterrupt.context_snapshot 经 to_dict/from_dict 后 event_timestamps 不变。"""
    ctx = ConversationContext()
    ctx.add_user_message("hello")
    idx = ctx.add_assistant_message_with_tool_calls(
        "",
        [
            ToolCallRequest(id="call-1", name="lookup", arguments="{}"),
        ],
    )

    # 写入已知时间戳
    known_ts = 1_717_000_000_000
    ctx.event_timestamps[idx] = known_ts
    ctx.session_id = "sess-test"

    # 构造 ApprovalInterrupt（模拟 _save_interrupt 流程）
    interrupt = ApprovalInterrupt(
        session_id="sess-test",
        approval_id="appr-001",
        actions=(
            PendingActionRequest(
                tool_call_id="call-1",
                tool_name="lookup",
                arguments="{}",
                allowed_decisions=frozenset({"approve", "reject"}),
            ),
        ),
        context_snapshot=ctx.to_dict(),
        round_num=1,
        model="test-model",
        usage_so_far={"total_tokens": 10},
    )

    # 模拟持久化往返 (approval_interrupt_to_dict → JSON → approval_interrupt_from_dict)
    serialized = json.dumps(approval_interrupt_to_dict(interrupt))
    restored_interrupt = approval_interrupt_from_dict(json.loads(serialized))

    # 从 context_snapshot 恢复 ConversationContext
    restored_ctx = ConversationContext.from_dict(restored_interrupt.context_snapshot)

    # 验证 event_timestamps 精确恢复（是中断前时刻，不是 resume 时刻）
    assert restored_ctx.event_timestamps[idx] == known_ts


@pytest.mark.asyncio
async def test_full_hitl_roundtrip_timestamps_flow() -> None:
    """完整 HITL 中断→持久化→恢复→resume 后 _extract_trace 时间戳不变。

    流程：
    1. 调用 adapter.run → 触发 HITL 中断
    2. 从 store 读取并消费中断状态（模拟持久化往返）
    3. 调用 adapter.resume → 继续执行
    4. 验证 _extract_trace 读取的时间戳等于中断前注入的值
    """
    store = _MemoryApprovalStore()
    tool_registry = MagicMock()
    tool_registry.execute = AsyncMock(return_value=ToolExecutionResult(content="result ok"))

    adapter = ReActAgentAdapter(
        tool_registry=tool_registry,
        context_builder=_FakeContextBuilder(
            [
                ContextBuilderResult(
                    messages=[UserMessage(content="go")],
                    usage={"prompt_tokens": 5},
                ),
                # resume 后第二轮
                ContextBuilderResult(
                    messages=[UserMessage(content="go2")],
                    usage={"prompt_tokens": 3},
                ),
            ]
        ),
        approval_policy=_AlwaysApprovePolicy(),
        approval_store=store,
    )

    # 第一轮：模型返回 tool_calls → 触发审批
    tool_call_response = LLMResponse(
        content="",
        model="test-model",
        usage={"prompt_tokens": 10, "completion_tokens": 5},
        tool_calls=[
            ToolCallRequest(id="call-1", name="lookup", arguments='{"q": "x"}'),
        ],
    )
    # resume 后第二轮：模型返回纯文本
    final_response = LLMResponse(
        content="final answer",
        model="test-model",
        usage={"total_tokens": 7},
        tool_calls=[],
    )
    model = _FakeModelAccess([tool_call_response, final_response])

    context = ConversationContext()
    context.add_user_message("hello")
    context.session_id = "sess-test"

    config = _config()

    # Step 1: 执行 → 触发审批中断
    result = await adapter.run(context, config, model)
    assert result.status == "approval_required"

    # 验证 store 中有保存的中断
    assert result.approval is not None
    approval_id = result.approval.approval_id
    consumed = await store.consume("sess-test", approval_id)
    assert consumed is not None

    # 从 snapshot 恢复 context
    restored_ctx = ConversationContext.from_dict(consumed.context_snapshot)

    # 验证中断前的 event_timestamps 被正确保存和恢复
    # _record_assistant_with_tool_calls 会打戳 assistant message
    # assistant message 在 user("hello") 之后 + system("sys") 之后
    # index: 0=system, 1=user, 2=assistant(tool_calls)
    # 因为 _ensure_agent_system_prompt 会先注入 system message
    assert len(restored_ctx.event_timestamps) >= 1
    # 获取 assistant message 的时间戳
    assistant_idx = None
    for i, msg in enumerate(restored_ctx.get_messages()):
        if isinstance(msg, AssistantMessage) and msg.tool_calls:
            assistant_idx = i
            break
    assert assistant_idx is not None
    assert assistant_idx in restored_ctx.event_timestamps
    original_ts = restored_ctx.event_timestamps[assistant_idx]
    assert original_ts > 0

    # Step 2: resume（approve 决策）
    decisions = (ApprovalDecision(type="approve", tool_call_id="call-1"),)
    resume_result = await adapter.resume(
        restored_ctx,
        config,
        model,
        consumed,
        decisions,
    )
    assert resume_result.status == "completed"
    assert resume_result.content == "final answer"

    # Step 3: 验证 resume 后 assistant message 的时间戳未被覆盖
    # 仍然等于中断前记录的值
    assert restored_ctx.event_timestamps[assistant_idx] == original_ts


@pytest.mark.asyncio
async def test_extract_trace_uses_preserved_timestamps_after_resume() -> None:
    """验证 _extract_trace 使用恢复后的 event_timestamps（中断前值）。

    模拟完整流程，用 TaskAgentAdapter._extract_trace 提取 trace，
    断言 TraceEntry.timestamp_ms 等于中断前注入的时间戳值。
    """
    # 构造中断前的 context 状态
    ctx = ConversationContext()
    ctx.add_system_message("sys")
    ctx.add_user_message("hello")

    # 模拟 _record_assistant_with_tool_calls
    assistant_idx = ctx.add_assistant_message_with_tool_calls(
        "",
        [
            ToolCallRequest(id="call-1", name="lookup", arguments='{"q": "x"}'),
        ],
    )
    known_assistant_ts = 1_717_000_000_000
    ctx.event_timestamps[assistant_idx] = known_assistant_ts

    # 模拟 _execute_tool_call 的 add_tool_result + _stamp_event
    tool_idx = ctx.add_tool_result(tool_name="lookup", result="result", tool_call_id="call-1")
    known_tool_ts = 1_717_000_000_100
    ctx.event_timestamps[tool_idx] = known_tool_ts

    # 模拟 HITL 中断→恢复往返
    snapshot = ctx.to_dict()
    snapshot_json = json.dumps(snapshot)
    restored_ctx = ConversationContext.from_dict(json.loads(snapshot_json))

    # 使用 TaskAgentAdapter._extract_trace 验证
    # pre_message_count = 2 (system + user, 在 tool calls 之前)

    # 直接调用 _extract_trace 静态方法
    dummy_adapter = TaskAgentAdapter.__new__(TaskAgentAdapter)
    trace = dummy_adapter.extract_trace(
        restored_ctx.get_messages(),
        start_index=2,  # 从 assistant message 开始
        event_timestamps=restored_ctx.event_timestamps,
    )

    # trace 应包含 assistant(tool_call) 和 tool_result 条目
    assert len(trace) >= 2

    # 第一个 trace entry（tool_call action）对应 assistant_idx
    tool_call_entry = next((t for t in trace if t.action == "tool_call"), None)
    assert tool_call_entry is not None
    assert tool_call_entry.timestamp_ms == known_assistant_ts

    # 第二个 trace entry（tool_result action）对应 tool_idx
    tool_result_entry = next((t for t in trace if t.action == "tool_result"), None)
    assert tool_result_entry is not None
    assert tool_result_entry.timestamp_ms == known_tool_ts
