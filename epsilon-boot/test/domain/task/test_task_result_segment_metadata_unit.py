"""任务结果分段元数据单元测试。"""

from __future__ import annotations

from domain.agent.segmented_execution import SegmentRunMetadata
from domain.task.value_objects import TaskResult, TaskStatus


def test_task_result_default_segment_metadata() -> None:
    """TaskResult 默认携带单段 completed 元数据。"""
    result = TaskResult(
        content="ok",
        status=TaskStatus.SUCCESS,
        model="m",
        prompt_id="task-template@v1",
    )

    assert result.segment_metadata.segment_index == 1
    assert result.segment_metadata.segment_count == 1
    assert result.segment_metadata.segment_stop_reason == "completed"


def test_task_result_accepts_paused_segment_metadata() -> None:
    """TaskResult paused 响应可携带 auto_disabled 分段元数据。"""
    metadata = SegmentRunMetadata(segment_stop_reason="auto_disabled")
    result = TaskResult(
        content="",
        status=TaskStatus.PAUSED,
        model="m",
        prompt_id="task-template@v1",
        terminated_reason="max_rounds",
        can_continue=True,
        segment_metadata=metadata,
    )

    assert result.segment_metadata is metadata
