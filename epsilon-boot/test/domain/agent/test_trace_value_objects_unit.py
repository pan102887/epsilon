"""trace 值对象单元测试。"""

import dataclasses
import time

import pytest

from domain.agent.trace_value_objects import (
    AgentStepTrace,
    ApprovalTrace,
    ErrorTrace,
    ModelCallTrace,
    SessionTrace,
    ToolCallTrace,
)


class TestModelCallTrace:
    def test_kind_field(self):
        t = ModelCallTrace(
            round_num=1,
            model="gpt-4",
            prompt_id="chat@v1",
            input_tokens=100,
            output_tokens=50,
            latency_ms=200.0,
            timestamp_epoch=time.time(),
        )
        assert t.kind == "model_call"

    def test_frozen(self):
        t = ModelCallTrace(
            round_num=1,
            model="gpt-4",
            prompt_id="chat@v1",
            input_tokens=100,
            output_tokens=50,
            latency_ms=200.0,
            timestamp_epoch=time.time(),
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            t.round_num = 2  # type: ignore[misc]


class TestToolCallTrace:
    def test_kind_field(self):
        t = ToolCallTrace(
            round_num=1,
            tool_name="shell_exec",
            tool_call_id="tc_1",
            arguments_summary='{"cmd":"ls"}',
            result_summary="file.txt",
            success=True,
            latency_ms=50.0,
            timestamp_epoch=time.time(),
        )
        assert t.kind == "tool_call"

    def test_error_fields_optional(self):
        t = ToolCallTrace(
            round_num=1,
            tool_name="shell_exec",
            tool_call_id="tc_1",
            arguments_summary="",
            result_summary="",
            success=False,
            latency_ms=50.0,
            timestamp_epoch=time.time(),
            error_class="TimeoutError",
            error_message="超时",
        )
        assert t.error_class == "TimeoutError"


class TestApprovalTrace:
    def test_kind_field(self):
        t = ApprovalTrace(
            round_num=2,
            approval_id="appr_1",
            actions_summary=["shell_exec", "write_file"],
            timestamp_epoch=time.time(),
        )
        assert t.kind == "approval"


class TestErrorTrace:
    def test_kind_field(self):
        t = ErrorTrace(
            round_num=3,
            error_class="RuntimeError",
            error_message="something went wrong",
            timestamp_epoch=time.time(),
        )
        assert t.kind == "error"


class TestSessionTrace:
    def test_mixed_steps(self):
        steps: list[AgentStepTrace] = [
            ModelCallTrace(
                round_num=1,
                model="gpt-4",
                prompt_id="chat@v1",
                input_tokens=100,
                output_tokens=50,
                latency_ms=200.0,
                timestamp_epoch=1000.0,
            ),
            ToolCallTrace(
                round_num=1,
                tool_name="shell",
                tool_call_id="tc1",
                arguments_summary="ls",
                result_summary="ok",
                success=True,
                latency_ms=10.0,
                timestamp_epoch=1001.0,
            ),
        ]
        st = SessionTrace(session_id="sess_1", started_at_epoch=1000.0, steps=steps)
        assert len(st.steps) == 2
        assert st.steps[0].kind == "model_call"
        assert st.steps[1].kind == "tool_call"


class TestAllTracesHaveKind:
    def test_all_types_have_kind(self):
        """验证 AgentStepTrace union 中每个类型都有 kind 字段。"""
        import typing

        args = typing.get_args(AgentStepTrace)
        for cls in args:
            fields = {f.name for f in dataclasses.fields(cls)}
            assert "kind" in fields, f"{cls.__name__} 缺少 kind 字段"
