"""值对象单元测试模块。

针对 TaskStatus、Task、TraceEntry、TaskResult 值对象的边界情况和具体示例进行单元测试，
与属性测试互补，覆盖枚举成员值、默认值、构造校验等场景。
"""

import pytest

from domain.agent.value_objects import ApprovalDecision
from domain.task.value_objects import (
    Task,
    TaskApprovalResumeRequest,
    TaskResult,
    TaskStatus,
    TraceEntry,
)


class TestTaskStatus:
    """TaskStatus 枚举单元测试。"""

    def test_task_status_members(self) -> None:
        """验证 TaskStatus 四个成员及其字符串值。"""
        assert TaskStatus.SUCCESS.value == "success"
        assert TaskStatus.FAILED.value == "failed"
        assert TaskStatus.PAUSED.value == "paused"
        assert TaskStatus.HUMAN_INTERVENTION_REQUIRED.value == "human_intervention_required"


class TestTask:
    """Task 值对象单元测试。"""

    def test_task_basic_construction(self) -> None:
        """创建 Task 并验证所有字段赋值正确，包括默认值。"""
        task = Task(goal="分析数据")

        assert task.goal == "分析数据"
        assert task.input_data == {}
        assert task.constraints == []
        assert task.output_format is None
        assert task.model is None
        assert task.session_id is None

    def test_task_defaults(self) -> None:
        """验证 Task 各可选字段的默认值。"""
        task = Task(goal="测试默认值")

        assert task.input_data == {}
        assert task.constraints == []
        assert task.output_format is None
        assert task.model is None
        assert task.session_id is None

    def test_task_goal_empty_raises(self) -> None:
        """goal 为空字符串时应抛出 ValueError。"""
        with pytest.raises(ValueError):
            Task(goal="")

    def test_task_goal_whitespace_raises(self) -> None:
        """goal 为纯空白字符时应抛出 ValueError。"""
        with pytest.raises(ValueError):
            Task(goal="   ")


class TestTraceEntry:
    """TraceEntry 值对象单元测试。"""

    def test_trace_entry_basic_construction(self) -> None:
        """创建 TraceEntry 并验证各字段赋值正确。"""
        entry = TraceEntry(step=1, action="tool_call", detail="调用搜索工具", timestamp_ms=1234.5)

        assert entry.step == 1
        assert entry.action == "tool_call"
        assert entry.detail == "调用搜索工具"
        assert entry.timestamp_ms == 1234.5


class TestTaskResult:
    """TaskResult 值对象单元测试。"""

    def test_task_result_basic_construction(self) -> None:
        """创建 TaskResult 并验证各字段赋值正确。"""
        result = TaskResult(
            content="执行完成",
            status=TaskStatus.SUCCESS,
            model="gpt-4",
            prompt_id="task-template@v1",
        )

        assert result.content == "执行完成"
        assert result.status == TaskStatus.SUCCESS
        assert result.model == "gpt-4"
        assert result.prompt_id == "task-template@v1"
        assert result.usage == {}
        assert result.trace == []
        assert result.latency_ms == 0.0

    def test_task_result_defaults(self) -> None:
        """验证 TaskResult 可选字段的默认值。"""
        result = TaskResult(
            content="ok",
            status=TaskStatus.FAILED,
            model="test-model",
            prompt_id="task-template@v1",
        )

        assert result.usage == {}
        assert result.trace == []
        assert result.latency_ms == 0.0
        assert result.approval_id is None

    def test_task_result_accepts_approval_id(self) -> None:
        """人工审批态任务结果可透传 approval_id。"""
        result = TaskResult(
            content="需要审批",
            status=TaskStatus.HUMAN_INTERVENTION_REQUIRED,
            model="test-model",
            prompt_id="task-template@v1",
            approval_id="approval-1",
        )

        assert result.approval_id == "approval-1"

    # ── prompt_id 校验用例（Validates: Requirement 5.6, 7.4）──

    @pytest.mark.parametrize("status", list(TaskStatus))
    def test_valid_prompt_id_all_status_branches(self, status: TaskStatus) -> None:
        """所有 TaskStatus 分支下合法 prompt_id 构造成功。

        # Validates: Requirement 5.6 / 7.4
        """
        result = TaskResult(
            content="test",
            status=status,
            model="gpt-4",
            prompt_id="task-template@v1",
        )
        assert result.prompt_id == "task-template@v1"

    @pytest.mark.parametrize("bad_id", ["", "foo", "task-template@0"])
    def test_invalid_prompt_id_raises_value_error(self, bad_id: str) -> None:
        """非法 prompt_id 抛出 ValueError。

        # Validates: Requirement 5.6
        """
        with pytest.raises(ValueError, match="prompt_id"):
            TaskResult(
                content="test",
                status=TaskStatus.SUCCESS,
                model="gpt-4",
                prompt_id=bad_id,
            )


class TestTaskApprovalResumeRequest:
    """TaskApprovalResumeRequest 值对象单元测试。"""

    def test_task_approval_resume_request_basic_construction(self) -> None:
        """创建 TaskApprovalResumeRequest 并验证字段赋值正确。"""
        request = TaskApprovalResumeRequest(
            session_id="sess-1",
            approval_id="approval-1",
            decisions=(ApprovalDecision(type="approve", tool_call_id="call-1"),),
            model="qwen-plus",
        )

        assert request.session_id == "sess-1"
        assert request.approval_id == "approval-1"
        assert request.decisions == (ApprovalDecision(type="approve", tool_call_id="call-1"),)
        assert request.model == "qwen-plus"

    def test_task_approval_resume_request_empty_session_id_raises(self) -> None:
        """session_id 为空时应抛出 ValueError。"""
        with pytest.raises(ValueError, match="session_id"):
            TaskApprovalResumeRequest(
                session_id="",
                approval_id="approval-1",
                decisions=(ApprovalDecision(type="approve", tool_call_id="call-1"),),
            )

    def test_task_approval_resume_request_empty_approval_id_raises(self) -> None:
        """approval_id 为空时应抛出 ValueError。"""
        with pytest.raises(ValueError, match="approval_id"):
            TaskApprovalResumeRequest(
                session_id="sess-1",
                approval_id="",
                decisions=(ApprovalDecision(type="approve", tool_call_id="call-1"),),
            )


class TestTaskToolNames:
    """Task.tool_names 字段单元测试。

    验证 tool_names 字段的默认值和显式赋值行为，确保向后兼容。
    """

    def test_task_default_tool_names_is_none(self) -> None:
        """不传 tool_names 时默认为 None，确保向后兼容。"""
        task = Task(goal="测试默认值")
        assert task.tool_names is None

    def test_task_explicit_tool_names(self) -> None:
        """显式传入 tool_names 时应保留传入值。"""
        names = frozenset({"tool_a", "tool_b"})
        task = Task(goal="测试显式赋值", tool_names=names)
        assert task.tool_names == names
        assert isinstance(task.tool_names, frozenset)

    def test_task_empty_tool_names(self) -> None:
        """显式传入空 frozenset 时应保留空值，不等于 None。"""
        task = Task(goal="测试空集合", tool_names=frozenset())
        assert task.tool_names is not None
        assert task.tool_names == frozenset()
