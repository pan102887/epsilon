"""TaskAgentAdapter 属性测试模块。

使用 Hypothesis 对 TaskAgentAdapter.build_system_prompt 静态方法进行属性测试，验证：
- 系统提示词包含 goal
- input_data 非空时包含 JSON 序列化内容
- constraints 非空时包含每条约束字符串
- output_format 不为 None 时包含 output_format
- 相同 Task 两次调用产生相同结果（确定性）
"""

import itertools
import json
from unittest.mock import AsyncMock, MagicMock

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from domain.agent.value_objects import AgentResult
from domain.chat.context import AssistantMessage, ConversationContext, ToolMessage
from domain.model_access.value_objects import ToolCallRequest
from domain.prompt.value_objects import LoadedPrompt
from domain.task.value_objects import Task, TaskStatus
from infrastructure.task.task_agent_adapter import TaskAgentAdapter

# Feature: task-oriented-agent, Property 3: System prompt generation correctness and determinism

# ── Hypothesis 策略 ──

goal_st = st.text(min_size=1).filter(lambda s: s.strip())
input_data_st = st.dictionaries(
    st.text(min_size=1, max_size=20),
    st.text(min_size=1, max_size=50),
    max_size=5,
)
constraints_st = st.lists(st.text(min_size=1, max_size=50), max_size=5)
output_format_st = st.none() | st.text(min_size=1, max_size=50)


@settings(max_examples=100, deadline=5000)
@given(
    goal=goal_st,
    input_data=input_data_st,
    constraints=constraints_st,
    output_format=output_format_st,
)
def test_system_prompt_contains_goal(
    goal: str,
    input_data: dict,
    constraints: list[str],
    output_format: str | None,
) -> None:
    """验证 build_system_prompt 输出始终包含 task.goal。

    对于任意合法 Task，生成的系统提示词必须包含 goal 文本，
    因为 goal 是任务的核心指令部分。

    Validates: Requirements 9.1
    """
    task = Task(
        goal=goal,
        input_data=input_data,
        constraints=constraints,
        output_format=output_format,
    )
    prompt = TaskAgentAdapter.build_system_prompt(task)

    assert goal in prompt, f"系统提示词应包含 goal\ngoal: {goal!r}\nprompt: {prompt!r}"


@settings(max_examples=100, deadline=5000)
@given(
    goal=goal_st,
    input_data=st.dictionaries(
        st.text(min_size=1, max_size=20),
        st.text(min_size=1, max_size=50),
        min_size=1,
        max_size=5,
    ),
    constraints=constraints_st,
    output_format=output_format_st,
)
def test_system_prompt_contains_input_data_json(
    goal: str,
    input_data: dict,
    constraints: list[str],
    output_format: str | None,
) -> None:
    """验证 input_data 非空时，系统提示词包含其 JSON 序列化内容。

    当 Task.input_data 非空时，build_system_prompt 应将 input_data
    序列化为 JSON 并嵌入 "Input Data" 段落。

    Validates: Requirements 9.2
    """
    task = Task(
        goal=goal,
        input_data=input_data,
        constraints=constraints,
        output_format=output_format,
    )
    prompt = TaskAgentAdapter.build_system_prompt(task)
    input_json = json.dumps(input_data, ensure_ascii=False, indent=2)

    assert input_json in prompt, (
        f"系统提示词应包含 input_data 的 JSON 序列化内容\n"
        f"input_json: {input_json!r}\n"
        f"prompt: {prompt!r}"
    )


@settings(max_examples=100, deadline=5000)
@given(
    goal=goal_st,
    input_data=input_data_st,
    constraints=st.lists(st.text(min_size=1, max_size=50), min_size=1, max_size=5),
    output_format=output_format_st,
)
def test_system_prompt_contains_every_constraint(
    goal: str,
    input_data: dict,
    constraints: list[str],
    output_format: str | None,
) -> None:
    """验证 constraints 非空时，系统提示词包含每条约束字符串。

    当 Task.constraints 非空时，build_system_prompt 应将每条约束
    作为编号列表项嵌入 "Constraints" 段落，每条约束文本都应出现在输出中。

    Validates: Requirements 9.3
    """
    task = Task(
        goal=goal,
        input_data=input_data,
        constraints=constraints,
        output_format=output_format,
    )
    prompt = TaskAgentAdapter.build_system_prompt(task)

    for constraint in constraints:
        assert constraint in prompt, (
            f"系统提示词应包含 constraint: {constraint!r}\nprompt: {prompt!r}"
        )


@settings(max_examples=100, deadline=5000)
@given(
    goal=goal_st,
    input_data=input_data_st,
    constraints=constraints_st,
    output_format=st.text(min_size=1, max_size=50),
)
def test_system_prompt_contains_output_format(
    goal: str,
    input_data: dict,
    constraints: list[str],
    output_format: str,
) -> None:
    """验证 output_format 不为 None 时，系统提示词包含 output_format。

    当 Task.output_format 不为 None 时，build_system_prompt 应将
    output_format 嵌入 "Expected Output Format" 段落。

    Validates: Requirements 9.4
    """
    task = Task(
        goal=goal,
        input_data=input_data,
        constraints=constraints,
        output_format=output_format,
    )
    prompt = TaskAgentAdapter.build_system_prompt(task)

    assert output_format in prompt, (
        f"系统提示词应包含 output_format: {output_format!r}\nprompt: {prompt!r}"
    )


@settings(max_examples=100, deadline=5000)
@given(
    goal=goal_st,
    input_data=input_data_st,
    constraints=constraints_st,
    output_format=output_format_st,
)
def test_system_prompt_determinism(
    goal: str,
    input_data: dict,
    constraints: list[str],
    output_format: str | None,
) -> None:
    """验证相同 Task 两次调用 build_system_prompt 产生相同结果。

    系统提示词生成是确定性的纯函数，相同的 Task 输入
    始终产生相同的系统提示词输出。

    Validates: Requirements 9.5
    """
    task = Task(
        goal=goal,
        input_data=input_data,
        constraints=constraints,
        output_format=output_format,
    )
    prompt1 = TaskAgentAdapter.build_system_prompt(task)
    prompt2 = TaskAgentAdapter.build_system_prompt(task)

    assert prompt1 == prompt2, (
        f"两次调用 build_system_prompt 结果不一致\n第一次: {prompt1!r}\n第二次: {prompt2!r}"
    )


# ── 辅助工厂函数 ──


def _create_mock_adapter(agent_result=None, agent_exception=None):
    """创建带有 mock 依赖的 TaskAgentAdapter 实例。

    根据参数配置 AgentPort.run() 的行为：返回指定结果或抛出指定异常。

    Args:
        agent_result: AgentPort.run() 的返回值，默认返回简单成功结果
        agent_exception: AgentPort.run() 抛出的异常，优先于 agent_result

    Returns:
        (adapter, agent_mock, session_store_mock) 三元组
    """
    agent = AsyncMock()
    if agent_exception:
        agent.run.side_effect = agent_exception
    else:
        agent.run.return_value = agent_result or AgentResult(content="ok", model="test-model")

    tool_registry = MagicMock()
    tool_registry.get_schemas.return_value = []

    model_registry = MagicMock()
    model_access = AsyncMock()
    model_registry.get_adapter_for_model.return_value = model_access
    model_registry.get_default_model.return_value = "default-model"

    compaction = MagicMock()

    session_store = AsyncMock()
    session_store.load.return_value = ConversationContext()

    return (
        TaskAgentAdapter(
            agent=agent,
            tool_registry=tool_registry,
            model_registry=model_registry,
            compaction=compaction,
            session_store=session_store,
            prompt_registry=MagicMock(
                get=MagicMock(
                    return_value=LoadedPrompt(
                        prompt_id="task-template@v1",
                        name="task-template",
                        version="v1",
                        content="骨架",
                    )
                )
            ),
        ),
        agent,
        session_store,
    )


# ── Hypothesis 策略（Property 4–7 共用） ──

session_id_st = st.none() | st.text(min_size=1, max_size=30)
model_st = st.none() | st.text(min_size=1, max_size=30)
content_st = st.text(min_size=1, max_size=100)
model_name_st = st.text(min_size=1, max_size=30)
usage_st = st.dictionaries(
    st.sampled_from(["prompt_tokens", "completion_tokens", "total_tokens"]),
    st.integers(min_value=0, max_value=10000),
    max_size=3,
)
latency_st = st.floats(min_value=0.0, max_value=100000.0, allow_nan=False, allow_infinity=False)


# Feature: task-oriented-agent, Property 4: Session context load/save routing


@pytest.mark.asyncio
@settings(max_examples=100, deadline=5000)
@given(
    goal=goal_st,
    input_data=input_data_st,
    constraints=constraints_st,
    output_format=output_format_st,
    session_id=st.text(min_size=1, max_size=30),
    model=model_st,
)
async def test_session_context_load_save_with_session_id(
    goal: str,
    input_data: dict,
    constraints: list[str],
    output_format: str | None,
    session_id: str,
    model: str | None,
) -> None:
    """验证有 session_id 时，execute 调用 load 和 save。

    当 Task.session_id 不为 None 时，TaskAgentAdapter.execute() 应在执行前
    调用 SessionContextStorePort.load(session_id) 加载已有上下文，
    并在执行成功后调用 SessionContextStorePort.save(session_id, context) 保存上下文。

    Validates: Requirements 5.4, 6.4, 6.5
    """
    task = Task(
        goal=goal,
        input_data=input_data,
        constraints=constraints,
        output_format=output_format,
        model=model,
        session_id=session_id,
    )
    adapter, _agent, session_store = _create_mock_adapter()

    await adapter.execute(task)

    session_store.load.assert_called_once_with(session_id)
    session_store.save.assert_called_once()
    saved_sid = session_store.save.call_args[0][0]
    assert saved_sid == session_id, f"save 的 session_id 应为 {session_id!r}，实际为 {saved_sid!r}"


@pytest.mark.asyncio
@settings(max_examples=100, deadline=5000)
@given(
    goal=goal_st,
    input_data=input_data_st,
    constraints=constraints_st,
    output_format=output_format_st,
    model=model_st,
)
async def test_session_context_no_load_save_without_session_id(
    goal: str,
    input_data: dict,
    constraints: list[str],
    output_format: str | None,
    model: str | None,
) -> None:
    """验证无 session_id 时，execute 不调用 load 和 save。

    当 Task.session_id 为 None 时，TaskAgentAdapter.execute() 应创建空的
    ConversationContext，执行完成后不调用 SessionContextStorePort 的 load 和 save。

    Validates: Requirements 5.4, 6.4, 6.5
    """
    task = Task(
        goal=goal,
        input_data=input_data,
        constraints=constraints,
        output_format=output_format,
        model=model,
        session_id=None,
    )
    adapter, _agent, session_store = _create_mock_adapter()

    await adapter.execute(task)

    session_store.load.assert_not_called()
    session_store.save.assert_not_called()


# Feature: task-oriented-agent, Property 5: Successful execution produces SUCCESS result


@pytest.mark.asyncio
@settings(max_examples=100, deadline=5000)
@given(
    goal=goal_st,
    input_data=input_data_st,
    constraints=constraints_st,
    output_format=output_format_st,
    model=model_st,
    session_id=session_id_st,
    result_content=content_st,
    result_model=model_name_st,
    result_usage=usage_st,
    result_latency=latency_st,
)
async def test_successful_execution_produces_success_result(
    goal: str,
    input_data: dict,
    constraints: list[str],
    output_format: str | None,
    model: str | None,
    session_id: str | None,
    result_content: str,
    result_model: str,
    result_usage: dict[str, int],
    result_latency: float,
) -> None:
    """验证 AgentPort.run() 成功时，TaskResult.status == SUCCESS 且字段匹配。

    对于任意 Task 和 AgentResult，当 AgentPort.run() 正常返回时，
    TaskAgentAdapter.execute() 应返回 TaskResult，其 status 为 SUCCESS，
    content、model、usage 分别匹配 AgentResult 的对应字段。

    Validates: Requirements 6.3, 6.6
    """
    agent_result = AgentResult(
        content=result_content,
        model=result_model,
        usage=result_usage,
        latency_ms=result_latency,
    )
    task = Task(
        goal=goal,
        input_data=input_data,
        constraints=constraints,
        output_format=output_format,
        model=model,
        session_id=session_id,
    )
    adapter, _agent, _store = _create_mock_adapter(agent_result=agent_result)

    task_result = await adapter.execute(task)

    assert task_result.status == TaskStatus.SUCCESS, f"期望 SUCCESS，实际 {task_result.status}"
    assert task_result.content == result_content, (
        f"content 不匹配: {task_result.content!r} != {result_content!r}"
    )
    assert task_result.model == result_model, (
        f"model 不匹配: {task_result.model!r} != {result_model!r}"
    )
    assert task_result.usage == result_usage, (
        f"usage 不匹配: {task_result.usage!r} != {result_usage!r}"
    )


# Feature: task-oriented-agent, Property 6: Exception handling produces FAILED result


@pytest.mark.asyncio
@settings(max_examples=100, deadline=5000)
@given(
    goal=goal_st,
    input_data=input_data_st,
    constraints=constraints_st,
    output_format=output_format_st,
    model=model_st,
    session_id=session_id_st,
    error_message=st.text(min_size=1, max_size=200),
)
async def test_exception_handling_produces_failed_result(
    goal: str,
    input_data: dict,
    constraints: list[str],
    output_format: str | None,
    model: str | None,
    session_id: str | None,
    error_message: str,
) -> None:
    """验证 AgentPort.run() 抛出异常时，TaskResult.status == FAILED 且不传播异常。

    对于任意 Task 和异常消息，当 AgentPort.run() 抛出 RuntimeError 时，
    TaskAgentAdapter.execute() 应捕获异常并返回 TaskResult，
    其 status 为 FAILED，content 为异常的字符串表示，不向调用方传播异常。

    Validates: Requirements 6.7
    """
    exception = RuntimeError(error_message)
    task = Task(
        goal=goal,
        input_data=input_data,
        constraints=constraints,
        output_format=output_format,
        model=model,
        session_id=session_id,
    )
    adapter, _agent, _store = _create_mock_adapter(agent_exception=exception)

    # 不应抛出异常
    task_result = await adapter.execute(task)

    assert task_result.status == TaskStatus.FAILED, f"期望 FAILED，实际 {task_result.status}"
    assert task_result.content == str(exception), (
        f"content 应为异常字符串: {task_result.content!r} != {str(exception)!r}"
    )


# Feature: task-oriented-agent, Property 7: Trace extraction from context messages

# ── Property 7 专用策略 ──

tool_call_request_st = st.builds(
    ToolCallRequest,
    id=st.text(min_size=1, max_size=20).map(lambda s: f"call_{s}"),
    name=st.text(min_size=1, max_size=20),
    arguments=st.text(min_size=1, max_size=50).map(lambda s: f'{{"arg": "{s}"}}'),
)

assistant_msg_st = st.builds(
    AssistantMessage,
    content=st.text(max_size=50),
    tool_calls=st.lists(tool_call_request_st, min_size=1, max_size=3),
)

tool_msg_st = st.builds(
    ToolMessage,
    content=st.text(min_size=1, max_size=50),
    tool_name=st.text(min_size=1, max_size=20),
    tool_call_id=st.text(min_size=1, max_size=20).map(lambda s: f"call_{s}"),
)

# 生成交替的 AssistantMessage 和 ToolMessage 序列
message_sequence_st = st.lists(
    st.one_of(assistant_msg_st, tool_msg_st),
    min_size=1,
    max_size=10,
)


@settings(max_examples=100, deadline=5000)
@given(messages=message_sequence_st)
def test_trace_extraction_from_context_messages(
    messages: list,
) -> None:
    """验证 _extract_trace 正确提取执行轨迹。

    对于任意 AssistantMessage（含 tool_calls）和 ToolMessage 序列，
    _extract_trace 应将 AssistantMessage 中的每个 tool_call 映射为
    action="tool_call" 的 TraceEntry，将每个 ToolMessage 映射为
    action="tool_result" 的 TraceEntry，step 从 1 单调递增。

    Validates: Requirements 6.8
    """
    adapter, _, _ = _create_mock_adapter()

    trace = adapter._extract_trace(messages, start_index=0)

    # 计算期望的 trace 条目数
    expected_count = 0
    for msg in messages:
        if isinstance(msg, AssistantMessage) and msg.tool_calls:
            expected_count += len(msg.tool_calls)
        elif isinstance(msg, ToolMessage):
            expected_count += 1

    assert len(trace) == expected_count, f"trace 条目数不匹配: {len(trace)} != {expected_count}"

    # 验证 step 从 1 单调递增
    for i, entry in enumerate(trace):
        assert entry.step == i + 1, f"step 应为 {i + 1}，实际为 {entry.step}"

    # 验证 action 类型正确映射
    trace_idx = 0
    for msg in messages:
        if isinstance(msg, AssistantMessage) and msg.tool_calls:
            for tc in msg.tool_calls:
                assert trace[trace_idx].action == "tool_call", (
                    f"期望 tool_call，实际 {trace[trace_idx].action}"
                )
                assert tc.name in trace[trace_idx].detail, (
                    f"detail 应包含工具名 {tc.name!r}，实际 {trace[trace_idx].detail!r}"
                )
                trace_idx += 1
        elif isinstance(msg, ToolMessage):
            assert trace[trace_idx].action == "tool_result", (
                f"期望 tool_result，实际 {trace[trace_idx].action}"
            )
            assert trace[trace_idx].detail == msg.content, (
                f"detail 应为 ToolMessage.content: {trace[trace_idx].detail!r} != {msg.content!r}"
            )
            trace_idx += 1


# Feature: agent-adapter-refactor, Property: Trace timestamps are non-decreasing
# 当 _extract_trace 接收事件时刻映射时，``trace`` 中的 timestamp_ms 应保持
# 单调非递减，反映工具调用与工具结果在物理时间上的先后顺序。


@settings(max_examples=100, deadline=5000)
@given(
    messages=message_sequence_st,
    start_ms=st.integers(min_value=0, max_value=10**12),
    step_ms=st.integers(min_value=0, max_value=10**6),
)
def test_trace_timestamps_non_decreasing(
    messages: list,
    start_ms: int,
    step_ms: int,
) -> None:
    """对任意工具调用序列，``trace[i].timestamp_ms <= trace[i+1].timestamp_ms``。

    Given：``messages`` 中包含若干 AssistantMessage / ToolMessage；
    ``event_timestamps`` 按消息全局索引依次递增分配
    （``start_ms``、``start_ms + step_ms``、``start_ms + 2*step_ms`` ……）；
    When：调用 ``_extract_trace`` 并传入 ``event_timestamps``；
    Then：``trace[i].timestamp_ms`` 单调非递减。

    Validates: 需求 4.4
    """
    adapter, _, _ = _create_mock_adapter()
    event_timestamps = {i: start_ms + step_ms * i for i in range(len(messages))}

    trace = adapter._extract_trace(
        messages,
        start_index=0,
        event_timestamps=event_timestamps,
    )

    for prev, curr in itertools.pairwise(trace):
        assert prev.timestamp_ms <= curr.timestamp_ms, (
            f"timestamp_ms 不单调: {prev.timestamp_ms} > {curr.timestamp_ms}"
        )
