"""任务暂停结果值对象单元测试。"""

import pytest

from domain.task.value_objects import TaskContinueRequest, TaskResult, TaskStatus


def test_task_status_paused_value() -> None:
    """验证任务暂停状态枚举值。"""
    assert TaskStatus.PAUSED.value == "paused"


def test_task_result_continuation_defaults() -> None:
    """验证 TaskResult 新增继续字段保持兼容默认值。"""
    result = TaskResult(
        content="执行完成",
        status=TaskStatus.SUCCESS,
        model="test-model",
        prompt_id="task-template@v1",
    )

    assert result.terminated_reason == "completed"
    assert result.can_continue is False


def test_task_result_paused_fields() -> None:
    """验证 paused 结果可携带终止原因和继续标记。"""
    result = TaskResult(
        content="",
        status=TaskStatus.PAUSED,
        model="test-model",
        prompt_id="task-template@v1",
        terminated_reason="max_rounds",
        can_continue=True,
    )

    assert result.status == TaskStatus.PAUSED
    assert result.terminated_reason == "max_rounds"
    assert result.can_continue is True


def test_task_continue_request_empty_session_id_raises() -> None:
    """验证任务继续请求要求 session_id 非空。"""
    with pytest.raises(ValueError, match="session_id"):
        TaskContinueRequest(session_id="")
