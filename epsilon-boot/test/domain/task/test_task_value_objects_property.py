"""任务领域值对象属性测试模块。

使用 Hypothesis 对 Task、TraceEntry、TaskResult 值对象进行属性测试，验证：
- 构造成功且字段值保留
- frozen dataclass 不可变性（赋值属性时抛出 FrozenInstanceError）
"""

import dataclasses

import hypothesis.strategies as st
import pytest
from hypothesis import given, settings

from domain.task.value_objects import Task, TaskResult, TaskStatus, TraceEntry

# ── Hypothesis 策略 ──

# Task 字段策略
goal_st = st.text(min_size=1).filter(lambda s: s.strip())
input_data_st = st.dictionaries(st.text(), st.text())
constraints_st = st.lists(st.text())
output_format_st = st.none() | st.text()
model_st = st.none() | st.text(min_size=1)
session_id_st = st.none() | st.text(min_size=1)

# TraceEntry 字段策略
step_st = st.integers(min_value=1)
action_st = st.sampled_from(["tool_call", "tool_result", "llm_response"])
detail_st = st.text()
timestamp_ms_st = st.floats(min_value=0, allow_nan=False, allow_infinity=False)

# TaskResult 字段策略
content_st = st.text()
status_st = st.sampled_from(TaskStatus)
result_model_st = st.text(min_size=1)
usage_st = st.dictionaries(st.text(), st.integers(min_value=0))
trace_st = st.just([])
latency_ms_st = st.floats(min_value=0, allow_nan=False, allow_infinity=False)


# ── Property 1: Value object construction and immutability ──
# Feature: task-oriented-agent, Property 1: Value object construction and immutability


@settings(max_examples=100, deadline=5000)
@given(
    goal=goal_st,
    input_data=input_data_st,
    constraints=constraints_st,
    output_format=output_format_st,
    model=model_st,
    session_id=session_id_st,
)
def test_task_construction_and_field_preservation(
    goal: str,
    input_data: dict,
    constraints: list,
    output_format: str | None,
    model: str | None,
    session_id: str | None,
) -> None:
    """验证 Task 构造成功且所有字段值保留。

    对于任意合法的 goal（非空非纯空白）、input_data、constraints、
    output_format、model、session_id，构造 Task 应成功且字段值与输入一致。

    Validates: Requirements 2.1
    """
    task = Task(
        goal=goal,
        input_data=input_data,
        constraints=constraints,
        output_format=output_format,
        model=model,
        session_id=session_id,
    )

    assert task.goal == goal
    assert task.input_data == input_data
    assert task.constraints == constraints
    assert task.output_format == output_format
    assert task.model == model
    assert task.session_id == session_id


@settings(max_examples=100, deadline=5000)
@given(goal=goal_st)
def test_task_is_frozen(goal: str) -> None:
    """验证 Task 为 frozen dataclass，赋值属性时抛出 FrozenInstanceError。

    Validates: Requirements 2.1
    """
    task = Task(goal=goal)

    with pytest.raises(dataclasses.FrozenInstanceError):
        task.goal = "new goal"  # type: ignore[misc]


@settings(max_examples=100, deadline=5000)
@given(
    step=step_st,
    action=action_st,
    detail=detail_st,
    timestamp_ms=timestamp_ms_st,
)
def test_trace_entry_construction_and_field_preservation(
    step: int,
    action: str,
    detail: str,
    timestamp_ms: float,
) -> None:
    """验证 TraceEntry 构造成功且所有字段值保留。

    对于任意合法的 step、action、detail、timestamp_ms，
    构造 TraceEntry 应成功且字段值与输入一致。

    Validates: Requirements 3.1
    """
    entry = TraceEntry(
        step=step,
        action=action,
        detail=detail,
        timestamp_ms=timestamp_ms,
    )

    assert entry.step == step
    assert entry.action == action
    assert entry.detail == detail
    assert entry.timestamp_ms == timestamp_ms


@settings(max_examples=100, deadline=5000)
@given(step=step_st, action=action_st, detail=detail_st, timestamp_ms=timestamp_ms_st)
def test_trace_entry_is_frozen(
    step: int,
    action: str,
    detail: str,
    timestamp_ms: float,
) -> None:
    """验证 TraceEntry 为 frozen dataclass，赋值属性时抛出 FrozenInstanceError。

    Validates: Requirements 3.1
    """
    entry = TraceEntry(step=step, action=action, detail=detail, timestamp_ms=timestamp_ms)

    with pytest.raises(dataclasses.FrozenInstanceError):
        entry.step = 999  # type: ignore[misc]


@settings(max_examples=100, deadline=5000)
@given(
    content=content_st,
    status=status_st,
    model=result_model_st,
    usage=usage_st,
    trace=trace_st,
    latency_ms=latency_ms_st,
)
def test_task_result_construction_and_field_preservation(
    content: str,
    status: TaskStatus,
    model: str,
    usage: dict,
    trace: list,
    latency_ms: float,
) -> None:
    """验证 TaskResult 构造成功且所有字段值保留。

    对于任意合法的 content、status、model、usage、trace、latency_ms，
    构造 TaskResult 应成功且字段值与输入一致。

    Validates: Requirements 4.1
    """
    result = TaskResult(
        content=content,
        status=status,
        model=model,
        prompt_id="task-template@v1",
        usage=usage,
        trace=trace,
        latency_ms=latency_ms,
    )

    assert result.content == content
    assert result.status == status
    assert result.model == model
    assert result.prompt_id == "task-template@v1"
    assert result.usage == usage
    assert result.trace == trace
    assert result.latency_ms == latency_ms


@settings(max_examples=100, deadline=5000)
@given(content=content_st, status=status_st, model=result_model_st)
def test_task_result_is_frozen(content: str, status: TaskStatus, model: str) -> None:
    """验证 TaskResult 为 frozen dataclass，赋值属性时抛出 FrozenInstanceError。

    Validates: Requirements 4.1
    """
    result = TaskResult(
        content=content,
        status=status,
        model=model,
        prompt_id="task-template@v1",
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        result.content = "new content"  # type: ignore[misc]


# ── Property 2: Task goal whitespace validation ──
# Feature: task-oriented-agent, Property 2: Task goal whitespace validation


# 纯空白字符串策略（含空字符串）
whitespace_only_st = st.text(
    alphabet=st.sampled_from([" ", "\t", "\n", "\r"]),
).filter(lambda s: len(s) == 0 or s.strip() == "")

# 含至少一个非空白字符的字符串策略
non_whitespace_st = st.text(min_size=1).filter(lambda s: s.strip())


@settings(max_examples=100, deadline=5000)
@given(goal=whitespace_only_st)
def test_task_goal_rejects_whitespace_only(goal: str) -> None:
    """验证纯空白字符串（含空字符串）作为 goal 时，Task 构造抛出 ValueError。

    对于任意由空白字符组成的字符串（包括空字符串），
    构造 Task 应抛出 ValueError，拒绝无效的 goal。

    Validates: Requirements 2.2
    """
    with pytest.raises(ValueError):
        Task(goal=goal)


@settings(max_examples=100, deadline=5000)
@given(goal=non_whitespace_st)
def test_task_goal_accepts_non_whitespace(goal: str) -> None:
    """验证含至少一个非空白字符的字符串作为 goal 时，Task 构造成功。

    对于任意包含至少一个非空白字符的字符串，
    构造 Task 应成功且 goal 字段值与输入一致。

    Validates: Requirements 2.2
    """
    task = Task(goal=goal)
    assert task.goal == goal
